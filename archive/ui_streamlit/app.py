"""Streamlit UI for Medical Triage Chatbot - Hackathon Demo"""
import streamlit as st
import time
from typing import Optional, Dict, Any
from api_client import TriageAPIClient
from demo_scenarios import DEMO_SCENARIOS

# Page config
UI_VERSION = "v1.8.4"
st.set_page_config(
    page_title="EDGE CARE",
    page_icon="EC",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    body, .stApp {
        background-color: #ffffff;
        color: #000000;
    }
    .stApp * {
        color: #000000;
    }
    section[data-testid="stSidebar"] {
        background-color: #f0f0f0;
    }
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #e9480d;
        text-align: center;
        margin-bottom: 1rem;
    }
    .chat-message {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
        display: flex;
        flex-direction: column;
        color: #000000;
    }
    .user-message {
        background-color: #f0f0f0;
        align-items: flex-end;
        border: 1px solid #e9480d;
    }
    .assistant-message {
        background-color: #ffffff;
        align-items: flex-start;
        border: 1px solid #f0f0f0;
    }
    .chat-message strong {
        color: #000000;
        font-weight: 600;
    }
    .stButton button,
    .stFormSubmitButton button,
    button[data-testid="baseButton-secondary"],
    button[data-testid="baseButton-primary"] {
        width: 100%;
        background-color: #e9480d;
        color: #000000;
        border: none;
    }
    .stButton button:hover,
    .stFormSubmitButton button:hover,
    button[data-testid="baseButton-secondary"]:hover,
    button[data-testid="baseButton-primary"]:hover,
    .stButton button:active,
    .stFormSubmitButton button:active,
    button[data-testid="baseButton-secondary"]:active,
    button[data-testid="baseButton-primary"]:active {
        background-color: #f05a24;
        color: #000000;
    }
    .stTextInput > div > div > input {
        background-color: #ffffff;
        color: #000000;
        border: 2px solid #000000;
        border-radius: 10px;
        caret-color: #000000;
        outline: none;
        box-shadow: none;
    }
    .stTextInput > div > div > input:focus {
        border-color: #000000;
        outline: none;
        box-shadow: none;
    }
    .stTextInput label {
        color: #000000;
    }
    .stTextInput div[data-baseweb="input"] {
        background-color: #ffffff;
    }
    .stTextInput div[data-baseweb="base-input"] {
        background-color: #ffffff;
    }
</style>
""", unsafe_allow_html=True)

st.caption(f"UI Version: {UI_VERSION}")

# Initialize API client
@st.cache_resource
def get_api_client():
    return TriageAPIClient(base_url="http://127.0.0.1:8001")

api_client = get_api_client()

# Initialize session state
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_question" not in st.session_state:
    st.session_state.current_question = None
if "triage_complete" not in st.session_state:
    st.session_state.triage_complete = False
if "final_result" not in st.session_state:
    st.session_state.final_result = None
if "intake_summary" not in st.session_state:
    st.session_state.intake_summary = None
if "is_sending" not in st.session_state:
    st.session_state.is_sending = False
if "last_sent_message" not in st.session_state:
    st.session_state.last_sent_message = ""
if "message_counter" not in st.session_state:
    st.session_state.message_counter = 0
if "request_id" not in st.session_state:
    st.session_state.request_id = 0
if "conversation_stage" not in st.session_state:
    st.session_state.conversation_stage = "NOT_STARTED"
if "ui_version" not in st.session_state:
    st.session_state.ui_version = UI_VERSION

def reset_session():
    """Reset the session state and clear all caches"""
    st.session_state.session_id = None
    st.session_state.messages = []
    st.session_state.current_question = None
    st.session_state.triage_complete = False
    st.session_state.final_result = None
    st.session_state.intake_summary = None
    st.session_state.is_sending = False
    st.session_state.last_sent_message = ""
    st.session_state.message_counter = 0
    st.session_state.request_id = 0
    st.session_state.conversation_stage = "NOT_STARTED"
    
    # Clear Streamlit caches
    st.cache_data.clear()
    st.cache_resource.clear()

if st.session_state.ui_version != UI_VERSION:
    reset_session()
    st.session_state.ui_version = UI_VERSION

def handle_user_input(user_input: str):
    """Handle user input with duplicate send prevention and loading state"""
    # Guard against empty input
    if not user_input or not user_input.strip():
        return
    
    # Guard against duplicate sends
    if st.session_state.is_sending:
        print(f"[UI] Blocked duplicate send - already sending")
        return
    
    # Guard against sending the same message twice
    if user_input.strip() == st.session_state.last_sent_message:
        print(f"[UI] Blocked duplicate send - same message")
        return
    
    # Set sending state with request ID
    st.session_state.is_sending = True
    st.session_state.request_id += 1
    st.session_state.last_sent_message = user_input.strip()
    st.session_state.message_counter += 1
    
    request_id = st.session_state.request_id
    print(f"[UI] Request #{request_id}: Sending user input '{user_input[:50]}'")
    
    try:
        send_user_answer(user_input, request_id)
    finally:
        # Always reset sending state
        st.session_state.is_sending = False
        print(f"[UI] Request #{request_id}: Complete")

def start_new_session():
    """Start a new triage session"""
    reset_session()
    print(f"[UI] Starting new session...")
    response = api_client.start_session()
    if response:
        st.session_state.session_id = response["session_id"]
        st.session_state.current_question = response["first_question"]
        st.session_state.conversation_stage = "GREETING"
        st.session_state.messages.append({
            "role": "assistant",
            "content": response["first_question"]
        })
        print(f"[UI] New session started: {response['session_id'][:8]}, Stage=GREETING")
        return True
    print(f"[UI] Failed to start session")
    return False

@st.cache_data(ttl=300)
def get_demo_scenario(scenario_key: str) -> Optional[Dict[str, Any]]:
    """Get demo scenario data (cached)"""
    return DEMO_SCENARIOS.get(scenario_key)

def load_demo_scenario(scenario_key: str):
    """Load a demo scenario"""
    scenario = get_demo_scenario(scenario_key)
    if not scenario:
        st.error(f"Scenario {scenario_key} not found")
        return
    
    # Start new session
    if not start_new_session():
        st.error("Failed to start session")
        return
    
    # Auto-answer questions with scenario responses
    st.session_state.messages.append({
        "role": "system",
        "content": f"**Demo Scenario: {scenario['name']}** - {scenario['description']}"
    })

    def _select_demo_answer(question: str, scenario_data: Dict[str, Any], followup_index: int) -> tuple[Optional[str], int]:
        q = (question or "").lower()
        answers = scenario_data.get("answers", {})
        yes_no = scenario_data.get("yes_no", {})

        if "name" in q:
            return answers.get("name"), followup_index
        if "old are you" in q or "age" in q:
            return answers.get("age"), followup_index
        if "sex" in q or "gender" in q:
            return answers.get("sex"), followup_index
        if "category" in q or "which" in q and any(k in q for k in ["breathing", "chest", "injury", "fever", "stomach", "other"]):
            return answers.get("category"), followup_index
        if any(k in q for k in ["bring you", "what's wrong", "what is wrong", "chief complaint", "problem", "symptom"]):
            return answers.get("complaint"), followup_index
        if "how long" in q or "duration" in q:
            return answers.get("duration"), followup_index
        if "sudden" in q or "gradual" in q or "start" in q or "onset" in q:
            return answers.get("onset"), followup_index
        if "severity" in q or "scale" in q or "severe" in q:
            return answers.get("severity"), followup_index

        if "chest pain" in q and "now" in q:
            return yes_no.get("chest_pain_now"), followup_index
        if "speak" in q and "sentences" in q:
            return yes_no.get("can_speak_full_sentences"), followup_index
        if "faint" in q or "passed out" in q or "confusion" in q:
            return yes_no.get("fainted_or_altered"), followup_index
        if "bleeding" in q:
            return yes_no.get("heavy_bleeding"), followup_index
        if "fever" in q:
            return yes_no.get("fever_present"), followup_index
        if "dehydration" in q or "keeping fluids" in q or "dizziness" in q:
            return yes_no.get("dehydration_signs"), followup_index

        followups = scenario_data.get("followups", [])
        if followup_index < len(followups):
            return followups[followup_index], followup_index + 1

        return None, followup_index

    followup_index = 0
    max_steps = 12 + len(scenario.get("followups", []))
    steps = 0

    while steps < max_steps:
        question = st.session_state.current_question or ""
        answer_text, followup_index = _select_demo_answer(question, scenario, followup_index)
        if not answer_text:
            break

        time.sleep(0.3)  # Small delay for demo effect

        # Add user answer to chat
        st.session_state.messages.append({
            "role": "user",
            "content": answer_text
        })

        # Send answer to API
        response = api_client.send_answer(st.session_state.session_id, answer_text)

        if not response:
            st.error("Failed to send answer")
            return

        steps += 1

        # Check if triage is complete
        if not response.get("continue_intake", True):
            st.session_state.triage_complete = True

            # Get intake summary
            summary = api_client.get_summary(st.session_state.session_id)
            st.session_state.intake_summary = summary

            st.session_state.messages.append({
                "role": "assistant",
                "content": "Thank you. I have enough information. Let me analyze your symptoms..."
            })

            # Auto-finalize to generate reports
            result = api_client.finalize_session(st.session_state.session_id)
            if result:
                st.session_state.final_result = result
            break
        else:
            # Add next question
            st.session_state.current_question = response.get("next_question")
            st.session_state.messages.append({
                "role": "assistant",
                "content": response["next_question"]
            })

    st.rerun()

def send_user_answer(answer: str, request_id: int):
    """Send user's answer to the API"""
    if not st.session_state.session_id:
        st.error("No active session. Please start a new conversation.")
        print(f"[UI] Request #{request_id}: No active session")
        return
    
    print(f"[UI] Request #{request_id}: Adding user message to UI")
    # Add user message
    st.session_state.messages.append({
        "role": "user",
        "content": answer
    })
    
    # Send to API
    print(f"[UI] Request #{request_id}: Calling backend API...")
    response = api_client.send_answer(st.session_state.session_id, answer)
    
    if not response:
        st.error("Failed to send answer to server")
        print(f"[UI] Request #{request_id}: API call failed")
        return
    
    print(f"[UI] Request #{request_id}: Received response, continue_intake={response.get('continue_intake', True)}")
    
    # Check if triage is complete
    if not response.get("continue_intake", True):
        st.session_state.triage_complete = True
        st.session_state.conversation_stage = "COMPLETE"
        
        # Get intake summary
        summary = api_client.get_summary(st.session_state.session_id)
        st.session_state.intake_summary = summary
        
        st.session_state.messages.append({
            "role": "assistant",
            "content": "Thank you. I have enough information. Let me analyze your symptoms..."
        })

        # Auto-finalize to generate reports
        result = api_client.finalize_session(st.session_state.session_id)
        if result:
            st.session_state.final_result = result
        
        print(f"[UI] Request #{request_id}: Triage complete, Stage=COMPLETE")
        
        # Clear caches when session ends
        st.cache_data.clear()
        st.cache_resource.clear()
    else:
        # Update stage based on question content
        next_q = response.get("next_question", "").lower()
        if "name" in next_q:
            st.session_state.conversation_stage = "ASK_NAME"
        elif "age" in next_q:
            st.session_state.conversation_stage = "ASK_AGE"
        elif "sex" in next_q:
            st.session_state.conversation_stage = "ASK_SEX"
        elif "bring" in next_q or "wrong" in next_q:
            st.session_state.conversation_stage = "ASK_COMPLAINT"
        else:
            st.session_state.conversation_stage = "SYMPTOMS"
        
        # Add next question
        st.session_state.current_question = response.get("next_question")
        st.session_state.messages.append({
            "role": "assistant",
            "content": response["next_question"]
        })
        
        print(f"[UI] Request #{request_id}: Next question added, Stage={st.session_state.conversation_stage}")

def finalize_triage():
    """Get final triage results"""
    if not st.session_state.session_id:
        st.error("No active session")
        return
    
    result = api_client.finalize_session(st.session_state.session_id)
    if result:
        st.session_state.final_result = result

# ============= MAIN UI =============

st.markdown('<h1 class="main-header">EDGE CARE</h1>', unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #666;'>Automated phone triage powered by Large Language Models</p>", unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.header("Quick Demo")
    st.markdown("**Load Pre-Scripted Scenarios**")
    
    if st.button("🟢 Scenario A: Non-Urgent", use_container_width=True):
        load_demo_scenario("scenario_a")
    
    if st.button("Scenario B: Red Flag Emergency", use_container_width=True):
        load_demo_scenario("scenario_b")
    
    if st.button("🟡 Scenario C: Ambiguous Case", use_container_width=True):
        load_demo_scenario("scenario_c")
    
    st.markdown("---")
    
    if st.button("🆕 Start New Conversation", use_container_width=True):
        if start_new_session():
            st.rerun()
    
    if st.button("🔄 Reset", use_container_width=True):
        reset_session()
        st.rerun()
    
    st.markdown("---")
    
    # Debug panel
    with st.expander("🐛 Debug Info"):
        st.caption("**Session State:**")
        st.caption(f"Session ID: {st.session_state.session_id[:8] if st.session_state.session_id else 'None'}")
        st.caption(f"Stage: {st.session_state.conversation_stage}")
        st.caption(f"Messages: {len(st.session_state.messages)}")
        st.caption(f"Request ID: {st.session_state.request_id}")
        st.caption(f"Sending: {st.session_state.is_sending}")
        st.caption(f"Complete: {st.session_state.triage_complete}")
    
    st.markdown("---")
    st.caption("Hackathon Demo - 2026")

# Main content area
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("💬 Conversation")
    
    # Chat container
    chat_container = st.container(height=500)
    
    with chat_container:
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                st.markdown(f"""
                <div class="chat-message user-message">
                    <strong>You:</strong> {msg['content']}
                </div>
                """, unsafe_allow_html=True)
            elif msg["role"] == "assistant":
                st.markdown(f"""
                <div class="chat-message assistant-message">
                    <strong>Assistant:</strong> {msg['content']}
                </div>
                """, unsafe_allow_html=True)
            elif msg["role"] == "system":
                st.info(msg["content"])
    
    # Input area
    if not st.session_state.triage_complete and st.session_state.session_id:
        # Use form for Enter key support
        with st.form(key="user_input_form", clear_on_submit=True):
            user_input = st.text_input(
                "Your answer:",
                key="user_input",
                placeholder="Type a response..."
            )
            
            col_send, col_space = st.columns([1, 3])
            with col_send:
                submitted = st.form_submit_button("Send", use_container_width=True)
            
            if submitted and user_input.strip():
                with st.spinner("Sending..."):
                    handle_user_input(user_input)
                st.rerun()
        
        # Auto-scroll to bottom after new messages
        st.markdown(
            """<script>
            setTimeout(function() {
                var chatContainer = window.parent.document.querySelector('[data-testid="stVerticalBlock"]');
                if (chatContainer) {
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                }
            }, 100);
            </script>""",
            unsafe_allow_html=True
        )
    
    elif not st.session_state.session_id:
        st.info("Click 'Start New Conversation' or load a demo scenario to begin")

with col2:
    st.subheader("Intake Summary")
    
    if st.session_state.intake_summary:
        summary = st.session_state.intake_summary
        
        st.markdown("**Current Disposition:**")
        disp = summary.get("current_disposition")
        if disp:
            if disp == "EMERGENCY":
                st.error(f"{disp}")
            elif disp == "URGENT":
                st.warning(f"{disp}")
            else:
                st.info(f"{disp}")
        else:
            st.write("Not yet determined")
        
        if summary.get("confidence"):
            st.metric("Confidence", f"{int(summary['confidence'] * 100)}%")
        
        st.markdown("**Red Flags:**")
        red_flags = summary.get("red_flags", [])
        if red_flags:
            for flag in red_flags:
                if flag == "LLM_ERROR":
                    st.error(f"{flag} - Check 'Diagnostic Info' below for details")
                else:
                    st.warning(f"{flag}")
        else:
            st.write("None detected")

        st.markdown("**Symptoms Recorded:**")
        symptoms = summary.get("symptoms", [])
        if symptoms:
            for symptom in symptoms:
                name = symptom.get("name", "Unknown")
                severity = symptom.get("severity", "unknown")
                notes = symptom.get("notes")
                if notes:
                    st.write(f"• {name} — {severity} ({notes})")
                else:
                    st.write(f"• {name} — {severity}")
        else:
            st.write("None recorded yet")

        st.markdown("**Severity Reasoning:**")
        rationale = summary.get("rationale_bullets", [])
        if rationale:
            for bullet in rationale:
                st.write(f"• {bullet}")
        else:
            st.write("Not available yet")
        
        # Show diagnostic info if there are issues
        if "LLM_ERROR" in red_flags:
            with st.expander("Diagnostic Info", expanded=True):
                st.markdown("**Error Details:**")
                # The actual error is in the intake summary (if available)
                if st.session_state.intake_summary:
                    st.code(str(st.session_state.intake_summary), language="json")
        
        st.markdown(f"**Messages:** {summary.get('message_count', 0)}")
        
        st.markdown("---")
        
        if not st.session_state.final_result:
            if st.button("Get Triage Decision", use_container_width=True):
                with st.spinner("Analyzing..."):
                    finalize_triage()
                    st.rerun()
    else:
        st.info("Summary will appear here as you answer questions")

# Final Results Panel
if st.session_state.final_result:
    st.markdown("---")
    st.subheader("Final Triage Results")
    
    result = st.session_state.final_result
    
    col_disp, col_conf = st.columns(2)
    
    with col_disp:
        disposition = result.get("disposition", "UNKNOWN")
        
        # Color code by disposition
        if disposition == "EMERGENCY":
            st.error(f"### {disposition}")
        elif disposition == "URGENT":
            st.warning(f"### {disposition}")
        else:
            st.success(f"### {disposition}")
    
    with col_conf:
        confidence = result.get("confidence", 0)
        st.metric("Confidence", f"{int(confidence * 100)}%")
    
    # Red flags
    if result.get("red_flags"):
        st.markdown("**Red Flags Detected:**")
        for flag in result["red_flags"]:
            st.error(f"• {flag}")
    
    # Rationale
    if result.get("rationale_bullets"):
        st.markdown("**Clinical Reasoning:**")
        for bullet in result["rationale_bullets"]:
            st.info(f"• {bullet}")
    
    # SBAR Handoff
    with st.expander("SBAR Clinical Handoff (for healthcare provider)"):
        st.markdown(result.get("clinician_summary", "N/A"))
    
    # Patient summary
    with st.expander("Patient Summary"):
        st.markdown(result.get("patient_summary", "N/A"))
