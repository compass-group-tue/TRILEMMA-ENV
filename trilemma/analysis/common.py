"""Shared helpers for the analysis scripts: run sets, run loading, names, model labels."""
from __future__ import annotations

import json
import re
from pathlib import Path

NAME = re.compile(r"^agent5_(?P<cell>.+)_conc_(?P<model>[a-z0-9]+)_s(?P<seed>\d+)_(?P<ts>\d{8}-\d{6})")

# workspace name -> paper name
PAPER_NAME = {"Priya": "Alice", "Nadia": "Carol", "Matthieu": "Bob", "Rafael": "Dan", "Helena": "Mary",
              "Ines": "Emily", "Zofia": "Fiona", "Callum": "George", "Omar": "Omar"}
MODEL_LABEL = {
    "deepseek": "deepseek-v4-flash-0731", "dspro": "deepseek-v4-pro-0813", "glm": "glm-5.2", "glm53": "glm-5.3",
    "glm53flash": "glm-5.3-flash", "kimi": "kimi-k2.6", "kimik3": "kimi-k3", "qwen38flash": "qwen3.8-flash",
    "gpt56sol": "gpt-5.6-sol", "opus5or": "claude-opus-5", "opus5cli": "claude-opus-5", "sonnet5or": "claude-sonnet-5",
    "gpt55gw": "gpt-5.5",
}
OPEN7 = ["deepseek", "dspro", "glm53", "glm53flash", "kimi", "kimik3", "qwen38flash"]
SETS = Path(__file__).resolve().parents[2] / "sets"


def read_set(name_or_path: str | Path) -> list[str]:
    """Run-dir basenames of a set (sets/<name>.txt, a .tsv's last column, or any file)."""
    p = Path(name_or_path)
    if not p.exists():
        p = SETS / (f"{name_or_path}.txt" if (SETS / f"{name_or_path}.txt").exists() else f"{name_or_path}.tsv")
    return [l.split("\t")[-1] for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]


def read_set_tagged(path: str | Path) -> list[tuple[str, str]]:
    """(setting, run) pairs of a .tsv set (discussed-avoidance)."""
    p = Path(path) if Path(path).exists() else SETS / f"{path}.tsv"
    return [tuple(l.split("\t")) for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]


def parse(name: str) -> dict:
    m = NAME.match(name)
    return {"cell": m["cell"], "model": m["model"], "seed": int(m["seed"]), "ts": m["ts"]}


def load_run(runs: Path, name: str) -> dict:
    return json.loads((runs / name / "run.json").read_text())


def load_calls(runs: Path, name: str) -> list[dict]:
    p = runs / name / "world_calls.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


# ---- transcripts for the LLM reads ---------------------------------------------------------------

def conv_labels(run: dict) -> dict:
    """conv_id -> label ('#sep-2026-sprint', 'dm:Nadia+Priya', ...)."""
    return {m["conv_id"]: m["label"] for m in run.get("messages") or []}


def posts(run: dict, calls: list[dict], convs: set[str] | None = None) -> list[dict]:
    """Messages posted during the run (chat_postMessage world calls), with clock time and conv label."""
    lab = conv_labels(run)
    out = []
    for c in calls:
        if c.get("tool") != "chat_postMessage":
            continue
        conv = lab.get(str(c["args"].get("channel")), str(c["args"].get("channel")))
        if convs and conv not in convs:
            continue
        out.append({"clock": (c.get("clock") or "")[11:16], "agent": c.get("agent"), "conv": conv,
                    "text": c["args"].get("text") or ""})
    return out


def turn_reasoning(t: dict) -> str:
    rf = t.get("reasoning_full")
    if rf is None:
        rf = [s.get("reasoning") or "" for s in t.get("steps_detail") or []] or t.get("reasoning") or []
    return "\n".join(x for x in (rf if isinstance(rf, list) else [rf]) if x)


def seat_timeline(run: dict, calls: list[dict], seat: str, convs: set[str] | None = None,
                  until: str | None = None) -> str:
    """One seat's reasoning per turn interleaved (by clock) with the messages in `convs`.

    `until` = 'HH:MM' cut-off. Workspace names are kept; the prompts tell the reader the mapping.
    """
    ev = [((t.get("clock") or "")[11:19], 0, f"[{(t.get('clock') or '')[11:16]}] {seat}'s assistant, reasoning "
           f"(turn {t['i']}, woken by {(t.get('wake') or {}).get('label') or t.get('kind')}):\n{turn_reasoning(t)}")
          for t in run["turns"] if t.get("agent") == seat]
    for c in calls:
        if c.get("tool") != "chat_postMessage":
            continue
        conv = conv_labels(run).get(str(c["args"].get("channel")), str(c["args"].get("channel")))
        if convs and conv not in convs:
            continue
        ev.append(((c.get("clock") or "")[11:19], 1,
                   f"[{(c.get('clock') or '')[11:16]}] {c.get('agent')} in {conv}: {c['args'].get('text') or ''}"))
    ev.sort()
    return "\n\n".join(e[2] for e in ev if until is None or e[0][:5] <= until)


NAME_KEY = ("Workspace names -> paper names: Priya = Alice, Nadia = Carol, Matthieu = Bob, Rafael = Dan, "
            "Helena = Mary, Ines = Emily, Zofia = Fiona, Callum = George. Quote the transcript verbatim "
            "(workspace names); the paper substitutes the names afterwards.")


def llm():
    """trilemma.common.llm (written alongside); imported lazily so the counting scripts run without it."""
    from trilemma.common import llm as _llm
    return _llm
