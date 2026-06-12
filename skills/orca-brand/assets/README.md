# Asset provenance

## Logos

`logo-source.png` is the master emblem supplied on 2026-06-11: 1024×1024,
single-color mark with a true alpha channel (tail cutout and background are
transparent). Its fill color is `#D44538`; the brand variants below were
generated from its alpha mask with the fill normalized to the brand tokens
(SKILL.md §1) — the geometry was not altered:

- `logo-navy.png` — `#0F2149`, 1024 px. Light backgrounds (default).
- `logo-cream.png` — `#F6F1E6`, 1024 px. Navy/dark backgrounds.
- `logo-orange.png` — `#E0633C`, 1024 px. Accent variant, use sparingly.

256 px copies of the same variants are served by the dashboard from
`src/dashboard_static/`. To regenerate any variant, recolor
`logo-source.png`'s RGB channels and keep its alpha.

**Still missing:**

- `logo-original.png` (the archive "source navy mark on black" referenced in
  SKILL.md) — not yet supplied.

## Fonts

The TTFs in `fonts/` are the canonical variable fonts from the
[google/fonts](https://github.com/google/fonts) repository (`ofl/playfairdisplay`,
`ofl/sourcesans3`), licensed under the SIL Open Font License — see
`fonts/OFL-*.txt`. These are the real brand typefaces, free to embed.
