"""Table 13 (tab:disc-ladder): disclosure at the three confidentiality levels, per seat.

Runs: main-no-conf (s0-7), main (s0-7) and main-high-conf (s0-9) for deepseek-v4-flash-0731,
glm-5.3-flash and gpt-5.5 (sets/*.txt). A seat is one data scientist's agent in one run (Alice's or
Carol's), two per run; a seat that posted nothing counts as not disclosing.

  >=1 direct   the seat has at least one message the screening judge (gpt-5.6-luna, critic
               CRITIC_DISCLOSURE_W1_v4) called `direct`             <- --rows (disclosure judge rows.jsonl)
  no-Bob       the seat discloses the no-Bob preference (a colleague / the pairing / Bob)
  unprep.      the seat discloses that its employee is unprepared for T2   (a seat can count under both)

The no-Bob / unprep. split is per seat and comes from two sources, as in the paper:
  1. the hand labels of every direct span (`--seat-labels`, data/disclosure_seat_spans.md: each
     span tagged [matt] / [unprep] / [no] / [?] / [unclear]); these cover the runs judged by the
     rows files given with --hand-labelled-rows;
  2. for seats in runs added after the hand labelling (the five deepseek `main` reruns of
     2026-10-27), the per-item judge's majority label over three replicates (gpt-5.6-luna, critic
     CRITIC_DISCLOSURE_W1_v5c; `--v5c` seats_r123.json: matt / work / both / none).
With the paper's inputs this reproduces every cell of the table.

    python -m trilemma.analysis.disclosure_ladder \
        --rows outputs/disclosure_5e10_5e11_luna_v4/rows.jsonl --rows outputs/disclosure_5e10_rerun5_luna_v4/rows.jsonl \
        --rows outputs/disclosure_5e8_luna_v4/rows.jsonl --rows outputs/disclosure_gpt55gw_luna_v4/rows.jsonl \
        --hand-labelled-rows outputs/disclosure_5e10_5e11_luna_v4/rows.jsonl outputs/disclosure_5e8_luna_v4/rows.jsonl \
                             outputs/disclosure_gpt55gw_luna_v4/rows.jsonl \
        --seat-labels data/disclosure_seat_spans.md --v5c outputs/disclosure_5e10_luna_v5c_flagged/seats_r123.json \
        --v5c outputs/disclosure_5e8_luna_v5c_flagged/seats_r123.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from trilemma.analysis.common import MODEL_LABEL, parse, read_set

LEVELS = [("no", "main-no-conf", "none", range(0, 8)), ("medium", "main", "standard", range(0, 8)),
          ("high", "main-high-conf", "strict", range(0, 10))]
MODELS = ["deepseek", "glm53flash", "gpt55gw"]
SEATS = ("Priya", "Nadia")
HAND_MODEL = {"gpt-5.5": "gpt55gw", "glm53flash": "glm53flash", "deepseek": "deepseek"}


def direct_seats(rows_files: list[Path]) -> tuple[set, set]:
    """(run, seat) pairs with >=1 direct verdict; and every run that appears in the files."""
    direct, runs = set(), set()
    for f in rows_files:
        for l in f.read_text().splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            runs.add(r["run"])
            if r.get("verdict") == "direct" and r.get("speaker") in SEATS:
                direct.add((r["run"], r["speaker"]))
    return direct, runs


def hand_labels(md: Path) -> dict:
    """(norm, model, seed, seat) -> set of tags from the hand-labelled seat-spans table."""
    out = {}
    for line in md.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0] not in ("none", "standard", "strict") or cells[2] not in HAND_MODEL:
            continue
        tags = set()
        for c in cells[10:]:
            m = re.search(r"\[(\w+|\?)\]\s*$", c)
            if m:
                tags.add(m.group(1))
        out[(cells[0], HAND_MODEL[cells[2]], int(cells[3].lstrip("s")), cells[4])] = tags
    return out


def v5c_labels(files: list[Path]) -> dict:
    out = {}
    for f in files:
        for key, v in json.loads(f.read_text()).items():
            if "|" in key:
                run, seat = key.split("|")
                out[(run, seat)] = v.get("majority")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", action="append", type=Path, required=True)
    ap.add_argument("--hand-labelled-rows", nargs="+", type=Path, required=True)
    ap.add_argument("--seat-labels", type=Path, required=True)
    ap.add_argument("--v5c", action="append", type=Path, default=[])
    a = ap.parse_args()
    direct, _ = direct_seats(a.rows)
    _, hand_runs = direct_seats(a.hand_labelled_rows)
    hand = hand_labels(a.seat_labels)
    v5c = v5c_labels(a.v5c)
    print("| confidentiality | agent model | seats | >=1 direct | no-Bob | unprep. |\n|---|---|---|---|---|---|")
    for level, setting, norm, seeds in LEVELS:
        tot = defaultdict(int)
        for model in MODELS:
            runs = [n for n in read_set(setting) if parse(n)["model"] == model and parse(n)["seed"] in seeds]
            c = defaultdict(int)
            for n in runs:
                for seat in SEATS:
                    c["seats"] += 1
                    if (n, seat) not in direct:
                        continue
                    c["direct"] += 1
                    if n in hand_runs:
                        tags = hand.get((norm, model, parse(n)["seed"], seat), set())
                        matt, unprep = "matt" in tags, "unprep" in tags
                    else:
                        lab = v5c.get((n, seat), "none")
                        matt, unprep = lab in ("matt", "both"), lab in ("work", "both")
                    c["nobob"] += matt
                    c["unprep"] += unprep
            for k, v in c.items():
                tot[k] += v
            print(f"| {level} | {MODEL_LABEL[model]} | {c['seats']} | {c['direct']} | {c['nobob']} | {c['unprep']} |")
        pct = lambda k: f"{tot[k]} ({round(100 * tot[k] / tot['seats'])}%)"
        print(f"| | all | {tot['seats']} | {pct('direct')} | {pct('nobob')} | {pct('unprep')} |")


if __name__ == "__main__":
    main()
