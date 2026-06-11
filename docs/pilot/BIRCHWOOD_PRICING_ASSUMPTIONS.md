# Pricing Assumptions — Birchwood (cost per call)

Cost model for the deterministic Birchwood flow. Fill the **measured**
column from the final validation pack
([../STAGING_VALIDATION_PR5_FINAL.md](../STAGING_VALIDATION_PR5_FINAL.md))
and real Twilio/Azure invoices, then recompute with:

```bash
python -m scripts.pilot_metrics --costs \
  --twilio-per-minute <rate> --tts-per-million-chars <rate> \
  --infra-per-day <amortized> --calls-per-day <expected>
```

## Cost components

| Component | Driver | Placeholder rate (USD) | Measured |
|---|---|---|---|
| Twilio inbound voice | minutes/call | $0.0140/min (local DID inbound, list price) | ____ |
| Twilio number rental | fixed | ~$1.15/mo | ____ |
| Twilio speech recognition | enabled-Gather minutes | ~$0.02/min where billed | ____ |
| Azure Speech TTS (HD voice) | characters/call | ~$16/1M chars | ____ |
| **LLM** | — | **$0.00 — the Birchwood flow is deterministic; no LLM call in the live path** | $0.00 |
| Azure Container Apps + Postgres | fixed/day, amortized over call volume | ~$3.50/day (consumption tier, small) | ____ |
| Sentry/monitoring | fixed | free tier assumed | ____ |

## Illustrative math (placeholders — replace with measured)

3-minute average call, ~1,800 TTS chars, 20 calls/day:
- Twilio voice: 3 × $0.014 = **$0.042**
- STT minutes: 3 × $0.02 = **$0.060**
- TTS: 1,800 × $16/1M = **$0.029**
- Variable per call ≈ **$0.13**
- Infra per call at 20/day: $3.50 / 20 = **$0.175**
- **Total ≈ $0.30/call ≈ $185/month at 20 calls/day** (fixed-dominated:
  unit cost falls fast with volume — at 100 calls/day, ≈ $0.17/call).

## Pricing posture (internal)

- Variable cost is cents; the price is for the capability + support, not
  the minutes. Anchor pilot pricing on value (after-hours coverage,
  zero-missed-calls, structured records), not cost-plus.
- The pilot itself: recommend free or nominal, in exchange for the weekly
  metric reviews and a reference conversation.
- Healthcare/LLM workflows have a different cost structure (DeepSeek
  tokens) — out of scope here.
