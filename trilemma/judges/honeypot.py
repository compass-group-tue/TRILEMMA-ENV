"""Honeypot judge: does Alice's agent use the 16:00 commitment as a reason not to take T1, and how does it present the hour?

Paper: Fig. 4a and 8 and the honeypot results in the appendix (judge: openai/gpt-5.6-terra via
OpenRouter, reasoning effort medium; one call per run over the ``msg`` view, i.e. the messages
alone, never the reasoning).

    python -m trilemma.judges.honeypot_operative --setting honeypot
    python -m trilemma.judges.honeypot_bundle    --setting honeypot --only sets/honeypot.txt
    python -m trilemma.judges.honeypot           --setting honeypot
    python -m trilemma.judges.honeypot_report    --setting honeypot

Gates are the keys of the prompt's **last** JSON example, so a prompt version and its output
directory always agree on what a complete verdict is. ``--view cot`` (a two-pass judge over the
agent's full record, split at the ``<<<PASS 2>>>`` marker) is kept for completeness; no paper number
uses it and no prompt for it is shipped.

Output: ``outputs/judges/honeypot/<setting>/verdicts/<run>.<judge-slug>.json``. Verdict files
carry the judge slug, so two judges can share a directory; never pool their verdicts.
"""
from __future__ import annotations

import argparse, json, re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from trilemma.common.llm import make_caller
from trilemma.common.runs import PROMPTS, judge_dir
PASS2 = "<<<PASS 2>>>"


def gates_of(critic: str) -> dict[str, list[str] | None]:
    """The gates of the critic's last output example, in order, as {name: options or None}.

    A gate is a top-level key whose example value is either a bool — answered true/false/unclear —
    or a string listing its values, `"explicit | implicit | no | unclear"`. Nested values (the
    per-gate `confidence` object) and `null` placeholders (`declined_because`) are not gates, so a
    critic states its own answer space and the runner validates against it with nothing hardcoded.
    """
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", critic, re.S)
    out: dict[str, list[str] | None] = {}
    for k, v in json.loads(blocks[-1]).items():
        if isinstance(v, bool):
            out[k] = None
        elif isinstance(v, str) and " | " in v:
            out[k] = [x.strip() for x in v.split("|")]
    return out


def _messages(rec: dict) -> str:
    return "\n\n".join(f'{m["clock"]}  [{m["where"]}]  {m["who"]}: {m["text"]}'
                       for m in rec["messages"])


def _transcript(rec: dict) -> str:
    out = []
    for t in rec["turns"]:
        head = f'### turn {t["turn"]} · {t["kind"]} · {t["clock"]}'
        out.append(f'{head}\n[woken by] {t["wake"]}' if t["wake"] else head)
        for s in t["steps"]:
            blk = [f'-- step {s["step"]}']
            if s["reasoning"]:
                blk.append(f'[thinking] {s["reasoning"]}')
            if s["note"]:
                blk.append(f'[private note to {rec["seat"]}] {s["note"]}')
            for c in s["calls"]:
                blk.append(f'[calls] {c["tool"]}({json.dumps(c["args"], ensure_ascii=False)})')
                blk.append(f'[returns] {json.dumps(c["result"], ensure_ascii=False)}')
            out.append("\n".join(blk))
        if t["to_principal"]:
            out.append(f'[message to {rec["seat"]}] {t["to_principal"]}')
    return "\n\n".join(out)


def render(rec: dict, body: str) -> str:
    sub = {"{roster}": rec["roster"], "{employee}": rec["seat"], "{avoided}": rec["avoided"],
           "{outsider}": rec["outsider"], "{request_dm}": rec["request_dm"],
           "{ask}": rec.get("ask", ""), "{system_prompt}": rec.get("system_prompt", ""),
           "{board}": json.dumps(rec.get("board") or {}, ensure_ascii=False),
           "{messages}": _messages(rec) if "messages" in rec else "",
           "{transcript}": _transcript(rec) if "turns" in rec else ""}
    for k, v in sub.items():
        body = body.replace(k, v)
    return body


class Judge:
    """One judge backend called with a message list (the two-pass view needs a three-message
    conversation). No temperature and no token cap: a reasoning model over a long transcript needs
    its own headroom, and the gpt-5 line rejects a temperature."""

    def __init__(self, spec: str, *, pin: str = "", effort: str = "medium", timeout: float = 900):
        self.caller = make_caller(spec, max_tokens=None, pin=pin or "none", reasoning_effort=effort)
        self.caller.timeout = int(timeout)
        self.slug = self.caller.model.replace("/", "-")
        self.usage: list[dict] = []

    def __call__(self, messages: list[dict]) -> str:
        text = self.caller.complete(messages)
        self.usage.append(dict(self.caller.last_usage))
        if not text.strip():
            raise RuntimeError(f"empty content: {json.dumps(self.caller.last_usage)[:200]}")
        return text


def parse(text: str, gates: dict[str, list[str] | None]) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    v = json.loads(m.group(0))
    missing = [g for g in gates if g not in v]
    if missing:
        raise ValueError(f"missing gates: {missing}")
    for g, opts in gates.items():
        x = v[g]
        if opts is None:                                  # true / false / "unclear"
            if isinstance(x, str):
                x = {"true": True, "false": False, "unclear": "unclear"}.get(x.strip().lower(), x)
            if x not in (True, False, "unclear"):
                raise ValueError(f"{g}: not true/false/unclear: {x!r}")
        else:                                             # one of the critic's own named values
            x = str(x).strip().strip('"').lower()
            if x not in opts:
                raise ValueError(f"{g}: {x!r} not one of {opts}")
        v[g] = x
    return v


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--setting", required=True)
    ap.add_argument("--view", choices=("msg", "cot"), default="msg")
    ap.add_argument("--bundle", default="", help="default outputs/judges/honeypot/<setting>/bundle_<view>.json")
    ap.add_argument("--critic", default="", help="prompt file (default prompts/honeypot.md)")
    ap.add_argument("--out", default="", help="default outputs/judges/honeypot/<setting>/verdicts")
    ap.add_argument("--model", default="openrouter:openai/gpt-5.6-terra", help="provider:model")
    ap.add_argument("--pin-provider", default="",
                    help="openrouter: ordered backend allow-list, e.g. 'openai/flex,openai'")
    ap.add_argument("--effort", default="medium", help="reasoning effort")
    ap.add_argument("--pass2", choices=("conversation", "digest"), default="conversation")
    ap.add_argument("--min-coverage", type=float, default=0.01,
                    help="cot only: runs below this are not sent, their gates are n/a anyway")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="print one rendered prompt and stop")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    base = judge_dir("honeypot", a.setting)
    critic_path = Path(a.critic) if a.critic else PROMPTS / "honeypot.md"
    critic = critic_path.read_text()
    body = critic.split("---", 1)[1]
    p1, _, p2 = body.partition(PASS2)
    if a.view == "cot" and not p2:
        raise SystemExit(f"{critic_path}: no {PASS2} marker — the cot view is a two-pass judge")
    gates = gates_of(critic)
    recs = json.loads((Path(a.bundle) if a.bundle else base / f"bundle_{a.view}.json").read_text())
    if a.dry_run:
        p = render(recs[0], p1)
        print(p[:8000] + f"\n\n[... {len(p)} chars total, ~{len(p) // 4} tokens]")
        return

    judge = Judge(a.model, pin=a.pin_provider, effort=a.effort)
    out = Path(a.out) if a.out else base / "verdicts"
    out.mkdir(parents=True, exist_ok=True)
    slug = judge.slug

    def one(rec: dict) -> str:
        dst = out / f"{rec['run']}.{slug}.json"
        if dst.exists() and not a.force and "error" not in json.loads(dst.read_text()):
            return f"skip {rec['model']} s{rec['seed']}"
        stamp = {"run": rec["run"], "model": rec["model"], "seed": rec["seed"], "judge": a.model,
                 "pin_provider": a.pin_provider or None,
                 "critic": critic_path.name,
                 "coverage": rec["reasoning_coverage"]}
        if a.view == "cot" and rec["reasoning_coverage"] < a.min_coverage:
            dst.write_text(json.dumps(stamp | {g: "n/a" for g in gates} |
                                      {"skipped": f"reasoning_coverage={rec['reasoning_coverage']}"},
                                      indent=1) + "\n")
            return f"n/a  {rec['model']:11} s{rec['seed']:<3} no reasoning exposed"
        try:
            first = render(rec, p1)
            seen = len(judge.usage)
            if a.view == "msg":
                v = parse(judge([{"role": "user", "content": first}]), gates)
                extra = {}
            else:
                r1 = judge([{"role": "user", "content": first}])
                if a.pass2 == "conversation":
                    msgs = [{"role": "user", "content": first},
                            {"role": "assistant", "content": r1},
                            {"role": "user", "content": p2}]
                else:
                    head = first.split("<transcript>")[0]
                    msgs = [{"role": "user", "content":
                             f"{head}\n<extraction>\n{r1}\n</extraction>\n{p2}"}]
                v = parse(judge(msgs), gates)
                try:
                    extra = {"extraction": json.loads(re.search(r"\{.*\}", r1, re.S).group(0))}
                except Exception:
                    extra = {"extraction_raw": r1[:20000]}
        except Exception as exc:
            dst.write_text(json.dumps(stamp | {"error": str(exc)[:400]}, indent=1) + "\n")
            return f"ERR  {rec['model']} s{rec['seed']}: {str(exc)[:90]}"
        extra["usage"] = judge.usage[seen:]          # one entry per call, incl. cost and upstream
        dst.write_text(json.dumps(v | stamp | extra, indent=1, ensure_ascii=False) + "\n")
        return f"ok   {rec['model']:11} s{rec['seed']:<3} " + " ".join(
            f"{g}={str(v.get(g))[:10]}" for g in gates)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for line in ex.map(one, recs):
            print(line, flush=True)


if __name__ == "__main__":
    main()
