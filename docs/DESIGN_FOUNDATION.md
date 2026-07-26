# netadmin Design Foundation

**Status:** binding design contract for all Phase 3+ UI work, alongside `docs/ARCHITECTURE.md` §12.
**Source:** July 2026 research pass (Apple HIG, WWDC 19/20/22 sessions, Tailscale/Linear/Vercel craft writeups); contrast ratios computed locally with the WCAG relative-luminance formula. Uncertainty flags at the end.
**Review gate:** every UI change passes an adversarial front-end UX review (layout, readability, accessibility, dark-mode parity, edge cases) before it lands. Readability outranks density everywhere.

## Licensing constraints (hard)

- SF Pro and SF Symbols are licensed for Apple platforms only. Shipping either as a webfont/SVG set violates Apple's EULA. Do not do it.
- Approved stack instead: **InterVariable** (or Geist Sans, SIL OFL) for UI; **Geist Mono / ui-monospace** for MACs, IPs, diffs; **Lucide** icons (ISC), one set, one stroke weight, sized to adjacent text (16px next to 13-14px text).

## Typography

- UI stack: `InterVariable, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`, `font-optical-sizing: auto`.
- **`font-variant-numeric: tabular-nums lining-nums` on every numeric cell, metric, and axis label.** Right-aligned numerics, consistent precision, units in the header not the cells.
- Emphasis is one weight step up (400 → 500/600), never a new size or font. No Ultralight/Thin/Light weights.
- Scale (px/line-height, weight, tracking): page title 24/30 600 −0.015em; section title 17/24 600 −0.01em; card/metric label 13/18 500; body + table cells 14/20 400; secondary 13/18 400; caption/axis 12/16 400 +0.005em; big metric numeral (SLE score) 28/34 600 tabular. Floor 11px, one use only (dense chart annotations).

## Spacing

4px unit; scale 4, 8, 12, 16, 24, 32, 48, 64. Card padding 16-20. Table row height 40 (44 for the issues list — primary click target). Page gutter 24-32. Tables unconstrained width; prose ~720px max.

## Color tokens

Role-based tokens, both themes first-class. Dark mode is **not** inverted light: dark elevation = lighter surface + 1px border + stronger shadow (≥2× opacity vs light).

| Token | Light | Dark | Notes |
|---|---|---|---|
| bg/canvas | `#F5F5F7` | `#161618` | never pure #000 |
| bg/surface (cards, tables) | `#FFFFFF` | `#1F1F21` | dark surfaces + hairline border |
| bg/elevated (popovers, modals) | `#FFFFFF` + shadow | `#28282B` + border + strong shadow | |
| border/hairline | `#E5E5EA` | `rgba(255,255,255,0.10)` | decorative |
| border/strong (inputs) | `#C6C6C8` | `rgba(255,255,255,0.18)` | |
| text/primary | `#1D1D1F` (16.8:1) | `#F5F5F7` (16.6:1) | |
| text/secondary | `#55555A` (7.4:1) | `#A1A1A6` (7.0:1) | |
| text/tertiary | `#6E6E73` (5.1:1) | `#8E8E93` (5.5:1) | no sub-AA text tiers exist |
| accent | `#0066CC` (5.6:1) | `#64A8FF` (7.4:1) | ONE accent, blue, everywhere |

Severity (text/icon colors, AA-verified on surface and canvas in each theme):

| Role | Light | Dark |
|---|---|---|
| P1 critical (red) | `#D70015` | `#FF6961` |
| P2 major (orange) | `#C93400` | `#FFB340` |
| P3 minor (amber) | `#B25000` | `#FFD426` |
| Healthy (green) | `#1E7A34` | `#30DB5B` |
| Neutral/resolved (gray) | `#6E6E73` | `#8E8E93` |

Never use Apple's default `systemRed #FF3B30` / `systemOrange #FF9500` / `systemGreen #34C759` as light-mode text — they fail AA (3.6:1 / 2.2:1 / 2.2:1). Defaults are acceptable only as chart fills/large glyphs (3:1 rule).

Pill fills: severity color at 10% opacity (light) / 16% (dark) behind the AA text color. Solid fill chips only for P1 (white on `#D70015` passes at 5.4:1).

## Severity & lifecycle presentation

- Color = meaning only. Solid fills are reserved for the highest-urgency smallest elements (P1 badge). Mid-urgency = tinted text/icon on neutral surface. Low = 6-8px dot + neutral text. **Never paint whole rows/cards/banners in severity colors.**
- Never color alone: pair every severity color with a shape (P1 octagon, P2 triangle, P3 circle) so state survives colorblindness.
- Lifecycle pills: pending = neutral gray outline; active = severity-tinted (the only severity fills in the UI); resolving = tinted with progress affordance; resolved = gray + checkmark, **not green** — green is reserved for health so real "healthy" signals stay meaningful.

## Charts (monitoring craft)

- Primary series = accent blue; comparison series = gray; two shades of one hue before a second hue; max 4 series. Severity color appears only when the data *is* severity (threshold band, anomaly window).
- Bars for discrete/gappy data; lines for rates; **render data gaps as gaps, never interpolate**. Bar baselines at zero; never truncate an axis; no dual y-axes.
- Percentage axes fixed 0-100 (SLE scores) like Apple's battery chart. ~4 horizontal gridlines (`#E5E5EA` light / 8% white dark), no vertical gridlines by default, no axis boxes.
- Every chart gets three layers: context label ("WAN latency"), summary statistic as title ("p95 42 ms"), takeaway caption where useful. A chart with no takeaway is decoration.
- Selection: scrub crosshair + single annotation (value + timestamp), dim non-selected data (Health-app pattern: emphasis by dimming everything else).
- Area fills ≤8% opacity or none. No glow, no gradients, no rounded-cap thick lines. Dark-mode charts re-pick colors (lighter, slightly more saturated), never reuse light fills.

## Interaction

- Inline expand for short comparison-oriented detail viewed across many rows (SLE root-cause breakdown); navigate (own URL) for anything with sub-structure or over a screenful (issue detail, device/client pages). Issue rows: whole row is the link target.
- Sidebar: 5-9 flat destinations, quiet gray count badges (red only when P1s exist), one nesting level max, collapsible to icons.
- Keyboard-first: Cmd+K palette that teaches shortcuts inline, `/` filters current view, arrows/j-k traverse rows, Enter opens, Esc closes.
- Empty states distinguish three cases honestly: no data yet (setup guidance), filters match nothing (offer clear), genuinely healthy ("No active issues" stated plainly as a positive — one line, no illustration).
- Loading: skeletons only when load exceeds ~500ms; never fake chart shapes or placeholder numbers; stale data labeled "as of HH:MM:SS", never presented as live.

## The 10 never-do rules

1. Never more than one accent color; color encodes meaning or doesn't appear.
2. Never state by color alone — icon shape or label alongside, always.
3. Never build dark mode by inverting light — re-pick every color; elevation = lighter surface + border + stronger shadow.
4. Never ship SF Pro or SF Symbols on the web (license) — Inter/Geist + Lucide.
5. Never use gauges, speedometers, donut KPIs, or sparkline-free big-number walls.
6. Never paint whole rows/cards/banners in severity colors.
7. Never use proportional or centered figures in data columns — right-aligned tabular-nums.
8. Never fake state: no sub-500ms skeletons, no placeholder numbers, no interpolated gaps, no unlabeled stale data.
9. Never truncate bar axes, use dual y-axes, or make red/green the sole differentiator.
10. Never use glassmorphism cards, purple-blue gradients, glow effects, emoji headings, or a second display font. If it looks like a template dashboard, it's wrong.

## Pattern references

Apple's own dense-data surfaces set the bar: Activity Monitor (neutral tables, color only in the pressure graphs), Health (range bars, dimming as emphasis, summary stat as chart title), Battery/Screen Time (fixed axes, two shades of one hue), Weather (severity as a small colored indicator on a neutral card), System Settings (strict hierarchy, value text in secondary right-aligned). Copy Linear's restraint and keyboard model, Tailscale's dark-mode token discipline. Refuse the Grafana default aesthetic (panel sprawl, rainbow series, red/green walls) — Grafana's own docs warn against it.

## Uncertainty flags

- Apple hex values are community-measured, not Apple-published; they can drift between OS versions (semantic table from iOS 13-era measurements).
- The "accessible variant" severity hexes came via community extraction from Apple design resources; contrast math re-verified locally, canonical status not guaranteed.
- Activity Monitor / Instruments / Weather patterns are product observation, not documented sources.
- The 500ms skeleton threshold is directionally solid (NN/g/Viget), exact number less so.

Full source list: HIG Typography/Dark Mode/Color pages; WWDC22 "Design an effective chart" + "Design app experiences with charts"; WWDC20 "The details of UI typography"; WWDC19 "Implementing Dark Mode"; Tailscale "The heart of dark mode"; Inter Dynamic Metrics; Linear "Invisible details"; A List Apart web-typography-tables; NN/g progressive disclosure + empty states; Grafana dashboard best practices.
