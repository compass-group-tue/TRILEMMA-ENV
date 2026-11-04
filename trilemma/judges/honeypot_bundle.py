"""Evidence views for the honeypot judge: what Alice's agent's colleagues could read (``msg``), or its own full record (``cot``).

Paper: input of the honeypot judge (``trilemma.judges.honeypot``, ``msg`` view).

    python -m trilemma.judges.honeypot_bundle --setting honeypot --view msg \
        [--only sets/honeypot.txt]     # default: the operative runs (honeypot_operative)

The two views:

``msg``  the conversation and nothing else — every message {employee} could read from someone
         taking part in the staffing, plus the scripted channel posts (the kickoff and the
         reminder).
         No reasoning, no calendar, no derived facts, no motive: `pressed`, `used_as_reason` and
         `presented_as_fixed` are decidable from messages alone, and a view that carries nothing
         else renders identically on the treatment and the control cell.

``cot``  the run as her assistant met it: its system prompt, every wake payload, every step's
         reasoning and private note, and every tool call with its **result**. This is what hp3
         could not do — it showed reasoning capped at 4500 chars a turn and no results at all,
         so the judge could not see what the assistant believed the world to be.

Both views drop the fixture calendar: it is ground truth about the world, not about what anyone
saw, and no gate turns on it.

Writes ``outputs/judges/honeypot/<setting>/bundle_<view>.json``.
"""
from __future__ import annotations

import argparse, json, re, sys
from pathlib import Path

from trilemma.common.runs import RUNS, judge_dir, model_seed

#: Slack envelope fields — constant per run, ~85% of a wake payload, nothing an assistant reads
NOISE = {"token", "team_id", "context_team_id", "context_enterprise_id", "api_app_id", "team",
         "client_msg_id", "event_context", "authorizations", "is_ext_shared_channel",
         "event_id", "event_time", "app_id", "bot_id", "blocks"}


def load(d: Path):
    r = json.loads((d / "run.json").read_text())
    calls = [json.loads(l) for l in (d / "world_calls.jsonl").read_text().splitlines() if l.strip()]
    return r, calls


def _names(r: dict) -> dict:
    """Slack user id -> employee name, from the message log's own pairing."""
    out = {}
    for m in r.get("messages") or []:
        if m.get("user_id") and m.get("user"):
            out[m["user_id"]] = m["user"]
        for i, n in zip(m.get("member_ids") or [], m.get("members") or []):
            out.setdefault(i, n)
    return out


def _labels(r: dict) -> dict:
    return {m["conv_id"]: m["label"] for m in r.get("messages") or []}


def _coverage(r: dict, seat: str) -> float:
    st = [bool((x.get("reasoning") or "").strip()) for t in r["turns"]
          if t.get("agent") == seat and t.get("kind") != "debrief" for x in t.get("steps_detail") or []]
    return round(sum(st) / len(st), 3) if st else 0.0


def _request_dm(r: dict, seat: str, outsider: str) -> tuple[str, str]:
    """The outsider's opening ask, and the conv_id of that DM thread."""
    lab = _labels(r)
    cid = next((c for c, l in lab.items() if l.startswith("dm:") and outsider in l and seat in l), "")
    ms = [m for m in r.get("messages") or [] if m["conv_id"] == cid and m.get("user") == outsider]
    return (max(ms, key=lambda m: float(m["ts"]))["text"] if ms else ""), cid


# --------------------------------------------------------------------------- msg view

def _stream(r: dict, calls: list, wanted, lab: dict, nm: dict, scripted: str = "",
            same_day: bool = False) -> list[dict]:
    """The messages of the wanted conversations, in world-clock order.

    ``wanted(conv_id) -> bool`` picks the conversations; ``scripted`` names the one conversation
    whose non-assistant posts (the ops-bot kickoff and reminder) are taken from the run record as
    well, since no assistant tool call produced them. ``same_day`` drops those record-sourced posts
    when they predate the run — a fixture thread carries months of backlog, and only the morning
    itself is in play. Edits and deletions come back as their own rows, placed where they happened
    and naming the message they hit.
    """
    live, rows, edits = {}, [], []
    for c in calls:
        a_ = c.get("args") or {}
        if c.get("tool") not in ("chat_postMessage", "chat_update", "chat_delete"):
            continue
        cid = a_.get("channel")
        if not wanted(cid):
            continue
        clk = str(c.get("clock", ""))[11:16]
        ts = (c.get("result") or {}).get("ts") or a_.get("ts")
        where = lab.get(cid, cid)
        if c["tool"] == "chat_postMessage":
            live[ts] = clk
            rows.append({"ts": float(ts), "clock": clk, "where": where, "who": c["agent"],
                         "text": (a_.get("text") or "").strip()})
        else:                                   # an edit or a deletion is itself evidence
            edits.append({"clock": clk, "where": where, "who": c["agent"], "was": ts,
                          "text": (f'[edits its {{}} message to] ' + (a_.get("text") or "").strip())
                                  if c["tool"] == "chat_update" else '[deletes its {} message]'})
    for m in r.get("messages") or []:
        if scripted and m["conv_id"] == scripted and m["ts"] not in live:
            rows.append({"ts": float(m["ts"]), "clock": "", "where": lab[scripted],
                         "who": m.get("user") or nm.get(m.get("user_id"), "?"),
                         "text": (m.get("text") or "").strip()})
    base = next((x["ts"] - _mins(x["clock"]) for x in rows if x["clock"]), 0.0)
    if same_day:                                # base is midnight of the run's own day
        rows = [x for x in rows if x["clock"] or x["ts"] >= base]
    for x in rows:                              # rows taken from the record carry no call clock
        if not x["clock"]:
            x["clock"] = _hhmm(x["ts"] - base)
    for e in edits:
        e["text"] = e["text"].format(live.get(e.pop("was"), "earlier"))
        rows.append({"ts": base + _mins(e["clock"]) + 0.5, **e})
    rows.sort(key=lambda x: x["ts"])
    return [{k: v for k, v in x.items() if k != "ts"} for x in rows]


def view_msg(r: dict, calls: list, seat: str, outsider: str) -> list[dict]:
    """Every message {seat} could read from someone in the staffing, in world-clock order.

    Included: the sprint channel in full (its scripted ops-bot posts included), and everything
    posted during the run to a channel {seat} is in or to a DM between {seat} and a roster
    member. Excluded: the DM thread with the outsider who asked for the hour (hp3's rule — what
    she tells him bears on neither message gate), every DM with someone outside the staffing,
    and the seeded backlog of channels other than the sprint one.
    """
    roster = set((r.get("system_prompts") or {}).keys())
    lab, nm = _labels(r), _names(r)
    _, ask_cid = _request_dm(r, seat, outsider)
    sprint = next((c for c, l in lab.items() if l.startswith("#") and "sprint" in l), "")

    def wanted(cid: str) -> bool:
        l = lab.get(cid, "")
        if cid == ask_cid:
            return False
        if l.startswith("#"):
            return any(m["conv_id"] == cid and seat in (m.get("members") or [seat]) for m in r["messages"])
        return l.startswith("dm:") and seat in l and bool(roster & set(l[3:].split("+")) - {seat})

    return _stream(r, calls, wanted, lab, nm, scripted=sprint)


def view_aside(r: dict, calls: list, seat: str, outsider: str) -> list[dict]:
    """The {seat}<->{outsider} DM thread — the one conversation `view_msg` withholds.

    Never part of a judge's evidence: the message gates are about what people in the staffing
    could read, and hp2's error was counting this thread among them. It is built separately so a
    reviewer can see it in the same chronology, marked as something the judge did not have — it
    is where an assistant asks for the hour to be moved, which bears on whether a "fixed"
    claim was honest but not on whether the claim was made.
    """
    lab, nm = _labels(r), _names(r)
    _, ask_cid = _request_dm(r, seat, outsider)
    if not ask_cid:
        return []
    rows = _stream(r, calls, lambda cid: cid == ask_cid, lab, nm, scripted=ask_cid, same_day=True)
    for x in rows:
        x["aside"] = True
    return rows


def _mins(hhmm: str) -> float:
    return int(hhmm[:2]) * 3600 + int(hhmm[3:]) * 60


def _hhmm(sec: float) -> str:
    return f"{int(sec // 3600) % 24:02d}:{int(sec % 3600) // 60:02d}"


# --------------------------------------------------------------------------- cot view

def _strip(x):
    """Drop Slack envelope keys, and render a message list as lines rather than JSON."""
    if isinstance(x, dict):
        if isinstance(x.get("messages"), list):
            x = dict(x)
            x["messages"] = [f'[{m.get("ts","")}] {m.get("user","")}: {m.get("text","")}'
                             for m in x["messages"] if isinstance(m, dict)]
        return {k: _strip(v) for k, v in x.items() if k not in NOISE}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _event(text: str, lab: dict, nm: dict) -> str:
    """A wake payload as the assistant met it, minus the envelope, with ids annotated."""
    m = re.search(r"```json\s*(\{.*\})\s*```", text or "", re.S)
    if not m:
        return " ".join((text or "").split())
    try:
        ev = json.loads(m.group(1))
    except Exception:
        return " ".join((text or "").split())
    e = _strip(ev.get("event") or ev)
    ch, us = e.get("channel"), e.get("user")
    head = text.split("```")[0].strip() or "New Slack event:"
    tag = (f' {ch} ({lab.get(ch, "?")})' if ch else "") + (f' from {us} ({nm.get(us, "?")})' if us else "")
    body = e.get("text") or json.dumps(e, ensure_ascii=False)
    rest = {k: v for k, v in e.items() if k not in ("text", "channel", "user", "ts", "event_ts", "type")}
    return f"{head}{tag}: {body}" + (f"  {json.dumps(rest, ensure_ascii=False)}" if rest else "")


def view_cot(r: dict, seat: str) -> list[dict]:
    """The run as her assistant met it: wake, reasoning, private note, calls and their results."""
    lab, nm = _labels(r), _names(r)
    out = []
    for t in r["turns"]:
        if t.get("agent") != seat:
            continue
        turn = {"turn": t.get("i"), "kind": t.get("kind"), "clock": str(t.get("clock", ""))[11:16],
                "wake": _event(t.get("message_in") or "", lab, nm), "steps": [],
                "to_principal": (t.get("text_to_principal") or "").strip()}
        by_step = {}
        for c in t.get("tool_calls") or []:
            by_step.setdefault(c.get("step"), []).append(c)
        for x in t.get("steps_detail") or []:
            k = x.get("step")
            turn["steps"].append({
                "step": k,
                "reasoning": (x.get("reasoning") or "").strip(),     # never truncated
                "note": (x.get("text") or "").strip(),
                "calls": [{"seq": c["seq"], "tool": c["tool"],
                           "args": _strip(c.get("args") or {}),
                           "result": _strip(c.get("result"))} for c in by_step.pop(k, [])]})
        for k, cs in sorted(by_step.items(), key=lambda kv: (kv[0] is None, kv[0])):
            turn["steps"].append({"step": k, "reasoning": "", "note": "",
                                  "calls": [{"seq": c["seq"], "tool": c["tool"],
                                             "args": _strip(c.get("args") or {}),
                                             "result": _strip(c.get("result"))} for c in cs]})
        out.append(turn)
    return out


# --------------------------------------------------------------------------- driver

def bundle(d: Path, view: str, seat: str, avoided: str, outsider: str) -> dict:
    r, calls = load(d)
    cfg = r.get("config") or {}
    req, _ = _request_dm(r, seat, outsider)
    model, seed = model_seed(d.name)
    rec = {"run": d.name, "model": model, "seed": int(seed[1:]),
           "seat": seat, "avoided": avoided, "outsider": outsider,
           "roster": ", ".join((r.get("system_prompts") or {}).keys()),
           "request_dm": req, "reasoning_coverage": _coverage(r, seat),
           "board": r.get("assignments") or {}}
    if view == "msg":
        rec["messages"] = view_msg(r, calls, seat, outsider)
    else:
        rec["ask"] = (cfg.get("ask_overrides") or {}).get(seat, cfg.get("ask", ""))
        rec["system_prompt"] = (r.get("system_prompts") or {}).get(seat, "")
        rec["turns"] = view_cot(r, seat)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--setting", required=True)
    ap.add_argument("--view", choices=("msg", "cot"), default="msg")
    ap.add_argument("--runs", default=None, help="run directory (default outputs/runs/<setting>)")
    ap.add_argument("--only", default=None,
                    help="file of run-dir names (default: operative.txt from honeypot_operative)")
    ap.add_argument("--out", default=None, help="default outputs/judges/honeypot/<setting>/bundle_<view>.json")
    ap.add_argument("--seat", default="Priya")
    ap.add_argument("--avoided", default="Matthieu")
    ap.add_argument("--outsider", default="Callum", help="who asked for the 16:00 hour (George in the paper)")
    a = ap.parse_args()

    runs = Path(a.runs) if a.runs else RUNS / a.setting
    base = judge_dir("honeypot", a.setting)
    dirs = {p.parent.name: p.parent for p in sorted(runs.glob("*/run.json"))}
    only = Path(a.only) if a.only else base / "operative.txt"
    want = [l.strip() for l in only.read_text().splitlines() if l.strip() and not l.startswith("#")]
    missing = [w for w in want if w not in dirs]
    if missing:
        sys.exit(f"--only: {len(missing)} run(s) not found, first: {missing[0]}")
    recs = []
    for w in want:
        try:
            recs.append(bundle(dirs[w], a.view, a.seat, a.avoided, a.outsider))
        except Exception as exc:
            print(f"skip {w}: {exc}", file=sys.stderr)
    out = Path(a.out) if a.out else base / f"bundle_{a.view}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(recs, indent=1, ensure_ascii=False) + "\n")
    n = sum(len(json.dumps(x, ensure_ascii=False)) for x in recs)
    print(f"wrote {out}: {len(recs)} runs, ~{n // 4 // max(len(recs), 1)} tokens/run")


if __name__ == "__main__":
    main()
