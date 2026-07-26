# Diagram generator

Hand-drawn (pencil-style) figures that explain how netadmin works. They are
used in the [README](../../README.md) and in
[`docs/HOW_IT_WORKS.md`](../../docs/HOW_IT_WORKS.md).

`gen.py` draws three figures with a small vector toolkit: wobbly strokes,
sketched boxes, a cylinder for the store, arrows, and Patrick Hand lettering.
Every output lands in [`docs/img/`](../../docs/img/):

| File | Figure |
|---|---|
| `how-netadmin-watches.{svg,png}` | end-to-end flow: controller to store to detectors to you |
| `life-of-an-issue.{svg,png}` | one issue per fingerprint, its states, reopen, inhibition |
| `health-sle.{svg,png}` | Mist-style user-minutes and exclusive attribution |

## Regenerate

```bash
python3 tools/diagrams/gen.py
```

That rewrites all six files. The SVGs are always written; PNGs are written when
`rsvg-convert` is on the PATH (`brew install librsvg`). If Pillow is installed
the PNGs are palette-quantized down to roughly 120 KB each; without it you get
larger full-color PNGs that look identical.

## It is deterministic

Jitter is seeded from a fixed constant (`SEED` in `gen.py`), the font is
embedded from the vendored file, and nothing reads the clock. Re-running with
the same tools produces byte-identical files, so committing the output does not
churn the repo. If you change a figure, regenerate and commit the SVG and PNG
together.

## Self-contained SVGs

Each SVG embeds Patrick Hand as a base64 `@font-face`, so it renders correctly
with no network access and no installed font (GitHub, offline, any browser). The
PNGs are rasterized from those same SVGs.

## Font

Patrick Hand by Patrick Wagesreiter, under the SIL Open Font License 1.1. The
font file and its license live in
[`assets/fonts/`](assets/fonts/). Do not rename the family; the SVG and the
fontconfig fallback both look it up as `Patrick Hand`.

## Editing

Palette, seed, and the three `diagram_*()` builders are near the top and bottom
of `gen.py`. Keep labels generic and fictional: no real MACs, IPs, hostnames, or
room names belong in a public figure.
