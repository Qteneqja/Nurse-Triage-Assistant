---
name: orca-brand
description: ORCA's official brand system — color, typography, logo usage, and voice/tone. Use this whenever creating, designing, or writing ANYTHING that represents ORCA — pitch decks, slides, white papers, one-pagers, proposals, reports, web pages, landing pages, emails, email signatures, social posts or avatars, business cards, or any document or UI carrying the ORCA name. Apply it even when the user doesn't say "make it on-brand" — if the output is ORCA-facing, follow these specs exactly. ORCA is an AI phone-intake company; its look is deep navy + warm cream + a single orange accent, set in Playfair Display and Source Sans 3.
---

# ORCA Brand

ORCA is an AI phone-intake layer for high-volume service businesses: it answers every inbound call, runs a structured intake, and hands a clean file to the right person. The brand should feel **warm, precise, credible, calm, and direct** — confident because of real experience, never hyped.

**Mission:** Every inbound call answered, structured, and handed off — so people do the work only people can do.
**Vision:** A world where no caller waits on hold and no opportunity is lost to a phone that went unanswered.

Use the tokens below exactly. When you need the logo or fonts, pull them from `assets/`. For the full visual reference, open `references/brand-guidelines.html`.

---

## 1. Color

Deep navy foundation, warm cream for contrast, one confident orange accent. Neutrals do the supporting work. Keep the rough balance **~70% navy / ~22% cream / ~8% orange** — orange is an accent, never a fill.

| Token | Name | Hex | RGB | Use |
|-------|------|-----|-----|-----|
| `--navy` | Navy | `#0F2149` | 15, 33, 73 | Primary. Headlines, dark backgrounds, the emblem. |
| `--deep` | Deep Navy | `#0D1A33` | 13, 26, 51 | Immersive/full-bleed dark backgrounds. |
| `--cream` | Cream | `#F6F1E6` | 246, 241, 230 | Light canvas; reversed text on navy. |
| `--orange` | Orange | `#E0633C` | 224, 99, 60 | Accent ONLY — kickers, highlights, accent emblem. |
| `--slate` | Slate | `#5D6675` | 93, 102, 117 | Secondary/supporting text. |
| `--line` | Hairline | `#E3E0D6` | 227, 224, 214 | Borders, dividers, card outlines. |
| `--white` | White | `#FFFFFF` | 255, 255, 255 | Card surfaces on cream backgrounds. |

```css
:root{
  --navy:#0F2149; --deep:#0D1A33; --cream:#F6F1E6; --orange:#E0633C;
  --slate:#5D6675; --line:#E3E0D6; --white:#ffffff;
}
```

**Accessibility (WCAG 2.1):** cream-on-navy and navy-on-cream are ~14:1 (AAA); slate-on-cream ~5.1:1 (AA); orange-on-navy ~4.5:1 (AA for normal text). Even though orange clears AA, **reserve it for accents and large display, not body copy**, so it stays a highlight. Never set orange as small body text, and never put the navy emblem on a navy background.

---

## 2. Typography

Two open-source families (SIL OFL — free to embed). Bundled in `assets/fonts/`.

- **Playfair Display** — display & headlines. The wordmark, titles, big statements. Weights 400–900 + italic.
- **Source Sans 3** — body, labels, tables, UI. Weights 300–700 + italic.

**Pairing rule:** Playfair for headings, Source Sans for everything else. Don't introduce a third typeface or decorative fonts.

| Role | Font / weight | Size | Notes |
|------|---------------|------|-------|
| Display / wordmark | Playfair 800 | 48–140px | Tracking ~.06em; all-caps for "ORCA". |
| Heading 1 | Playfair 800 | 32–46px | Tight leading (~1.05). |
| Heading 2 | Playfair 700 | 24–30px | |
| Body | Source Sans 400 | 16–17px | Line-height ~1.6. |
| Label / kicker | Source Sans 700 | 11–12px | UPPERCASE, tracking ~.2em, usually orange. |

When generating HTML or PDF, embed the bundled `.ttf` files via `@font-face` rather than relying on system fonts.

---

## 3. Logo

The mark is a **whale tail set in a circle**. It pairs with the **ORCA wordmark** in Playfair Display (caps, tracked ~.22em). Files in `assets/`:

| File | What it is | Use on |
|------|------------|--------|
| `logo-navy.png` | Navy emblem (transparent) | Cream / white / light backgrounds (default). |
| `logo-cream.png` | Cream emblem (transparent) | Navy / dark backgrounds. |
| `logo-orange.png` | Orange emblem (transparent) | **Official accent variant** — covers, social avatars, moments of emphasis. Use sparingly. |
| `logo-original.png` | Source navy mark on black | Archive only; prefer the transparent versions above. |

**Lockup:** stack emblem → wordmark "ORCA" → descriptor "AI PHONE INTAKE". The wordmark may also stand alone in running contexts (nav bars, footers).

**Clear space:** keep free space of at least **half the emblem's diameter** on all sides.
**Minimum size:** emblem 24px / 0.35in; full lockup 120px wide.

**Logo don'ts:** don't recolor outside the palette; don't stretch, rotate, skew, or add shadows/glows; don't place the navy emblem on navy; don't crowd it or set it on busy, low-contrast imagery; don't recreate or re-letter the wordmark.

---

## 4. Voice & Tone

Speak like someone who has done the job: warm, plain, sure of the facts. Let the numbers talk; never oversell. The credibility is the experience, not the adjectives.

| We are | We are not |
|--------|------------|
| Warm — treat the reader like a guest | Chummy or gimmicky |
| Direct — get to the point | Blunt or cold |
| Confident — trust the work | Boastful or hypey |
| Precise — quantify, label assumptions | Vague or jargon-heavy |
| Honest — the floor, not the fantasy | Salesy or inflated |

**Tone by context:**
- **Sales / pitch:** confident, experience-led — *"We sat in your chairs, took these calls, and built the fix."*
- **The AI voice (product):** calm, warm, professional — *"Thanks for calling. I can get your intake started — this'll take about two minutes."*
- **Support:** plain, accountable — *"That didn't route correctly. Here's what happened and how we've fixed it."*
- **Security / legal:** clear, exact, no overpromising — *"Caller data is treated as sensitive and aligned to PIPEDA. Here's exactly how it's handled."*

**Writing rules:**
- Lead with the number, then the meaning ("107 hours a month — that's the cost of intake alone").
- Active voice, short sentences, address the reader as "you," cut filler.
- Quantify conservatively and label estimates as estimates. Never inflate to impress.
- Avoid hype words: *revolutionary, seamless, cutting-edge, game-changing.* Show, don't boast.

**Taglines** (primary + alternates — primary leads with both core promises):
- **Primary:** Every call answered. Every detail captured.
- Alt: No hold music. No missed calls.
- Alt: Pick up every call. Capture every lead.
- Alt: Intake, handled — in two minutes.
- Alt: The intake layer for every inbound call.

---

## 5. Applications

The pattern is consistent everywhere: **navy ground, cream type, orange used once for emphasis.**

- **Slides & documents:** navy or deep-navy ground, Playfair titles, an orange kicker label, Source Sans body. (This is the system used in ORCA's pitch deck, white paper, and one-pager.)
- **Social avatar:** orange accent emblem on navy, centered, full clear space. Consistent across LinkedIn, X, and any profile image.
- **Business card:** navy face, orange emblem + "ORCA" wordmark, contact in cream/orange.
- **Email signature** (current contact details):

```
Aylie Nagler
COO & Co-Founder, ORCA
Aylienagler@outlook.com · ORCA-Triage.com
```

**Contact / domain:** ORCA-Triage.com · Aylienagler@outlook.com

---

## 6. Quick Do / Don't

**Do:** lead with navy and let cream carry the reading; use orange once per view for the thing that matters most; pair Playfair headlines with Source Sans body; keep numbers conservative and labeled; hold the full clear space around the logo.

**Don't:** flood layouts with orange or add new colors; mix in extra typefaces; overclaim or use hype words; distort, recolor, or crowd the logo; set orange as small body text.

---

## Bundled assets

- `assets/logo-navy.png`, `assets/logo-cream.png`, `assets/logo-orange.png`, `assets/logo-original.png`
- `assets/fonts/PlayfairDisplay.ttf` (+ Italic), `assets/fonts/SourceSans3.ttf` (+ Italic)
- `references/brand-guidelines.html` — the full visual brand guide (open for swatches, specimens, and worked examples)

When the task is to produce a branded artifact (deck, doc, page, PDF), embed the bundled fonts, drop in the correct logo variant for the background, and apply the color tokens and voice rules above without deviation.
