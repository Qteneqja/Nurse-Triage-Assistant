# Insurance FNOL Demo Pack

## What It Is

A repeatable demo package for insurance first notice of loss intake. It shows how the voice decision-support platform can collect claim details, apply safe routing rules, and prepare structured data for broker, agency, carrier, dashboard, or CRM review.

## How It Works

The demo uses scripted fake claim scenarios and expected JSON outputs. It runs offline by default and does not call Twilio, an LLM, a carrier system, or any external API.

## Supported Claim Types

- Auto accident
- Property damage
- Water damage
- Theft/loss
- Glass/window damage
- Liability/business claim
- Other or information-only inquiry

## Routing Outcomes

- `EMERGENCY_SERVICES_NOW`
- `URGENT_ADJUSTER_REVIEW`
- `STANDARD_CLAIM_INTAKE`
- `DOCUMENTS_NEEDED`
- `INFORMATION_ONLY`
- `HUMAN_REVIEW`

## What Data It Captures

- Caller name and callback number
- Policy number when available
- Claim type
- Loss date/time
- Loss location
- Incident summary
- Injury or immediate safety indicators
- Emergency services, police report, or fire report status
- Property security and mitigation need
- Photo, receipt, or document availability
- Missing information
- Recommended routing and disclaimers

## Safety And Compliance Boundaries

The assistant collects initial claim details for review. It does not confirm coverage, approve or deny a claim, estimate payout, give legal advice, or replace a licensed broker or adjuster. If there is immediate danger, it instructs the caller to contact emergency services now.

## Pilot Use Case

After-hours FNOL intake for brokers, agencies, or carriers that want fewer missed details, faster triage, cleaner morning handoff, and structured records for dashboard or CRM workflows.

## Placeholder Phone Number Note

The local/demo placeholder route is:

```env
INSURANCE_FNOL_PHONE_NUMBER=+15555550130
```

This is not a production insurance phone number. Production deployments should replace it with seeded phone routing records and organization-specific configuration.

## Future Integrations

- Broker management system or CRM handoff
- Claims platform ticket creation
- Secure document upload links
- Adjuster queue routing
- SMS or email follow-up
- Carrier-specific field mapping
