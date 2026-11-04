"""Unseeded cooperation (Tables 28/29): do Alice's and Carol's agents, without a planted shared DM,
stop competing for T2 and hand the staffing to Mary?

Stage 1 (deterministic screen): runs with at least one message between Alice's and Carol's agents in
their DM. Flag `both_t2` = both end on T2 (all four paper runs R1-R4 do).
Stage 2 (--judge, LLM): read the DMs, the sprint channel, both agents' DMs with Mary, and both agents'
reasoning at their DM turns; classify escalation (joint / fallback / standoff / none), reveal, and
whether the agent models the other as an avoider (prompts/unseeded_coop.md).

The paper searched 88 glm-5.3-flash runs (main + main-high-conf + similar settings, and S1/S2) and read
the candidates by hand with Claude; the exact 88-run pool was not saved. The paper's four runs are in
sets/unseeded-coop-R.txt and the script checks they pass the screen.

    python -m trilemma.analysis.unseeded_coop --runs <dir> --sets main main-high-conf unseeded-coop-S1 unseeded-coop-S2 \
        [--model glm53flash] [--judge --llm openrouter:openai/gpt-6.1-sol]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from trilemma.analysis.common import (NAME_KEY, conv_labels, llm, load_calls, load_run, parse, read_set,
                                      seat_timeline)

DM = "dm:Nadia+Priya"
CONVS = {DM, "#sep-2026-sprint", "dm:Helena+Priya", "dm:Helena+Nadia"}
PROMPT = Path(__file__).parent / "prompts" / "unseeded_coop.md"
PAPER = Path(__file__).resolve().parents[2] / "sets" / "unseeded-coop-R.txt"


def screen(run: dict, calls: list[dict]) -> dict:
    lab = conv_labels(run)
    dms = [c for c in calls if c.get("tool") == "chat_postMessage" and c.get("agent") in ("Priya", "Nadia")
           and lab.get(str(c["args"].get("channel"))) == DM]
    a = run.get("assignments") or {}
    return {"dms": len(dms), "both_t2": a.get("Priya") == "T2" and a.get("Nadia") == "T2",
            "dm_times": sorted({(c.get("clock") or "")[11:16] for c in dms})}


def timeline(run: dict, calls: list[dict], dm_times: list[str]) -> str:
    parts = []
    for seat in ("Priya", "Nadia"):
        parts.append(f"===== {seat}'s assistant: reasoning interleaved with the messages =====\n" +
                     seat_timeline(run, calls, seat, CONVS, until="10:15"))
    return "\n\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--sets", nargs="+", default=["main", "main-high-conf", "unseeded-coop-S1", "unseeded-coop-S2"])
    ap.add_argument("--model", default="glm53flash")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--llm", default="openrouter:openai/gpt-6.1-sol")
    ap.add_argument("--out", default=Path("outputs/unseeded_coop.jsonl"), type=Path)
    a = ap.parse_args()
    pool = sorted({n for s in a.sets for n in read_set(s) if parse(n)["model"] == a.model})
    paper = set(read_set(PAPER)) if PAPER.exists() else set()
    tmpl = PROMPT.read_text()
    f = None
    if a.judge:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        f = a.out.open("w")
    n_dm = n_t2 = 0
    for n in pool:
        run, calls = load_run(a.runs, n), load_calls(a.runs, n)
        s = screen(run, calls)
        if not s["dms"]:
            continue
        n_dm += 1
        n_t2 += s["both_t2"]
        mark = " <- paper " if n in paper else ""
        print(f"{'both-T2' if s['both_t2'] else '       '} dms={s['dms']:2} {n}{mark}")
        if a.judge:
            v = llm().chat_json(a.llm, [{"role": "user", "content": tmpl.replace("{name_key}", NAME_KEY).replace(
                "{timeline}", timeline(run, calls, s["dm_times"]))}])
            f.write(json.dumps({"run": n, **parse(n), **s, **v}) + "\n")
    print(f"pool {len(pool)} runs; {n_dm} with an Alice<->Carol DM; {n_t2} of those end with both on T2")
    missing = paper - set(pool)
    if missing:
        print("paper runs outside the pool:", *sorted(missing), sep="\n  ")


if __name__ == "__main__":
    main()
