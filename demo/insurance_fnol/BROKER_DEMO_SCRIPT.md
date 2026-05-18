# Insurance FNOL Broker Demo Script

## Demo Goal

Show how the platform can collect consistent first notice of loss details after hours, route urgent or unclear cases for human review, and produce structured data for broker, agency, carrier, dashboard, or CRM handoff.

## Who This Is For

- Independent brokers and agencies that receive claim calls outside business hours.
- Carrier intake teams that want cleaner FNOL capture before adjuster review.
- Internal operations leaders evaluating voice automation for non-clinical workflows.
- Product stakeholders comparing dashboard-ready intake against voicemail or free-text notes.

## 60-Second Elevator Pitch

This is an offline, deterministic insurance FNOL demo. A caller can report an auto accident, property damage, water damage, theft, glass damage, or an information-only question. The assistant collects the core facts, gives consistent safety and claim-decision disclaimers, and routes the case to emergency services guidance, urgent adjuster review, standard intake, documents needed, information only, or human review. It does not decide coverage, approve a claim, estimate payout, give legal advice, or call external systems by default.

## Problem This Solves

- After-hours callers often leave incomplete voicemail.
- Brokers and adjusters spend time chasing missing policy, location, date, and document details.
- Urgent losses can sit in the same queue as routine questions.
- Claim notes can be inconsistent across staff, channels, and offices.
- Early handoff often lacks structured data for dashboards or CRM workflows.

## What The AI Assistant Does

- Collects first notice of loss details in a repeatable voice flow.
- Captures policy number, claim type, loss date/time, location, and incident summary.
- Asks targeted follow-up questions based on claim type.
- Flags immediate safety issues and urgent losses.
- Produces structured claim data for dashboard or CRM handoff.
- Gives consistent disclaimers on every demo path.
- Routes unclear or incomplete cases to human review or documents needed.

## What The AI Assistant Does NOT Do

- It does not confirm coverage.
- It does not approve or deny claims.
- It does not estimate settlement, payout, or repair cost.
- It does not give legal advice.
- It does not replace a licensed broker, adjuster, or carrier representative.
- It does not make live Twilio, LLM, carrier, broker management system, or CRM calls in this demo.

## Demo Setup

1. Open `demo/insurance_fnol/scenarios.json`.
2. Choose one scenario for the live walk-through.
3. Optionally run:

   ```bash
   python scripts/run_insurance_demo.py --scenario water_damage_mitigation
   ```

4. Keep the matching expected output open from `demo/insurance_fnol/expected_outputs/`.
5. Keep the matching transcript open from `demo/insurance_fnol/transcripts/`.
6. Mention that the placeholder phone number is for local/demo routing only: `+15555550130`.

## Suggested Live Scenario To Run First

Use `water_damage_mitigation`.

Why it works well:

- The scenario is urgent but not an emergency.
- It shows after-hours value clearly.
- It captures mitigation need, property safety, photos, and complete FNOL details.
- It naturally leads to a broker/adjuster handoff conversation.

## What To Point Out During The Demo

- The assistant opens with a safe scope statement.
- The scripted intake gathers the same required fields every time.
- Follow-up questions are claim-type aware.
- The water scenario routes to `URGENT_ADJUSTER_REVIEW` because mitigation is needed.
- The active fire scenario routes to `EMERGENCY_SERVICES_NOW` and prioritizes safety.
- The information-only scenario avoids creating an implied claim approval or denial.
- Missing details are surfaced as structured `missing_information`.
- Disclaimers are stored in the structured output, not just spoken.

## How Structured Data Appears After The Call

The expected output JSON mirrors the implemented insurance extraction schema. It includes:

- `workflow_id` and `vertical`
- caller and policy fields
- claim type, loss date/time, loss location, and incident summary
- safety indicators such as injury, emergency services, and property security
- document indicators such as police/fire report and photos/documents
- `missing_information`
- `recommended_routing`
- `confidence`
- `human_review_required`
- `disclaimers_given`

## Safety And Compliance Positioning

Position the assistant as intake and routing support. It captures facts, applies deterministic routing, and creates a cleaner handoff for licensed humans. It does not make coverage, liability, claim approval, legal, or payout decisions. For active danger, it instructs the caller to contact emergency services immediately.

## Example Closing Pitch

For a broker or agency, the value is not replacing the adjuster. The value is making sure the first call produces usable facts: who called, how to reach them, what happened, where and when it happened, whether anyone is unsafe, whether mitigation is needed, and what documents are available. That means fewer missed details, faster triage, and cleaner handoff when your team opens the dashboard in the morning.

## Follow-Up Questions To Ask The Broker Or Agency

- How do after-hours claim calls reach your team today?
- Which claim types create the most back-and-forth for missing details?
- Who should receive urgent after-hours notifications?
- What fields would need to sync into your broker management system or CRM?
- Which disclaimers does your compliance team require?
- Would you pilot with one line of business, one office, or all after-hours FNOL calls?

## Pilot Success Metrics

- Percentage of after-hours FNOL calls captured with complete required fields.
- Reduction in callback attempts for missing details.
- Time from call to broker/adjuster review.
- Percentage of urgent cases routed correctly.
- Number of information-only calls separated from claim starts.
- Broker/adjuster satisfaction with handoff quality.
- Caller satisfaction with after-hours availability.
