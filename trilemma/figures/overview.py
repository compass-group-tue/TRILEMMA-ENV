"""Paper Fig. 2 (``main``) and its ``main-high-conf`` twin from the judge labels.

    python -m trilemma.figures.overview

main:           outputs/figures/overview_bars_main.{png,pdf} (+ _bars / _cards halves), cards from
                data/overview_callouts_main.json (drawn once with ``judged_data.random_callouts("main", seed=1)``;
                delete the file to redraw them from your own judge outputs).
main-high-conf: outputs/figures/overview_bars_main-high-conf.{png,pdf}, cards from
                data/overview_callouts_main-high-conf.json.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from trilemma.figures.judged_data import random_callouts

HERE = Path(__file__).resolve().parent


def draw(**env):
    subprocess.run([sys.executable, "-m", "trilemma.figures.overview_bars"], check=True,
                   env={**os.environ, "POSTER": "", "GROUPED": "", **env})


def main():
    draw(DATASET="main-high-conf", STEM="overview_bars_main-high-conf")
    cards = HERE / "data" / "overview_callouts_main.json"
    if not cards.exists():
        cards.write_text(json.dumps(random_callouts("main", seed=1), indent=2, ensure_ascii=False) + "\n")
    for only in ("", "bars", "cards"):  # the full figure, then the chart and the cards on their own
        draw(DATASET="main", CALLOUTS=str(cards), STEM="overview_bars_main", COUNTS="0", XL="0.7", ONLY=only)


if __name__ == "__main__":
    main()
