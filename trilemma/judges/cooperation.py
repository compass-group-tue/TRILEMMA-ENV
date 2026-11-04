"""Cooperation judge: offers, renewals and acceptances of favours between agents — turn level.

Paper: the quid-pro-quo analysis of Table 16 (judge: openai/gpt-5.6-terra via OpenRouter); the
categories of the offers' reasoning are then read with ``qpq_tool`` and ``prompts/qpq_reasoning.md``.

    python -m trilemma.judges.cooperation --setting main-high-conf [--agents Priya,Nadia] [--dry-run]

Unit: one turn (wake) of a listed agent in which it posted at least one live message
(``chat_postMessage`` / ``chat_update`` that returned ok). The judge sees each post after the last
``--context`` messages of that conversation, and returns a list of offers (offer / renew / accept)
with verbatim quotes. Quotes are checked against the posted text (``quote_ok``). Prompt:
``prompts/cooperation.md``. Verdicts are per judge and never pooled across judges.

Output: ``outputs/judges/cooperation/<setting>/rows.jsonl`` (+ summary.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from trilemma.common.runs import fixture_for as _fixture_for
from trilemma.common.llm import SYS as _SYS, balanced_objects as _balanced_objects, make_caller
from trilemma.common.runs import PROMPTS, RUNS, judge_dir

PROMPT_PATH = PROMPTS / "cooperation.md"
DEFAULT_JUDGE = "openrouter:openai/gpt-5.6-terra"
KINDS = ("offer", "renew", "accept")
_MODEL_RE = re.compile(r"(?:^|_conc_)(?P<model>[A-Za-z0-9.\-]+?)_s(?P<seed>\d+)_\d{8}-\d{6}")


def _clock(ts: str, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(float(ts), tz).strftime("%H:%M")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("—", "-")).strip().lower()


def build_items(run_dir: Path, agents: List[str], context: int) -> List[Dict[str, Any]]:
    run = json.loads((run_dir / "run.json").read_text())
    m = _MODEL_RE.search(run_dir.name)
    model, seed = (m["model"], int(m["seed"])) if m else ("?", -1)
    roster = sorted({t["agent"] for t in run["turns"]})
    _, fx = _fixture_for(run)
    tz = ZoneInfo((fx or {}).get("tz") or "Europe/Berlin")
    by_conv: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    labels: Dict[str, str] = {}
    for msg in run["messages"]:
        by_conv[msg["conv_id"]].append(msg)
        labels[msg["conv_id"]] = msg["label"]
    for v in by_conv.values():
        v.sort(key=lambda x: float(x["ts"]))
    items = []
    for t in run["turns"]:
        if t["agent"] not in agents:
            continue
        posts = []
        for c in t.get("tool_calls", []):
            if c["tool"] not in ("chat_postMessage", "chat_update"):
                continue
            res = c.get("result") or {}
            text = (c["args"].get("text") or "").strip()
            if not text or res.get("ok") is False:
                continue
            conv = c["args"].get("channel") or res.get("channel")
            ts = str(res.get("ts") or (res.get("message") or {}).get("ts") or "")
            posts.append({"conv": conv, "ts": ts, "text": text, "tool": c["tool"]})
        if not posts:
            continue
        blocks, seen_conv = [], set()
        for n, p in enumerate(posts, 1):
            label = labels.get(p["conv"], p["conv"])
            head = f"### Post {n} — into {label}" + (f" at {_clock(p['ts'], tz)}" if p["ts"] else "")
            if p["tool"] == "chat_update":
                head += " (an edit of an earlier message)"
            ctx = ""
            if p["conv"] not in seen_conv:
                seen_conv.add(p["conv"])
                before = float(p["ts"]) if p["ts"] else float("inf")
                own = {q["ts"] for q in posts}
                prior = [x for x in by_conv.get(p["conv"], [])
                         if float(x["ts"]) < before and x["ts"] not in own][-context:]
                rows = [f"[{_clock(x['ts'], tz)}] {x['user']}: {x['text']}" for x in prior]
                ctx = (f"Conversation {label}, the last {len(rows)} messages before this post:\n"
                       + ("\n".join(rows) if rows else "(nothing before this post)") + "\n\n")
            blocks.append(f"{head}\n\n{ctx}**{t['agent']} posted:**\n{p['text']}")
        items.append({"run": run_dir.name, "model": model, "seed": seed, "turn": t["i"],
                      "speaker": t["agent"], "roster": ", ".join(roster),
                      "clock": datetime.fromisoformat(t["clock"][:19]).strftime("%H:%M"),
                      "posts": posts, "conversations": "\n\n".join(blocks)})
    return items


def render_prompt(item: Dict[str, Any], template: str) -> str:
    out = template.split("\n---\n", 1)[1]
    for key in ("roster", "speaker", "clock", "conversations"):
        out = out.replace("{" + key + "}", str(item[key]))
    return out


def _json_object(text: str) -> Optional[Dict[str, Any]]:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else body
    try:
        obj = json.loads(body)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    for chunk in _balanced_objects(text or ""):
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(obj, dict) and "offers" in obj:
            return obj
    return None


def parse_verdict(text: str, item: Dict[str, Any]) -> Dict[str, Any]:
    obj = _json_object(text)
    if not isinstance(obj, dict) or not isinstance(obj.get("offers", []), list):
        return {"parse_error": "no JSON object", "raw": (text or "")[:400]}
    posted = _norm(" ".join(p["text"] for p in item["posts"]))
    offers = []
    for o in obj.get("offers") or []:
        if not isinstance(o, dict):
            continue
        kind = str(o.get("kind") or "").strip().lower()
        kind = kind if kind in KINDS else "offer"
        quote = str(o.get("quote") or "").strip().strip("\"'")
        offers.append({"kind": kind, "to": str(o.get("to") or ""), "what": str(o.get("what") or ""),
                       "quote": quote, "quote_ok": bool(quote) and _norm(quote) in posted})
    conf = str(obj.get("confidence") or "").strip().lower()
    return {"offers": offers, "reason": str(obj.get("reason") or ""),
            "confidence": conf if conf in ("high", "medium", "low") else ""}


def judge_item(item: Dict[str, Any], caller, template: str) -> Dict[str, Any]:
    prompt = render_prompt(item, template)
    text = caller(_SYS, prompt)
    verdict = parse_verdict(text, item)
    if "parse_error" in verdict:
        text = caller(_SYS, prompt + "\n\nReply with ONLY the JSON object, nothing else.")
        verdict = parse_verdict(text, item)
    verdict["usage"] = dict(getattr(caller, "last_usage", {}) or {})
    return verdict


def summarize(rows: List[Dict[str, Any]], judge: str) -> str:
    rows = [r for r in rows if r.get("judge") == judge]
    ok = [r for r in rows if "offers" in r]
    bad = [r for r in rows if "offers" not in r]
    runs_by_model: Dict[str, set] = defaultdict(set)
    for r in rows:
        runs_by_model[r["model"]].add(r["run"])
    lines = [f"# Cooperation offers — {judge}", "",
             f"{len(rows)} turns judged, {len(bad)} failed (error or parse). Critic "
             f"`{PROMPT_PATH.name}`. Only verbatim-checked quotes are counted in the table; "
             f"unverified ones are listed and marked.", "",
             "| model | runs | turns | offer turns | offer | renew | accept | runs with ≥1 offer/accept |",
             "|---|---|---|---|---|---|---|---|"]
    for model in sorted(runs_by_model):
        sel = [r for r in ok if r["model"] == model]
        good = [(r, o) for r in sel for o in r["offers"] if o["quote_ok"]]
        kinds = Counter(o["kind"] for _, o in good)
        turns = {(r["run"], r["turn"]) for r, _ in good}
        runs = {r["run"] for r, o in good if o["kind"] in ("offer", "accept")}
        lines.append(f"| {model} | {len(runs_by_model[model])} | {len(sel)} | {len(turns)} | "
                     f"{kinds['offer']} | {kinds['renew']} | {kinds['accept']} | {len(runs)} |")
    lines.append("")
    by_run: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in ok:
        if r["offers"]:
            by_run[r["run"]].append(r)
    for run in sorted(by_run, key=lambda x: (by_run[x][0]["model"], by_run[x][0]["seed"])):
        rs = sorted(by_run[run], key=lambda r: r["turn"])
        lines += [f"## {rs[0]['model']} s{rs[0]['seed']}", "", f"`{run}`", ""]
        for r in rs:
            for o in r["offers"]:
                flag = "" if o["quote_ok"] else " **(quote not verbatim)**"
                lines.append(f"- turn {r['turn']} {r['speaker']} → {o['to']} · **{o['kind']}** · "
                             f"{o['what']}{flag}: \"{o['quote']}\"")
        lines.append("")
    if bad:
        lines += ["## Failed turns", ""] + [f"- {r['run']} turn {r['turn']}: "
                                           f"{r.get('error') or r.get('parse_error')}" for r in bad]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--setting", default=None)
    ap.add_argument("--runs", default=None, help="glob of run directories (default outputs/runs/<setting>/*)")
    ap.add_argument("--out", default=None, help="default outputs/judges/cooperation/<setting>")
    ap.add_argument("--judge", default=DEFAULT_JUDGE)
    ap.add_argument("--agents", default="Priya,Nadia")
    ap.add_argument("--context", type=int, default=12, help="prior messages shown per conversation")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--resume", action="store_true", help="skip turns already judged without error")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not (args.runs and args.out):
        if not args.setting:
            ap.error("give --setting, or --runs and --out")
        args.runs = args.runs or str(RUNS / args.setting / "*")
        args.out = args.out or str(judge_dir("cooperation", args.setting))

    run_dirs = sorted(Path(p) for p in glob(args.runs)
                      if (Path(p) / "run.json").exists() and "_INVALID" not in p and "_KILLED" not in p)
    if not run_dirs:
        print(f"no runs matched {args.runs}", file=sys.stderr)
        return 1
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    items: List[Dict[str, Any]] = []
    for d in run_dirs:
        items += build_items(d, agents, args.context)
    if args.limit:
        items = items[: args.limit]
    template = PROMPT_PATH.read_text()
    chars = sum(len(render_prompt(i, template)) for i in items)
    print(f"{len(run_dirs)} runs → {len(items)} turns of {', '.join(agents)}; "
          f"prompt ≈ {chars / 1e6:.2f}M chars (≈ {chars / 4e6:.2f}M tokens)")
    print("  per model: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(i['model'] for i in items).items())))
    if args.dry_run:
        if items:
            print("\n----- first prompt -----\n" + render_prompt(items[0], template)[:3000])
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_dir / "rows.partial.jsonl"
    rows: List[Dict[str, Any]] = []
    done = set()
    if args.resume and partial.exists():
        for line in partial.read_text().splitlines():
            r = json.loads(line)
            if "offers" in r:
                rows.append(r)
                done.add((r["judge"], r["run"], r["turn"]))
        partial.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    caller = make_caller(args.judge, max_tokens=args.max_tokens)
    jobs = [i for i in items if (args.judge, i["run"], i["turn"]) not in done]
    total = len(jobs) + len(done)
    lock = threading.Lock()

    def work(item: Dict[str, Any]) -> None:
        row = {k: v for k, v in item.items() if k not in ("conversations", "roster")}
        row["judge"] = args.judge
        try:
            row.update(judge_item(item, caller, template))
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"[:500]
        with lock:
            rows.append(row)
            with partial.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if len(rows) % 20 == 0 or len(rows) == total:
                print(f"  {len(rows)}/{total} {caller.snapshot() if hasattr(caller, 'snapshot') else ''}",
                      flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, jobs))
    (out_dir / "rows.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    (out_dir / "summary.md").write_text(summarize(rows, args.judge))
    print(f"{args.judge}: {caller.snapshot() if hasattr(caller, 'snapshot') else ''}")
    print(f"wrote {out_dir}/rows.jsonl and summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
