"""Read-only helper for the quid-pro-quo reasoning audit (``prompts/qpq_reasoning.md``).

Paper: Table 16 — the reasoning categories behind the offers the cooperation judge found. The audit
was done by Claude agents (Opus 5.5 / Sonnet 5.5) given the codebook in ``prompts/qpq_reasoning.md``
and this tool; the codebook's "cell" names map to settings here: 5e8 = main-high-conf, 5e10 = main.

  python -m trilemma.judges.qpq_tool items <setting> [run_substr]   judge-flagged items with ids
  python -m trilemma.judges.qpq_tool turn <run> <turn>              full posts + COMPLETE per-step reasoning + tool calls
  python -m trilemma.judges.qpq_tool msgs <run> <HH:MM> <HH:MM>     every message between two clocks (New York time)
  python -m trilemma.judges.qpq_tool board <run>                    Priya/Nadia board_assign sequence + final assignments
  python -m trilemma.judges.qpq_tool find <run> <agent> <regex>     turns of <agent> whose reasoning or posts match

<run> may be the full run dir name or a unique substring like 'qwen38flash_s13_'. The tool searches
every setting under outputs/runs/. reasoning_full is complete even where steps_detail says
reasoning_truncated (a false flag).
"""
import json
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from trilemma.common.runs import RUNS, judge_dir

ALIASES = {"5e8": "main-high-conf", "5e10": "main"}


def rows_file(cell):
    return judge_dir("cooperation", ALIASES.get(cell, cell)) / "rows.jsonl"


def run_dir(run):
    hits = sorted(RUNS.glob(f"*/{run}"))
    if not hits:
        sys.exit(f"run {run!r} not found under {RUNS}")
    return hits[0]
TZ = ZoneInfo("America/New_York")


def resolve(sub):
    names = {p.parent.name for p in RUNS.glob("*/*/run.json") if sub in p.parent.name}
    if sub in names:
        return sub
    if len(names) != 1:
        sys.exit(f"run '{sub}' matches {len(names)} runs: {sorted(names)[:5]}")
    return names.pop()


def load(run):
    return json.load(open(run_dir(run) / "run.json"))


def clock(ts):
    return datetime.fromtimestamp(float(ts), TZ).strftime("%H:%M:%S")


def cmd_items(cell, sub=""):
    for line in open(rows_file(cell)):
        r = json.loads(line)
        if r["model"] != "qwen38flash" or sub not in r["run"]:
            continue
        for k, o in enumerate(r.get("offers") or []):
            v = "" if o.get("quote_ok", True) else " (QUOTE NOT VERBATIM - skip)"
            print(f"{r['run']} | t{r['turn']} #{k} | {r['speaker']} -> {o.get('to')} | {o.get('kind')} | "
                  f"{o.get('what')} | \"{o.get('quote')}\"{v}")


def cmd_turn(run, turn):
    run = resolve(run)
    R = load(run)
    t = next(x for x in R["turns"] if x["i"] == int(turn))
    print(f"### {run} turn {t['i']} agent={t['agent']} clock={t['clock']}..{t.get('clock_end')}")
    print("\n--- WAKE / message_in ---\n", json.dumps(t.get("message_in"), ensure_ascii=False)[:3000])
    for sd in t.get("steps_detail") or []:
        rs = sd.get("reasoning") or ""
        tx = sd.get("text") or ""
        if rs.strip() or tx.strip():
            print(f"\n--- step {sd['step']} REASONING ({len(rs)} chars) ---\n{rs}")
            if tx.strip():
                print(f"\n--- step {sd['step']} TEXT ---\n{tx}")
    print("\n--- TOOL CALLS ---")
    for c in t.get("tool_calls") or []:
        print(f"[step {c.get('step')}] {c.get('name') or c.get('tool')}: "
              f"{json.dumps(c.get('args') or c.get('arguments') or c.get('input'), ensure_ascii=False)[:8000]}")
    print("\n--- text_to_principal ---\n", t.get("text_to_principal"))


def cmd_msgs(run, a, b):
    run = resolve(run)
    R = load(run)
    for m in sorted(R["messages"], key=lambda x: float(x["ts"])):
        c = clock(m["ts"])
        if a <= c[:5] <= b:
            print(f"[{c}] {m['user']} @ {m.get('label')}: {m['text']}\n")


def cmd_board(run):
    run = resolve(run)
    for line in open(run_dir(run) / "world_calls.jsonl"):
        r = json.loads(line)
        if r["tool"] in ("board_assign", "board_unassign") and r["agent"] in ("Priya", "Nadia"):
            print(r["clock"][11:19], r["agent"], r["tool"], r["args"], "ok" if r["result"].get("ok", True) else "FAIL")
    print("final:", load(run).get("assignments"))


def cmd_find(run, agent, rx):
    run = resolve(run)
    pat = re.compile(rx, re.I)
    for t in load(run)["turns"]:
        if t["agent"] != agent:
            continue
        rf = "\n".join(x or "" for x in (t.get("reasoning_full") or []))
        posts = json.dumps(t.get("tool_calls") or [], ensure_ascii=False)
        hr, hp = len(pat.findall(rf)), len(pat.findall(posts))
        if hr or hp:
            print(f"turn {t['i']} {t['clock'][11:16]}: {hr} hits in reasoning, {hp} in tool calls/posts")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    cmd, *a = sys.argv[1:]
    {"items": cmd_items, "turn": cmd_turn, "msgs": cmd_msgs, "board": cmd_board, "find": cmd_find}[cmd](*a)
