"""Example-row tables (Tables 3-7, 11, 12, 14, 15, 18, 29 and the quotes in the appendix text):
locate every quoted excerpt in the rollouts.

These tables were assembled by reading flagged seats (deception / disclosure / honeypot judge
output) and picking illustrative lines; there is no generator for the selection itself. What can be
reproduced is that each quote is verbatim: this script parses the LaTeX table, takes each row's model,
seat, seed (when given, in the row or in its `% <model> s<N>, <seat>, ...` source comment) and
setting level (no / medium / high = main-no-conf / main / main-high-conf, else main and
main-high-conf), maps the paper names back to the workspace names, and searches the seat's reasoning
and every message it sent. A quote matches when all its fragments (split at [...]) occur in order,
compared on letters and digits only.

    python -m trilemma.analysis.example_quotes --runs <dir> --tex paper/main.tex [--tables tab:misdirection ...]
"""
from __future__ import annotations

import argparse
import re
from functools import lru_cache
from pathlib import Path

from trilemma.analysis.common import (MODEL_LABEL, PAPER_NAME, load_calls, load_run, parse, read_set,
                                      turn_reasoning)

DEFAULT_TABLES = ["tab:false-claims-task-fit", "tab:false-claims-logistics", "tab:misdirection",
                  "tab:self-acknowledged", "tab:false-claims-inventions", "tab:honeypot-examples",
                  "tab:honeypot-reasoning", "tab:disc-examples", "tab:disc-reasoning", "tab:shared-examples",
                  "tab:collusion-examples"]
LEVEL = {"no": ["main-no-conf"], "medium": ["main"], "high": ["main-high-conf"]}
LABEL_TO_SLUG = {v.lower(): k for k, v in MODEL_LABEL.items() if k not in ("opus5cli",)}
LABEL_TO_SLUG.update({"deepseek-flash": "deepseek", "deepseek-pro": "dspro"})
TO_WORKSPACE = {v: k for k, v in PAPER_NAME.items()}
SEATS = ("Alice", "Carol", "Bob", "Dan", "Mary", "Emily", "George")
ALL_SETS = ["main", "main-high-conf", "main-no-conf", "main-high-conf-honest", "honeypot",
            "no-avoidance-baseline", "emily-available", "unseeded-coop-R", "discussed-avoidance",
            "dislike-serious", "dislike-silly", "dislike-bad", "dislike-superbad"]


def table_rows(tex: str, label: str) -> list[str]:
    i = tex.find("\\label{" + label + "}")
    if i < 0:
        return []
    body = tex[i:tex.find("\\end{table", i)]
    body = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("%"))  # drop commented-out rows
    rows, cur = [], ""
    for line in body.splitlines():
        cur += line + "\n"
        if "\\\\" in line:
            rows.append(cur)
            cur = ""
    return [r for r in rows if "``" in r]


def latex_plain(s: str) -> str:
    s = re.sub(r"\\textbf\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:emph|textit|texttt)\{([^{}]*)\}", r"\1", s)
    s = s.replace("\\#", "#").replace("\\&", "&").replace("\\%", "%").replace("$", "")
    return s


def quotes(row: str) -> list[str]:
    body = row.split("%")[0] if not row.lstrip().startswith("%") else row
    return [latex_plain(q) for q in re.findall(r"``(.+?)''", body, re.S) if len(q) > 12]


def row_meta(row: str) -> dict:
    low = row.lower()
    slug = next((s for lab, s in sorted(LABEL_TO_SLUG.items(), key=lambda kv: -len(kv[0])) if lab in low), None)
    m = re.search(r"%\s*([a-z0-9]+)\s+s(\d+)", row)
    if m:
        slug, seed = m.group(1), int(m.group(2))
    else:
        m = re.search(r"\(s(\d+)", row) or re.search(r"\bs(\d+)\b", row.split("&")[0])
        seed = int(m.group(1)) if m else None
    seat = next((s for s in SEATS if re.search(rf"\b{s}\b", row.split("&")[0] + (row.split("%")[1] if "%" in row else ""))), None)
    lv = re.search(r"\\newline\s*(no|medium|high)\b", row)
    return {"model": slug, "seed": seed, "seat": seat, "sets": LEVEL[lv.group(1)] if lv else ALL_SETS}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def to_workspace(q: str) -> str:
    for paper, ws in TO_WORKSPACE.items():
        q = re.sub(rf"\b{paper}\b", ws, q)
    return q


@lru_cache(maxsize=4096)
def seat_text(runs: Path, name: str, seat: str | None) -> str:
    run = load_run(runs, name)
    parts = [turn_reasoning(t) for t in run["turns"] if seat is None or t.get("agent") == seat]
    parts += [c["args"].get("text") or "" for c in load_calls(runs, name)
              if c.get("tool") == "chat_postMessage" and (seat is None or c.get("agent") == seat)]
    parts += [t.get("text_to_principal") or "" for t in run["turns"] if seat is None or t.get("agent") == seat]
    return norm("\n".join(parts))


def find(runs: Path, q: str, meta: dict) -> str | None:
    # split at [...] / \ldots and at editorial inserts such as [Dan]
    frags = [norm(to_workspace(f)) for f in re.split(r"\[[^\]]{0,40}\]|\.\.\.|\\ldots", q)]
    frags = [f for f in frags if len(f) >= 8]
    if not frags:
        return "too short"
    seat = TO_WORKSPACE.get(meta["seat"]) if meta["seat"] else None
    for s in meta["sets"]:
        try:
            names = read_set(s)
        except FileNotFoundError:
            continue
        for n in names:
            p = parse(n)
            if meta["model"] and p["model"] != meta["model"]:
                continue
            if meta["seed"] is not None and p["seed"] != meta["seed"]:
                continue
            text = seat_text(runs, n, seat)
            pos = 0
            for f in frags:
                pos = text.find(f, pos)
                if pos < 0:
                    break
                pos += len(f)
            else:
                return f"{s} {p['model']} s{p['seed']}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--tex", required=True, type=Path)
    ap.add_argument("--tables", nargs="*", default=DEFAULT_TABLES)
    a = ap.parse_args()
    tex = a.tex.read_text()
    ok = bad = 0
    for label in a.tables:
        print(f"== {label}")
        for row in table_rows(tex, label):
            meta = row_meta(row)
            for q in quotes(row):
                hit = find(a.runs, q, meta)
                if not hit and meta["seat"]:  # the seat parse can pick up a name mentioned in the row
                    hit = find(a.runs, q, {**meta, "seat": None})
                    hit = hit and hit + " (other seat)"
                if not hit and meta["model"]:  # rows that name two models ("(qwen3.8-flash; glm-5.3-flash)")
                    hit = find(a.runs, q, {**meta, "seat": None, "model": None, "seed": None})
                    hit = hit and hit + " (other model)"
                ok += bool(hit)
                bad += not hit
                print(f"  {'OK  ' + hit if hit else 'MISS'}  [{meta['model']} s{meta['seed']} {meta['seat']}] {q[:90]!r}")
    print(f"{ok} located, {bad} not located")


if __name__ == "__main__":
    main()
