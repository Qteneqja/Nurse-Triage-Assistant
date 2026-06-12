# ORCA Brand Usage — Client-Facing Views

The ORCA brand system is defined in [`skills/orca-brand/SKILL.md`](../skills/orca-brand/SKILL.md)
(tokens, type scale, logo rules, voice). This doc covers the one thing the
skill doesn't: how views in *this repo* inherit the brand.

## The short version

Every client-facing page imports the shared brand stylesheet first, then its
own layout-only stylesheet:

```html
<link rel="stylesheet" href="/dashboard/static/brand.css" />
<link rel="stylesheet" href="/dashboard/static/your-view.css" />
```

That's it. `brand.css` carries the tokens, the embedded fonts, and the
component styles — a new view that uses the existing class names is on-brand
by default.

## What lives where

| File | Role |
|------|------|
| `src/dashboard_static/brand.css` | Tokens (`--navy`, `--cream`, …), `@font-face`, type scale, logo lockup, kicker, buttons, cards/panels, tables, badges, alert + preview banners, KV tiles, error box. |
| `src/dashboard_static/dashboard.css` | Dashboard layout only (grid, sidebar, filters, responsive). No hex values — tokens only. |
| `src/dashboard_static/fonts/` | Playfair Display + Source Sans 3 variable TTFs (SIL OFL), served same-origin because the CSP is `font-src 'self'`. |
| `src/dashboard_static/logo-{navy,cream,orange}.png` | Transparent emblem variants. Navy on light grounds, cream on dark, orange sparingly for accent. |
| `skills/orca-brand/` | The canonical brand definition + bundled assets. Copy assets from here; don't fork the values. |

## Rules that bite

- **One orange accent per view.** In the dashboard it's the injury/urgent
  flag (navy banner or badge with the orange kicker/dot). Don't add a second.
- **Orange never goes on cream/white at text sizes** — it's ~3:1 there. The
  orange kicker (`.kicker-accent`, `.alert-kicker`) is for navy/deep grounds
  only; on light surfaces use the slate `.kicker`.
- **No new colors.** No semantic green/red. "Done" states use slate; the
  thing that matters most gets the orange.
- **Logo:** `logo-cream.png` on navy/deep, `logo-navy.png` on cream/white.
  Clear space ≥ half the emblem's height; never the navy emblem on navy.
- **Type:** Playfair Display for `h1`–`h3` and big numbers; Source Sans 3 for
  everything else. No third typeface.
- **CSP:** `style-src 'self'`, `script-src 'self'`, `font-src 'self'` — no
  inline styles/scripts, no font CDNs. Everything ships from
  `/dashboard/static/`. (`tests/test_pr4_dashboard_records.py` enforces the
  inline part.)
- **Voice:** lead with the number, address the reader as "you", no hype
  words. See SKILL.md §4.

## Adding a new view

1. Serve it under the existing static mount (or add an equivalent same-origin
   mount).
2. Import `brand.css` first.
3. Build the page from the existing components (`.panel`, `.badge`,
   `.kicker`, `.button`, `.alert-banner`, `.empty-state`, …) and put only
   layout in your view stylesheet, using the tokens.
4. Place the lockup (emblem → ORCA → AI PHONE INTAKE) in the header or
   footer with its clear space.
5. Pick the view's single orange accent deliberately.
