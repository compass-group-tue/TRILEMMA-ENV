"""Shared legend for the build_matrix*.py verdict matrices: colour swatches, the T1 ring and the two
disclosure emojis in one matplotlib legend, anchored in data coordinates just below the grid so
bbox_inches="tight" leaves no dead space."""
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D
from matplotlib.offsetbox import AnnotationBbox, OffsetImage

VERDICT_KEYS = ["FABR", "FRAM", "NOTH", "goes for T1", "sends no message"]


class _Emoji:
    def __init__(self, im, label):
        self.im, self.label = im, label

    def get_label(self):
        return self.label


class _EmojiHandler(HandlerBase):
    def __init__(self, zoom):
        super().__init__()
        self.zoom = zoom

    def create_artists(self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans):
        return [AnnotationBbox(OffsetImage(orig_handle.im, zoom=self.zoom),
                               (width / 2 - xdescent, height / 2 - ydescent), xycoords=trans, frameon=False, pad=0)]


def draw_legend(ax, colors, labels, t1_dot_color, t1_dot_ring, emoji_man, emoji_briefcase,
                anchor, ncol, fontsize=9, emoji_zoom=0.075, loc="upper center"):
    """`anchor` = (x, y) in data coordinates: the legend's top centre (or its bottom centre with
    loc="lower center", for a legend above the grid)."""
    handles = [Line2D([0], [0], marker="s", linestyle="", markersize=12,
                      markerfacecolor=colors[k], markeredgecolor="none", label=labels[k]) for k in VERDICT_KEYS]
    handles.append(Line2D([0], [0], marker="o", linestyle="", markersize=9, markerfacecolor=t1_dot_color,
                          markeredgecolor=t1_dot_ring, markeredgewidth=1.1, label="Claimed T1 on final board"))
    emojis = [_Emoji(emoji_man, "Personal preference disclosure"), _Emoji(emoji_briefcase, "Work-experience disclosure")]
    handler = _EmojiHandler(emoji_zoom)
    ax.legend(handles=handles + emojis, handler_map={_Emoji: handler},
              loc=loc, bbox_to_anchor=anchor, bbox_transform=ax.transData,
              ncol=ncol, frameon=False, fontsize=fontsize, handletextpad=0.5, columnspacing=1.4,
              labelspacing=0.7, borderaxespad=0)
