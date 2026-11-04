"""Disclosure judge, stage 2: per-ground labels (Bob / work) on the messages stage 1 flagged.

Paper: disclosure markers of Fig. 5 and Tables 13-15 (judge: openai/gpt-5.6-luna via OpenRouter,
free routing, 3 replicates over every message stage 1 flagged direct or indirect).

    python -m trilemma.judges.disclosure_flagged --setting main --replicate 1   # 2, 3
    python -m trilemma.judges.disclosure_seats --setting main                   # seat labels

Which messages. Every stage-1 row with ``verdict`` direct or indirect (``--select flagged``, the
default; ``--select direct`` keeps only direct). The world, material and same-conversation context
are rebuilt with ``disclosure.build_items`` so the judge sees exactly what stage 1 saw.

``{prior}``. Every earlier *post* in the same run (any conversation, any speaker, ordered by Slack
ts) that stage 1 flagged direct/indirect or ``relays-other`` — the material already on the page —
numbered ``P1..Pk`` per run. Debriefs are excluded: they reach only the assistant's own employee.

``--human`` (optional): a markdown file of human labels (``#N`` sections matched by run, speaker
and clock); the summary then reports agreement.

Output: ``outputs/judges/disclosure/<setting>/flagged/r<K>/rows.jsonl`` (+ summary.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from trilemma.judges.disclosure import _json_object, build_items
from trilemma.common.llm import SYS as _SYS, make_caller
from trilemma.common.runs import PROMPTS, RUNS, judge_dir

PROMPT_PATH = PROMPTS / "disclosure_flagged.md"
JUDGE = "openrouter:openai/gpt-5.6-luna"
LABELS = ("no", "mixed", "relayed", "direct-matt", "indirect-matt", "direct-work",
          "indirect-work", "both")
PRIOR_CHARS = 900
KEYS = ("background", "roster", "speaker", "speaker_status", "audience", "outside", "clock",
        "context", "prior", "message", "subjects")


def _ts(row_id: str) -> float:
    return float(row_id.rsplit(":", 1)[1])


def prior_block(run_rows: List[Dict[str, Any]], before: float) -> str:
    """The run's earlier posts that already put material on the page, numbered per run."""
    on_page = sorted((r for r in run_rows if r["kind"] == "post"
                      and (r.get("verdict") in ("direct", "indirect")
                           or r.get("relay") == "relays-other")), key=lambda r: _ts(r["id"]))
    lines = []
    for k, r in enumerate(on_page, 1):
        if _ts(r["id"]) >= before:
            break
        text = r["message"] if len(r["message"]) <= PRIOR_CHARS else r["message"][:PRIOR_CHARS] + " …"
        lines.append(f"[P{k}] {r['speaker']} → {r['audience']} @ {r['clock']}: {text}")
    return "\n\n".join(lines) or "(none)"


def render(item: Dict[str, Any], template: str) -> str:
    out = template.split("\n---\n", 1)[1]
    for key in KEYS:
        val = item[key]
        out = out.replace("{" + key + "}", ", ".join(val) if isinstance(val, list) else str(val))
    return out


def parse(text: str) -> Dict[str, Any]:
    obj = _json_object(text)
    if not isinstance(obj, dict) or "label" not in obj:
        return {"parse_error": "no JSON object with a label", "raw": (text or "")[:400]}
    label = str(obj.get("label") or "").strip().strip("`\"'").lower()
    grounds = obj.get("grounds") if isinstance(obj.get("grounds"), list) else []
    conf = str(obj.get("confidence") or "").strip().lower()
    return {"label": label if label in LABELS else f"?{label}", "grounds": grounds,
            "relay_of": str(obj.get("relay_of") or ""), "reason": str(obj.get("reason") or ""),
            "confidence": conf if conf in ("high", "medium", "low") else ""}


def normalise_human(label: str) -> Optional[str]:
    """A hand label → the stage-2 label set (None = left out of agreement)."""
    s = label.strip().lower()
    m = re.match(r"(direct|indirect) & about (matt|work)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    if s.startswith(("relayed", "relays")):
        return "relayed"
    if "does not establish" in s:  # #46 "weird — quoted part alone does not establish this"
        return "no"
    if s.startswith("weird"):
        return "mixed"
    if s.startswith("no") or "dont understand" in s or "don't understand" in s:
        return "no"
    return None


def read_human(path: Path) -> List[Dict[str, Any]]:
    """``#N`` sections of a direct-disclosures.md: run, speaker, audience, clock, label."""
    out, run = [], None
    for block in re.split(r"\n(?=## |<a id=)", path.read_text()):
        if block.startswith("## "):
            run = block[3:].splitlines()[0].strip()
            continue
        m = re.search(r"### #(\d+) — (\S+) → (.+?) @ (\w{3} \d\d:\d\d) \((\w+)\)", block)
        lab = re.search(r"\*\*human label:\*\* ?(.*)", block)
        if m and run:
            out.append({"n": int(m.group(1)), "run": run, "speaker": m.group(2),
                        "audience": m.group(3), "clock": m.group(4), "kind": m.group(5),
                        "human": (lab.group(1).strip() if lab else "")})
    return out


def summarize(rows: List[Dict[str, Any]]) -> str:
    out = ["# disclosure judge v5 — summary", "",
           f"Judge `{rows[0]['judge']}`, critic `{rows[0]['prompt']}`. {len(rows)} messages, "
           f"{sum(1 for r in rows if r.get('error'))} errors.", "",
           "| label | n |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in sorted(Counter(r.get("label", "error") for r in rows).items())]
    scored = [r for r in rows if r.get("human_norm")]
    if scored:
        agree = sum(1 for r in scored if r.get("label") == r["human_norm"])
        out += ["", f"## Agreement with the human labels: {agree}/{len(scored)}", "",
                "Rows: human label, columns: v5 label.", ""]
        cols = sorted({r.get("label", "error") for r in scored})
        out += ["| human \\ v5 | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
        for h in sorted({r["human_norm"] for r in scored}):
            c = Counter(r.get("label", "error") for r in scored if r["human_norm"] == h)
            out.append(f"| {h} | " + " | ".join(str(c.get(x, "")) for x in cols) + " |")
        out += ["", "## Disagreements", ""]
        for r in sorted(scored, key=lambda r: r["n"]):
            if r.get("label") == r["human_norm"]:
                continue
            spans = "; ".join(f"{g.get('ground')}/{g.get('strength')}"
                              f"{'' if g.get('new', True) else ' (repeat ' + str(g.get('repeats')) + ')'}"
                              f": “{g.get('span', '')}”" for g in r.get("grounds") or [])
            out += [f"- **#{r['n']}** {r['speaker']} @ {r['clock']} — human `{r['human']}` · "
                    f"v5 `{r.get('label')}` ({r.get('confidence')})  ",
                    f"  {spans or '(no grounds)'}  ", f"  {r.get('reason', '')}", ""]
    left_out = [r for r in rows if r.get("human") and not r.get("human_norm")]
    if left_out:
        out += ["", "Left out of agreement (label does not normalise): "
                + ", ".join(f"#{r['n']} `{r['human']}` → v5 `{r.get('label')}`" for r in left_out)]
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--setting", default=None, help="setting name; default paths follow from it")
    ap.add_argument("--replicate", type=int, default=1, help="replicate number (output r<K>/)")
    ap.add_argument("--v4-rows", default=None, type=Path,
                    help="stage-1 rows (default outputs/judges/disclosure/<setting>/screen/rows.jsonl)")
    ap.add_argument("--runs-dir", default=None, type=Path, help="default outputs/runs/<setting>")
    ap.add_argument("--human", type=Path, default=None)
    ap.add_argument("--adjudicated", type=Path, default=None,
                    help="JSON {\"#N\": v5 label} overriding the normalised human label")
    ap.add_argument("--out", default=None, type=Path,
                    help="default outputs/judges/disclosure/<setting>/flagged/r<replicate>")
    ap.add_argument("--select", choices=("direct", "flagged"), default="flagged")
    ap.add_argument("--judge", default=JUDGE)
    ap.add_argument("--prompt", type=Path, default=PROMPT_PATH)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--pin-provider", default="none")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="build items, print one prompt, stop")
    args = ap.parse_args(argv)
    if not (args.v4_rows and args.out and args.runs_dir):
        if not args.setting:
            ap.error("give --setting, or --v4-rows, --runs-dir and --out")
        base = judge_dir("disclosure", args.setting)
        args.v4_rows = args.v4_rows or base / "screen" / "rows.jsonl"
        args.out = args.out or base / "flagged" / f"r{args.replicate}"
        args.runs_dir = args.runs_dir or RUNS / args.setting

    v4 = [json.loads(l) for l in args.v4_rows.read_text().splitlines() if l.strip()]
    by_run: Dict[str, List[Dict[str, Any]]] = {}
    for r in v4:
        by_run.setdefault(r["run"], []).append(r)
    want = ("direct",) if args.select == "direct" else ("direct", "indirect")
    targets = sorted((r for r in v4 if r.get("verdict") in want),
                     key=lambda r: (r["run"], r["clock"], _ts(r["id"]) if ":" in r["id"] else 0))

    adjudicated: Dict[str, str] = {}
    if args.adjudicated:
        adjudicated = {k: v for k, v in json.loads(args.adjudicated.read_text()).items()
                       if not k.startswith("_")}
        bad = {k: v for k, v in adjudicated.items() if v not in LABELS}
        if bad:
            print(f"FATAL: adjudicated labels outside {LABELS}: {bad}", file=sys.stderr)
            return 1
    human = {}
    if args.human:
        for h in read_human(args.human):
            human.setdefault((h["run"], h["speaker"], h["clock"]), []).append(h)

    items: List[Dict[str, Any]] = []
    built: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in targets:
        if r["run"] not in built:
            built[r["run"]] = {i["id"]: i for i in build_items(args.runs_dir / r["run"]) if "id" in i}
        it = built[r["run"]].get(r["id"])
        if it is None:
            print(f"MISSING {r['run']} {r['id']} — run dir or message gone", file=sys.stderr)
            return 1
        it = dict(it, prior=prior_block(by_run[r["run"]], _ts(r["id"])) if r["kind"] == "post" else "(none)",
                  v4_verdict=r["verdict"], v4_span=r.get("span", ""))
        hs = human.get((r["run"], r["speaker"], r["clock"])) or []
        if hs:
            h = hs.pop(0)
            it.update(n=h["n"], human=h["human"], human_norm=normalise_human(h["human"]))
            if str(h["n"]) in adjudicated:
                it.update(human_orig_norm=it["human_norm"], human_norm=adjudicated[str(h["n"])],
                          human=f"{h['human']} [adjudicated: {adjudicated[str(h['n'])]}]")
        items.append(it)
    if args.human:
        unmatched = [h for hs in human.values() for h in hs]
        print(f"human labels matched: {sum(1 for i in items if 'n' in i)}/{len(items)}; "
              f"unmatched sections: {[h['n'] for h in unmatched]}")
        if unmatched or any("n" not in i for i in items):
            print("FATAL: human labels and v4 rows do not line up one-to-one", file=sys.stderr)
            return 1
    print(f"{len(items)} messages from {len({i['run'] for i in items})} runs "
          f"({args.select}); judge {args.judge}; critic {args.prompt.name}")

    template = args.prompt.read_text()
    if args.dry_run:
        i = max(items, key=lambda i: i["prior"].count("[P"))
        print(f"\n--- example prompt: #{i.get('n')} {i['run']} {i['speaker']} @ {i['clock']} "
              f"({i['prior'].count('[P')} prior items) ---\n")
        print(render(i, template))
        left = [k for k in KEYS if "{" + k + "}" in render(i, template)]
        print(f"\nunfilled placeholders: {left or 'none'}")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    partial = args.out / "rows.partial.jsonl"
    rows: List[Dict[str, Any]] = []
    done = set()
    if args.resume and partial.exists():
        for line in partial.read_text().splitlines():
            r = json.loads(line)
            if not r.get("error"):
                rows.append(r)
                done.add((r["run"], r["id"]))
    caller = make_caller(args.judge, max_tokens=args.max_tokens, pin=args.pin_provider)
    lock = threading.Lock()

    def work(item: Dict[str, Any]) -> None:
        row = {k: v for k, v in item.items() if k not in ("background", "context", "secrets", "prior")}
        row.update(judge=args.judge, prompt=args.prompt.name)
        try:
            prompt = render(item, template)
            res = parse(caller(_SYS, prompt))
            if "parse_error" in res:
                res = parse(caller(_SYS, prompt + "\n\nReply with ONLY the JSON object, nothing else."))
            row.update(res)
            row["usage"] = dict(getattr(caller, "last_usage", {}) or {})
        except Exception as exc:  # noqa: BLE001 - recorded per row, re-judged with --resume
            row["error"] = f"{type(exc).__name__}: {exc}"[:500]
        with lock:
            rows.append(row)
            with partial.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"  {len(rows)}/{len(items)} #{row.get('n', '')} {row.get('label', row.get('error'))}",
                  flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, [i for i in items if (i["run"], i["id"]) not in done]))
    rows.sort(key=lambda r: (r.get("n") or 0, r["run"], r["clock"]))
    (args.out / "rows.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    (args.out / "summary.md").write_text(summarize(rows))
    print(f"wrote {args.out}/rows.jsonl and summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
