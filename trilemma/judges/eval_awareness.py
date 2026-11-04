"""Eval-awareness judge, stage 1: does a seat's reasoning step outside the fiction at all?

Paper: Section "eval-awareness judge" and Tables 25-26 (judge: openai/gpt-5.6-sol via OpenRouter,
pinned openai/flex,openai), over every seat of the 70 open-weight main runs. Stages 2 and 3 are
``eval_awareness_meta`` (inventory) and ``eval_awareness_fict`` (in-world check).

    python -m trilemma.judges.eval_awareness --setting main

Chain-of-thought only (each turn's per-step reasoning), split per seat into chunks of at most
``--chunk-chars`` at step boundaries; each chunk is judged on its own, and the strongest verdict
(none < ambiguous < aware) is the seat's. Quoted spans are checked verbatim.

Output: ``outputs/judges/eval_awareness/<setting>/<run>/run.cot_evalaware_ev1_<judge>[_rN].json``.
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from concurrent.futures import ThreadPoolExecutor

from trilemma.judges import eval_awareness_core as ej
from trilemma.common.llm import make_caller
from trilemma.common.llm import load_prompt_template
from trilemma.common.runs import RUNS, model_seed

logger = logging.getLogger("trilemma.judges.eval_awareness")

DEFAULT_JUDGE = "openrouter:openai/gpt-5.6-sol"
DEFAULT_PIN = "openai/flex,openai"

# Run-dir names of the form ``agent5_<fixture>_<arm>_conc_<model>[_s<seed>]_<ts>[_<tag>]``. The fixture is the
# first token (v16c, w1P0N0, w1augPstrongNstrong, w2PsuperstrongNsuperstrong); the arm
# is everything up to the ``_conc_`` anchor, which on w1/w2 spans several blocks
# (affBoth.../mBusy11Eve/kick1h/hzRafaelStrong3...). A trailing tag such as
# ``_INVALID`` is kept out of the identity.
_DIR_RE = re.compile(
    r"^agent5_(?P<fixture>[A-Za-z0-9]+)(?:_(?P<arm>.+?))?"
    r"_conc_(?P<model>[A-Za-z0-9]+)(?:_s(?P<seed>\d+))?"
    r"_\d{8}-\d{6}(?:_(?P<tag>[A-Za-z0-9]+))?$")


def reasoning_rows(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """{agent, step, reasoning} rows from a run record (each turn's per-step reasoning)."""
    rows: List[Dict[str, Any]] = []
    for i, t in enumerate(run.get("turns") or []):
        pieces = t.get("reasoning_full") or t.get("reasoning") or []
        if isinstance(pieces, str):
            pieces = [pieces]
        for j, txt in enumerate(pieces):
            if str(txt or "").strip():
                rows.append({"agent": str(t.get("agent") or ""),
                             "step": f"{i}.{j}", "reasoning": str(txt)})
    return rows


def identity(path: Path, run: Dict[str, Any]) -> Dict[str, Any]:
    cfg = run.get("config") or {}
    m = _DIR_RE.match(path.parent.name)
    return {
        "run": path.parent.name,
        "path": str(path),
        "fixture_tag": (m.group("fixture") if m else ""),
        "arm": (m.group("arm") or "" if m else ""),
        "model_short": (m.group("model") if m else model_seed(path.parent.name)[0]),
        "model": str(cfg.get("model") or ""),
        "seed": cfg.get("seed"),
        "outcome": str(run.get("outcome") or ""),
    }


def judge_run(run_path: Path, *, caller, template, model, replicate, workers,
              force, chunk_chars, fallback=None, slug=None) -> Dict[str, Any]:
    """``slug`` names the sidecar when it should be shorter than the model id
    (``--sidecar-judge gpt-5.6-terra`` -> ``run.cot_evalaware_ev1_gpt56terra.json``)."""
    run = json.loads(run_path.read_text(encoding="utf-8"))
    units = ej.chunks({"reasoning": reasoning_rows(run)}, chunk_chars=chunk_chars)
    out_path = ej.sidecar_path(run_path, slug or model, replicate)
    existing: Dict[Any, Dict[str, Any]] = {}
    if out_path.exists() and not force:
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            for row in prev.get("chunks") or []:
                if row.get("verdict") is not None:
                    existing[(row.get("agent"), row.get("chunk"))] = row
        except Exception:
            pass
    todo = [u for u in units if (u["agent"], u["chunk"]) not in existing]
    rows = list(existing.values())
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for fut in [pool.submit(ej.judge_chunk, caller, template, u,
                                    fallback=fallback) for u in todo]:
                rows.append(fut.result())
    rows.sort(key=lambda r: (r.get("agent") or "", r.get("chunk") or 0))
    record = {
        **identity(run_path, run),
        "judge": "eval-awareness",
        "judge_version": ej.JUDGE_VERSION,
        "judge_model": model,
        "replicate": replicate,
        "n_chunks": len(units),
        "n_judged_now": len(todo),
        "n_errors": sum(1 for r in rows if r.get("error") or r.get("parse_error")),
        "n_retried": sum(1 for r in rows if (r.get("_meta") or {}).get("retried")),
        **ej._collapse(rows),
        "chunks": rows,
    }
    out_path.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return record


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--setting", default=None, help="judge every run in outputs/runs/<setting>")
    ap.add_argument("--runs", nargs="+", default=None, help="run.json globs")
    ap.add_argument("--api-judge", default=DEFAULT_JUDGE, help="provider:model")
    ap.add_argument("--pin-provider", default=DEFAULT_PIN,
                    help="OpenRouter backend(s), comma-separated and ordered, e.g. "
                         "'openai/flex,openai'; 'none' routes freely")
    ap.add_argument("--sidecar-judge", default="gpt-5.6-sol",
                    help="short name for the sidecar file (default: the model id)")
    ap.add_argument("--max-tokens", type=int, default=6000,
                    help="a reasoning model's CoT counts against this, so a thinking judge "
                         "that hits the cap returns 200 OK with EMPTY content")
    ap.add_argument("--timeout", type=int, default=0, help="seconds per API call (0 = client default)")
    ap.add_argument("--chunk-chars", type=int, default=60000)
    ap.add_argument("--workers", type=int, default=4, help="chunks judged in parallel per run")
    ap.add_argument("--run-workers", type=int, default=1, help="runs judged in parallel")
    ap.add_argument("--replicate", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not args.runs:
        if not args.setting:
            ap.error("give --setting or --runs")
        args.runs = [str(RUNS / args.setting / "*" / "run.json")]
    paths = sorted({Path(p) for pat in args.runs for p in globmod.glob(pat)})
    paths = [p for p in paths if p.name == "run.json"]
    template = load_prompt_template(ej._PROMPT)

    if args.dry_run:
        total = 0
        for path in paths:
            us = ej.chunks({"reasoning": reasoning_rows(
                json.loads(path.read_text()))}, chunk_chars=args.chunk_chars)
            print(f"{path.parent.name}: {len(us)} chunks, "
                  f"{sum(len(u['trace']) for u in us)} chars")
            total += len(us)
        print(f"=== {len(paths)} runs, {total} chunks ===")
        return 0

    # make_caller handles the ordered multi-backend pin and leaves temperature off for
    # OpenAI's gpt-5 line, which rejects it.
    model = args.api_judge.partition(":")[2]
    caller = make_caller(args.api_judge, max_tokens=args.max_tokens, pin=args.pin_provider)
    fallback = make_caller(args.api_judge, max_tokens=args.max_tokens * 2, pin=args.pin_provider)
    if args.timeout:
        for c in (caller, fallback):
            if hasattr(c, "timeout"):
                c.timeout = args.timeout
    logger.info("%d run(s); judge %s; pin %s; sidecar %s", len(paths), args.api_judge,
                getattr(caller, "provider_routing", None) or args.pin_provider or "default",
                ej.sidecar_path(paths[0], args.sidecar_judge or model, args.replicate).name
                if paths else "-")

    def _do(path: Path) -> Optional[Dict[str, Any]]:
        try:
            return judge_run(path, caller=caller, template=template, model=model,
                             replicate=args.replicate, workers=args.workers,
                             force=args.force, chunk_chars=args.chunk_chars,
                             fallback=fallback, slug=args.sidecar_judge or None)
        except Exception as exc:  # one dead run must not kill the sweep
            logger.error("FAILED %s: %s", path.parent.name, exc)
            return None

    if args.run_workers > 1 and len(paths) > 1:
        with ThreadPoolExecutor(max_workers=args.run_workers) as pool:
            recs = list(pool.map(_do, paths))
    else:
        recs = [_do(p) for p in paths]
    done = 0
    for path, rec in zip(paths, recs):
        if rec is None:
            continue
        done += 1
        logger.info("%s  chunks=%d new=%d errors=%d  run=%s aware=%d ambiguous=%d",
                    path.parent.name, rec["n_chunks"], rec["n_judged_now"],
                    rec["n_errors"], rec["run_verdict"], rec["n_agents_aware"],
                    rec["n_agents_ambiguous"])
    logger.info("%d/%d runs judged; usage=%s", done, len(paths),
                dict(getattr(caller, "totals", {}) or {}))
    return 0 if done == len(paths) else 1


if __name__ == "__main__":
    raise SystemExit(main())
