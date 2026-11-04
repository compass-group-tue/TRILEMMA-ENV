"""The reasoning part of the dislike-reasons appendix as an LLM read (superbad runs).

The paper's categories (3 never push / 3 ask once and fold / 2 hold) and the four giving-way moves
come from a close read of all 8 dislike-superbad runs: Alice's agent's full reasoning at every turn
from kickoff to lock, interleaved with #sep-2026-sprint and her DMs with Carol, Mary and Bob. This
script reproduces that read with a model; it was not run for the paper (the paper's read was done by
hand with Claude), so expect it to agree on the categories rather than on the exact quotes.

    python -m trilemma.analysis.dislike_reasons_read --runs <dir> [--model openrouter:openai/gpt-6.1-sol] [--out outputs/dislike_read.jsonl]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from trilemma.analysis.common import NAME_KEY, llm, load_calls, load_run, parse, read_set, seat_timeline

PROMPT = Path(__file__).parent / "prompts" / "dislike_superbad_read.md"
CONVS = {"#sep-2026-sprint", "dm:Nadia+Priya", "dm:Helena+Priya", "dm:Matthieu+Priya"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--set", default="dislike-superbad")
    ap.add_argument("--model", default="openrouter:openai/gpt-6.1-sol")
    ap.add_argument("--out", default=Path("outputs/dislike_read.jsonl"), type=Path)
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmpl = PROMPT.read_text()
    cats = Counter()
    with a.out.open("w") as f:
        for n in read_set(a.set):
            run = load_run(a.runs, n)
            tl = seat_timeline(run, load_calls(a.runs, n), "Priya", CONVS, until="10:00")
            prompt = tmpl.replace("{name_key}", NAME_KEY).replace("{timeline}", tl)
            v = llm().chat_json(a.model, [{"role": "user", "content": prompt}])
            f.write(json.dumps({"run": n, **parse(n), **v}) + "\n")
            cats[v.get("category")] += 1
            print(parse(n)["model"], parse(n)["seed"], v.get("category"), v.get("moves"))
    print(dict(cats), "(paper: never_pushes 3, asks_once_folds 3, holds 2)")


if __name__ == "__main__":
    main()
