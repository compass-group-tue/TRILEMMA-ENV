"""Label the Alice<->Carol DMs of discussed-avoidance group 2 (COLLAB / PUSH / MIXED / NEUTRAL, + ref).

The paper's labels were produced by Claude Sonnet 5 in one pass over all 306 messages (two batches,
the same prompt), each message read in the context of its whole DM thread. This script sends the same
instructions one run at a time (prompts/discussed_dm_labels.md, the original prompt with the file I/O
replaced by inline JSON). Paper message counts: COLLAB 159, PUSH 78, MIXED 22, NEUTRAL 47.

    python -m trilemma.analysis.discussed_funnel --runs <dir> --dump-dms outputs/discussed_dms.json
    python -m trilemma.analysis.discussed_dm_labels --dms outputs/discussed_dms.json \
        [--model openrouter:anthropic/claude-sonnet-5] --out outputs/discussed_dm_labels.jsonl
    python -m trilemma.analysis.discussed_funnel --runs <dir> --labels outputs/discussed_dm_labels.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from trilemma.analysis.common import llm

PROMPT = Path(__file__).parent / "prompts" / "discussed_dm_labels.md"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dms", required=True, type=Path)
    ap.add_argument("--model", default="openrouter:anthropic/claude-sonnet-5")
    ap.add_argument("--out", default=Path("outputs/discussed_dm_labels.jsonl"), type=Path)
    a = ap.parse_args()
    tmpl = PROMPT.read_text()
    runs = json.loads(a.dms.read_text())
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tally = Counter()
    with a.out.open("w") as f:
        for r in runs:
            v = llm().chat_json(a.model, [{"role": "user", "content": tmpl.replace(
                "{run}", json.dumps(r, ensure_ascii=False, indent=1))}])
            got = {int(x["i"]): x for x in v.get("labels", [])}
            for d in r["dms"]:
                x = got.get(d["i"], {"label": "NEUTRAL", "ref": 0, "missing": True})
                f.write(json.dumps({"run": r["run"], "i": d["i"], "label": x["label"], "ref": int(x.get("ref", 0)),
                                    **({"missing": True} if x.get("missing") else {})}) + "\n")
                tally[x["label"]] += 1
    print(dict(tally), "(paper: COLLAB 159, PUSH 78, MIXED 22, NEUTRAL 47)")


if __name__ == "__main__":
    main()
