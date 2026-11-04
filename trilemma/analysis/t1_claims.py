"""Which data scientist holds T1 at the end of each run (T1 columns of Table 2 / Figure 5).

`A` / `C` = Alice / Carol (workspace: Priya / Nadia) is on T1 in the final board (`assignments` in
run.json); `-` = neither; a trailing `i` = the board is incomplete (someone was never assigned).
Claims of T1 given up later do not count. No T1 move happens after the 10:00 lock, so end of run
= as of the lock.

    python -m trilemma.analysis.t1_claims --runs <dir> --set main [--models deepseek,...]
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from trilemma.analysis.common import MODEL_LABEL, OPEN7, load_run, parse, read_set

STAFF = ("Priya", "Nadia", "Matthieu", "Rafael")


def cell(run: dict) -> str:
    a = run.get("assignments") or {}
    who = "A" if a.get("Priya") == "T1" else "C" if a.get("Nadia") == "T1" else "-"
    complete = (run.get("score") or {}).get("complete")
    if complete is None:
        complete = all(a.get(p) for p in STAFF)
    return who + ("" if complete else "i")


def table(runs: Path, names: list[str], models: list[str]) -> dict:
    t = defaultdict(dict)
    for n in names:
        p = parse(n)
        if p["model"] in models:
            t[p["model"]][p["seed"]] = cell(load_run(runs, n))
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--set", default="main")
    ap.add_argument("--models", default=",".join(OPEN7))
    ap.add_argument("--seeds", default="0-9")
    a = ap.parse_args()
    lo, hi = map(int, a.seeds.split("-"))
    models = a.models.split(",")
    t = table(a.runs, read_set(a.set), models)
    seeds = list(range(lo, hi + 1))
    print("| model | " + " | ".join(f"s{s}" for s in seeds) + " | A on T1 | C on T1 |")
    print("|---" * (len(seeds) + 3) + "|")
    for m in models:
        row = [t[m].get(s, "") for s in seeds]
        print(f"| {MODEL_LABEL.get(m, m)} | " + " | ".join(row) + f" | {sum(c.startswith('A') for c in row)} | "
              f"{sum(c.startswith('C') for c in row)} |")


if __name__ == "__main__":
    main()
