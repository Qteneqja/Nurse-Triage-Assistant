"""
DeepSeek Client - Single LLM Provider for Triage
Person A: Triage reasoning, patient summaries, and clinician SBAR via DeepSeek
"""
import logging
import json
import re
from typing import Optional
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.llm.config import DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, LLM_TIMEOUT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal legacy types — NEVER exported. Kept only so the guarded-context
# test infrastructure can verify _check_guarded() exists in each method.
# These types NEVER leave this module; all external callers get FinalDecision
# from the safety gate via GuardedLLM.
# ---------------------------------------------------------------------------

from enum import Enum as _Enum  # noqa: E402


class DispositionType(str, _Enum):
    """Internal legacy enum — maps to canonical values at the gate layer.
    NEVER EXPORTED. Exists only for internal backward compat.
    """
    SAFE = "SAFE"
    PCP = "PCP"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class TriageResult:
    """Internal legacy result stub — NEVER exits this module."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class SymptomItem:
    """Internal stub — NEVER exits this module."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

# Maximum questions before forcing disposition (safety limit)
MAX_QUESTIONS = 12

# Clinical information targets (internal checklist items - NOT spoken to patient)
CLINICAL_TARGETS = {
    "chest_pain": ["onset", "location_radiation", "severity", "sob", "diaphoresis", "cardiac_history"],
    "headache": ["onset_sudden", "severity", "vision_neuro", "neck_stiffness", "trauma"],
    "abdominal": ["location", "severity", "vomiting_blood", "fever", "last_bm"],
    "respiratory": ["severity_sob", "speaking_ability", "fever", "cough_type", "chest_pain", "self_care_tried"],
    "fever": ["temperature", "duration", "other_symptoms", "immunocompromised", "self_care_tried"],
    "injury": ["mechanism", "bleeding_severity", "pain_severity", "function_loss"],
    "neurological": ["onset", "weakness_numbness", "speech_vision", "balance", "headache"],
    "cold_flu": ["symptoms_present", "duration", "severity", "self_care_tried", "fever_level"],
    "gi_mild": ["symptoms", "duration", "severity", "self_care_tried", "hydration"],
    "general": ["onset", "severity", "associated_symptoms", "red_flags", "self_care_tried"]
}

# System prompt for natural, complaint-framed questions with safety guarantees
TRIAGE_SYSTEM_PROMPT = """You are the TRIAGE DECISION ENGINE for a Twilio voice intake app.

Your #1 job is to NEVER break the app.
That means: you MUST return VALID JSON every time. No extra text. No markdown. No explanations.

If you ever return non-JSON, empty text, or partially cut JSON, the call crashes and the user hears an app error. Don't do that.

========================
ABSOLUTE OUTPUT RULES
========================
1) Output MUST be a single JSON object (parseable by json.loads).
2) Do NOT wrap in ``` fences.
3) Do NOT include leading/trailing commentary.
4) Every required key MUST exist, even if you must use defaults.
5) Strings must be complete (no cut-off sentences).
6) Never return an empty response.

If you are uncertain, still output valid JSON with safe defaults.
If you are uncertain, still output valid JSON with safe defaults.

========================
WHAT YOU ARE DOING
========================
You receive the conversation so far (demographics + chief complaint + caller answers).
You must decide:
- what category this complaint is
- what intake items are already covered
- what to ask next (one question)
- OR when to stop and give a disposition
- AND always produce a final spoken message for the caller when stopping (or a blank string when not stopping).

========================
DISPOSITION RULES (IMPORTANT)
========================
Allowed dispositions:
- "EMERGENCY"
- "URGENT"
- "PCP"
- "SAFE"
- "HUMAN_REVIEW"

NOTE: These values are mapped to canonical dispositions (ER_NOW, URGENT, SCHEDULE, SELF_CARE, HUMAN_REVIEW) by the safety gate. The LLM may use either set.

Emergency triggers (not exhaustive): chest pain with red flags, severe SOB, stroke signs, uncontrolled bleeding, suicidal intent, severe allergic reaction, new bowel/bladder loss with back pain, etc.

If you stop:
- stop_flag MUST be true
- disposition MUST be one of the allowed values
- next_question MUST be "" (empty string)
- final_message MUST be non-empty

If you continue:
- stop_flag MUST be false
- disposition MUST be "" (empty string)
- next_question MUST be non-empty
- final_message MUST be "" (empty string)

========================
FINAL MESSAGE RULES (YOUR OTHER JOB)
========================
- If disposition == "EMERGENCY":
  - Tell them to call 911 or go to the nearest emergency department NOW.
  - Do NOT say a nurse will contact them.
- For ANY OTHER disposition ("URGENT", "PCP", "SAFE", "HUMAN_REVIEW", or anything non-emergency):
  - You MUST clearly say: "A nurse will contact you soon."
  - Add ONE safety-net line: if symptoms worsen or they feel unsafe, go to the ER or call emergency services.
- 2 to 4 sentences max. Plain language. No diagnosis. No questions.
- Avoid symbols that can break TwiML: do not use ampersand, less than, greater than. Use "and".

========================
REQUIRED JSON SCHEMA
========================
Return EXACTLY these keys:

{
  "complaint_category": string,
  "covered_items": [string],
  "remaining_items": [string],
  "target_item": string,
  "next_question": string,
  "stop_flag": boolean,
  "disposition": string,
  "final_message": string,
  "style_notes": string
}

========================
QUESTION STYLE (WHEN CONTINUING)
========================
- Ask ONE question only.
- Keep it short, voice-friendly.
- Prefer closed questions when possible (yes or no, scale 0 to 10, time duration).
- Use the caller's own words where reasonable.
- NEVER repeat the same question intent twice.

========================
CRASH-PROOF FALLBACKS
========================
If you cannot confidently classify:
- complaint_category = "general"
- stop_flag = false
- disposition = ""
- Ask a safe clarifying question.

If you decide to stop but lack details:
- disposition = "HUMAN_REVIEW" unless clear EMERGENCY.
- final_message must still follow the rules above (nurse contact soon for non-emergency).

========================
NATURAL CONVERSATION REQUIREMENTS
========================
========================
NATURAL CONVERSATION REQUIREMENTS
========================

1. USE THE CALLER'S EXACT WORDS:
   - Quote or mirror the caller's complaint terminology in your questions
   - If they say "chest pain", your questions must mention "chest pain"
   - If they say "persistent cold", your questions must mention "cold" or "cold symptoms"
   
2. COMPLAINT-FRAMED QUESTIONS (NOT GENERIC SCRIPTS):
   Examples:
   Chest pain: "How severe is your chest pain on a scale of 0 to 10?"
   Persistent cold: "What cold symptoms are you having: congestion, sore throat, cough, fever?"
   
3. WARM, CONVERSATIONAL TONE:
   - One question at a time
   - Natural phrasing, not robotic
   - Sound like a caring nurse

========================
STOP CONDITIONS (YOU MUST STOP WHEN)
========================

1. All clinical targets for the complaint are covered (remaining_items=[]), OR
2. Emergency red flag detected, OR
3. Enough information to safely make disposition, OR
4. Asked 10+ questions

========================
NOW PROCESS THE INPUT
========================
Use the messages provided below and return ONLY the JSON object that matches the schema."""


class DeepSeekClient:
    """Legacy DeepSeek LLM client.

    RUNTIME GUARD: Direct usage outside GuardedLLM / StructuredLLMClient
    is blocked.  Call ``_set_guarded_context(True)`` before using methods;
    GuardedLLM does this automatically via StructuredLLMClient._raw_call.
    Any other caller will get a RuntimeError.
    """

    # Class-level flag — set to True by GuardedLLM wrapper
    _guarded_context: bool = False

    @classmethod
    def _set_guarded_context(cls, value: bool) -> None:
        """Called by GuardedLLM / StructuredLLMClient to allow usage."""
        cls._guarded_context = value

    def __init__(self):
        # PRODUCTION GUARD: Direct usage is forbidden in production
        from src.config import ENVIRONMENT
        if ENVIRONMENT == "production" and not self.__class__._guarded_context:
            raise RuntimeError(
                "Direct DeepSeekClient instantiation is forbidden in production. "
                "All LLM calls must go through GuardedLLM."
            )
        self.client = AsyncOpenAI(
            base_url=DEEPSEEK_BASE_URL,
            api_key=DEEPSEEK_API_KEY,
            timeout=LLM_TIMEOUT
        )
        self.model = DEEPSEEK_MODEL
        logger.info("[DEEPSEEK] Client initialized")
        logger.info(f"[DEEPSEEK] base_url: {DEEPSEEK_BASE_URL}")
        logger.info(f"[DEEPSEEK] model: {self.model}")
        logger.info(f"[DEEPSEEK] API key configured: {bool(DEEPSEEK_API_KEY)}")

    def _check_guarded(self, method_name: str) -> None:
        """Raise if called outside GuardedLLM context."""
        if not self.__class__._guarded_context:
            raise RuntimeError(
                f"DeepSeekClient.{method_name}() called outside GuardedLLM context. "
                "All LLM calls must go through GuardedLLM to ensure safety gate coverage."
            )
    
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_triage_decision(
        self, 
        conversation_history: list[dict], 
        patient_facts: Optional[dict] = None
    ) -> TriageResult:
        """
        Analyze conversation and return triage decision with next question or final disposition
        Uses structured JSON output with complaint-based checklists and explicit stop conditions.
        
        Args:
            conversation_history: List of {"role": "user/assistant", "content": "..."}
            patient_facts: Optional dict with keys: patient_name, patient_age, patient_sex
        
        Returns:
            TriageResult with either next_question (if continuing) or stop_intake=True (if done)
        """
        self._check_guarded("get_triage_decision")
        try:
            # Count user responses to enforce MAX_QUESTIONS
            user_responses = [m for m in conversation_history if m.get("role") == "user"]
            question_count = len(user_responses)
            
            logger.info(f"[TRIAGE] Question count: {question_count}/{MAX_QUESTIONS}")
            
            # Force stop if exceeded MAX_QUESTIONS
            if question_count >= MAX_QUESTIONS:
                logger.warning(f"[TRIAGE] MAX_QUESTIONS ({MAX_QUESTIONS}) exceeded, forcing stop")
                return TriageResult(
                    triage_disposition=DispositionType.HUMAN_REVIEW,
                    confidence=0.8,
                    stop_intake=True,
                    next_question=None,
                    red_flags=["Maximum question limit reached"],
                    rationale_bullets=[
                        "Interview exceeded maximum question limit",
                        "Escalating to human clinician for comprehensive review"
                    ],
                    symptoms=[]
                )
            
            # Build context with patient facts
            messages = [{"role": "system", "content": TRIAGE_SYSTEM_PROMPT}]
            
            if patient_facts:
                facts_str = f"Patient demographics: Name: {patient_facts.get('patient_name', 'not provided')}, Age: {patient_facts.get('patient_age', 'not provided')}, Sex: {patient_facts.get('patient_sex', 'not provided')}"
                messages.append({"role": "system", "content": facts_str})
            
            # Use stored chief complaint from patient_facts to remind LLM to use caller's words
            chief_complaint = patient_facts.get("chief_complaint") if patient_facts else None
            if chief_complaint:
                logger.info(f"[TRIAGE] Using chief complaint for natural questions: '{chief_complaint}'")
                messages.append({
                    "role": "system",
                    "content": f"CRITICAL: The caller's chief complaint is '{chief_complaint}'. You MUST use these exact words ('{chief_complaint}') in your questions. Frame every question around '{chief_complaint}'."
                })
            else:
                logger.warning("[TRIAGE] No chief complaint found in patient_facts")
            
            # Add reminder about question count
            messages.append({
                "role": "system", 
                "content": f"Questions asked so far: {question_count}/{MAX_QUESTIONS}. Stop early if you have enough information."
            })
            
            # Add conversation history (keep last 15 messages for context)
            messages.extend(conversation_history[-15:])
            
            logger.info(f"[TRIAGE] Calling DeepSeek with {len(messages)} messages")
            
            # Call LLM - DO NOT use response_format as it causes DeepSeek to return empty responses
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore
                max_tokens=500,
                temperature=0.3
            )
            
            content = response.choices[0].message.content
            if not content:
                logger.warning("[TRIAGE] Empty response from DeepSeek")
                return self._fallback_result(conversation_history, question_count)
            
            content = content.strip()
            logger.info(f"[TRIAGE] Raw response: {content[:300]}")
            
            # Try to parse structured JSON output
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(0))
                    
                    # Log progress tracking
                    logger.info(f"[TRIAGE] Complaint category: {data.get('complaint_category', 'N/A')}")
                    logger.info(f"[TRIAGE] Covered items: {data.get('covered_items', [])}")
                    logger.info(f"[TRIAGE] Remaining items: {data.get('remaining_items', [])}")
                    logger.info(f"[TRIAGE] Target item: {data.get('target_item', 'N/A')}")
                    logger.info(f"[TRIAGE] Stop flag: {data.get('stop_flag', False)}")
                    
                    # Check if interview should stop
                    should_stop = data.get("stop_flag", False)
                    
                    if should_stop:
                        # Parse final disposition
                        disposition_str = data.get("disposition", "HUMAN_REVIEW").upper()
                        
                        # Map disposition
                        if disposition_str in DispositionType.__members__:
                            disposition = DispositionType[disposition_str]
                        else:
                            disposition = DispositionType.HUMAN_REVIEW
                        
                        logger.info(f"[TRIAGE] STOPPING - Disposition: {disposition.value}")
                        
                        # Store final_message in the result for use by Twilio handler
                        final_message = data.get("final_message", "")
                        logger.info(f"[TRIAGE] Final message: {final_message[:100]}...")
                        
                        return TriageResult(
                            triage_disposition=disposition,
                            confidence=0.85,
                            stop_intake=True,
                            next_question=final_message,  # Store final message in next_question for retrieval
                            red_flags=data.get("red_flags", []) if isinstance(data.get("red_flags"), list) else [],
                            rationale_bullets=[data.get("final_message", "Triage complete")] if data.get("final_message") else ["Triage complete"],
                            symptoms=[]
                        )
                    else:
                        # Continue with next question
                        next_question = data.get("next_question", "")
                        
                        if not next_question:
                            logger.warning("[TRIAGE] No next_question in JSON, using fallback")
                            return self._fallback_result(conversation_history, question_count)
                        
                        logger.info(f"[TRIAGE] CONTINUING - Next question: {next_question[:100]}")
                        
                        return TriageResult(
                            triage_disposition=DispositionType.HUMAN_REVIEW,
                            confidence=0.8,
                            stop_intake=False,
                            next_question=next_question,
                            red_flags=data.get("red_flags", []),
                            rationale_bullets=[],
                            symptoms=[]
                        )
                        
                except json.JSONDecodeError as e:
                    logger.warning(f"[TRIAGE] Failed to parse JSON: {e}")
                    logger.warning(f"[TRIAGE] Content was: {content}")
                except KeyError as e:
                    logger.warning(f"[TRIAGE] Missing expected key in JSON: {e}")
                except Exception as e:
                    logger.warning(f"[TRIAGE] Error processing JSON: {e}")
            
            # Attempt JSON repair with a second call
            logger.warning("[TRIAGE] No valid JSON found, attempting repair call")
            repaired = await self._attempt_json_repair(messages, content)
            if repaired:
                return repaired
            
            # Final fallback if repair also fails
            logger.warning("[TRIAGE] Repair failed, using fallback")
            return self._fallback_result(conversation_history, question_count)
            
        except Exception as e:
            logger.error(f"[TRIAGE] Error in get_triage_decision: {e}", exc_info=True)
            return self._fallback_result(conversation_history, question_count if 'question_count' in locals() else 0)
    
    async def _attempt_json_repair(self, messages: list, bad_content: str) -> Optional[TriageResult]:
        """Attempt to repair non-JSON response with a focused repair prompt"""
        try:
            # Extract the last user response from conversation to ensure we move forward
            last_user_msg = ""
            for msg in reversed(messages):
                if msg.get("role") == "user" and not msg.get("content", "").startswith("The previous"):
                    last_user_msg = msg.get("content", "")
                    break
            
            repair_prompt = f"""CRITICAL: You returned invalid output. The app CRASHED. You MUST return valid JSON NOW.

LAST ANSWER FROM CALLER: "{last_user_msg}"

You MUST:
1. Acknowledge this answer was received
2. Ask a NEW different question (NOT the same one)
3. Return ONLY valid JSON with NO markdown, NO explanations

Required JSON schema:
{{
  "complaint_category": "chest_pain|respiratory|general",
  "covered_items": ["item1", "item2"],
  "remaining_items": ["item3"],
  "target_item": "item3",
  "next_question": "NEW question here",
  "stop_flag": false,
  "disposition": "",
  "final_message": "",
  "style_notes": "note"
}}

If enough info to stop:
{{
  "complaint_category": "chest_pain",
  "covered_items": ["all", "items"],
  "remaining_items": [],
  "target_item": "",
  "next_question": "",
  "stop_flag": true,
  "disposition": "EMERGENCY",
  "final_message": "Call 911 immediately for severe chest pain",
  "style_notes": "emergency"
}}

Return ONLY JSON. No markdown. No text. Just the JSON object."""
            
            messages_repair = messages[:-1] + [{"role": "user", "content": repair_prompt}]
            
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages_repair,
                max_tokens=500,
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            if not content:
                return None
            
            # Clean content - remove markdown fences if present
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r'^```[a-z]*\n', '', content)
                content = re.sub(r'\n```$', '', content)
            
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                should_stop = data.get("stop_flag", False)
                
                if should_stop:
                    disposition_str = data.get("disposition", "HUMAN_REVIEW").upper()
                    disposition = DispositionType[disposition_str] if disposition_str in DispositionType.__members__ else DispositionType.HUMAN_REVIEW
                    
                    final_message = data.get("final_message", "")
                    logger.info(f"[TRIAGE] Repair successful with final message: {final_message[:100]}...")
                    
                    return TriageResult(
                        triage_disposition=disposition,
                        confidence=0.8,
                        stop_intake=True,
                        next_question=final_message,  # Store final message
                        red_flags=data.get("red_flags", []) if isinstance(data.get("red_flags"), list) else [],
                        rationale_bullets=[final_message] if final_message else ["Triage complete"],
                        symptoms=[]
                    )
                else:
                    next_question = data.get("next_question", "")
                    if next_question:
                        logger.info(f"[TRIAGE] Repair successful: {next_question[:100]}")
                        return TriageResult(
                            triage_disposition=DispositionType.HUMAN_REVIEW,
                            confidence=0.8,
                            stop_intake=False,
                            next_question=next_question,
                            red_flags=data.get("red_flags", []) if isinstance(data.get("red_flags"), list) else [],
                            rationale_bullets=[],
                            symptoms=[]
                        )
            
            return None
        except Exception as e:
            logger.error(f"[TRIAGE] Repair attempt failed: {e}")
            return None
    
    def _extract_chief_complaint(self, conversation_history: list[dict]) -> Optional[str]:
        """
        Extract the chief complaint from conversation history to use in natural questions.
        Looks for the first substantial user response (usually the chief complaint).
        """
        for msg in conversation_history:
            if msg.get("role") == "user":
                content = msg.get("content", "").strip()
                # Skip short responses like "yes", "no", single words
                if len(content) > 5 and content.lower() not in ["yes", "no", "yeah", "nope", "male", "female"]:
                    # This is likely the chief complaint
                    logger.info(f"[TRIAGE] Extracted chief complaint: '{content}'")
                    return content
        return None
    
    def _fallback_result(self, conversation_history: list[dict], question_count: int = 0) -> TriageResult:
        """Generate fallback result when LLM fails or output is invalid"""
        
        # Force stop if we're near the limit
        if question_count >= MAX_QUESTIONS - 1:
            logger.warning("[TRIAGE] Fallback triggered near MAX_QUESTIONS, forcing stop")
            return TriageResult(
                triage_disposition=DispositionType.HUMAN_REVIEW,
                confidence=0.7,
                stop_intake=True,
                next_question=None,
                red_flags=["Interview limit reached"],
                rationale_bullets=[
                    "Unable to parse LLM response near question limit",
                    "Escalating to human clinician for safety"
                ],
                symptoms=[]
            )
        
        # Progressive fallback questions based on count
        if question_count < 2:
            question = "Can you describe your main symptom or concern?"
        elif question_count < 4:
            question = "How long have you been experiencing this?"
        elif question_count < 6:
            question = "On a scale from 1 to 10, how severe is this symptom?"
        elif question_count < 8:
            question = "Have you noticed anything that makes it better or worse?"
        else:
            question = "Is there anything else important I should know?"
        
        return TriageResult(
            triage_disposition=DispositionType.HUMAN_REVIEW,
            confidence=0.6,
            stop_intake=False,
            next_question=question,
            red_flags=[],
            rationale_bullets=["Using fallback due to LLM error"]
        )
    
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def generate_patient_summary(
        self,
        triage_result: TriageResult,
        patient_facts: dict,
        recent_snippet: str
    ) -> str:
        """
        Generate patient-friendly summary of triage outcome
        
        Args:
            triage_result: The triage decision
            patient_facts: Patient demographics
            recent_snippet: Recent conversation excerpt
        
        Returns:
            Patient-friendly summary string
        """
        self._check_guarded("generate_patient_summary")
        try:
            prompt = f"""Based on this triage assessment, create a brief, compassionate message for the patient.

Disposition: {triage_result.triage_disposition.value}
Confidence: {triage_result.confidence:.0%}
Red Flags: {', '.join(triage_result.red_flags) if triage_result.red_flags else 'None identified'}
Clinical Reasoning: {', '.join(triage_result.rationale_bullets)}

Recent conversation:
{recent_snippet}

Write a clear, warm, 2-3 sentence message that:
1. Acknowledges their concern
2. Explains what they should do next (e.g., "call 911", "visit urgent care today", "schedule with your doctor", "try self-care")
3. Is reassuring and professional

Message for patient:"""

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.7
            )
            
            content = response.choices[0].message.content
            if content:
                return content.strip()
            
            # Fallback message
            return self._default_patient_message(triage_result.triage_disposition)
            
        except Exception as e:
            logger.error(f"[PATIENT_SUMMARY] Error: {e}", exc_info=True)
            return self._default_patient_message(triage_result.triage_disposition)
    
    def _default_patient_message(self, disposition: DispositionType) -> str:
        """Generate default patient message based on disposition"""
        messages = {
            DispositionType.EMERGENCY: "Based on your symptoms, this may be a medical emergency. Please call 911 or go to the nearest emergency room immediately.",
            DispositionType.URGENT: "Your symptoms suggest you should be seen soon. Please visit an urgent care center or emergency department today.",
            DispositionType.PCP: "You should schedule an appointment with your primary care doctor within the next few days to discuss these symptoms.",
            DispositionType.SAFE: "Your symptoms appear manageable with self-care. Monitor your condition and seek care if symptoms worsen.",
            DispositionType.HUMAN_REVIEW: "Thank you for providing this information. A healthcare professional will review your case and contact you shortly."
        }
        return messages.get(disposition, "Thank you for using our triage service. Please follow up with a healthcare provider as needed.")
    
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def generate_clinician_sbar(
        self,
        triage_result: TriageResult,
        patient_facts: dict,
        conversation_history: list[dict]
    ) -> str:
        """
        Generate SBAR (Situation-Background-Assessment-Recommendation) summary for clinician
        
        Args:
            triage_result: The triage decision
            patient_facts: Patient demographics
            conversation_history: Full conversation
        
        Returns:
            SBAR-formatted summary string
        """
        self._check_guarded("generate_clinician_sbar")
        try:
            # Build conversation text
            convo_text = "\n".join([
                f"{msg['role'].upper()}: {msg['content']}" 
                for msg in conversation_history[-20:]  # Last 20 messages
            ])
            
            prompt = f"""Create a clinical SBAR summary for this triage case.

PATIENT INFO:
Name: {patient_facts.get('patient_name', 'Not provided')}
Age: {patient_facts.get('patient_age', 'Not provided')}
Sex: {patient_facts.get('patient_sex', 'Not provided')}

TRIAGE OUTCOME:
Disposition: {triage_result.triage_disposition.value}
Confidence: {triage_result.confidence:.0%}
Red Flags: {', '.join(triage_result.red_flags) if triage_result.red_flags else 'None'}

CONVERSATION:
{convo_text}

Generate an SBAR summary using this format:
SITUATION: [Chief complaint and presenting symptoms]
BACKGROUND: [Relevant history, onset, duration, severity]
ASSESSMENT: [Clinical impression and red flags]
RECOMMENDATION: [Disposition and urgency]

Keep it concise, clinical, and focused on decision-relevant information."""

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.5
            )
            
            content = response.choices[0].message.content
            if content:
                return content.strip()
            
            # Fallback SBAR
            return self._default_sbar(triage_result, patient_facts)
            
        except Exception as e:
            logger.error(f"[SBAR] Error: {e}", exc_info=True)
            return self._default_sbar(triage_result, patient_facts)
    
    def _default_sbar(self, triage_result: TriageResult, patient_facts: dict) -> str:
        """Generate default SBAR when LLM fails"""
        name = patient_facts.get('patient_name', 'Patient')
        age = patient_facts.get('patient_age', 'Unknown')
        sex = patient_facts.get('patient_sex', 'Unknown')
        
        sbar = f"""SITUATION: {name}, {age}yo {sex}, completed triage intake

BACKGROUND: Patient called for triage assessment. Demographics and symptoms documented in conversation history.

ASSESSMENT: 
- Disposition: {triage_result.triage_disposition.value}
- Confidence: {triage_result.confidence:.0%}
- Red Flags: {', '.join(triage_result.red_flags) if triage_result.red_flags else 'None identified'}
- Reasoning: {', '.join(triage_result.rationale_bullets)}

RECOMMENDATION: {triage_result.triage_disposition.value} - Follow standard protocols for this disposition level."""
        
        return sbar


    async def generate_handoff_report(self, session_id: str, session_metadata: dict, conversation_history: list[dict], triage_result: TriageResult) -> dict:
        """
        Generate comprehensive handoff report with SBAR and structured JSON
        
        Args:
            session_id: Session identifier
            session_metadata: Patient demographics and stored metadata
            conversation_history: Full conversation
            triage_result: Final triage decision
        
        Returns:
            Dict with 'sbar' (text) and 'structured' (dict) keys
        """
        self._check_guarded("generate_handoff_report")
        try:
            # Build conversation text
            convo_text = "\n".join([
                f"{msg['role'].upper()}: {msg['content']}" 
                for msg in conversation_history
            ])
            
            patient_name = session_metadata.get('patient_name', 'Unknown')
            patient_age = session_metadata.get('patient_age', 'Unknown')
            patient_sex = session_metadata.get('patient_sex', 'Unknown')
            chief_complaint = session_metadata.get('chief_complaint', 'Not documented')
            
            # Generate SBAR
            sbar_prompt = f"""Create a clinical SBAR handoff note for this triage call.

PATIENT INFO:
Name: {patient_name}
Age: {patient_age}
Sex: {patient_sex}
Chief Complaint: {chief_complaint}

TRIAGE OUTCOME:
Disposition: {triage_result.triage_disposition.value}
Confidence: {triage_result.confidence:.0%}
Red Flags: {', '.join(triage_result.red_flags) if triage_result.red_flags else 'None'}

CONVERSATION:
{convo_text}

Generate an SBAR note following this format:

S (SITUATION): Patient identifiers, chief complaint, disposition/acuity level
B (BACKGROUND): Relevant history, onset/duration, medications/allergies if mentioned
A (ASSESSMENT): Key positive findings, key negative findings, red flags present/absent, working complaint category
R (RECOMMENDATION): What was advised (ER/urgent/PCP/self-care), safety-net instructions

Be concise and clinical. Only include information that was actually discussed. Use 'not assessed' for missing data."""
            
            sbar_response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": sbar_prompt}],
                max_tokens=600,
                temperature=0.4
            )
            
            sbar_text = sbar_response.choices[0].message.content or "SBAR generation failed"
            
            # Generate structured JSON
            json_prompt = f"""Extract structured data from this triage call. Return ONLY valid JSON.

CONVERSATION:
{convo_text}

Return JSON with this schema:
{{
  "patient": {{"name": "{patient_name}", "age": "{patient_age}", "sex": "{patient_sex}"}},
  "chief_complaint": "...",
  "onset_duration": "...",
  "severity_0_10": "...",
  "key_symptoms_positive": [],
  "key_symptoms_negative": [],
  "red_flags": {{"present": false, "items": []}},
  "risk_factors": [],
  "disposition": {{"level": "{triage_result.triage_disposition.value}", "rationale": "..."}},
  "caller_quotes": [],
  "recommended_next_steps": "..."
}}

Use null or 'unknown' for data not collected. No hallucinations."""
            
            json_response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": json_prompt}],
                max_tokens=800,
                temperature=0.3
            )
            
            json_content = json_response.choices[0].message.content or "{}"
            json_match = re.search(r'\{.*\}', json_content, re.DOTALL)
            
            if json_match:
                structured_data = json.loads(json_match.group(0))
            else:
                # Fallback structured data
                structured_data = {
                    "patient": {"name": patient_name, "age": patient_age, "sex": patient_sex},
                    "chief_complaint": chief_complaint,
                    "disposition": {"level": triage_result.triage_disposition.value, "rationale": ", ".join(triage_result.rationale_bullets)}
                }
            
            return {
                "sbar": sbar_text.strip(),
                "structured": structured_data,
                "session_id": session_id
            }
            
        except Exception as e:
            logger.error(f"[HANDOFF] Error generating report: {e}", exc_info=True)
            # Return minimal fallback
            return {
                "sbar": f"SITUATION: {session_metadata.get('patient_name', 'Unknown')} called for triage.\nDisposition: {triage_result.triage_disposition.value}\nError generating full report.",
                "structured": {
                    "patient": {
                        "name": session_metadata.get('patient_name', 'Unknown'),
                        "age": session_metadata.get('patient_age', 'Unknown'),
                        "sex": session_metadata.get('patient_sex', 'Unknown')
                    },
                    "chief_complaint": session_metadata.get('chief_complaint', 'Not documented'),
                    "disposition": {"level": triage_result.triage_disposition.value, "rationale": "Error in report generation"}
                },
                "session_id": session_id
            }


# Singleton instance
_client = None

def get_deepseek_client() -> DeepSeekClient:
    global _client
    if _client is None:
        _client = DeepSeekClient()
    return _client
