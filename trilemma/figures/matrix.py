"""Per-seat verdict matrices (paper Fig. 5): one cell per agent and run, coloured by the deception label,
with the T1 ring and the two disclosure emojis, plus per-model counters.

    python -m trilemma.figures.matrix --paper          # Fig. 5a (main) and 5b (main-high-conf without glm-5.2)
    python -m trilemma.figures.matrix main main-high-conf main-high-conf-noglm52

Data: ``judged_data`` (deception seat labels, disclosure flags, final board). ``--paper``: white
background, larger fonts, paper model spelling, closed-weight block last, the shared legend only on
main-high-conf-noglm52; both pages pinned to the paper's size (545 x 744 pt) with equal cells.
Writes ``outputs/figures/[matrices/]matrix-<variant>_rotated.{png,pdf}``.

Needs matplotlib, numpy, pillow, uharfbuzz, fonttools, pypdf, and macOS's Apple Color Emoji font.
"""
import os
import sys
from pathlib import Path
import re
import io
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image
import uharfbuzz as hb
from fontTools.ttLib import TTFont

from trilemma.common.runs import OUTPUTS
from trilemma.figures import judged_data
from trilemma.figures.matrix_legend import draw_legend

EMOJI_FONT_PATH = "/System/Library/Fonts/Apple Color Emoji.ttc"
with open(EMOJI_FONT_PATH, "rb") as _f:
    _HB_FACE = hb.Face(_f.read())
_HB_FONT = hb.Font(_HB_FACE)
_TT_FONT = TTFont(EMOJI_FONT_PATH, fontNumber=0)
_SBIX_STRIKE = _TT_FONT["sbix"].strikes[160]


def render_emoji(text):
    """Shape `text` (handles skin-tone/ZWJ sequences via harfbuzz) and pull the
    resulting glyph's color bitmap directly out of the font's sbix table."""
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(_HB_FONT, buf)
    gid = buf.glyph_infos[0].codepoint
    glyph_name = _TT_FONT.getGlyphName(gid)
    glyph = _SBIX_STRIKE.glyphs[glyph_name]
    im = Image.open(io.BytesIO(glyph.imageData)).convert("RGBA")
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


EMOJI_MAN = render_emoji("\U0001F468\U0001F3FB")  # man, light skin tone (black hair)
EMOJI_BRIEFCASE = render_emoji("\U0001F4BC")


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


# glm family first, glm-5.2 set apart by GLM52_GAP (main: gpt56sol/opus5or appended after the same gap) so main-high-conf (has it) and main (doesn't) line up
MODEL_ORDER_ALL = ["glm", "glm53", "glm53flash", "deepseek", "dspro", "kimi", "kimik3", "qwen38flash"]
GLM52_GAP = 0.6
# variant -> (dataset, models left out, output file)
DATASETS = {"main-high-conf": ("main-high-conf", (), "matrix-main-high-conf_rotated.png"),
            "main-high-conf-noglm52": ("main-high-conf", ("glm",), "matrix-main-high-conf-noglm52_rotated.png"),
            "main": ("main", (), "matrix-main_rotated.png")}

MODEL_FULL_NAME = {
    "deepseek": "deepseek-v4\nflash-0731",
    "dspro": "deepseek-v4\npro-0813",
    "glm": "glm-5.2",
    "glm53": "glm-5.3",
    "glm53flash": "glm-5.3-flash",
    "kimi": "Kimi-K2.6",
    "kimik3": "Kimi-K3",
    "qwen38flash": "Qwen3.8-Flash",
    "gpt56sol": "gpt-5.6-sol\n(no-CoT)",
    "opus5or": "Opus 5\n(no-CoT)",
}
SEED_ORDER = [f"s{i}" for i in range(10)]  # s0..s9, glm53flash s10/s11 omitted
EMP_INITIAL = {"Priya": "A", "Nadia": "C"}  # Priya -> Alice, Nadia -> Carol

COLORS = {
    "FABR": "#CC79A7",
    "FRAM": "#E69F00",
    "NOTH": "#009E73",
    "goes for T1": "#6A3D9A",  # purple, as in the 16oct paper bars
    "sends no message": "#BBBBBB",
}
T1_DOT_COLOR = "#FFFFFF"
T1_DOT_RING = "#222222"
def count_chips(ax, x, y, rows, model, fontsize=9, dx=0.34, disclosure=None, dy=0.42, emoji_zoom=0.062, em=1.0,
                left_numbers=False):
    """Two rounded chips, #False claim then #Misdirection, in the legend colours; (x, y) = their centre.
    With `disclosure` ((model, seed, employee) -> (personal, work)) a second row beneath counts the
    personal-preference and work-experience disclosures, each as its legend emoji plus the count."""
    verdicts = [v for (m, _, _), v in rows.items() if m == model]
    y_top = y + dy if disclosure is not None else y
    for k, off in (("FABR", -dx), ("FRAM", dx)):
        ax.text(x + off, y_top, str(verdicts.count(k)), ha="center", va="center", fontsize=fontsize,
                fontweight="bold", color="#1f1f1f", zorder=6, clip_on=False,
                bbox=dict(boxstyle="round,pad=0.28,rounding_size=0.35", facecolor=COLORS[k], edgecolor="none"))
    if disclosure is None:
        return
    flags = [f for (m, _, _), f in disclosure.items() if m == model]
    for im, idx, off in ((EMOJI_MAN, 0, -dx), (EMOJI_BRIEFCASE, 1, dx)):
        n = sum(f[idx] for f in flags)
        # left_numbers: emoji just left of the chip centre, count left-aligned after it (room for two digits)
        e_off, n_off, n_ha = (-0.17 * em, 0.03 * em, "left") if left_numbers else (-0.19 * em, 0.17 * em, "center")
        ax.add_artist(AnnotationBbox(OffsetImage(im, zoom=emoji_zoom), (x + off + e_off, y - dy),
                                     frameon=False, pad=0, zorder=6, annotation_clip=False))
        ax.text(x + off + n_off, y - dy, str(n), ha=n_ha, va="center", fontsize=fontsize,
                fontweight="bold", color="#1f1f1f", zorder=6, clip_on=False)


LABELS = {
    "FABR": "False claim",
    "FRAM": "Misdirection",
    "NOTH": "No deception",
    "goes for T1": "Goes for T1",
    "sends no message": "Sends no message",
}

EMP_ORDER = ["Priya", "Nadia"]


# --paper: white background, bigger fonts, one shared legend (on main-high-conf-noglm52), equal PDF heights
# paper spelling (lowercase, as in the paper's tables/text), broken over two lines where long; no "(no-CoT)" suffix
PAPER_NAMES = {
    "deepseek": "deepseek-v4-\nflash-0731",
    "dspro": "deepseek-v4-\npro-0813",
    "glm": "glm-5.2",
    "glm53": "glm-5.3",
    "glm53flash": "glm-5.3-\nflash",
    "kimi": "kimi-k2.6",
    "kimik3": "kimi-k3",
    "qwen38flash": "qwen3.8-\nflash",
    "gpt56sol": "gpt-5.6-\nsol",
    "opus5or": "claude-\nopus-5",
}
# chip_em scales the counter column (chip spacing, emoji size/offsets) with chip_fs; paper_size_pt pins the
# page size (free_aspect: cells may be taller than wide), so larger text shrinks the grid, not the page
PAPER = {"bg": "#ffffff", "legend_fs": 14.5, "legend_zoom": 0.115, "model_fs": 16.5, "emp_fs": 11,
         "seed_fs": 12, "seed_label_fs": 13, "chip_fs": 14.5, "chip_em": 1.45, "chip_dy": 0.47, "chip_x": 1.12, "chip_dx": 0.62, "chip_left_numbers": True,
         "legend_on": ("main-high-conf-noglm52",), "subdir": "matrices/", "names": PAPER_NAMES,
         "closed_first": False, "legend_at": "bottom", "free_aspect": True,
         # page size of the paper PDFs (main; main-high-conf-noglm52 gets the same height)
         "paper_size_pt": (544.94, 744.08)}
NORMAL = {"bg": "#fcfcfb", "legend_fs": 9, "legend_zoom": 0.075, "model_fs": 9, "emp_fs": 7,
          "seed_fs": 8, "seed_label_fs": 10, "chip_fs": 9, "chip_em": 1.0, "chip_dy": 0.42, "chip_x": 0.62, "chip_dx": 0.36, "chip_left_numbers": False,
          "legend_on": tuple(DATASETS), "subdir": "", "closed_first": False, "legend_at": "bottom"}


def _pdf_height(path):
    from pypdf import PdfReader
    box = PdfReader(path).pages[0].mediabox
    return float(box.height)


def draw(variant, style=NORMAL, legend_offset=1.2, pad_pt=0.0, fig_h_scale=1.0, fig_k=1.0, fig_size=None,
         extra_rows=None):
    """Returns the saved PDF path. legend_offset: legend edge in cells away from the grid (below, or above
    with style legend_at="top"); pad_pt: extra white space appended to the PDF page on the legend side;
    fig_h_scale: figure-height factor (paper-mode cell matching); fig_k: overall figure-size factor (paper-mode
    width pinning). Free-aspect styles (paper) instead take fig_size=(w, h) inches and extra_rows (y units of
    legend space below the grid): cells may be taller than wide so the page size can be pinned."""
    free = style.get("free_aspect", False)
    ds, left_out, out_name = DATASETS[variant]
    closed = judged_data.CLOSED.get(ds, [])  # appended as a separate block: no-CoT labels, no ack check
    open_models = [m for m in MODEL_ORDER_ALL if m in judged_data.MODELS[ds] and m not in left_out]
    MODEL_ORDER = closed + open_models if style["closed_first"] else open_models + closed
    rows = judged_data.reason_labels(ds)  # (model, seed, employee) -> verdict
    disclosure = judged_data.leak_flags(ds)  # (model, seed, employee) -> (bob: bool, work: bool)
    t1 = judged_data.t1_claims(ds)
    if closed:
        rows.update(judged_data.closed_reason_labels(ds))
        disclosure.update(judged_data.closed_leak_flags(ds))
        t1 |= judged_data.closed_t1_claims(ds)
    claimed_t1 = {seat: seat in t1 for seat in rows}
    draw.n_rows = None
    rows = {seat: v for seat, v in rows.items() if seat[0] in MODEL_ORDER}
    disclosure = {seat: f for seat, f in disclosure.items() if seat[0] in MODEL_ORDER}
    expected = len(MODEL_ORDER) * len(SEED_ORDER) * 2
    assert len(rows) == expected and set(disclosure) == set(rows), (ds, len(rows), len(disclosure))

    # ── rotated layout: 16 rows (model x employee) x 10 columns (samples, s0 leftmost) ──
    G, pos = [], 0  # first column/row of each model group; a gap after glm-5.2 and between open and closed blocks
    for ci, model in enumerate(MODEL_ORDER):
        if ci > 0 and (MODEL_ORDER[ci - 1] == "glm" or (MODEL_ORDER[ci - 1] in closed) != (model in closed)):
            pos += GLM52_GAP
        G.append(pos)
        pos += 2
    n_rows = G[-1] + 2
    draw.n_rows = n_rows
    n_cols = len(SEED_ORDER)  # 10

    # a lower legend needs a taller figure and axes range, else tight_layout shrinks the grid to fit it in
    extra = max(0.0, legend_offset - 1.2)
    fig_w, fig_h = 7.6 * fig_k, (2.0 + 8.5 * (n_rows + extra) / 16) * fig_h_scale * fig_k
    if free:
        fig_w, fig_h = fig_size
        extra = extra_rows or 0.0
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=200)

    cell = 1.0
    gap = 0.08  # surface gap between cells

    for ci, model in enumerate(MODEL_ORDER):
        for ei, employee in enumerate(EMP_ORDER):
            row = G[ci] + ei
            y = n_rows - 1 - row
            for ri, seed in enumerate(SEED_ORDER):
                x = ri
                v = rows[(model, seed, employee)]
                color = COLORS[v]
                ax.add_patch(Rectangle(
                    (x + gap / 2, y + gap / 2), cell - gap, cell - gap,
                    facecolor=color, edgecolor="white", linewidth=1.2,
                ))

                matt, work = disclosure[(model, seed, employee)]
                icons = [im for im, flag in ((EMOJI_MAN, matt), (EMOJI_BRIEFCASE, work)) if flag]
                if icons:
                    cx = x + cell / 2
                    cy = y + cell / 2
                    icon_zoom = 0.155 if len(icons) == 1 else 0.085
                    offsets = [0] if len(icons) == 1 else [-0.2, 0.2]
                    for im, dx in zip(icons, offsets):
                        ab = AnnotationBbox(
                            OffsetImage(im, zoom=icon_zoom), (cx + dx, cy),
                            frameon=False, pad=0, zorder=4,
                        )
                        ax.add_artist(ab)

                if v != "goes for T1" and claimed_t1[(model, seed, employee)]:
                    if free:  # a marker stays round when cells are not square
                        ax.plot([x + cell - 0.20], [y + cell - 0.20], marker="o", markersize=4.6,
                                markerfacecolor=T1_DOT_COLOR, markeredgecolor=T1_DOT_RING,
                                markeredgewidth=1.1, linestyle="", zorder=5)
                    else:
                        ax.add_patch(plt.Circle(
                            (x + cell - 0.20, y + cell - 0.20), 0.078,
                            facecolor=T1_DOT_COLOR, edgecolor=T1_DOT_RING, linewidth=1.1, zorder=5,
                        ))

    # model-group separators (sharp dark rule every 2 rows, plus outer edges)
    for yy in sorted({n_rows - g for g in G} | {n_rows - g - 2 for g in G}):
        ax.plot([0, n_cols], [yy, yy], color="#4d4d4d", linewidth=2.2, zorder=3, solid_capstyle="butt")

    ax.set_xlim(0, n_cols)
    top = style["legend_at"] == "top"
    ax.set_ylim(0 if top else -extra, n_rows + extra if top else n_rows)
    ax.set_aspect("auto" if free else "equal")

    ax.set_xticks([])
    # seed labels as text pinned to the grid bottom (tick labels would sit at ylim, which the legend extends)
    for ri, seed in enumerate(SEED_ORDER):
        ax.text(ri + 0.5, -0.30, seed, ha="center", va="center", fontsize=style["seed_fs"], color="#666666")
    ax.text(n_cols / 2, -0.30 - 0.022 * (style["seed_fs"] + style["seed_label_fs"]), "seed", ha="center",
            va="center", fontsize=style["seed_label_fs"], color="#666666")

    ax.set_yticks([])

    brace_x = -0.38  # left of the A/C letters (right-aligned at -0.12)
    brace_width = 0.22
    for ci, model in enumerate(MODEL_ORDER):
        y0, y1 = n_rows - (G[ci] + 2) + 0.06, n_rows - G[ci] - 0.06
        draw_left_brace(ax, y0, y1, brace_x, brace_width, color="#666666", lw=1.3)
        ax.text(brace_x - brace_width - 0.10, (y0 + y1) / 2, style.get("names", {}).get(model, MODEL_FULL_NAME[model]),
                ha="right", va="center", fontsize=style["model_fs"], color="#666666")
        em = style["chip_em"]
        count_chips(ax, n_cols + style["chip_x"], (y0 + y1) / 2, rows, model, fontsize=style["chip_fs"],
                    dx=style["chip_dx"], disclosure=disclosure, dy=style["chip_dy"], emoji_zoom=0.062 * em, em=em,
                    left_numbers=style["chip_left_numbers"])

    for spine in ax.spines.values():
        spine.set_visible(False)

    # Alice/Carol sub-labels left of the grid
    for ci, model in enumerate(MODEL_ORDER):
        row_a = n_rows - 1 - (G[ci] + 0)
        row_c = n_rows - 1 - (G[ci] + 1)
        ax.text(-0.12, row_a + 0.5, EMP_INITIAL["Priya"], ha="right", va="center", fontsize=style["emp_fs"], color="#999999")
        ax.text(-0.12, row_c + 0.5, EMP_INITIAL["Nadia"], ha="right", va="center", fontsize=style["emp_fs"], color="#999999")

    if variant in style["legend_on"]:
        draw_legend(ax, COLORS, LABELS, T1_DOT_COLOR, T1_DOT_RING, EMOJI_MAN, EMOJI_BRIEFCASE,
                    anchor=(n_cols / 2, (-(1.3 + extra) / 2 if free else
                                         n_rows + legend_offset if top else -legend_offset)), ncol=2,
                    fontsize=style["legend_fs"], emoji_zoom=style["legend_zoom"],
                    loc="center" if free else "lower center" if top else "upper center")

    fig.patch.set_facecolor(style["bg"])
    ax.set_facecolor(style["bg"])

    plt.tight_layout()
    out_dir = str(OUTPUTS / "figures") + "/" + style["subdir"]
    os.makedirs(out_dir, exist_ok=True)
    out = out_dir + out_name
    plt.savefig(out, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.05)
    plt.savefig(out[:-4] + ".pdf", facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.05)
    # one data unit (cell) in PDF points, for paper-mode height matching
    draw.cell_pt = (ax.transData.transform((1, 0)) - ax.transData.transform((0, 0)))[0] * 72 / fig.dpi
    draw.cell_h_pt = (ax.transData.transform((0, 1)) - ax.transData.transform((0, 0)))[1] * 72 / fig.dpi
    print("saved", out)
    plt.close(fig)
    if pad_pt > 0:  # extend the page on the legend side
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import RectangleObject
        r = PdfReader(out[:-4] + ".pdf")
        w = PdfWriter()
        w.append(r)
        pg = w.pages[0]
        box = pg.mediabox
        pg.mediabox = RectangleObject((float(box.left), float(box.bottom) - (0 if top else pad_pt),
                                       float(box.right), float(box.top) + (pad_pt if top else 0)))
        pg.cropbox = pg.mediabox
        with open(out[:-4] + ".pdf", "wb") as f:
            w.write(f)
        im = Image.open(out)
        pad_px = int(round(pad_pt / 72 * fig.dpi))
        canvas = Image.new(im.mode, (im.width, im.height + pad_px), style["bg"])
        canvas.paste(im, (0, pad_px if top else 0))
        canvas.save(out)
    return out[:-4] + ".pdf"


def _pdf_size(path):
    from pypdf import PdfReader
    box = PdfReader(path).pages[0].mediabox
    return float(box.width), float(box.height)


def draw_paper():
    """main pinned to PAPER["paper_size_pt"] (figure size corrected until the tight page matches; cells go
    taller than wide as needed), then main-high-conf-noglm52 at the same height with the same cell width and height,
    its legend centred in the extra rows below the grid."""
    W, H = PAPER["paper_size_pt"]
    w, h = W / 72, H / 72
    for _ in range(8):
        cw, ch = _pdf_size(draw("main", PAPER, fig_size=(w, h)))
        if abs(cw - W) < 0.3 and abs(ch - H) < 0.3:
            break
        w, h = w + (W - cw) / 72, h + (H - ch) / 72
    ux, uy = draw.cell_pt, draw.cell_h_pt
    print(f"main: {cw:.2f} x {ch:.2f} pt, cell {ux:.2f} x {uy:.2f} pt")
    w2, h2, extra = w, h, 6.0
    for _ in range(12):
        cw2, ch2 = _pdf_size(draw("main-high-conf-noglm52", PAPER, fig_size=(w2, h2), extra_rows=extra))
        dux, duy, n_rows = ux - draw.cell_pt, uy - draw.cell_h_pt, draw.n_rows
        if abs(dux) < 0.02 and abs(duy) < 0.02 and abs(ch2 - H) < 0.3:
            break
        axes_h = draw.cell_h_pt * (n_rows + extra)
        extra = max(2.0, axes_h / uy - n_rows)
        w2, h2 = w2 + dux * 10 / 72, h2 + (H - ch2) / 72
    print(f"main-high-conf-noglm52: {cw2:.2f} x {ch2:.2f} pt, cell {draw.cell_pt:.2f} x {draw.cell_h_pt:.2f} pt, "
          f"legend rows {extra:.2f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if {"-h", "--help"} & set(args):
        sys.exit(__doc__)
    if "--paper" in args:
        draw_paper()
    else:
        for variant in args or DATASETS:
            draw(variant)
