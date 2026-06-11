# System Limitations — Birchwood Pilot (what ORCA does NOT do)

ORCA is **decision support for intake**. It collects and structures
collision details so your team can act faster. Explicitly out of scope:

- **No repair estimates** — never quotes a price, a range, or "probably
  covered by..." anything.
- **No liability or fault determination** — it records what the caller
  says happened; it never says whose fault it was.
- **No insurance adjudication or coverage advice** — it records the
  insurer and claim number; it never says what a policy covers.
- **No appointment commitments** — every call closes with "this doesn't
  confirm coverage, pricing, or an appointment yet."
- **No medical advice** — if injuries come up, ORCA says exactly one
  thing: seek medical attention / call 9-1-1, then flags the record for
  human follow-up. Nothing more, by design and by test.
- **No payments, no document collection, no SMS** in this pilot.
- **English only** in this pilot.

Operational limits to be aware of:

- Speech recognition is imperfect — names, plates, and claim numbers can
  be mis-heard; the readback confirmation catches most of this, and the
  record shows the verbatim transcript for checking.
- The story-capture flow caps a narrative at roughly two minutes of
  continuous storytelling before moving on (everything said is kept).
- Records the system couldn't complete are flagged, never silently
  dropped; the failure contract is "apologize + promise a callback + flag."
- Dashboard auth is a single shared token for the pilot; the per-change
  "actor" name is the accountability mechanism.

## Who processes call content (data disclosure)

- **Twilio** carries the call and performs speech-to-text.
- **Microsoft Azure** hosts the application and database (Canada Central)
  and synthesizes ORCA's voice (Azure Speech).
- The Birchwood collision flow is **fully deterministic — no large
  language model processes Birchwood call content** in the live pilot
  flow. (The platform's LLM provider, DeepSeek, is used by the healthcare
  vertical, which is not part of this pilot; a provider-abstraction layer
  is planned before any healthcare pilot.)
- Caller details (name, phone, vehicle, incident) are stored to run the
  intake service and shown only behind the authenticated dashboard.
