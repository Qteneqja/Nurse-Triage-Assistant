# Phase 4 Testing: Known Limitations & Future Work

This document outlines the current scope limitations and future enhancements for Phase 4 testing.

---

## Current Scope Limitations

### 1. LLM Mocking
**Limitation:** Tests use mocked/replayed LLM responses instead of live DeepSeek API calls.

- **Why:** 
  - Reduces test cost and latency
  - Makes tests deterministic and reproducible
  - Avoids API rate limits and quotas
  - No dependency on LLM availability

- **Impact:**
  - Tests verify orchestrator logic, not LLM accuracy
  - LLM hallucinations not caught (separate smoke test needed)
  - Real prompt engineering issues only surface in staging/production

- **Mitigation:**
  - Separate "smoke test" suite for live LLM (run on deployment)
  - Golden calls include expected SBAR/outputs as oracle
  - Manual review of LLM responses in staging

- **Future Work (Phase 5):**
  - Integrate real LLM calls with cost tracking
  - Replay fixtures for LLM responses (record → replay)
  - A/B test real vs mocked responses in staging

### 2. Speech-to-Text Accuracy
**Limitation:** Tests assume perfect transcription. No STT confidence/accuracy modeling.

- **Why:** Speech recognition is separate service (Twilio); testing focus is triage logic

- **Impact:**
  - Homophones ("no" vs "know") not simulated
  - Background noise, accents not modeled
  - Real-world caller speech variations not captured

- **Gaps:**
  - May not catch speech-matching edge cases
  - Implicit assumption: good audio quality

- **Future Work (Phase 5+):**
  - Add STT confidence levels to golden calls (`"metadata": {"stt_confidence": 0.95}`)
  - Simulate common STT errors:
    ```json
    {
      "original_speech": "I have no pain",
      "transcribed_as": "I have know pain"
    }
    ```
  - Offline speech recognition testing with sample audio files

### 3. Emotional & Prosodic Signals
**Limitation:** Tests are text-only; don't model tone, urgency, emotional distress.

- **Why:** Triage focuses on medical facts, not caller emotional state

- **Impact:**
  - Suicidal callers who say "I'm fine" not distinguished from genuinely calm callers
  - Panic/anxiety level not assessed from voice
  - Can't detect when caller is in acute distress

- **Gaps:**
  - May miss non-verbal cues indicating emergency

- **Future Work (Phase 5+):**
  - Add prosody markers: `"metadata": {"tone": "panicked", "speech_rate": "fast"}`
  - Integration with voice emotion detection API
  - Escalate on high-stress indicators even if words seem mild

### 4. Multi-Language Support
**Limitation:** All tests use English; no non-English caller scenarios.

- **Why:** Initial deployment is English-speaking regions only

- **Impact:**
  - May miss cultural/linguistic misunderstandings
  - No testing of medical term translation
  - Accent-related STT failures not covered

- **Scope:**
  - English-speaking patients only
  - US medical terminology and dispositions

- **Future Work (Phase 6+):**
  - Spanish test cases and translation validation
  - Mandarin/Cantonese for diversity
  - Medical term accuracy across languages
  - Localization of safety messages

### 5. Accessibility
**Limitation:** No testing for deaf/hard-of-hearing, speech-impaired, or blind callers.

- **Why:** Current system is voice-only via Twilio

- **Impact:**
  - Deaf callers cannot use system (accessibility failure)
  - Speech-impaired callers may not be understood
  - Blind callers who call from screen reader may have issues

- **Future Work (Phase 6+):**
  - Text-based triage option (SMS, chat)
  - TTY/relay service support
  - Screen-reader compatible web interface
  - Support for communication devices

### 6. Cluster & Distributed Deployment
**Limitation:** Load tests assume single-node deployment; no multi-server testing.

- **Why:** Current MVP is single-node; distributed setup is future

- **Impact:**
  - Session isolation tests don't cover shared state across servers
  - Redis cache invalidation not tested
  - Cross-server race conditions not caught

- **Gaps:**
  - May fail silently in distributed clusters
  - Session sync issues may appear in production

- **Future Work (Phase 5+):**
  - Use Docker Compose for multi-node testing
  - Test with Redis shared session store
  - Verify transaction consistency across writes
  - Load balancer sticky session behavior

### 7. Real-World PHI Handling
**Limitation:** PHI masking is mocked; not validated against actual PII databases.

- **Why:** Testing with real PII violates HIPAA/privacy; need synthetic data

- **Impact:**
  - May miss legitimate medical abbreviations that look like PHI
  - False positives (masking "St. Louis" as location)
  - No validation against national identifier databases

- **Gaps:**
  - Real-world PII patterns may not be caught

- **Future Work (Phase 5+):**
  - Synthetic PII generation (fake SSNs, license numbers)
  - Pattern validation against real PII format specs
  - Legal/compliance review

### 8. Regulatory Compliance Testing
**Limitation:** Technical tests don't cover legal/regulatory requirements.

- **Why:** Compliance is separate from functionality testing

- **Scope:**
  - HIPAA: Tech in place, but legal review separate
  - FDA: Device classification not assessed here
  - State-specific telemedicine laws: Not included

- **Future Work (Phase 5+):**
  - Audit logs compliance checklist
  - Informed consent flow testing
  - Regulatory documentation
  - Legal review for each state

### 9. A/B Testing & User Feedback
**Limitation:** Golden cases are baseline; no real user feedback loop.

- **Why:** Clinical deployment hasn't started yet

- **Impact:**
  - Cases may not reflect actual caller patterns
  - Assistant behavior may feel unnatural to real callers
  - Missed opportunities for UX improvement

- **Future Work (Phase 5+):**
  - Record actual caller interactions (with consent)
  - Compare real outcomes vs expected outcomes
  - Iterative refinement based on user feedback
  - Nurse feedback on SBAR quality

### 10. Edge Cases & Rare Conditions
**Limitation:** Golden cases focus on common complaints; rare/complex cases underrepresented.

- **Why:** Scope limited to 25 cases; can't cover all medical scenarios

- **Coverage:**
  - Common: chest pain, fever, headache, rash, etc. ✓
  - Uncommon: rare genetic diseases, exotic infections ✗
  - Comorbidities: Multiple simultaneous conditions (limited)

- **Future Work (Phase 5+):**
  - Crowdsource cases from real nurse triage lines
  - Add 50-100 more golden calls for rare conditions
  - Validation by clinical advisory board

---

## Testing Assumptions

### 1. System Availability
**Assumption:** DeepSeek API and Postgres are functioning.

- **Reality:** May be downtime, quota limits, regional outages
- **Mitigation:** Graceful fallback to HUMAN_REVIEW; error logging

### 2. User Cooperation
**Assumption:** Callers answer questions honestly and coherently.

- **Reality:** Some will lie, refuse to answer, or be incoherent
- **Covered:** Adversarial tests handle this
- **Gap:** May not catch all deceptive patterns

### 3. English Fluency
**Assumption:** Caller speaks English at conversational level.

- **Reality:** Some callers have limited English proficiency
- **Covered:** N/A (English-only scope)
- **Future:** Add language support

### 4. Device Functionality
**Assumption:** Caller's phone has working speaker/microphone.

- **Reality:** May have audio issues, noise, interference
- **Covered:** Partially (retry logic for unclear answers)
- **Gap:** No audio quality detection

### 5. Legal Age
**Assumption:** Caller is adult or guardian is present for minor.

- **Reality:** Children may call alone; guardians may not authorize
- **Covered:** Age capture only; no verification
- **Gap:** May provide care/advice to minor without parental consent

---

## Performance Characteristics

### Expected Latencies (with mocked LLM)

| Operation | Target | Typical | Max |
|-----------|--------|---------|-----|
| Single turn | <1 sec | 100 ms | 1 sec |
| Finalization | <2 sec | 500 ms | 2 sec |
| Session create | <10 ms | 2 ms | 50 ms |
| Session lookup | <10 ms | 1 ms | 100 ms |
| Concurrent 50 | <5 sec total | 3 sec | 10 sec |

**Note:** Real latencies with live LLM will be 3-10x higher.

---

## Scalability Constraints

### Single-Node Limits
- Max concurrent sessions: ~1000 (RAM-bound)
- Max turns per session: 12 (by design)
- Max stored sessions (in-memory): 5000

### Storage Limits
- In-memory: All sessions lost on restart (MVP acceptable)
- Postgres: Scales to arbitrary session count
- Transcript storage: Limited by disk (configure retention policy)

### Cost Implications
- Live testing: ~$0.10 per turn (DeepSeek API)
- 1000 test cases × 8 turns = $800 (motivation for mocking)
- Production: Cost monitoring recommended

---

## Security Testing Gaps

### What's NOT Tested
- SQL injection (abstracted by ORM)
- CSRF (stateless API; not applicable)
- Rate limiting (mock storage doesn't enforce)
- Brute force (authentication not in scope; Twilio handles)
- Man-in-the-middle (TLS is infrastructure concern)

### What IS Tested
- Prompt injection resistance ✓
- Authorization bypass (no auth in MVP, but safety gates) ✓
- Data exposure via error messages ✓
- Default credentials ✗ (not in scope)

### Future Security Testing (Phase 5)
- OWASP Top 10 assessment
- Penetration testing
- Vulnerability scanning
- Security audit by third party

---

## Integration Testing Gaps

### Twilio Voice Integration
**Status:** Not tested in Phase 4
- Tests assume voice input is already transcribed
- TwiML response generation is mocked
- Call recording/storage is not tested

**Future:** Integration test with Twilio sandbox

### EHR System Integration
**Status:** Out of scope (Phase 4)
- No testing of HL7/FHIR export
- No testing of bidirectional sync
- No testing of data format compatibility

**Future:** Integration with pilot hospital EHR

### Analytics & Monitoring
**Status:** Partial (metrics collection mocked)
- Prometheus metrics: Not tested
- Datadog integration: Not tested
- Grafana dashboards: Not tested

**Future:** Full observability stack testing

---

## Documentation Gaps

### Missing Documentation
- [ ] Architecture decision records (ADRs) for Phase 4 choices
- [ ] Test coverage mapping to requirements
- [ ] Data dictionary for golden call fields
- [ ] Regulatory justification for testing approach

### Material Reviewed But Not Included
- Vendor risk assessment for DeepSeek
- Market analysis of competing triage systems
- Cost-benefit analysis of automation vs nurse-only

---

## Future Work Roadmap

### Phase 5 (Next)
1. Add 50+ more golden calls (rare conditions, complex cases)
2. Implement real LLM smoke tests (live DeepSeek calls)
3. Add multi-language support (Spanish focus)
4. Integrate with real Twilio Voice sandbox
5. Performance profiling with realistic load (100+ concurrent)

### Phase 6 (Later)
1. EHR system integration testing
2. Distributed deployment testing (Kubernetes)
3. Accessibility testing (text-based alternative)
4. Regulatory compliance documentation
5. User acceptance testing (UAT) with real nurses

### Phase 7 (Future)
1. Federated learning for model improvements
2. Real-time outcome tracking vs predicted disposition
3. Continuous improvement loop from production data
4. International rollout & localization
5. Integration with wearable health devices

---

## Summary: What's Covered vs. Not Covered

### Well Covered ✓
- Red flag detection (deterministic rules)
- SBAR-first behavior
- Session isolation
- Error handling & fallbacks
- Prompt injection resilience
- JSON validation & retry logic
- Concurrent session handling
- Adversarial caller scenarios

### Partially Covered ⚠
- Protocol retrieval (mocked retrieval)
- Twilio integration (mocked)
- Observability (mocked metrics)
- PHI masking (tested, but simplified)
- Network failures (simulated)

### Not Covered ✗
- Live LLM accuracy
- Speech-to-text accuracy
- Multi-language support
- EHR integration
- Distributed deployment
- Real-world compliance
- User acceptance
- Accessibility features

---

## Recommendations for Deployment

1. **Pre-Deployment:**
   - Run full Phase 4 test suite (green lights required)
   - Manual QA with internal testers (n=5)
   - Security audit (penetration testing)

2. **Pilot Deployment:**
   - Soft launch with 1 hospital (~100 calls/day)
   - Real outcomes tracking (nurse feedback loop)
   - 2-week observation period

3. **Full Rollout:**
   - Multi-hospital deployment (5-10 sites)
   - Continuous monitoring (error rates, latency, user satisfaction)
   - Feedback-driven iterations

4. **Post-Deployment:**
   - Monthly outcome analysis vs. golden cases
   - Quarterly test suite expansion
   - Annual regulatory review

---

## Contact & Questions

For questions about test limitations or future work:
- Review this doc: `docs/phase4/known_limitations.md`
- Check test plan: `docs/phase4/test_plan.md`
- Review golden spec: `docs/phase4/golden_dataset_spec.md`
- Ask the team in the clinical safety meeting

