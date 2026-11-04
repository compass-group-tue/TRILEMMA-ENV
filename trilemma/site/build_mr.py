"""Motivated-reasoning examples for the landing page (mr7 judge, 3 replicates, `main`).

    python -m trilemma.site.build_mr --runs-root RUNS --outputs-root OUTPUTS --data SITE/data

Needs the run files from build_site.py. Writes <data>/mr.js: window.TRILEMMA_MR = [...], one example per
seat whose majority verdict is motivated / motivated_substitute / acknowledged.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

from .build_site import read_jsonp

REPS = ["", "_r2", "_r3"]


def norm(s):
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", type=Path, required=True)
    ap.add_argument("--outputs-root", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--verdicts", default="mr7_a_5e10_all_sol_verdicts.json")
    ap.add_argument("--judge-tag", default="openrouteropenaigpt61sol", help="suffix of the run.mr_a_<tag>.json sidecars")
    a = ap.parse_args()
    V = json.loads((a.outputs_root / a.verdicts).read_text())
    idx = json.loads((a.data / "index.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    model_name = {m["key"]: m["name"] for m in idx["models"]}
    out = []
    for seatkey, reps in sorted(V.items()):
        model, seed, seat = seatkey.rsplit("_", 2)
        top, n = collections.Counter(reps.values()).most_common(1)[0]
        if n < 2 or top not in ("motivated", "motivated_substitute", "acknowledged"):
            continue
        run = read_jsonp(a.data / "runs" / "main" / f"{model}_{seed}.js")
        pick = None
        for r, rk in zip(REPS, ["r1", "r2", "r3"]):
            if reps.get(rk) != top:
                continue
            x = json.loads((a.runs_root / run["dir"] / f"run.mr_a_{a.judge_tag}{r}.json").read_text())
            for st in x["seats"][seat]["steps"]:
                if st.get("verdict") != top:
                    continue
                for o in st["objections"]:
                    oc = o.get("outcome")
                    if oc == "motivated" and o.get("same_as_contested") == "no":
                        oc = "motivated_substitute"
                    if oc == top:
                        pick = (st, o)
                        break
                if pick:
                    break
            if pick:
                break
        if not pick:
            print("no pick", seatkey)
            continue
        st, o = pick

        def find_turn(q):
            q = norm(q)[:60]
            return next((t for t in run["turns"] if t["agent"] == seat
                         and any(q in norm(s.get("reasoning")) for s in t["steps"])), None)

        def find_msg(q, user=None):
            q = norm(q)[:60]
            return next((m for m in run["msgs"] if (user is None or m["user"] == user) and q in norm(m["text"])), None)

        ot, src = find_turn(o["objection_quote"]), find_msg(o["source_quote"])
        if src is None:  # the source is often a message from before the run: look in the world
            w = read_jsonp(a.data / "worlds" / f"{run['world']}.js")
            q = norm(o["source_quote"])[:60]
            src = next((m for m in w["msgs"] if q in norm(m["text"])), None)
        shipped = find_msg(o["shipped_quote"], seat) if o.get("shipped_quote") else None
        moves, seen = [], set()
        for mv in o.get("moves", []):
            if mv["code"] in ("revise", "evidence") or (mv["code"], mv["quote"]) in seen:
                continue
            seen.add((mv["code"], mv["quote"]))
            moves.append({"code": mv["code"], "family": mv["family"], "q": mv["quote"]})
        if len(moves) > 4:
            moves = moves[:3] + moves[-1:]
        out.append({
            "id": f"{model}_{seed}_{seat}", "run": f"{model}_{seed}", "model": model,
            "model_name": model_name.get(model, model), "seed": seed, "seat": seat,
            "verdict": top, "votes": n, "form": o["form"],
            "contested": o["contested_claim"], "certified": o.get("certified_claim"),
            "source": {"q": o["source_quote"], "time": src and src["time"][:5], "conv": src and src["conv"], "user": src and src["user"]},
            "objection": {"q": o["objection_quote"], "time": ot and ot["time"][:5], "turn": ot and ot["i"]},
            "moves": moves,
            "stance": {"q": o.get("final_stance_quote"), "kind": o.get("final_stance")},
            "shipped": {"q": o.get("shipped_quote"), "text": shipped and shipped["text"], "time": shipped and shipped["time"][:5],
                        "conv": shipped and shipped["conv"], "id": shipped and shipped["id"]},
            "why": o.get("why"),
        })
    (a.data / "mr.js").write_text("window.TRILEMMA_MR = " + json.dumps(out, ensure_ascii=False) + ";\n")
    print(len(out), collections.Counter(e["verdict"] for e in out))
    print("missing objection turn", sum(not e["objection"]["turn"] for e in out),
          "missing shipped msg", sum(not e["shipped"]["id"] for e in out),
          "missing src", sum(not e["source"]["time"] for e in out))


if __name__ == "__main__":
    main()
