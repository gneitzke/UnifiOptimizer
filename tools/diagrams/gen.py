#!/usr/bin/env python3
"""Deterministic hand-drawn (pencil-style) diagram generator for netadmin.

Emits three self-contained SVGs (font embedded as a base64 @font-face so the
files render anywhere, including GitHub and offline) plus rasterized PNGs.

Design: warm cream paper, graphite-gray wobbly strokes, ONE restrained accent
(terracotta), Patrick Hand lettering. Every stroke gets a small, DETERMINISTIC
per-vertex jitter seeded from a fixed constant -- rebuilds are byte-identical,
so committing the output does not churn the repo.

Run:  python3 tools/diagrams/gen.py
Deps: rsvg-convert on PATH for PNGs (SVGs are written regardless).
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FONT = HERE / "assets" / "fonts" / "PatrickHand-Regular.ttf"
OUT = ROOT / "docs" / "img"
SEED = 20260722  # fixed: deterministic jitter, no clock, no random churn

# Palette -- graphite ink on cream, single terracotta accent.
PAPER = "#F3ECD9"
PAPER_EDGE = "#E7DCC2"
INK = "#41403A"  # graphite
INK2 = "#6E6B60"  # soft graphite for secondary text
ACCENT = "#C15A38"  # terracotta -- used sparingly
ACCENT_FILL = "#ECCBB9"
OK_FILL = "#DCE3CC"  # muted sage for "pass"
IDLE_FILL = "#E1DAC7"  # dim for "idle / not counted"
BOX_FILL = "#FBF6EC"  # slightly lighter than paper for panels


# ---------------------------------------------------------------------------
# Deterministic pseudo-random jitter
# ---------------------------------------------------------------------------
def _rand(*keys) -> float:
    h = hashlib.sha256((str(SEED) + "|" + "|".join(str(k) for k in keys)).encode()).digest()
    return int.from_bytes(h[:6], "big") / float(1 << 48)


def jit(amp: float, *keys) -> float:
    return (_rand(*keys) - 0.5) * 2.0 * amp


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Wobbly primitives
# ---------------------------------------------------------------------------
def wobble(x1, y1, x2, y2, amp=1.7, key=0, seg=None):
    dx, dy = x2 - x1, y2 - y1
    dist = math.hypot(dx, dy) or 1.0
    if seg is None:
        seg = max(2, int(dist // 46) + 1)
    nx, ny = -dy / dist, dx / dist
    tx, ty = dx / dist, dy / dist
    pts = []
    for i in range(seg + 1):
        t = i / seg
        px, py = x1 + dx * t, y1 + dy * t
        if 0 < i < seg:
            o = jit(amp, key, "n", i)
            a = jit(amp * 0.5, key, "t", i)
            px += nx * o + tx * a
            py += ny * o + ty * a
        else:  # tiny end wander so lines don't look mechanical
            e = jit(amp * 0.35, key, "e", i)
            px += nx * e
            py += ny * e
        pts.append((px, py))
    return pts


def polystr(pts):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def pl(pts, w=2.3, col=INK, op=0.92, dash=None, closed=False):
    p = pts + [pts[0]] if closed else pts
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<polyline points="{polystr(p)}" fill="none" stroke="{col}" '
        f'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round" '
        f'opacity="{op}"{d}/>'
    )


def stroke(x1, y1, x2, y2, col=INK, amp=1.7, key=0, double=True, dash=None, w=2.3):
    s = pl(wobble(x1, y1, x2, y2, amp=amp, key=(key, "a")), w=w, col=col, op=0.9, dash=dash)
    if double and dash is None:
        s += pl(
            wobble(x1, y1, x2, y2, amp=amp * 1.35, key=(key, "b")), w=w * 0.55, col=col, op=0.38
        )
    return s


def ellipse_pts(cx, cy, rx, ry, key, amp=1.2, n=44, a0=0.0, a1=2 * math.pi):
    pts = []
    for i in range(n + 1):
        a = a0 + (a1 - a0) * i / n
        o = jit(amp, key, "el", i)
        pts.append((cx + (rx + o) * math.cos(a), cy + (ry + o) * math.sin(a)))
    return pts


def rough_rect_pts(x, y, w, h, key, amp=1.8):
    t = wobble(x, y, x + w, y, amp=amp, key=(key, "t"))
    r = wobble(x + w, y, x + w, y + h, amp=amp, key=(key, "r"))
    b = wobble(x + w, y + h, x, y + h, amp=amp, key=(key, "b"))
    l = wobble(x, y + h, x, y, amp=amp, key=(key, "l"))
    return t + r[1:] + b[1:] + l[1:]


def box(x, y, w, h, key=0, col=INK, fill=None, amp=1.8, wmain=2.3, fillop=1.0):
    pts = rough_rect_pts(x, y, w, h, key, amp)
    out = []
    if fill:
        out.append(
            f'<polygon points="{polystr(pts)}" fill="{fill}" opacity="{fillop}" stroke="none"/>'
        )
    out.append(pl(pts, w=wmain, col=col, op=0.92, closed=True))
    p2 = rough_rect_pts(x, y, w, h, (key, "d"), amp * 1.2)
    out.append(pl(p2, w=wmain * 0.5, col=col, op=0.3, closed=True))
    return "".join(out)


def arrow(x1, y1, x2, y2, col=INK, key=0, amp=1.7, dash=None, head=13, w=2.3):
    out = [
        stroke(
            x1, y1, x2, y2, col=col, amp=amp, key=(key, "sh"), double=(dash is None), dash=dash, w=w
        )
    ]
    ang = math.atan2(y2 - y1, x2 - x1)
    for s in (1, -1):
        a = ang + math.pi - s * 0.42
        hx, hy = x2 + head * math.cos(a), y2 + head * math.sin(a)
        out.append(stroke(x2, y2, hx, hy, col=col, amp=1.0, key=(key, "hd", s), double=False, w=w))
    return "".join(out)


def curve_arrow(pts, col=INK, key=0, amp=1.5, dash=None, head=13, w=2.3):
    """Arrow that follows a polyline path (for loops / detours)."""
    seg = []
    for i in range(len(pts) - 1):
        seg.append(
            pl(
                wobble(*pts[i], *pts[i + 1], amp=amp, key=(key, "c", i)),
                w=w,
                col=col,
                op=0.9,
                dash=dash,
            )
        )
    x1, y1 = pts[-2]
    x2, y2 = pts[-1]
    ang = math.atan2(y2 - y1, x2 - x1)
    for s in (1, -1):
        a = ang + math.pi - s * 0.42
        hx, hy = x2 + head * math.cos(a), y2 + head * math.sin(a)
        seg.append(stroke(x2, y2, hx, hy, col=col, amp=1.0, key=(key, "hh", s), double=False, w=w))
    return "".join(seg)


def text(x, y, s, size=20, col=INK, anchor="middle", spacing=None):
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}"{ls} '
        f'font-family="Patrick Hand, cursive" font-size="{size}px" fill="{col}">'
        f"{esc(s)}</text>"
    )


def lines(x, y, rows, size=19, col=INK, anchor="middle", lh=None):
    lh = lh or size * 1.22
    return "".join(
        text(x, y + i * lh, r, size=size, col=col, anchor=anchor) for i, r in enumerate(rows)
    )


def underline(x1, x2, y, key, col=ACCENT, amp=1.3, w=2.6):
    return stroke(x1, y, x2, y, col=col, amp=amp, key=(key, "ul"), double=False, w=w)


def clock(cx, cy, r, key, col=INK):
    out = [
        pl(
            ellipse_pts(cx, cy, r, r, (key, "clk"), amp=0.9, n=30),
            w=2.0,
            col=col,
            op=0.9,
            closed=True,
        )
    ]
    out.append(
        stroke(cx, cy, cx, cy - r * 0.62, col=col, amp=0.6, key=(key, "h1"), double=False, w=2.0)
    )
    out.append(
        stroke(
            cx,
            cy,
            cx + r * 0.5,
            cy + r * 0.12,
            col=col,
            amp=0.6,
            key=(key, "h2"),
            double=False,
            w=2.0,
        )
    )
    return "".join(out)


def check(cx, cy, s, key, col=ACCENT, w=3.0):
    return stroke(
        cx - s,
        cy,
        cx - s * 0.25,
        cy + s * 0.7,
        col=col,
        amp=0.7,
        key=(key, "k1"),
        double=False,
        w=w,
    ) + stroke(
        cx - s * 0.25,
        cy + s * 0.7,
        cx + s,
        cy - s * 0.7,
        col=col,
        amp=0.7,
        key=(key, "k2"),
        double=False,
        w=w,
    )


def cylinder(cx, top, w, h, key, fill=ACCENT_FILL, col=INK):
    rx = w / 2.0
    ry = w * 0.12
    left, right = cx - rx, cx + rx
    bottom = top + h
    out = []
    body = [(left, top), (left, bottom)]
    body += ellipse_pts(cx, bottom, rx, ry, (key, "bf"), amp=1.0, n=26, a0=math.pi, a1=2 * math.pi)
    body += [(right, bottom), (right, top)]
    out.append(f'<polygon points="{polystr(body)}" fill="{fill}" opacity="0.55" stroke="none"/>')
    tp = ellipse_pts(cx, top, rx, ry, (key, "tp"), amp=1.0, n=44)
    out.append(f'<polygon points="{polystr(tp)}" fill="{fill}" opacity="0.9" stroke="none"/>')
    out.append(pl(tp, w=2.3, col=col, op=0.92, closed=True))
    out.append(stroke(left, top, left, bottom, col=col, amp=1.3, key=(key, "l"), w=2.3))
    out.append(stroke(right, top, right, bottom, col=col, amp=1.3, key=(key, "r"), w=2.3))
    front = ellipse_pts(cx, bottom, rx, ry, (key, "fr"), amp=1.0, n=26, a0=0.0, a1=math.pi)
    out.append(pl(front, w=2.3, col=col, op=0.92))
    for frac in (0.40, 0.70):
        yy = top + h * frac
        arc = ellipse_pts(
            cx, yy, rx, ry, (key, "sh", round(frac * 100)), amp=0.8, n=22, a0=0.0, a1=math.pi
        )
        out.append(pl(arc, w=1.2, col=col, op=0.26))
    return "".join(out)


# ---------------------------------------------------------------------------
# SVG document assembly (embeds the font as base64 @font-face)
# ---------------------------------------------------------------------------
def _font_b64():
    return base64.b64encode(FONT.read_bytes()).decode("ascii")


def svg_doc(w, h, body):
    b64 = _font_b64()
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">'
        "<defs>"
        "<style>"
        "@font-face{font-family:'Patrick Hand';font-style:normal;font-weight:400;"
        f"src:url(data:font/ttf;base64,{b64}) format('truetype');}}"
        "text{font-family:'Patrick Hand',cursive;}"
        "</style>"
        "</defs>"
    )
    bg = (
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="{PAPER_EDGE}"/>'
        f'<rect x="7" y="7" width="{w - 14}" height="{h - 14}" fill="{PAPER}"/>'
    )
    frame = box(24, 24, w - 48, h - 48, key="frame", col=INK2, amp=2.2, wmain=1.6)
    return head + bg + frame + body + "</svg>"


def header(w, title, subtitle, tag):
    out = [text(56, 74, title, size=42, col=INK, anchor="start")]
    out.append(underline(56, 56 + len(title) * 20.5, 88, key=("hdr", title)))
    out.append(text(56, 118, subtitle, size=22, col=INK2, anchor="start"))
    # figure tag, top-right
    out.append(
        box(w - 168, 44, 118, 40, key=("tag", tag), col=INK2, fill=BOX_FILL, amp=1.6, wmain=1.6)
    )
    out.append(text(w - 109, 71, tag, size=19, col=INK2))
    return "".join(out)


# ===========================================================================
# Diagram 1 -- how netadmin watches (end-to-end flow, the store as the hero)
# ===========================================================================
def diagram_watches():
    W, H = 1860, 1080
    b = [
        header(
            W,
            "How UnifiOptimizer watches",
            "collect, remember, detect, track, then tell you",
            "fig 1",
        )
    ]

    # ---- A: UniFi controller -------------------------------------------
    ax, ay, aw, ah = 58, 250, 236, 300
    b.append(box(ax, ay, aw, ah, key="A", col=INK, fill=BOX_FILL, amp=2.0))
    b.append(text(ax + aw / 2, ay + 40, "UniFi controller", size=25))
    b.append(box(ax + 20, ay + 62, aw - 40, 96, key="A1", col=INK, fill=PAPER, amp=1.6, wmain=1.7))
    b.append(
        lines(
            ax + aw / 2,
            ay + 96,
            ["REST API", "stat/device, sta,", "health   (every 60s)"],
            size=17,
            col=INK2,
        )
    )
    b.append(box(ax + 20, ay + 176, aw - 40, 96, key="A2", col=INK, fill=PAPER, amp=1.6, wmain=1.7))
    b.append(
        lines(ax + aw / 2, ay + 210, ["Event WebSocket", "EVT_* keys,", "live"], size=17, col=INK2)
    )

    # ---- B: collector + probes -----------------------------------------
    bx, bw = 344, 214
    b.append(box(bx, 250, bw, 128, key="B1", col=INK, fill=BOX_FILL, amp=1.9))
    b.append(text(bx + bw / 2, 290, "Collector", size=24))
    b.append(clock(bx + 40, 336, 18, key="clk"))
    b.append(text(bx + bw / 2 + 18, 342, "every 60s", size=19, col=INK2))
    b.append(box(bx, 402, bw, 148, key="B2", col=INK, fill=BOX_FILL, amp=1.9))
    b.append(lines(bx + bw / 2, 440, ["DNS + ICMP probes"], size=22))
    b.append(lines(bx + bw / 2, 472, ["timing the controller", "never reports"], size=16, col=INK2))

    # ---- C: HERO -- the one SQLite store -------------------------------
    cx = 792
    cyl_w, cyl_top, cyl_h = 300, 240, 372
    b.append(cylinder(cx, cyl_top, cyl_w, cyl_h, key="db", fill=ACCENT_FILL))
    b.append(text(cx, cyl_top + 88, "ONE SQLite store", size=27))
    b.append(text(cx, cyl_top + 120, "data/netadmin.db  (WAL)", size=18, col=INK2))
    b.append(
        lines(
            cx,
            cyl_top + 176,
            ["raw 30d, then hourly 18mo,", "then daily  -  forever"],
            size=17,
            col=INK2,
        )
    )
    b.append(
        lines(
            cx,
            cyl_top + 250,
            ["counters kept as rates;", "gaps recorded, never zeroed"],
            size=17,
            col=INK2,
        )
    )
    # "it remembers" banner
    b.append(
        box(
            cx - 118,
            cyl_top - 66,
            236,
            46,
            key="rem",
            col=ACCENT,
            fill=ACCENT_FILL,
            amp=1.7,
            wmain=2.4,
        )
    )
    b.append(text(cx, cyl_top - 36, "it REMEMBERS", size=24, col=ACCENT))

    # ---- D: detectors + SLE --------------------------------------------
    dx, dw = 1136, 268
    b.append(box(dx, 250, dw, 172, key="D1", col=INK, fill=BOX_FILL, amp=1.9))
    b.append(text(dx + dw / 2, 288, "Detectors", size=24))
    b.append(lines(dx + dw / 2, 320, ["thresholds + rolling", "quantile bands"], size=17, col=INK2))
    b.append(
        box(dx + 16, 356, dw - 32, 52, key="D1c", col=ACCENT, fill=ACCENT_FILL, amp=1.5, wmain=2.0)
    )
    b.append(
        lines(
            dx + dw / 2,
            380,
            ["confounder checks:", "the false alarms it ruled out"],
            size=15,
            col=ACCENT,
            lh=17,
        )
    )

    b.append(box(dx, 452, dw, 128, key="D2", col=INK, fill=BOX_FILL, amp=1.9))
    b.append(text(dx + dw / 2, 490, "SLE health minutes", size=23))
    b.append(
        lines(
            dx + dw / 2,
            522,
            ["each active client-minute", "judged pass or fail"],
            size=17,
            col=INK2,
        )
    )

    # ---- E: issue lifecycle --------------------------------------------
    ex, ew = 1470, 210
    b.append(box(ex, 300, ew, 250, key="E", col=INK, fill=BOX_FILL, amp=1.9))
    b.append(text(ex + ew / 2, 338, "Issue lifecycle", size=22))
    states = ["pending", "active", "resolving", "resolved"]
    for i, s in enumerate(states):
        yy = 372 + i * 42
        b.append(text(ex + ew / 2, yy + 4, s, size=19, col=(ACCENT if s == "active" else INK2)))
        if i < len(states) - 1:
            b.append(
                arrow(
                    ex + ew / 2,
                    yy + 12,
                    ex + ew / 2,
                    yy + 28,
                    key=("ea", i),
                    amp=0.8,
                    head=8,
                    w=1.7,
                    col=INK2,
                )
            )
    b.append(text(ex + ew / 2, 372 + 4 + 42 + 4, "", size=1))  # spacer noop

    # ---- F: you --------------------------------------------------------
    fx, fw = 1720, 96  # narrow column near the right frame
    # keep inside frame; use a stacked pair
    fx, fw = 1704, 120
    b.append(box(fx, 300, fw, 96, key="F1", col=INK, fill=BOX_FILL, amp=1.8))
    b.append(lines(fx + fw / 2, 342, ["Web", "+ API"], size=20))
    b.append(box(fx, 420, fw, 96, key="F2", col=INK, fill=BOX_FILL, amp=1.8))
    b.append(lines(fx + fw / 2, 456, ["Home", "Assistant"], size=18))
    b.append(text(fx + fw / 2, 556, "YOU", size=26, col=ACCENT))

    # ---- flow arrows ----------------------------------------------------
    b.append(arrow(ax + aw, 400, bx - 8, 372, key="fa1", amp=1.6))  # A -> collector
    b.append(arrow(ax + aw, 430, bx - 8, 470, key="fa1b", amp=1.6))  # A -> probes
    b.append(arrow(bx + bw, 330, cx - cyl_w / 2 - 8, 372, key="fa2", amp=1.6))  # collector -> DB
    b.append(arrow(bx + bw, 476, cx - cyl_w / 2 - 8, 452, key="fa2b", amp=1.6))  # probes -> DB
    b.append(arrow(cx + cyl_w / 2 - 4, 372, dx - 8, 336, key="fa3", amp=1.6))  # DB -> detectors
    b.append(arrow(cx + cyl_w / 2 - 4, 452, dx - 8, 512, key="fa3b", amp=1.6))  # DB -> SLE
    b.append(arrow(dx + dw, 380, ex - 8, 410, key="fa4", amp=1.6))  # detectors -> issues
    b.append(arrow(dx + dw, 516, ex - 8, 470, key="fa4b", amp=1.6))  # SLE -> issues
    b.append(arrow(ex + ew, 360, fx - 8, 344, key="fa5", amp=1.5))  # issues -> web
    b.append(arrow(ex + ew, 470, fx - 8, 462, key="fa5b", amp=1.5))  # issues -> HA

    # ---- approval loop (issues -> controller, only when you approve) ----
    ly = 940
    b.append(
        curve_arrow(
            [(ex + ew / 2, 550), (ex + ew / 2, ly), (ax + aw / 2, ly), (ax + aw / 2, ay + ah + 8)],
            key="loop",
            amp=1.6,
            dash="9 8",
            col=ACCENT,
        )
    )
    b.append(
        box(cx - 300, ly - 34, 600, 62, key="apr", col=ACCENT, fill=ACCENT_FILL, amp=1.8, wmain=2.4)
    )
    b.append(check(cx - 262, ly - 3, 12, key="aprk"))
    b.append(
        text(cx + 14, ly + 6, "a fix is applied only when YOU approve it", size=23, col=ACCENT)
    )

    return svg_doc(W, H, "".join(b))


# ===========================================================================
# Diagram 2 -- life of an issue (state path + reopen + inhibition)
# ===========================================================================
def diagram_issue():
    W, H = 1820, 1040
    b = [
        header(
            W, "Life of an issue", "one issue per fingerprint, not a new alert every poll", "fig 2"
        )
    ]

    ymid = 360
    nodew, nodeh = 196, 92

    def node(cx, label, sub=None, accent=False, key=""):
        x = cx - nodew / 2
        col = ACCENT if accent else INK
        fill = ACCENT_FILL if accent else BOX_FILL
        out = [box(x, ymid - nodeh / 2, nodew, nodeh, key=("n", key), col=col, fill=fill, amp=1.9)]
        if sub:
            out.append(text(cx, ymid - 4, label, size=23, col=col))
            out.append(text(cx, ymid + 24, sub, size=16, col=INK2))
        else:
            out.append(text(cx, ymid + 8, label, size=24, col=col))
        return "".join(out)

    fp, pe, ac, rs, rd = 470, 762, 1054, 1346, 1620
    # findings funnel (left)
    fnx = 150
    for i, yy in enumerate((ymid - 92, ymid, ymid + 92)):
        b.append(
            box(
                fnx - 62,
                yy - 26,
                124,
                52,
                key=("find", i),
                col=INK2,
                fill=PAPER,
                amp=1.7,
                wmain=1.7,
            )
        )
        b.append(text(fnx, yy + 6, "finding", size=18, col=INK2))
        b.append(
            arrow(
                fnx + 64,
                yy,
                fp - nodew / 2 - 8,
                ymid + (yy - ymid) * 0.18,
                key=("ff", i),
                amp=1.4,
                col=INK2,
                w=1.9,
            )
        )
    b.append(text(fnx, ymid + 176, "every poll", size=17, col=INK2))
    b.append(text(fnx, ymid + 200, "re-emits the finding", size=17, col=INK2))

    # chain
    b.append(node(fp, "fingerprint", key="fp"))
    b.append(node(pe, "pending", key="pe"))
    b.append(node(ac, "active", accent=True, key="ac"))
    b.append(node(rs, "resolving", key="rs"))
    b.append(node(rd, "resolved", key="rd"))

    for a, c, k in ((fp, pe, "1"), (pe, ac, "2"), (ac, rs, "3"), (rs, rd, "4")):
        b.append(arrow(a + nodew / 2 + 6, ymid, c - nodew / 2 - 6, ymid, key=("ch", k), amp=1.5))

    # annotations under nodes
    b.append(
        box(
            fp - 150,
            ymid + 70,
            300,
            78,
            key="fpn",
            col=ACCENT,
            fill=ACCENT_FILL,
            amp=1.7,
            wmain=2.2,
        )
    )
    b.append(
        lines(
            fp, ymid + 100, ["many findings collapse", "into ONE issue"], size=18, col=ACCENT, lh=22
        )
    )
    b.append(text(fp, ymid + 176, "sha1(detector + device + dims)", size=15, col=INK2))

    b.append(
        lines(pe, ymid + 92, ["seen -- but wait.", "hold ~3 polls first"], size=17, col=INK2, lh=21)
    )
    b.append(
        lines(rs, ymid + 92, ["looks clear:", "count 6 clean polls"], size=17, col=INK2, lh=21)
    )

    # active callout: "still broken, day 5" -- upper-left of active, out of the reopen loop's path
    sbx = (pe + ac) / 2 + 10
    sby = ymid - 150
    b.append(text(sbx, sby - 58, "day 5 = now - first_seen", size=15, col=INK2))
    b.append(
        box(
            sbx - 118,
            sby - 37,
            236,
            74,
            key="acd",
            col=ACCENT,
            fill=ACCENT_FILL,
            amp=1.7,
            wmain=2.3,
        )
    )
    b.append(clock(sbx - 86, sby, 17, key="acclk", col=ACCENT))
    b.append(text(sbx + 18, sby - 6, "still broken", size=21, col=ACCENT))
    b.append(text(sbx + 18, sby + 22, "day 5", size=19, col=ACCENT))
    b.append(
        arrow(
            sbx + 40,
            sby + 37,
            ac - 55,
            ymid - nodeh / 2 - 6,
            key="acdA",
            amp=1.2,
            head=10,
            w=1.9,
            col=ACCENT,
        )
    )

    # resolved check
    b.append(check(rd + nodew / 2 - 26, ymid - nodeh / 2 + 20, 10, key="rdk"))

    # snap-back: fire during resolving -> active
    b.append(
        curve_arrow(
            [
                (rs, ymid + nodeh / 2 + 6),
                (rs - 90, ymid + 128),
                (ac, ymid + 128),
                (ac, ymid + nodeh / 2 + 6),
            ],
            key="snap",
            amp=1.4,
            dash="7 7",
            col=INK2,
            w=1.9,
        )
    )
    b.append(
        text(
            (ac + rs) / 2,
            ymid + 150,
            "a fire during resolving snaps back to active",
            size=16,
            col=INK2,
        )
    )

    # reopen loop (resolved -> active), routed high to clear the callout
    topy = ymid - 260
    b.append(
        curve_arrow(
            [
                (rd, ymid - nodeh / 2 - 6),
                (rd, topy),
                (ac + 55, topy),
                (ac + 55, ymid - nodeh / 2 - 6),
            ],
            key="reopen",
            amp=1.4,
            dash="9 8",
            col=ACCENT,
            w=2.1,
        )
    )
    b.append(
        text(
            (ac + rd) / 2 + 30,
            topy - 12,
            "refires within a day: reopen the SAME row (not a new issue)",
            size=17,
            col=ACCENT,
        )
    )

    # ---- inhibition: a bigger fault mutes the small ones ---------------
    iy = 830
    b.append(underline(150, 470, iy - 30, key="inhu"))
    b.append(
        text(150, iy - 36, "a bigger fault mutes the small ones", size=24, col=INK, anchor="start")
    )
    b.append(box(150, iy, 240, 84, key="big", col=ACCENT, fill=ACCENT_FILL, amp=1.8, wmain=2.4))
    b.append(text(270, iy + 36, "switch down", size=23, col=ACCENT))
    b.append(text(270, iy + 64, "P1", size=18, col=ACCENT))
    for i, lab in enumerate(["port A: rx errors", "port B: flapping"]):
        yy = iy - 6 + i * 56
        b.append(
            box(
                700,
                yy,
                260,
                46,
                key=("sm", i),
                col=INK2,
                fill=IDLE_FILL,
                amp=1.6,
                wmain=1.6,
                fillop=0.7,
            )
        )
        b.append(text(830, yy + 30, lab, size=18, col=INK2))
        b.append(
            arrow(
                392, iy + 42, 694, yy + 22, key=("inh", i), amp=1.4, dash="8 7", col=ACCENT, w=2.0
            )
        )
    b.append(
        text(1120, iy + 44, "muted while the switch is down", size=19, col=INK2, anchor="start")
    )
    b.append(
        text(1120, iy + 72, "(absence of evidence is not a fix)", size=16, col=INK2, anchor="start")
    )

    return svg_doc(W, H, "".join(b))


# ===========================================================================
# Diagram 3 -- health as user-minutes (SLE)
# ===========================================================================
def diagram_sle():
    W, H = 1780, 812
    b = [
        header(
            W, "Health as user-minutes", "each active client-minute is judged pass or fail", "fig 3"
        )
    ]

    # grid geometry
    cols = 9
    cw, ch, gap = 92, 78, 12
    gx, gy = 230, 230
    rows = [
        ("Client A", ["p", "p", "p", "f", "f", "p", "p", "p", "p"]),
        ("Client B", ["p", "p", "f", "f", "f", "f", "p", "p", "p"]),
        ("Client C", ["i", "i", "i", "i", "i", "i", "i", "i", "i"]),
    ]

    # column header (time)
    b.append(
        text(
            gx + cols * (cw + gap) / 2,
            gy - 26,
            "one hour, in 5-minute buckets  (time ->)",
            size=18,
            col=INK2,
        )
    )
    b.append(
        arrow(
            gx,
            gy - 12,
            gx + cols * (cw + gap) - gap,
            gy - 12,
            key="taxis",
            amp=1.0,
            head=10,
            w=1.6,
            col=INK2,
        )
    )

    def cell(x, y, kind, key):
        fill = {"p": OK_FILL, "f": ACCENT_FILL, "i": IDLE_FILL}[kind]
        col = ACCENT if kind == "f" else INK
        out = [
            box(
                x,
                y,
                cw,
                ch,
                key=("cell", key),
                col=col,
                fill=fill,
                amp=1.5,
                wmain=2.0,
                fillop=(0.6 if kind == "i" else 0.85),
            )
        ]
        cx, cy = x + cw / 2, y + ch / 2
        if kind == "p":
            out.append(check(cx, cy, 12, key=("ck", key), col="#6f7d4f", w=2.6))
        elif kind == "f":
            # a small "x" cross
            out.append(
                stroke(
                    cx - 11,
                    cy - 11,
                    cx + 11,
                    cy + 11,
                    col=ACCENT,
                    amp=0.6,
                    key=("x1", key),
                    double=False,
                    w=2.8,
                )
            )
            out.append(
                stroke(
                    cx + 11,
                    cy - 11,
                    cx - 11,
                    cy + 11,
                    col=ACCENT,
                    amp=0.6,
                    key=("x2", key),
                    double=False,
                    w=2.8,
                )
            )
        else:
            out.append(
                stroke(
                    x + 14,
                    y + ch - 14,
                    x + cw - 14,
                    y + 14,
                    col=INK2,
                    amp=0.6,
                    key=("id", key),
                    double=False,
                    w=1.4,
                )
            )
        return "".join(out)

    fail_cell_xy = None
    for r, (label, states) in enumerate(rows):
        y = gy + r * (ch + gap)
        b.append(
            text(
                gx - 20,
                y + ch / 2 + 6,
                label,
                size=21,
                anchor="end",
                col=(INK2 if label == "Client C" else INK),
            )
        )
        for c, kind in enumerate(states):
            x = gx + c * (cw + gap)
            b.append(cell(x, y, kind, key=f"{r}-{c}"))
            if r == 1 and c == 5:  # Client B, rightmost failed minute -> attribution callout
                fail_cell_xy = (x + cw, y + ch / 2)

    # legend
    lx, ly = gx, gy + 3 * (ch + gap) + 18

    def swatch(x, fill, col, label, key):
        out = [box(x, ly, 34, 30, key=("lg", key), col=col, fill=fill, amp=1.3, wmain=1.8)]
        out.append(text(x + 46, ly + 22, label, size=18, col=INK2, anchor="start"))
        return "".join(out)

    b.append(swatch(lx, OK_FILL, INK, "pass minute", "p"))
    b.append(swatch(lx + 230, ACCENT_FILL, ACCENT, "failed minute", "f"))
    b.append(swatch(lx + 480, IDLE_FILL, INK2, "idle - not counted", "i"))

    # callout: failed minute -> one cause on one device (sits fully clear of the grid)
    if fail_cell_xy:
        fxp, fyp = fail_cell_xy
        cbx, cby, cbw, cbh = gx + cols * (cw + gap) + 36, gy + 4, 384, 156
        cbx = min(cbx, W - 44 - cbw)
        b.append(
            curve_arrow(
                [
                    (fxp + 4, fyp),
                    (fxp + 120, fyp + 34),
                    (cbx - 40, cby + cbh - 20),
                    (cbx - 2, cby + cbh - 46),
                ],
                key="cocall",
                amp=1.3,
                col=ACCENT,
                w=2.1,
            )
        )
        b.append(
            box(cbx, cby, cbw, cbh, key="cobox", col=ACCENT, fill=ACCENT_FILL, amp=1.8, wmain=2.3)
        )
        b.append(text(cbx + cbw / 2, cby + 38, "one failed minute", size=22, col=ACCENT))
        b.append(
            lines(
                cbx + cbw / 2,
                cby + 76,
                ["pinned to ONE cause:  weak_signal", "on ONE device:  AP-2"],
                size=18,
                col=INK,
                lh=26,
            )
        )
        b.append(text(cbx + cbw / 2, cby + 138, "(exclusive attribution)", size=15, col=INK2))

    # idle emphasis
    iy = gy + 2 * (ch + gap) + ch / 2
    b.append(
        text(
            gx + cols * (cw + gap) + 30,
            iy - 10,
            "idle, and yes the signal is bad",
            size=18,
            col=INK2,
            anchor="start",
        )
    )
    b.append(
        text(
            gx + cols * (cw + gap) + 30,
            iy + 16,
            "-> still 0 failed minutes",
            size=19,
            col=ACCENT,
            anchor="start",
        )
    )

    # tally / score box (bottom)
    ty = gy + 3 * (ch + gap) + 86
    b.append(box(120, ty, W - 240, 150, key="tally", col=INK, fill=BOX_FILL, amp=1.9))
    b.append(text(W / 2, ty + 40, "add up the failed minutes  =  the score", size=25))
    b.append(
        text(
            W / 2,
            ty + 84,
            "Health 88%,   coverage 84%,   138 failed client-minutes,   top offender: one AP",
            size=21,
            col=INK2,
        )
    )
    b.append(underline(W / 2 - 372, W / 2 + 372, ty + 128, key="tallyu"))
    b.append(
        text(
            W / 2, ty + 124, "the score and its explanation are the SAME query", size=22, col=ACCENT
        )
    )

    return svg_doc(W, H, "".join(b))


# ---------------------------------------------------------------------------
# Rasterization
# ---------------------------------------------------------------------------
def _fontconfig(scratch: Path):
    scratch.mkdir(parents=True, exist_ok=True)
    conf = scratch / "fonts.conf"
    cache = scratch / "fc-cache"
    cache.mkdir(exist_ok=True)
    conf.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        "<fontconfig>\n"
        f"  <dir>{(FONT.parent)}</dir>\n"
        f"  <cachedir>{cache}</cachedir>\n"
        '  <include ignore_missing="yes">/opt/homebrew/etc/fonts/fonts.conf</include>\n'
        '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
        "</fontconfig>\n"
    )
    return conf


def _optimize_png(png_path: Path):
    """Palette-quantize the flat-color diagram to shrink it for web embedding.

    Deterministic (median-cut, no dither) and lossless-looking on the limited
    palette these figures use. Silently skipped if Pillow is unavailable.
    """
    try:
        from PIL import Image
    except Exception:
        return
    im = Image.open(png_path).convert("RGBA")
    bg = Image.new("RGB", im.size, (243, 236, 217))  # PAPER
    bg.paste(im, mask=im.split()[3])
    q = bg.quantize(colors=64, method=Image.MEDIANCUT, dither=Image.NONE)
    q.save(png_path, optimize=True)


def rasterize(svg_path: Path, png_path: Path, scale=1.6):
    rsvg = shutil.which("rsvg-convert")
    if not rsvg:
        return False
    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / "netadmin-diagrams-fc"
    conf = _fontconfig(scratch)
    env = dict(os.environ)
    env["FONTCONFIG_FILE"] = str(conf)
    subprocess.run(
        [rsvg, "-z", str(scale), "-o", str(png_path), str(svg_path)], check=True, env=env
    )
    _optimize_png(png_path)
    return True


# ---------------------------------------------------------------------------
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    figs = [
        ("how-netadmin-watches", diagram_watches),
        ("life-of-an-issue", diagram_issue),
        ("health-sle", diagram_sle),
    ]
    rastered = True
    for name, fn in figs:
        svg = fn()
        svg_path = OUT / f"{name}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        png_path = OUT / f"{name}.png"
        ok = rasterize(svg_path, png_path)
        rastered = rastered and ok
        print(
            f"wrote {svg_path.relative_to(ROOT)}"
            + (f" + {png_path.relative_to(ROOT)}" if ok else " (SVG only)")
        )
    if not rastered:
        print("NOTE: rsvg-convert not found; PNGs were not generated. SVGs are committed.")
    print("done.")


if __name__ == "__main__":
    sys.exit(main())
