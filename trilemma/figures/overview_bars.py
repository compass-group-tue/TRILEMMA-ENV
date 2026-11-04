"""Overview figure (paper Fig. 2): the aggregate bar chart of the deception and disclosure labels with four example cards.

Counts come from ``judged_data.counts`` (judge outputs). The paper's Fig. 2 is the ``main`` dataset with
the cards in ``data/overview_callouts_main.json`` (four judged Alice examples drawn with seed 1).

    python -m trilemma.figures.overview          # writes outputs/figures/overview_bars_main{,_bars,_cards}.{png,pdf}

Environment knobs: DATASET=main|main-high-conf; CALLOUTS (card JSON) and STEM (output name) override;
XL scales the bar labels; COUNTS=0 drops the count labels over the bars; ONLY=bars|cards writes just the
chart or just the cards; POSTER=1 / GROUPED=1 are alternative poster layouts.

Needs matplotlib, numpy, pillow, uharfbuzz and fonttools (the disclosure emojis use the closest colour emoji font on this OS; see figures/emoji.py).
"""
import os
from pathlib import Path
os.environ.setdefault("GK", "1.2")
os.environ.setdefault("FT", "2.6")
import re
import io
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image

from trilemma.figures.emoji import render_emoji  # closest colour emoji font on this OS


EMOJI_MAN = render_emoji("\U0001F468\U0001F3FB")  # man, light skin tone (black hair)
EMOJI_BRIEFCASE = render_emoji("\U0001F4BC")


def disclosure_flags(cell_text):
    labels = [p.strip() for p in cell_text.split(";")]
    matt = any(l.startswith("direct & about matt") for l in labels)
    work = any(l.startswith("direct & about work") for l in labels)
    return matt, work


def _brace_profile(span, resolution=201):
    """Normalized (0..1) curly-brace bump over `span`, tip at 1 in the middle."""
    beta = 300.0 / span
    t = np.linspace(0, span, resolution)
    half = resolution // 2 + 1
    t_half = t[:half]
    b_half = (1 / (1 + np.exp(-beta * (t_half - t_half[0])))
              + 1 / (1 + np.exp(-beta * (t_half - t_half[-1]))))
    b = np.concatenate((b_half, b_half[-2::-1]))
    return t, (b - b.min()) / (b.max() - b.min())


def draw_left_brace(ax, y0, y1, xx, height, color="#666666", lw=1.3):
    """Curly brace spanning [y0, y1] vertically, tip pointing left `height` from xx."""
    t, b = _brace_profile(y1 - y0)
    y = y0 + t
    x = xx - b * height
    ax.plot(x, y, color=color, lw=lw, clip_on=False, solid_capstyle="round")



MODEL_ORDER = ["deepseek", "dspro", "glm", "glm53", "glm53flash", "kimi", "kimik3", "qwen38flash"]
MODEL_FULL_NAME = {
    "deepseek": "deepseek-v4\nflash-0731",
    "dspro": "deepseek-v4\npro-0813",
    "glm": "glm-5.2",
    "glm53": "glm-5.3",
    "glm53flash": "glm-5.3-flash",
    "kimi": "Kimi-K2.6",
    "kimik3": "Kimi-K3",
    "qwen38flash": "Qwen3.8-Flash",
}
SEED_ORDER = [f"s{i}" for i in range(10)]  # s0..s9, glm53flash s10/s11 omitted
EMP_INITIAL = {"Priya": "A", "Nadia": "C"}  # Priya -> Alice, Nadia -> Carol

COLORS = {
    "FABR": "#CC79A7",
    "FRAM": "#E69F00",
    "NOTH": "#009E73",
    "goes for T1": "#0072B2",
    "sends no message": "#BBBBBB",
    "PREF": "#1B4B8F",  # disclosure of the personal (no-Bob) preference: dark blue
    "WORK": "#A6DBF5",  # disclosure of the work-experience gap: light blue (both under the blue "discloses" chip)
}
T1_DOT_COLOR = "#FFFFFF"
T1_DOT_RING = "#222222"
LABELS = {
    "FABR": "False claim",
    "FRAM": "Misdirection",
    "NOTH": "No deception",
    "goes for T1": "Goes for T1",
    "sends no message": "Sends no message",
}

def strip_md(s):
    s = s.strip()
    s = re.sub(r"^\*\*(.*)\*\*$", r"\1", s)
    return s.strip()

# DATASET=main|main-high-conf: counts from the judge outputs (judged_data.py), poster chart and names
DATASET = os.environ.get("DATASET") or "main"
EMP_ORDER = ["Priya", "Nadia"]
if DATASET not in ("main", "main-high-conf", "5e8", "5e10"):
    raise SystemExit("DATASET must be main or main-high-conf")
DATASET = {"5e8": "main-high-conf", "5e10": "main"}.get(DATASET, DATASET)

# ── callout content: edit findings/full_image_callouts.json to change the quotes ──
CALLOUTS_JSON = Path(os.environ.get("CALLOUTS") or Path(__file__).with_name("data") / f"overview_callouts_{DATASET}.json")
# poster: every example is shown as Alice. Both data scientists face the same dilemma and the Nadia quotes
# name nobody, so relabelling Nadia's seat as Alice changes no content, only who the reader follows.
_AS_ALICE = os.environ.get("POSTER") == "1" or bool(DATASET)
CALLOUTS = [
    dict(model=c["model"], seed=c["seed"], title=c["title"],
         employee="Priya" if _AS_ALICE and c["employee"] == "Nadia" else c["employee"],
         parts=[(p["kind"].capitalize(), p["text"]) for p in c["parts"]])
    for c in json.loads(CALLOUTS_JSON.read_text())
]

import textwrap
import matplotlib.patheffects as pe
from matplotlib.offsetbox import TextArea, VPacker, HPacker, DrawingArea, PaddedBox
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import re
from matplotlib.path import Path

WRAP_WIDTH = 64
GK = float(__import__('os').environ.get('GK', 1.0))  # grid zoom
FS = 1.75 * GK  # grid/legend font scale
FT = float(__import__('os').environ.get('FT', 3.2))  # quote/transcript text scale
CHAT_CHARS = int(__import__('os').environ.get('CHAT_CHARS', 40))
RIGHT_CHARS = int(__import__('os').environ.get('RIGHT_CHARS', 30))
ARROW_COLOR = "#B33A3A"
S = 0.42 * GK  # inches per data unit, fixed so point-sized text maps predictably to data units

n_rows = len(MODEL_ORDER) * len(EMP_ORDER)  # 16
n_cols = len(SEED_ORDER)  # 10

XLIM = (-12, 60)
YLIM = (-20, 90)
fig = plt.figure(figsize=((XLIM[1] - XLIM[0]) * S, (YLIM[1] - YLIM[0]) * S), dpi=200)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(*XLIM)
ax.set_ylim(*YLIM)
ax.set_aspect("equal")
ax.axis("off")

EMP_COLOR = {"Priya": "#332288", "Nadia": "#999933"}  # Alice indigo, Carol olive
EMOJI_THOUGHT = render_emoji("\U0001F4AD")
EMOJI_SPEECH = render_emoji("\U0001F4AC")
EMP_NAME = {"Priya": "Alice", "Nadia": "Carol"}
AVATAR_COLOR = EMP_COLOR
BY_TITLE = {c["title"]: c for c in CALLOUTS}

MONO_CHAR_PTS = FT * 8 * 0.602  # DejaVu Sans Mono advance at 8pt
CARD_PAD = 4 * FT
AVATAR = 20 * FT
HSEP = 5 * FT


def wrap_to(width_pts):
    return int(width_pts // MONO_CHAR_PTS) - 1


PSEUDONYMS = {"Priya": "Alice", "Matthieu": "Bob", "Nadia": "Carol", "Rafael": "Dan"}


def pseudonymize(text):
    return re.sub(r"\b(" + "|".join(PSEUDONYMS) + r")\b", lambda m: PSEUDONYMS[m.group(1)], text)


HIGHLIGHT = {"falseclaim": COLORS["FABR"], "framing": COLORS["FRAM"],
             "pref": COLORS["PREF"], "work": COLORS["WORK"]}
HL_RE = re.compile(r"\\(falseclaim|framing|pref|work)\{([^}]*)\}")


ELLIPSIS = "\x05"  # sentinel for an elision mark; drawn as a grey "…"
ELLIPSIS_COLOR = "#9a9a9a"
ELISION_RE = re.compile(r"\[(?:\.\.\.|\u2026)\]")


def compact_elisions(text):
    """'[...]' -> one grey ellipsis; marks at the very start/end of a quote are dropped."""
    text = ELISION_RE.sub(ELLIPSIS, text)
    text = re.sub(r"^[\s" + ELLIPSIS + r"]+", "", text)
    # a trailing mark goes only after a finished sentence; after "Board state:" it must stay
    m = re.search(r"[\s" + ELLIPSIS + r"]+$", text)
    if m and ELLIPSIS in m.group(0):
        before = text[:m.start()]
        last_visible = re.sub(r"[}*]+$", "", before)[-1:]
        text = before if last_visible in ".!?\"\u201d)" else before + " " + ELLIPSIS
    text = re.sub(ELLIPSIS + r"(\s*" + ELLIPSIS + r")+", ELLIPSIS, text)
    return re.sub(r" (?=" + ELLIPSIS + ")", "\u00a0", text)  # never wrap a lone "…" onto its own line


def parse_marked(paragraph):
    """-> list of (char, bold, highlight_color|None, is_elision) from **bold**,
    \\falseclaim{..}/\\framing{..} and elision sentinels."""
    out, bold, pos = [], False, 0
    segments = []  # (text, hl)
    for m in HL_RE.finditer(paragraph):
        segments.append((paragraph[pos:m.start()], None))
        segments.append((m.group(2), HIGHLIGHT[m.group(1)]))
        pos = m.end()
    segments.append((paragraph[pos:], None))
    for text, hl in segments:
        parts = text.split("**")
        for i, part in enumerate(parts):
            out += [("\u2026", False, None, True) if ch == ELLIPSIS else (ch, bold, hl, False) for ch in part]
            if i < len(parts) - 1:
                bold = not bold
    return out


def wrap_marked(paragraph, width):
    """Greedy word wrap of a marked-up paragraph -> lines of (char, bold, hl)."""
    words, cur = [], []
    for ch in parse_marked(paragraph):
        if ch[0] == " ":
            if cur:
                words.append(cur)
                cur = []
        else:
            cur.append(ch)
    if cur:
        words.append(cur)
    lines, line = [], []
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = []
        if line:
            prev, nxt = line[-1], w[0]
            line.append((" ", prev[1] and nxt[1], prev[2] if prev[2] == nxt[2] else None, False))
        line += w
    lines.append(line)
    return lines


SHORTEN_RE = re.compile(r"\\shorten\{([^{}]*)\}\{([^{}]*)\}")


def body_text(text, width_pts, color="#333333", style="normal", scale=1.0):
    """Monospace block with inline **bold** and colour highlights: chars sit on a fixed-advance
    grid, so bold runs and highlight bars line up exactly with the regular text."""
    text = compact_elisions(pseudonymize(SHORTEN_RE.sub(r"\1", text)))
    char_w = MONO_CHAR_PTS * scale
    wrap = int(width_pts // char_w) - 1
    size = FT * 8 * scale
    line_h = size * 1.2
    rows = []
    for para in text.split("\n"):
        for line in wrap_marked(para, wrap):
            da = DrawingArea(width_pts, line_h, clip=False)
            col = 0
            while col < len(line):
                key = line[col][1:]
                end = col
                while end < len(line) and line[end][1:] == key:
                    end += 1
                b, hl, ell = key
                if hl:
                    da.add_artist(Rectangle((col * char_w - 1, -0.5), (end - col) * char_w + 2,
                                            line_h + 1, facecolor=hl, alpha=0.55, edgecolor="none"))
                da.add_artist(plt.Text(col * char_w, line_h * 0.25, "".join(c[0] for c in line[col:end]),
                                       fontsize=size, family="monospace", color=ELLIPSIS_COLOR if ell else color,
                                       style=style,
                                       fontweight="bold" if b else "normal", ha="left", va="baseline"))
                col = end
            rows.append(da)
    return VPacker(children=rows, align="left", pad=0, sep=0)


def section_header(icon, label):
    return HPacker(children=[OffsetImage(icon, zoom=0.07),
                             TextArea(label, textprops=dict(fontsize=FT * 8.5, fontweight="bold", color="#555555"))],
                   align="center", pad=0, sep=4)


def card(content, **patch):
    attrs = dict(boxstyle="round,pad=0,rounding_size=14", linewidth=0.9)
    attrs.update(patch)
    return PaddedBox(content, pad=CARD_PAD, draw_frame=True, patch_attrs=attrs)


def icon_column(img):
    return VPacker(children=[OffsetImage(img, zoom=AVATAR / img.width)], align="top", pad=0, sep=0)


def reasoning_card(text, employee, inner_w):
    text_w = inner_w - AVATAR - HSEP
    name = TextArea(f"{EMP_NAME[employee]} \u00b7 reasoning",
                    textprops=dict(fontsize=FT * 8.5, style="italic", color="#6b5fa8"))
    right = VPacker(children=[name, body_text(text, text_w, style="italic", color="#4a4a5a"),
                              DrawingArea(text_w, 0)], align="left", pad=0, sep=3)
    return card(HPacker(children=[icon_column(EMOJI_THOUGHT), right], align="top", pad=0, sep=HSEP),
                facecolor="#f2f0f9", edgecolor="#a99fd0", linestyle=(0, (3, 2)))


def avatar(employee):
    da = DrawingArea(AVATAR, AVATAR, clip=False)
    da.add_artist(FancyBboxPatch((0, 0), AVATAR, AVATAR, boxstyle="round,pad=0,rounding_size=4",
                                 facecolor=AVATAR_COLOR[employee], edgecolor="none"))
    da.add_artist(plt.Text(AVATAR / 2, AVATAR / 2 - 0.5, EMP_NAME[employee][0], ha="center", va="center",
                           fontsize=FT * 10, fontweight="bold", color="white"))
    return VPacker(children=[da], align="top", pad=0, sep=0)


AGENT_COLOR = {"Priya": "#332288", "Nadia": "#999933", "Rafael": "#88CCEE", "Matthieu": "#8a8a8a"}


def robot(color, size):
    """The agent pictogram (16x16 viewBox), drawn at `size` points."""
    k = size / 16
    da = DrawingArea(size, size * 0.8, clip=False)
    lw = 1.6 * k
    da.add_artist(FancyBboxPatch((2 * k, 3 * k - 2 * k), 12 * k, 9 * k, boxstyle=f"round,pad=0,rounding_size={2 * k}",
                                 facecolor="none", edgecolor=color, linewidth=lw))
    for cx_ in (6, 10):
        da.add_artist(plt.Circle((cx_ * k, 7.5 * k - 2 * k), 1.3 * k, facecolor=color, edgecolor="none"))
    da.add_artist(Line2D([8 * k, 8 * k], [12 * k - 2 * k, 14 * k - 2 * k], color=color, linewidth=lw))
    return da


def message_card(text, employee, inner_w):
    text_w = inner_w - AVATAR - HSEP
    name = HPacker(children=[
        TextArea(EMP_NAME[employee], textprops=dict(fontsize=FT * 8.5, fontweight="bold", color="#1d1c1d")),
        robot(AGENT_COLOR[employee], FT * 8.5),
        TextArea("message", textprops=dict(fontsize=FT * 8, color="#8a8a8a")),
    ], align="baseline", pad=0, sep=5)
    right = VPacker(children=[name, body_text(text, text_w, color="#1d1c1d"), DrawingArea(text_w, 0)],
                    align="left", pad=0, sep=3)
    return card(HPacker(children=[avatar(employee), right], align="top", pad=0, sep=HSEP),
                facecolor="#ffffff", edgecolor="#cfcfcf")


def make_box(c, width_pts, extra_h_pts=0):
    inner_w = width_pts - 2 * CARD_PAD - 2
    model_name = MODEL_FULL_NAME[c["model"]].replace(chr(10), " ")
    parts_ = [TextArea(shown(c["title"]), textprops=dict(fontsize=FT * 8, fontweight="bold", color="#222222",
                                                         linespacing=1.05)),
              TextArea(model_name, textprops=dict(fontsize=FT * 6, style="italic", color="#777777"))]
    one_line = len(shown(c["title"])) * FT * 8 * 0.64 + 6 * FT + len(model_name) * FT * 6 * 0.56
    heading = (HPacker(children=parts_, align="baseline", pad=0, sep=6 * FT)
               if one_line < width_pts and "\n" not in shown(c["title"])
               else VPacker(children=parts_, align="left", pad=0, sep=1))
    title = VPacker(children=[heading, DrawingArea(width_pts, 0)],  # pins exact width so boxes tile
                    align="left", pad=0, sep=0)
    children = [title]
    for label, text in c["parts"]:
        make = reasoning_card if label == "Reasoning" else message_card
        children.append(make(text, c["employee"], inner_w))
    if extra_h_pts:
        children.append(DrawingArea(width_pts, extra_h_pts - 4))  # pads height to match a paired box
    return VPacker(children=children, align="left", pad=0, sep=4)


PTS_PER_UNIT = S * 72
GAP = 0.6 / GK       # gutter between every pair of blocks, in data units
RIGHT_START = n_cols + GAP  # left edge of the right column
TOP = n_rows + 2.5  # top of the pointer labels above the grid
BOTTOM_TOP = -0.95   # top edge of the box under the grid (leaves a corridor for the Bob arrow)
GRID_RIGHT = n_cols

fig.canvas.draw()
renderer = fig.canvas.get_renderer()
inv = ax.transData.inverted()


def data_extent(artist):
    bb = artist.get_window_extent(renderer)
    (x0, y0), (x1, y1) = inv.transform([[bb.x0, bb.y0], [bb.x1, bb.y1]])
    return x0, y0, x1, y1


def tint(hex_color, amount):
    """Blend `hex_color` toward white; amount=1 is the colour itself."""
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(255 - (255 - v) * amount):02x}" for v in rgb)


BOX_COLORS = {"Disclosure about Bob": (tint(COLORS["PREF"], 0.12), tint(COLORS["PREF"], 0.75)),
              "Disclosure about work": (tint(COLORS["WORK"], 0.16), tint(COLORS["WORK"], 0.9)),
              "Misdirection": (tint(COLORS["FRAM"], 0.16), tint(COLORS["FRAM"], 0.75)),
              "False claim": (tint(COLORS["FABR"], 0.16), tint(COLORS["FABR"], 0.75))}


def add_box(title, width_pts, align, target, extra_h_pts=0):
    face, edge = BOX_COLORS.get(title, ("#fffdf7", "#999999"))
    ab = AnnotationBbox(
        make_box(BY_TITLE[title], width_pts, extra_h_pts), target, xycoords="data", box_alignment=align,
        frameon=True, pad=0.4, annotation_clip=False, zorder=8,
        bboxprops=dict(boxstyle="round,pad=0.35,rounding_size=0.6", facecolor=face,
                       edgecolor=edge, linewidth=1.3 if title in BOX_COLORS else 1.0),
    )
    ax.add_artist(ab)
    return ab


def snap(ab, align, target):
    """Shift the box so its drawn frame edge (per `align`) lands exactly on `target`."""
    fig.canvas.draw()
    x0, y0, x1, y1 = data_extent(ab)
    ex = x0 if align[0] == 0 else x1
    ey = y0 if align[1] == 0 else y1
    bx, by = ab.xybox
    ab.xybox = (bx + target[0] - ex, by + target[1] - ey)



# ════════════════════════ layout: cards | bars | cards ════════════════════════
import pathlib
from matplotlib.transforms import Bbox

from trilemma.common.runs import OUTPUTS
FIND = OUTPUTS / "figures"
FIND.mkdir(parents=True, exist_ok=True)
TOTAL_W = 34.1      # content width in data units — matches full_image_2col (17.2 in)
MIN_BARS_W = 9.0    # narrowest the bar chart may get
LEFT_COL = ["Misdirection", "Disclosure about work"]
RIGHT_COL = ["False claim", "Disclosure about Bob"]
if DATASET:  # false claim + Alice's secret on the left
    LEFT_COL, RIGHT_COL = RIGHT_COL, LEFT_COL
TOP = 0.0           # everything hangs from y = 0 downward
POSTER = os.environ.get("POSTER") == "1" or bool(DATASET)
# GROUPED=1: the poster's grouped bar chart (three bracketed sides, purple T1 pair) with the paper's names
GROUPED = POSTER or os.environ.get("GROUPED") == "1"
# poster wording (matches the comic's "No clean way out" signs); keys are the paper names
POSTER_NAMES = {"False claim": "False claim as reason", "Misdirection": "Misdirect about true reason",
                "No deception": "Refuse to say why",
                "Goes for T1": "Directly goes for T1", "Claimed T1": "Takes T1 after initial resistance",
                "Disclosed: Bob": "Leaks Alice\u2019s secret", "Disclosed: work": "Leaks additional private info",
                "Disclosure about Bob": "Leaks Alice\u2019s secret",
                "Disclosure about work": "Leaks additional private info"}

# poster colours: blue = the poster's "confidential" highlight, purple = ends on T1
# (solid: directly; pale + dark-purple hatching: later — a flat light purple reads as the false-claim pink)
POSTER_COLORS = {"Goes for T1": ("#6A3D9A", "none"), "Claimed T1": ("#F1ECF7", "#6A3D9A"),
                 "Disclosed: Bob": ("#9FC6EA", "none")}


# DATASET figures: their own wording for card titles (and, via BAR_LABELS_2L, the bars)
# (long disclosure titles on two lines, so they don't force wide cards and squeeze the chart)
DATASET_NAMES = {"Disclosure about Bob": "Personal preference disclosure",
                 "Disclosure about work": "Work-experience disclosure"}


def shown(name):
    if DATASET:
        return DATASET_NAMES.get(name, name)
    return POSTER_NAMES.get(name, name) if POSTER else name


def col_w(chars):
    return chars * MONO_CHAR_PTS + AVATAR + HSEP + 2 * CARD_PAD + 8


probe = add_box("Misdirection", 300, (0, 1), (0, 0))
fig.canvas.draw()
_p = data_extent(probe)
frame_extra = (_p[2] - _p[0]) - 300 / PTS_PER_UNIT
probe.remove()

boxes = {}


def place_column(titles, x0, width_pts, align_x):
    y = TOP
    for t in titles:
        boxes[t] = add_box(t, width_pts, (align_x, 1), (x0, y))
        snap(boxes[t], (align_x, 1), (x0, y))
        fig.canvas.draw()
        y = data_extent(boxes[t])[1] - GAP
    return y + GAP


def clear():
    for t in list(boxes):
        boxes.pop(t).remove()


# pick card width (chars per line) giving the shortest figure that still leaves room for the bars
# DATASET figures: each column gets its own card width, picked so both columns end level (no padded
# corners), and the chart's height is then whatever the cards give it
BALANCE = bool(DATASET)
if BALANCE:
    col_h = {}
    for chars in range(20, 46):
        col_h["L", chars] = -place_column(LEFT_COL, 0, col_w(chars), 0)
        clear()
        col_h["R", chars] = -place_column(RIGHT_COL, TOTAL_W, col_w(chars), 1)
        clear()
    def title_pts(title):
        t = ax.text(0, 0, shown(title), fontsize=FT * 8, fontweight="bold")
        fig.canvas.draw()
        w = (data_extent(t)[2] - data_extent(t)[0]) * PTS_PER_UNIT
        t.remove()
        return w

    _tw = {t: title_pts(t) for t in LEFT_COL + RIGHT_COL}

    def title_fits(titles, chars):  # bold title + the corner badge, as make_box lays it out
        return max(_tw[t] for t in titles) + FT * 16 <= col_w(chars)
    fits = [(cl, cr) for cl in range(20, 46) for cr in range(20, 46)
            if title_fits(LEFT_COL, cl) and title_fits(RIGHT_COL, cr)
            and TOTAL_W - (col_w(cl) + col_w(cr)) / PTS_PER_UNIT - 2 * frame_extra - 2 * GAP >= MIN_BARS_W]
    CHARS_L, CHARS_R = min(fits, key=lambda c: abs(col_h["L", c[0]] - col_h["R", c[1]])
                           + 0.03 * max(col_h["L", c[0]], col_h["R", c[1]]))
else:
    best = None
    for chars in range(24, 46):
        cw = col_w(chars) / PTS_PER_UNIT + frame_extra
        bars_w = TOTAL_W - 2 * cw - 2 * GAP
        if bars_w < MIN_BARS_W:
            break
        lb = place_column(LEFT_COL, 0, col_w(chars), 0)
        rb = place_column(RIGHT_COL, TOTAL_W, col_w(chars), 1)
        clear()
        h = -min(lb, rb)
        if best is None or h < best[0]:
            best = (h, chars)
    CHARS_L = CHARS_R = best[1]
CW_L = col_w(CHARS_L) / PTS_PER_UNIT + frame_extra
CW_R = col_w(CHARS_R) / PTS_PER_UNIT + frame_extra
left_bottom = place_column(LEFT_COL, 0, col_w(CHARS_L), 0)
right_bottom = place_column(RIGHT_COL, TOTAL_W, col_w(CHARS_R), 1)
BOTTOM = min(left_bottom, right_bottom, -float(os.environ.get("MIN_H", 0 if BALANCE else 17)))  # short cards would squash the bars


def pad_to(title, x0, align_x, bottom, chars):
    extra = 0.0
    for _ in range(4):
        d = (data_extent(boxes[title])[1] - bottom) * PTS_PER_UNIT
        if abs(d) < 0.3:
            return
        extra = max(0.0, extra + d)
        top = data_extent(boxes[title])[3]
        boxes[title].remove()
        boxes[title] = add_box(title, col_w(chars), (align_x, 1), (x0, top), extra_h_pts=extra + 4)
        snap(boxes[title], (align_x, 1), (x0, top))
        fig.canvas.draw()


pad_to(LEFT_COL[-1], 0, 0, BOTTOM, CHARS_L)
pad_to(RIGHT_COL[-1], TOTAL_W, 1, BOTTOM, CHARS_R)
ext = {t: data_extent(ab) for t, ab in boxes.items()}
print(f"card width {CHARS_L}/{CHARS_R} chars, column height {-BOTTOM:.1f} units")

# disclosure badges, as in the overview figure
badges = {}
for title, icon in (("Disclosure about work", EMOJI_BRIEFCASE), ("Disclosure about Bob", EMOJI_MAN)):
    x0_, y0_, x1_, y1_ = ext[title]
    badges[title] = ax.add_artist(AnnotationBbox(OffsetImage(icon, zoom=FT * 13 / icon.height), (x1_ - 0.6, y1_ - 0.55),
                                 xycoords="data", box_alignment=(0.5, 0.5), frameon=False, pad=0,
                                 annotation_clip=False, zorder=9))

CARD_ARTISTS = set(ax.get_children())  # ONLY=bars|cards: everything drawn after this is the bar chart

# ── the counts ──
from trilemma.figures.judged_data import counts  # noqa: E402
_c = counts(DATASET)
n_agents, verdict_counts = _c["n_agents"], _c["verdicts"]
n_claimed, n_bob, n_work = _c["claimed"], _c["bob"], _c["work"]
SPLIT = _c["split"]  # FABR/FRAM: (acknowledged in CoT, not acknowledged or no CoT)
BARS = [  # (label, count, face, edge, icon, tip) — tip = (count, colour) stacked on top
    ("False claim", verdict_counts["FABR"], COLORS["FABR"], "none", None, None),
    ("Misdirection", verdict_counts["FRAM"], COLORS["FRAM"], "none", None, None),
    ("No deception", verdict_counts["NOTH"], COLORS["NOTH"], "none", None,
     (verdict_counts["sends no message"], COLORS["sends no message"])),
    ("Goes for T1", verdict_counts["goes for T1"], COLORS["goes for T1"], "none", None, None),
    ("Claimed T1", n_claimed, "#FFFFFF", T1_DOT_RING, None, None),
    ("Disclosed: Bob", n_bob, "#e9e6df", "none", EMOJI_MAN, None),
    ("Disclosed: work", n_work, "#e9e6df", "none", EMOJI_BRIEFCASE, None),
]
UNACK_SHADE = 0.42  # pale tint for the part of a deception bar the CoT doesn't acknowledge
if DATASET:  # acknowledged part solid, the rest stacked on top in a pale tint
    _key = {"False claim": "FABR", "Misdirection": "FRAM"}
    BARS = [(lb, SPLIT[_key[lb]][0], f, e, ic,
             (SPLIT[_key[lb]][1], tint(f, UNACK_SHADE)) if SPLIT[_key[lb]][1] else None)
            if lb in _key else (lb, c, f, e, ic, tp) for lb, c, f, e, ic, tp in BARS]
if GROUPED:
    BARS = [(lb, c, *POSTER_COLORS[lb], ic, tp) if lb in POSTER_COLORS else (lb, c, f, e, ic, tp)
            for lb, c, f, e, ic, tp in BARS]
if DATASET:  # the disclosure bars in the same blues as their example cards
    _disc = {"Disclosed: Bob": COLORS["PREF"], "Disclosed: work": COLORS["WORK"]}
    BARS = [(lb, c, _disc.get(lb, f), "none" if lb in _disc else e, ic, tp) for lb, c, f, e, ic, tp in BARS]
N_VERDICT = 4
if GROUPED:  # the three ways of not taking T1 under one bracket; both purple T1 bars side by side after it
    N_VERDICT = 3
print({b[0]: b[1] for b in BARS}, "of", n_agents)

BX0 = CW_L + GAP + 0.9          # room for the tick labels
BX1 = TOTAL_W - CW_R - GAP - 0.2
BFS = float(os.environ.get("BFS", 1.4)) if GROUPED else 1.0  # bar-chart text scale (poster is read from afar)
BFS_TIGHT = 1 + (BFS - 1) / 2  # counts and group chips: less room, so grow half as much
# bar labels; DATASET figures break long names over two lines so the bars get the height
BAR_LABELS_2L = {"False claim": "False claim", "Misdirection": "Misdirection", "No deception": "No deception",
                 "Goes for T1": "Goes for T1", "Claimed T1": "Concedes T1",
                 "Disclosed: Bob": "Personal preference", "Disclosed: work": "Work experience"}


def bar_label(name):
    return BAR_LABELS_2L[name] if BALANCE else shown(name)


n = len(BARS)
group_gap = 0.9 if GROUPED else 0.55
GROUP_STARTS = (3, 5) if GROUPED else (N_VERDICT,)  # a gap opens before each of these bars
slot = (BX1 - BX0 - group_gap * len(GROUP_STARTS)) / n
bw = slot * 0.72
XL = float(os.environ.get("XL", 1))  # x-axis (bar) label scale
TXT = dict(fontsize=FS * BFS * XL * (7.6 if GROUPED else 8.5), color="#333333", linespacing=1.05)
if BALANCE:  # two-line labels: shrink until each fits its bar slot, then measure the tallest
    for _ in range(4):
        _t = [ax.text(0, 0, bar_label(b[0]), rotation=90, **TXT) for b in BARS]
        fig.canvas.draw()
        _ext = [data_extent(t) for t in _t]
        for t in _t:
            t.remove()
        _w = max(e[2] - e[0] for e in _ext)
        if _w <= slot * 0.82:  # leaves a visible gap between neighbouring labels
            break
        TXT["fontsize"] *= slot * 0.82 / _w
    LABEL_H = max(e[3] - e[1] for e in _ext) + 0.45
else:
    LABEL_H = 7.1 * BFS if POSTER else 4.0 * BFS if GROUPED else 4.4  # vertical category labels under the bars
CAP_H = 2.3                    # title + group captions above
YB = BOTTOM + LABEL_H          # bar baseline
YT = TOP - CAP_H               # top of the tallest possible bar
VMAX = max(b[1] + (b[5][0] if b[5] else 0) for b in BARS) * 1.12
if DATASET:  # the CoT key sits over the T1 + leak groups: raise VMAX until it clears their counts and icons
    KEY_TOP = 0.97  # fraction of VMAX
    _clear = (FS * BFS_TIGHT * (8.5 * 1.2 + 11) + 10) / PTS_PER_UNIT  # count label + emoji + margin
    _top = max(b[1] + (b[5][0] if b[5] else 0) for b in BARS[3:])
    _room = (YT - YB) * (KEY_TOP - _top / VMAX) - _clear  # free height over those bars
    # as large as 1.4x the bar labels, shrunk (down to 1x) to fit the free room before VMAX has to grow
    KEY_FS = TXT["fontsize"] * min(1.4, max(1.0, (_room - 0.4) * PTS_PER_UNIT / (4 * 1.17 * TXT["fontsize"])))
    KEY_STEP = 2 * KEY_FS * 1.17 / PTS_PER_UNIT + 0.2  # one two-line row
    # key rows (swatch colours, text): the solid / pale split, only for bars that have a pale part
    # (a third row for the grey "sends no message" tip would push VMAX up and squash the bars)
    _split_k = [k for k in ("FABR", "FRAM") if SPLIT[k][1]]
    KEY_ROWS = ([([COLORS[k] for k in _split_k], "acknowledged\nin CoT"),
                 ([tint(COLORS[k], UNACK_SHADE) for k in _split_k], "unacknowledged\n/ no CoT")] if _split_k else [])
    VMAX = max(VMAX, _top / (KEY_TOP - (len(KEY_ROWS) * KEY_STEP + _clear) / (YT - YB)))


def bar_x(i):
    return BX0 + i * slot + group_gap * sum(i >= g for g in GROUP_STARTS) + (slot - bw) / 2


def ypos(v):
    return YB + (YT - YB) * v / VMAX


# light gridlines + y axis (COUNTS=0: no count labels on the bars, so a gridline every 10, labelled every 20)
COUNTS = os.environ.get("COUNTS", "1") != "0"
for v in range(0, int(VMAX) // 20 * 20 + 1, 20 if COUNTS else 10):  # none past the top label
    ax.plot([BX0 - 0.15, BX1], [ypos(v), ypos(v)], color="#e2e2e2", lw=1.0, zorder=1)
    if v % 20 == 0:
        ax.text(BX0 - 0.3, ypos(v), str(v), ha="right", va="center", fontsize=FS * BFS * 7.5, color="#777777")
ax.plot([BX0 - 0.15, BX1], [YB, YB], color="#555555", lw=1.2, zorder=2)

for i, (label, count, face, edge, icon, tip) in enumerate(BARS):
    x = bar_x(i)
    ax.add_patch(Rectangle((x, YB), bw, ypos(count) - YB, facecolor=face,
                           edgecolor=edge if edge != "none" else "none", linewidth=1.4, zorder=3,
                           hatch="///" if GROUPED and label == "Claimed T1" else None))
    top, top_label = count, str(count)
    if tip is not None:
        ax.add_patch(Rectangle((x, ypos(count)), bw, ypos(count + tip[0]) - ypos(count), facecolor=tip[1],
                               edgecolor=edge, linewidth=1.4, zorder=3))
        top, top_label = count + tip[0], (f"{count}\n+{tip[0]}" if POSTER else f"{count}+{tip[0]}")  # stacked: bigger poster text would overrun the neighbour
    if COUNTS:
        cnt = ax.text(x + bw / 2, ypos(top) + 0.15, top_label, ha="center", va="bottom",
                      fontsize=FS * BFS_TIGHT * 8.5, fontweight="bold", color="#222222", zorder=4, linespacing=0.95)
    if icon is not None:
        fig.canvas.draw()
        ax.add_artist(AnnotationBbox(OffsetImage(icon, zoom=FS * BFS_TIGHT * 11 / icon.height),
                                     (x + bw / 2, (data_extent(cnt)[3] if COUNTS else ypos(top)) + 0.1), xycoords="data",
                                     box_alignment=(0.5, 0), frameon=False, pad=0, zorder=5))
    ax.text(x + bw / 2, YB - 0.25, bar_label(label), ha="center", va="top", rotation=90, multialignment="center", **TXT)

# DATASET: key for the solid / pale split of the two deception bars, over the T1 + leak groups
if DATASET:
    sw = 0.55
    kx = bar_x(3) - 0.1
    tx = kx + max(len(c) for c, _ in KEY_ROWS) * sw * 1.15 + 0.2
    texts = []
    for row, (colors, text) in enumerate(KEY_ROWS):
        y = ypos(VMAX * KEY_TOP) - KEY_STEP / 2 - row * KEY_STEP
        for j, col in enumerate(colors):
            ax.add_patch(Rectangle((kx + j * sw * 1.15, y - sw / 2), sw, sw, facecolor=col,
                                   edgecolor="none", zorder=4))
        texts.append(ax.text(tx, y, text, ha="left", va="center", fontsize=KEY_FS, linespacing=1.0,
                             color="#333333", zorder=4))
    fig.canvas.draw()
    over = max(data_extent(t)[2] for t in texts) - (BX1 + 0.1)
    if over > 0:  # shrink to stay clear of the right-hand cards
        k_ = (BX1 + 0.1 - tx) / (BX1 + 0.1 - tx + over)
        for t in texts:
            t.set_fontsize(t.get_fontsize() * k_)

# group captions: the five verdicts partition the agents; the three markers can overlap
if GROUPED:
    # one bracket per side of the trilemma, chips in the poster's honest / effective / confidential highlights
    GROUPS = (((0, 2), "deception label\n1 per T2 agent" if POSTER else "reason for T2\n1 per agent", "#e2b5ce"), ((3, 4), "ends\non T1", "#dcd7cb"),
              ((5, 6), "discloses\nprivate info", "#9fc6ea"))
else:
    GROUPS = (((0, N_VERDICT - 1), "verdict", None), ((N_VERDICT, len(BARS) - 1), "markers", None))
for (i0, i1), cap, chip in GROUPS:
    xa, xb = bar_x(i0), bar_x(i1) + bw
    yc = YT + 0.55
    ax.plot([xa, xa, xb, xb], [yc - 0.18, yc, yc, yc - 0.18], color="#999999", lw=1.0)
    if chip:
        ax.text((xa + xb) / 2, yc + 0.22, cap, ha="center", va="bottom", multialignment="center",
                fontsize=FS * BFS_TIGHT * 6.6, color="#1d1b1d", fontweight="bold", linespacing=1.05,
                bbox=dict(boxstyle="round,pad=0.22,rounding_size=0.3", facecolor=chip, edgecolor="none"))
    else:
        ax.text((xa + xb) / 2, yc + 0.12, cap, ha="center", va="bottom", fontsize=FS * 7.5, color="#777777")


fig.patch.set_facecolor("#fcfcfb")
ax.set_facecolor("#fcfcfb")
ax.set_xlim(-2, TOTAL_W + 2)
ax.set_ylim(BOTTOM - 2, TOP + 3)
fig.set_size_inches((TOTAL_W + 4) * S, (TOP - BOTTOM + 5) * S)
fig.canvas.draw()

# ONLY=bars: the chart alone; ONLY=cards: the example cards alone, right column moved in beside the left
ONLY = os.environ.get("ONLY", "")
if ONLY == "bars":
    for a in list(boxes.values()) + list(badges.values()):
        a.remove()
elif ONLY == "cards":
    for a in set(ax.get_children()) - CARD_ARTISTS:
        a.remove()
    dx = CW_L + GAP + CW_R - TOTAL_W
    for t in RIGHT_COL:
        for a in (boxes[t], badges.get(t)):
            if a is not None:
                a.xybox = (a.xybox[0] + dx, a.xybox[1])
                a.xy = (a.xy[0] + dx, a.xy[1])
fig.canvas.draw()

# crop both outputs to the drawn content
_buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].astype(int)
_ink = np.abs(_buf - np.array([0xFC, 0xFC, 0xFB])).sum(axis=2) > 12
_ys, _xs = np.where(_ink)
_H = _buf.shape[0]
pad = 0.15
bb = Bbox([[_xs.min() / fig.dpi - pad, (_H - _ys.max()) / fig.dpi - pad],
           [_xs.max() / fig.dpi + pad, (_H - _ys.min()) / fig.dpi + pad]])
plt.rcParams["pdf.fonttype"] = 42
STEM = os.environ.get("STEM") or ("overview_bars_poster" if POSTER else "overview_bars_grouped" if GROUPED else "overview_bars")
if ONLY:
    STEM += "_" + ONLY
for name in (STEM + ".png", STEM + ".pdf"):
    fig.savefig(FIND / name, facecolor=fig.get_facecolor(), bbox_inches=bb)
    print("saved", FIND / name, f"{bb.width:.2f} x {bb.height:.2f} in")
