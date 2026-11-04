"""Board viewer: a time-aligned multi-column grid over run.json.

Columns are conversations (channels + DMs) and one turn lane per assistant,
all toggleable. Rows are world-clock moments (ordinal, not proportional), so
everything an agent did in one turn — a DM, a channel post, a silent think —
lands on the same row across columns. Assistant messages carry a CoT expander
showing the exact reasoning step that produced them; hovering cross-highlights
cause and effect (wake edges, reads, posts). View state lives in the URL hash.

Run: python -m trilemma.env.viewer <run_dir_or_run.json> [...]
     (writes board.html next to each run.json)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

MAX_DETAIL = 700          # chars of a tool call's args/result kept in the page
GAP_SPACER_S = 240        # world-clock gap that earns a "+N min" spacer row
ROW_MERGE_S = 1           # atoms within the same rounded second share a row
POST_GROUP_S = 15         # same-turn-and-step posts further apart than this split rows


def _naive_utc(iso: str) -> float:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


def _epoch_offset(r: Dict[str, Any]) -> float:
    """Seconds to add to a naive world-clock ISO to land on message-ts epoch."""
    for t in r["turns"]:
        for c in t.get("tool_calls") or []:
            res = c.get("result") or {}
            if c.get("tool") == "chat_postMessage" and res.get("ts") and c.get("clock"):
                return float(res["ts"]) - _naive_utc(c["clock"])
    for t in r["turns"]:
        w = t.get("wake") or {}
        if w.get("ts") and t.get("clock"):
            return float(w["ts"]) - _naive_utc(t["clock"])
    return 0.0


def _hms(epoch: float, off: float) -> str:
    return datetime.fromtimestamp(epoch - off, tz=timezone.utc).strftime("%H:%M:%S")


def _clip(obj: Any, n: int = MAX_DETAIL) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " …"


def _conv_label_map(r: Dict[str, Any]) -> Dict[str, str]:
    return {m["conv_id"]: m["label"] for m in r["messages"]}


def _call_view(c: Dict[str, Any], labels: Dict[str, str]) -> Dict[str, Any]:
    tool = c.get("tool", "?")
    args = c.get("args") or {}
    res = c.get("result") or {}
    label = labels.get(str(args.get("channel")), str(args.get("channel") or ""))
    v: Dict[str, Any] = {"tool": tool, "step": c.get("step") or 0,
                         "detail": f"args: {_clip(args)}\nresult: {_clip(res)}",
                         "clock": str(c.get("clock") or "")}
    if tool in ("conversations_history", "conversations_replies"):
        msgs = res.get("messages") or []
        v["line"] = f"read {label} ({len(msgs)} msgs)"
        v["read_ts"] = [str(m.get("ts")) for m in msgs if m.get("ts")][:300]
        v["read_conv"] = str(args.get("channel") or "")
        v["read_n"] = len(msgs)
        v["read_clock"] = str(c.get("clock") or "")
    elif tool == "chat_postMessage":
        v["line"] = f"posted → {label}"
        if res.get("ts"):
            v["post_ts"] = str(res["ts"])
    elif tool == "chat_delete":
        v["line"] = f"deleted a message in {label}"
    elif tool == "notify_user":
        v["line"] = "notified principal"
        v["push"] = str(args.get("text") or "")
    elif tool == "board_assign":
        v["line"] = f"board_assign {json.dumps(args, ensure_ascii=False)}"
    elif tool == "reactions_add":
        v["line"] = f"reacted :{args.get('name', '?')}: in {label}"
    else:
        v["line"] = f"{tool} {_clip(args, 80)}"
    return v


def _turn_views(r: Dict[str, Any], off: float) -> List[Dict[str, Any]]:
    labels = _conv_label_map(r)
    out = []
    seen: Dict[str, set] = defaultdict(set)  # agent -> message ts it has already seen
    for t in r["turns"]:
        wake_batch = [str(b.get("ts")) for b in ((t.get("wake") or {}).get("batch") or []) if b.get("ts")]
        if not wake_batch and (t.get("wake") or {}).get("ts"):
            wake_batch = [str(t["wake"]["ts"])]
        seen[t["agent"]].update(wake_batch)
        calls_by_step: Dict[int, list] = defaultdict(list)
        reads, read_ts, posts = [], [], []
        read_events: List[Dict[str, Any]] = []  # {ts, time}: when each message was fetched
        readmarks: Dict[str, int] = {}  # conv id -> most messages fetched in one call
        for c in t.get("tool_calls") or []:
            cv = _call_view(c, labels)
            calls_by_step[cv["step"]].append(cv)
            if "read_ts" in cv:
                reads.append(cv["line"])
                cv_ts = cv.pop("read_ts")
                read_ts += cv_ts
                # the perspective view: messages this fetch showed the assistant for the
                # first time (its own posts and anything delivered as a wake count as seen)
                res_msgs = (c.get("result") or {}).get("messages") or []
                new = [{"ts": str(m.get("ts")), "user": str(m.get("user", "")), "text": str(m.get("text", ""))[:600],
                        "conv": str((c.get("args") or {}).get("channel") or "")}
                       for m in res_msgs if str(m.get("ts")) not in seen[t["agent"]]]
                cv["fetched"] = new
                cv["seen_n"] = len(res_msgs) - len(new)
                seen[t["agent"]].update(str(m.get("ts")) for m in res_msgs)
                conv = cv.pop("read_conv", ""); n = cv.pop("read_n", 0)
                cv["conv"] = conv
                clk = cv.pop("read_clock", "")
                when = _hms(_naive_utc(clk) + off, off) if clk else ""
                for ts_ in cv_ts:
                    read_events.append({"ts": ts_, "time": when})
                if conv:
                    readmarks[conv] = max(readmarks.get(conv, 0), n)
            if "post_ts" in cv:
                posts.append(cv["post_ts"])
                seen[t["agent"]].add(cv["post_ts"])
        steps = [{"step": sd.get("step"), "reasoning": sd.get("reasoning") or "",
                  "text": sd.get("text") or "", "calls": calls_by_step.pop(sd.get("step"), [])}
                 for sd in (t.get("steps_detail") or [])]
        for step, calls in sorted(calls_by_step.items()):  # calls with no step record
            steps.append({"step": step, "reasoning": "", "text": "", "calls": calls})
        # step clocks: a step's reasoning ends when its first tool call is issued; a step
        # without calls (the closing one) ends with the turn. Rows in "steps as rows" mode
        # use the same key as build_data's atoms (2026-09-03).
        end_iso = t.get("clock_end") or t["clock"]
        for sd in steps:
            clk = next((c["clock"] for c in sd["calls"] if c.get("clock")), "") or end_iso
            ep = _naive_utc(clk) + off
            sd["time"] = _hms(ep, off)
            sd["epoch"] = int(ep // ROW_MERGE_S)
            for c in sd["calls"]:
                c["t"] = _hms(_naive_utc(c["clock"]) + off, off) if c.get("clock") else ""
                c.pop("clock", None)
        wake = t.get("wake") or {}
        if t["kind"] == "wake" and wake.get("label"):
            src = f"woke ← {wake['label']}" + (f" ({wake['from']})" if wake.get("from") else "")
        elif t["kind"] == "added":
            src = f"added to {wake.get('label', '?')}"
        else:
            src = t["kind"] + " from principal"
        out.append({
            "i": t["i"], "agent": t["agent"], "kind": t["kind"],
            "time": _hms(_naive_utc(t["clock"]) + off, off),
            "end": _hms(_naive_utc(end_iso) + off, off),
            "t1": int(_naive_utc(end_iso) + off), "src": src,
            "ask": (t.get("message_in") or "") if t["kind"] in ("ask", "debrief") else "",
            "steps": steps, "reads": reads, "read_ts": sorted(set(read_ts)),
            "readmarks": [{"conv": k, "n": n} for k, n in readmarks.items()],
            "wake_batch": wake_batch,
            "read_events": read_events,
            "posts": posts, "report": t.get("text_to_principal") or "",
            "wake_ts": str(wake.get("ts")) if wake.get("ts") else "",
            "cost": (t.get("usage") or {}).get("cost"),
        })
    return out


def build_data(r: Dict[str, Any]) -> Dict[str, Any]:
    off = _epoch_offset(r)
    start = _naive_utc(r["clock_start"]) + off
    roster = list((r.get("system_prompts") or {}).keys()) or sorted({t["agent"] for t in r["turns"]})
    turns = _turn_views(r, off)

    post_map: Dict[str, Dict[str, int]] = {}    # msg ts -> producing turn/step
    for t, raw in zip(turns, r["turns"]):
        for c in raw.get("tool_calls") or []:
            res = c.get("result") or {}
            if c.get("tool") == "chat_postMessage" and res.get("ts"):
                post_map[str(res["ts"])] = {"turn": t["i"], "step": c.get("step") or 0}
    woken: Dict[str, List[int]] = defaultdict(list)  # msg ts -> turns it woke
    for t, raw in zip(turns, r["turns"]):
        batch = (raw.get("wake") or {}).get("batch") or []
        tss = [str(b.get("ts")) for b in batch if b.get("ts")] or ([t["wake_ts"]] if t["wake_ts"] else [])
        for ts_ in dict.fromkeys(tss):
            woken[ts_].append(t["i"])

    # who fetched each message via history (turn indices), for the per-message read line
    read_by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in turns:
        seen_ts = set()
        for ev in t.get("read_events") or []:
            if ev["ts"] in seen_ts:
                continue
            seen_ts.add(ev["ts"])
            read_by[ev["ts"]].append({"turn": t["i"], "time": ev["time"]})
    convs: Dict[str, Dict[str, Any]] = {}
    msgs = []
    backlog: Dict[str, list] = defaultdict(list)
    for m in r["messages"]:
        cid, ts = m["conv_id"], float(m["ts"])
        c = convs.setdefault(cid, {"id": cid, "label": m["label"], "type": m["type"],
                                   "total": 0, "run": 0, "members": list(m.get("members") or [])})
        c["total"] += 1
        src = post_map.get(m["ts"])
        mv = {"ts": m["ts"], "conv": cid, "user": m["user"], "text": m["text"],
              "time": _hms(ts, off), "src": src, "wakes": woken.get(m["ts"], []),
              "reads": read_by.get(m["ts"], [])}
        if ts < start - 30 and not src:
            backlog[cid].append(mv)
        else:
            c["run"] += 1
            msgs.append(mv)

    def conv_key(c):
        is_sprint = c["type"] == "channel" and "sprint" in c["label"]
        return (0 if is_sprint else 1 if c["type"] == "channel" else 2, -c["run"], c["label"])
    conv_order = sorted(convs.values(), key=conv_key)
    # default columns (2026-09-07): the sprint channel, DMs whose every member is an
    # assistant-backed principal, and the agents' turns. A DM with a scripted-only colleague
    # (Priya ⇄ Zofia, Nadia ⇄ Ines, …), a bot DM and the social channels all stay one
    # chip-click away — they are world texture, not conversation between the agents.
    roster_set = set(roster)
    for c in conv_order:
        lab = c["label"]
        is_sprint = c["type"] == "channel" and "sprint" in lab
        members = c.get("members") or []
        is_agent_dm = (c["type"] != "channel" and "bot" not in lab.lower()
                       and bool(members) and set(members) <= roster_set)
        c["default_on"] = bool(c["run"] > 0 and (is_sprint or is_agent_dm))

    # rows: one per rounded world-second. Turn cells sit at turn start; messages sit
    # at their own post time, except posts from the same (turn, step) share the row of
    # the group's earliest post — "sent together" stays same-height. A same-step group
    # is split on gaps > POST_GROUP_S: the step mapper leaves some calls at step 0, and
    # merging posts minutes apart would break channel chronology.
    grouped: Dict[tuple, list] = defaultdict(list)
    for i, mv in enumerate(msgs):
        if mv["src"]:
            grouped[(mv["src"]["turn"], mv["src"]["step"])].append(i)
    row_ts = {i: float(mv["ts"]) for i, mv in enumerate(msgs)}
    for members in grouped.values():
        members.sort(key=lambda i: float(msgs[i]["ts"]))
        anchor = float(msgs[members[0]]["ts"])
        for i in members:
            ts = float(msgs[i]["ts"])
            if ts - anchor > POST_GROUP_S:
                anchor = ts
            row_ts[i] = anchor
    # board claims (2026-09-07): every accepted board_assign, at the row of its own clock;
    # a person's later claim replaces the earlier one, "skip" releases — the board lane
    # column draws one bar per (person, ticket) interval
    board_events: List[Dict[str, Any]] = []
    for t, raw in zip(turns, r["turns"]):
        for c in raw.get("tool_calls") or []:
            if c.get("tool") != "board_assign":
                continue
            args = c.get("args") or {}
            res = c.get("result") or {}
            if isinstance(res, dict) and (res.get("ok") is False or res.get("error")):
                continue
            clk = c.get("clock") or raw["clock"]
            ep = int((_naive_utc(clk) + off) // ROW_MERGE_S)
            board_events.append({"agent": t["agent"], "task": str(args.get("task_id") or ""),
                                 "epoch": ep, "time": _hms(ep, off)})
    board_events.sort(key=lambda e: e["epoch"])
    atoms: Dict[int, Dict[str, Any]] = {}
    for t, raw in zip(turns, r["turns"]):
        key = int((_naive_utc(raw["clock"]) + off) // ROW_MERGE_S)
        t["t0"] = key
        atoms.setdefault(key, {"epoch": key, "turns": [], "msgs": []})["turns"].append(t["i"])
    for i in range(len(msgs)):
        key = int(row_ts[i] // ROW_MERGE_S)
        atoms.setdefault(key, {"epoch": key, "turns": [], "msgs": []})["msgs"].append(i)
    # "read" markers: one per (turn, conversation fetched), placed in that conversation's
    # column at the turn's row.
    important_convs: set = set()
    label_of = _conv_label_map(r)
    for t in turns:
        key = t["t0"]
        marks = atoms[key].setdefault("marks", [])
        for m in t.get("readmarks") or []:
            lab = label_of.get(m["conv"], m["conv"])
            marks.append({"conv": m["conv"], "agent": t["agent"], "turn": t["i"], "n": m["n"],
                          "important": lab in important_convs})
    rows = [atoms[k] | {"time": _hms(k, off), "marks": atoms[k].get("marks", [])} for k in sorted(atoms)]

    markers = []
    for name, iso in (("kickoff", r.get("kickoff")), ("deadline", r.get("deadline"))):
        if iso:
            ep = _naive_utc(iso) + off
            markers.append({"epoch": int(ep), "label": name, "time": _hms(ep, off)})

    cfg = r.get("config") or {}
    # ids → names overlay: users from the fixture the
    # run declares, conversation ids from the run's own message log.
    id_names: Dict[str, str] = {}
    try:
        fx_path = Path(__file__).resolve().parents[2] / str(cfg.get("fixture") or "")
        fx = json.loads(fx_path.read_text()) if cfg.get("fixture") else {}
        id_names.update({u["id"]: u["name"] for u in fx.get("users") or [] if u.get("id")})
    except Exception:
        pass
    for m in r.get("messages") or []:
        if m.get("conv_id") and m.get("label"):
            id_names.setdefault(str(m["conv_id"]), str(m["label"]))
    important_ids = [cid for cid, lab in label_of.items() if lab in important_convs]
    return {
        "id_names": id_names, "important_convs": important_ids,
        "meta": {"run_id": cfg.get("run_id") or cfg.get("name", ""), "model": cfg.get("model", ""),
                 "outcome": r.get("outcome", ""), "score": r.get("score"),
                 "assignments": r.get("assignments"), "started": r.get("clock_start", ""),
                 "kickoff": r.get("kickoff"), "deadline": r.get("deadline")},
        "roster": roster, "convs": conv_order, "turns": turns, "msgs": msgs,
        "backlog": {k: v for k, v in backlog.items()}, "rows": rows, "markers": markers,
        "gap_s": GAP_SPACER_S, "board_events": board_events,
    }


def render(run_path: str | Path, out_name: str = "board.html") -> Path:
    run_path = Path(run_path)
    if run_path.is_dir():
        run_path = run_path / "run.json"
    data = build_data(json.loads(run_path.read_text()))
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = _TEMPLATE.replace("__TITLE__", data["meta"]["run_id"] or run_path.parent.name)
    html = html.replace("__DATA__", blob)
    out = run_path.parent / out_name
    out.write_text(html)
    return out


_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · board</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#fff; --ink:#1c2330; --dim:#68738a; --line:#dfe3ea;
  --stripe:rgba(15,40,80,.025); --hl:#ffd54d33; --pin:#ffb30055;
  --accent:#2563eb; --marker:#c2410c; --spacer:#94a3b8;
  --Nadia:#2563eb; --Marcus:#059669; --Matthieu:#059669; --Tomas:#d97706; --Priya:#db2777; --Helena:#7c3aed;
  --Rafael:#d97706;
  --human:#8a93a6;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#12161d; --panel:#1a2029; --ink:#dfe5ee; --dim:#8b96aa; --line:#2a3342;
  --stripe:rgba(160,190,255,.04); --hl:#ffd54d22; --pin:#ffb30044;
  --Nadia:#5c8dff; --Marcus:#2fbf8f; --Matthieu:#2fbf8f; --Tomas:#e8a13c; --Priya:#ef6aa8; --Helena:#a07af0;
  --Rafael:#e8a13c;
}}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);overflow:hidden;
  display:flex;flex-direction:column;
  font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
#top{flex:none;background:var(--bg);border-bottom:1px solid var(--line);
  padding:8px 12px}
#top h1{font-size:14px;margin:0 0 2px;font-weight:650}
#top .meta{color:var(--dim);font-size:12px;margin-bottom:6px}
#chips{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
#chips .grp{color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.06em;
  margin:0 2px 0 8px}
#chips .grp:first-child{margin-left:0}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--dim);border-radius:20px;
  padding:2px 9px;font-size:11.5px;cursor:pointer;user-select:none;white-space:nowrap}
.chip.on{color:var(--ink);border-color:currentColor}
.chip .n{opacity:.55;font-size:10px;margin-left:3px}
#wrap{flex:1;overflow:auto}
#grid{display:grid;align-items:start;min-width:min-content;padding-bottom:40vh}
.stripe{background:var(--stripe);align-self:stretch;height:100%}
.tbar{width:3px;height:100%;align-self:stretch;justify-self:start;margin-left:1px;
  border-radius:2px;opacity:.5;cursor:pointer}
.tbar:hover{opacity:1;width:5px}
.bbar{width:4px;height:100%;align-self:stretch;justify-self:start;border-radius:2px;opacity:.9}
.bbar:hover{opacity:1;width:6px}
.bdiv{width:1px;height:100%;align-self:stretch;justify-self:start;background:var(--line);opacity:.8}
.colhead.bhead{font-size:10px;text-align:center;padding-left:0;padding-right:0}
.colhead{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--line);
  padding:6px 8px;font-weight:650;font-size:12px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.rule{position:sticky;left:0;z-index:10;background:var(--bg);color:var(--dim);font-size:10.5px;
  padding:8px 6px 0 8px;white-space:nowrap;font-variant-numeric:tabular-nums}
.rule.colhead{z-index:25}
.cell{padding:4px 6px;min-width:0}
.spacer{grid-column:1/-1;color:var(--spacer);font-size:10.5px;text-align:center;
  border-top:1px dashed var(--line);padding:1px 0;margin-top:4px}
.marker{grid-column:1/-1;color:var(--marker);font-size:11px;font-weight:650;
  border-top:2px solid var(--marker);padding:2px 8px;margin-top:4px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:5px 8px;
  margin-bottom:4px;border-left:3px solid var(--human);overflow-wrap:break-word}
.card.hl{background:var(--hl)}
.card.pin{outline:2px solid var(--pin);background:var(--pin)}
.card .hd{display:flex;gap:6px;align-items:baseline;font-size:11px;color:var(--dim)}
.card .hd b{color:var(--ink);font-size:12px}
.card .hd .t{margin-left:auto;font-variant-numeric:tabular-nums}
.msg .body{white-space:pre-wrap;margin-top:2px}
.msg.scripted .body{color:var(--dim)}
.turnc{border-left-width:3px}
.turnc .src{font-size:11px;color:var(--dim);margin-top:1px}
.turnc .rline{font-size:11px;color:var(--dim)}
.turnc .peek{margin-top:2px;font-style:italic;color:var(--dim);display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.turnc.silent{opacity:.75}
/* the assistant's private message to its own employee: shown in full, by default,
   in the agent column — it is the surface the employee actually reads (2026-09-07). */
.report{margin-top:4px;font-size:12px;white-space:pre-wrap;background:var(--hl);
  border-left:3px solid var(--accent);border-radius:0 3px 3px 0;padding:4px 6px}
.report + .report{margin-top:3px}
.report b{color:var(--accent);font-weight:650}
.report.push{border-left-color:var(--marker)}
.report.push b{color:var(--marker)}
details.cot{margin-top:3px;font-size:12px}
details.cot>summary{cursor:pointer;color:var(--accent);font-size:11px;user-select:none;
  list-style:none;display:inline-block;border:1px solid var(--line);border-radius:10px;
  padding:0 8px}
details.cot[open]>summary{background:var(--accent);color:#fff;border-color:var(--accent)}
.cotbody{max-height:65vh;overflow:auto;overscroll-behavior:contain}
.step{border-left:2px solid var(--line);margin:5px 0 5px 2px;padding-left:7px}
.step .sh{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.step.post{border-left-color:var(--accent)}
.step.post .sh{color:var(--accent)}
.reason{white-space:pre-wrap;color:var(--ink);opacity:.9}
.stext{white-space:pre-wrap;margin-top:3px;padding:3px 6px;background:var(--stripe);
  border-radius:6px}
details.tc{margin:2px 0}
details.tc>summary{cursor:pointer;font-size:11px;color:var(--dim);list-style:none}
details.tc>summary:before{content:"⚙ "}
details.tc pre{white-space:pre-wrap;font-size:10.5px;margin:2px 0;color:var(--dim);
  max-height:220px;overflow:auto}
.askfull{white-space:pre-wrap;font-size:12px;color:var(--dim);margin-top:3px}
.jump{color:var(--accent);cursor:pointer;font-size:11px}
.bkcell{font-size:11.5px}
.bkcell>details>summary{cursor:pointer;color:var(--dim)}
.bkcell .card{opacity:.8}
.wline{font-size:10.5px;color:var(--dim);margin-top:2px}
.card.readmark{font-size:11px;color:var(--dim);padding:2px 6px;margin-bottom:3px;background:transparent;border-style:dashed;cursor:pointer}
.card.readmark.imp{color:var(--ink);background:var(--hl)}
.card.persp{max-width:none}
.psec{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--accent);margin:8px 0 3px}
.pcall{font-size:11px;color:var(--dim);margin:3px 0}
.pmsg{border-left:2px solid var(--line);padding:2px 6px;margin:3px 0;font-size:12px;white-space:pre-wrap}
.pmsg.imp{background:var(--hl)}
.pmsg .pm{display:block;font-size:10.5px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wline.nobody{color:var(--marker)}
.wline.seen{display:flex;flex-wrap:wrap;gap:3px 6px;align-items:flex-start}
.wline.seen details.xcot{margin-top:0}
.wline.seen details.xcot>summary{border-left-width:3px}
.wline.seen details.xcot[open]{flex-basis:100%}
.wline.seen details.xcot>.cotbody{margin-top:4px;border:1px solid var(--line);border-radius:6px;padding:4px 8px;background:var(--bg)}
.step.hit{border-left-color:var(--accent);background:color-mix(in srgb, var(--accent) 6%, transparent)}
#legend{font-size:11px;color:var(--dim);margin-top:4px}
.thought{margin-top:4px;padding:3px 7px;border-left:2px dotted var(--dim);font-size:11.5px;color:var(--dim);cursor:pointer;background:color-mix(in srgb, var(--dim) 5%, transparent);border-radius:0 6px 6px 0}
.thought .th{font-size:10.5px;color:var(--dim)}
.thought .th b{color:var(--ink)}
.thought .tail{white-space:pre-wrap;font-style:italic;color:var(--ink);opacity:.85;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.thought .fulltxt{display:none;white-space:pre-wrap;color:var(--ink);opacity:.9;max-height:60vh;overflow:auto}
.thought.full .tail{display:none}
.thought.full .fulltxt{display:block}
.thought.full{border-left-style:solid;cursor:default}
.react{font-size:11px;color:var(--dim);margin-top:3px;padding-left:4px;border-left:2px solid var(--line)}
.react b{color:var(--ink)}
.react .q{font-style:italic}
.react.none{opacity:.7}
details.stp{margin:3px 0 3px 2px}
details.stp>summary{cursor:pointer;font-size:10.5px;color:var(--dim);list-style:none;border-left:2px solid var(--line);padding-left:7px}
details.stp>summary:before{content:"▸ ";font-size:9px}
details.stp[open]>summary:before{content:"▾ "}
details.stp>summary .pv{font-style:italic;opacity:.8}
.step .sh .st{color:var(--dim);text-transform:none;letter-spacing:0;margin-left:6px}
details.tc>summary .ct{color:var(--dim);opacity:.7;margin-right:4px}
.card.stepc{border-left-width:3px;border-style:dashed;font-size:12px}
.card.stepc .reason{display:-webkit-box;-webkit-line-clamp:8;-webkit-box-orient:vertical;overflow:hidden}
.card.stepc.full .reason{display:block}
.card.stepc .more{color:var(--accent);font-size:10.5px;cursor:pointer}
.card.stepc .cl{font-size:11px;color:var(--dim)}
.card.stepc .cl.post{color:var(--accent)}
.chip.act{border-style:dashed}
.card.postmark{border-left-width:3px;border-style:dashed;font-size:11.5px;cursor:pointer;background:transparent}
.card.postmark .tail{white-space:pre-wrap;font-style:italic;color:var(--ink);opacity:.85;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;margin-top:2px}
.card.postmark .fulltxt{display:none;white-space:pre-wrap;color:var(--ink);opacity:.9;max-height:60vh;overflow:auto;margin-top:2px}
.card.postmark.full .tail{display:none}
.card.postmark.full .fulltxt{display:block}
.card.postmark.full{border-style:solid;cursor:default}
.again{color:var(--dim);font-size:10px}
.slackid.named{background:rgba(90,140,255,.14);border-bottom:1px dotted rgba(90,140,255,.8);border-radius:3px;padding:0 2px}
</style></head><body>
<div id="top">
  <h1 id="title"></h1><div class="meta" id="meta"></div>
  <div id="chips"></div>
  <div class="meta" style="margin-top:6px"><button id="btn-names" onclick="toggleNames()" title="Replace raw Slack ids with ⟨names⟩ — a viewer overlay, not what was written">ids → names</button></div>
  <div id="legend">legend · <b>board column</b>: T1 lane left, T2 lane right, one bar per person in their colour from claim to release/replacement (hover for times) · <b>woke A</b> under a message: delivered to A's assistant as an event (the wake is a read) · <b>👁 read by A hh:mm</b>: A's assistant fetched it via history (author's own re-reads not listed) · <b>👁 read by nobody</b>: neither delivered nor fetched · <b>👁 A read n</b> cards in a conversation column: A's assistant fetched n messages there in that turn, ★ = the conversation carries this cell's layered material · <b>✎ A step k/n</b> cards in an agent column: A's assistant posted from there at this height (turn cards sit at turn start), with that step's closing thoughts · <b>💭</b> under a post: the tail of the reasoning that produced it (step k/n, when it ended, whether the turn ended there) · <b>↳ A ✎</b>: what A's assistant posted in the turn this message woke · <b>⚡/👁 chips</b>: expand to that assistant's CoT for the turn that saw the message</div>
</div>
<div id="wrap"><div id="grid"></div></div>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const AGENTS = new Set(D.roster);
const colDefs = [];             // {id,label,kind:'conv'|'agent'|'board',on}
// the board lanes: a slim column, T1 left / T2 right, one bar per person in their colour
// from the claim to its release or replacement (2026-09-07)
colDefs.push({id:"board", label:"board", kind:"board", n:(D.board_events||[]).length, on:true});
for (const c of D.convs) colDefs.push({id:"c:"+c.id, label:c.label, kind:"conv",
  n:c.run, on:("default_on" in c ? c.default_on : c.run>0)});
for (const a of D.roster) colDefs.push({id:"a:"+a, label:a+" · turns", kind:"agent",
  n:D.turns.filter(t=>t.agent===a).length, on:true});
const colById = Object.fromEntries(colDefs.map(c=>[c.id,c]));

function agentColor(name){
  return getComputedStyle(document.documentElement).getPropertyValue("--"+name) ?
    `var(--${name}, var(--human))` : "var(--human)";
}
const esc = s => (s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

/* ---------- hash state ---------- */
function readHash(){
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.has("cols")){
    const on = new Set(p.get("cols").split("|").filter(Boolean));
    for (const c of colDefs) c.on = on.has(c.id);
  }
  if (typeof persp !== "undefined") for (const a of (p.get("persp")||"").split("|").filter(Boolean)) persp[a] = true;
  if (typeof opts !== "undefined" && p.has("o")){
    const o = new Set(p.get("o").split("|").filter(Boolean));
    opts.thoughts = o.has("th"); opts.reactions = o.has("re"); opts.steprows = o.has("sr");
    opts.peek = o.has("pk:end") ? "end" : o.has("pk:post") ? "post" : "start";
  }
  return new Set((p.get("open")||"").split("|").filter(Boolean));
}
function writeHash(){
  const cols = colDefs.filter(c=>c.on).map(c=>c.id).join("|");
  const allOpen = [...document.querySelectorAll("details.cot[open]")].map(d=>d.dataset.oid).filter(Boolean);
  const open = allOpen.length > 40 ? "*" : allOpen.join("|");
  const p = new URLSearchParams();
  p.set("cols", cols); if (open) p.set("open", open);
  const pv = Object.keys(persp).filter(a=>persp[a]).join("|"); if (pv) p.set("persp", pv);
  const o = [opts.thoughts&&"th", opts.reactions&&"re", opts.steprows&&"sr", "pk:"+opts.peek].filter(Boolean).join("|");
  p.set("o", o);
  history.replaceState(null,"","#"+p.toString());
}
const persp = {};   // agent -> perspective mode on (full "what it saw / read / thought / did" cards)
// CoT ⇄ events readability options (2026-09-03): thoughts under posts, reactions under
// messages, steps as their own rows, which part of a turn the card peeks at.
const opts = {thoughts:true, reactions:true, steprows:false, peek:"start"};
const openSet = readHash();
const isOpen = oid => openSet.has(oid) || openSet.has("*");

/* ---------- header ---------- */
document.getElementById("title").textContent = D.meta.run_id || "run";
document.title = (D.meta.run_id||"run") + " · board";
{
  const m = D.meta, bits=[];
  if (m.model) bits.push(m.model);
  if (m.outcome) bits.push("outcome: "+m.outcome);
  if (m.score) bits.push("assignments "+(m.score.complete?"complete":"incomplete")+
    (m.score.valid===false?" (invalid)":m.score.valid?" (valid)":""));
  if (m.assignments) bits.push(Object.entries(m.assignments)
    .map(([k,v])=>`${k}→${v??"—"}`).join("  "));
  document.getElementById("meta").textContent = bits.join("   ·   ");
}
function renderChips(){
  const el = document.getElementById("chips"); el.innerHTML="";
  const groups = [["Board", c=>c.kind==="board"],
                  ["Channels", c=>c.kind==="conv" && !c.label.startsWith("dm:")],
                  ["DMs",      c=>c.kind==="conv" && c.label.startsWith("dm:")],
                  ["Agent turns", c=>c.kind==="agent"]];
  const pg = document.createElement("span"); pg.className="grp"; pg.textContent="Perspective"; el.appendChild(pg);
  for (const a of D.roster){
    const b = document.createElement("span"); b.className = "chip"+(persp[a]?" on":"");
    b.textContent = a; b.title = `show ${a}'s turns as what the assistant saw, read, thought and did, in order`;
    if (persp[a]) b.style.color = agentColor(a);
    b.onclick = ()=>{ persp[a]=!persp[a]; renderChips(); renderGrid(); writeHash(); };
    el.appendChild(b);
  }
  const og = document.createElement("span"); og.className="grp"; og.textContent="CoT ⇄ events"; el.appendChild(og);
  const optChip = (label, title, on, fn, act) => {
    const b = document.createElement("span"); b.className = "chip"+(on?" on":"")+(act?" act":"");
    b.textContent = label; b.title = title; b.onclick = fn; el.appendChild(b);
  };
  optChip("💭 thoughts under posts", "under each assistant post: the tail of the reasoning that produced it (click a block for the full step)",
    opts.thoughts, ()=>{ opts.thoughts=!opts.thoughts; renderChips(); renderGrid(); writeHash(); });
  optChip("↳ reactions", "under each message: what every assistant it woke then posted (or that it posted nothing)",
    opts.reactions, ()=>{ opts.reactions=!opts.reactions; renderChips(); renderGrid(); writeHash(); });
  optChip("steps as rows", "split each turn into one card per step, placed at the clock time the step ended — thoughts line up with the messages around them",
    opts.steprows, ()=>{ opts.steprows=!opts.steprows; renderChips(); renderGrid(); writeHash(); });
  optChip("peek: "+opts.peek, "what the turn card previews: start = opening thoughts, end = closing thoughts, post = thoughts right before its first post",
    true, ()=>{ opts.peek = {start:"end", end:"post", post:"start"}[opts.peek]; renderChips(); renderGrid(); writeHash(); });
  optChip("expand all CoT", "open every turn's CoT in the visible agent columns", false,
    ()=>{ document.querySelectorAll(".turnc details.cot").forEach(d=>d.open=true); writeHash(); }, true);
  optChip("collapse all", "close every open CoT / thought block", false,
    ()=>{ document.querySelectorAll("details.cot[open]").forEach(d=>d.open=false); document.querySelectorAll(".thought.full").forEach(d=>d.classList.remove("full")); writeHash(); }, true);
  const mkChip = c => {
    const b = document.createElement("span");
    b.className = "chip"+(c.on?" on":""); b.dataset.col=c.id;
    const lbl = c.kind==="conv" && c.label.startsWith("dm:")
      ? c.label.slice(3).replace("+"," ⇄ ") : c.label;
    b.innerHTML = esc(lbl)+`<span class="n">${c.n}</span>`;
    if (c.kind==="agent") b.style.color = c.on ? agentColor(c.label.split(" ")[0]) : "";
    b.onclick = ()=>{ c.on=!c.on; renderChips(); renderGrid(); writeHash(); };
    return b;
  };
  for (const [name, f] of groups){
    const g = document.createElement("span"); g.className="grp"; g.textContent=name;
    el.appendChild(g);
    const all = colDefs.filter(f);
    const quiet = all.filter(c=>c.kind==="conv" && !c.n && !c.on);
    for (const c of all.filter(c=>!quiet.includes(c))) el.appendChild(mkChip(c));
    if (quiet.length){
      if (showQuiet[name]) for (const c of quiet) el.appendChild(mkChip(c));
      else {
        const b = document.createElement("span"); b.className="chip";
        b.textContent = `+${quiet.length} quiet`;
        b.onclick = ()=>{ showQuiet[name]=true; renderChips(); };
        el.appendChild(b);
      }
    }
  }
}
const showQuiet = {};

/* ---------- cell builders ---------- */
function stepHtml(t, s, flagStep, extraCls){
  const isPost = s.calls.some(c=>c.post_ts) || s.step===flagStep;
  const k = t.steps.indexOf(s)+1;
  let h = `<div class="step${isPost?" post":""}${extraCls?" "+extraCls:""}"><div class="sh">step ${k}/${t.steps.length}` +
          `${s.calls.some(c=>c.post_ts)?" · posts":""}<span class="st">${esc(s.time||"")}</span></div>`;
  if (s.reasoning) h += `<div class="reason">${esc(s.reasoning)}</div>`;
  for (const c of s.calls)
    h += `<details class="tc"><summary>${c.t?`<span class="ct">${esc(c.t)}</span>`:""}${esc(c.line)}</summary><pre>${esc(c.detail)}</pre></details>`;
  if (s.text) h += `<div class="stext">${esc(s.text)}</div>`;
  return h+"</div>";
}
// a whole turn's steps with one step in focus: the focus step open and flagged, the others
// folded to one line each (step k · time · calls · first words) so the sequence stays visible
function stepsFocused(t, focus, cls){
  return t.steps.map(s=>{
    if (s===focus) return stepHtml(t, s, -1, cls);
    const k = t.steps.indexOf(s)+1, pv = (s.reasoning||s.text||"").replace(/\s+/g," ").slice(0,90);
    const calls = s.calls.length ? ` · ${s.calls.map(c=>c.post_ts?"✎":c.fetched!==undefined?"👁":"⚙").join("")}` : "";
    return `<details class="stp"><summary>step ${k}/${t.steps.length} · ${esc(s.time||"")}${calls}` +
           `${pv?` · <span class="pv">${esc(pv)}…</span>`:""}</summary>${stepHtml(t, s, -1)}</details>`;
  }).join("");
}
function cotHtml(t, oid, flagStep, only){
  const steps = only || t.steps;
  let h = `<details class="cot" data-oid="${oid}" ${isOpen(oid)?"open":""}>`+
          `<summary>CoT · ${steps.length}${only?"/"+t.steps.length:""} step${t.steps.length!==1?"s":""}</summary>`+
          `<div class="cotbody">`;
  if (t.ask) h += `<div class="askfull"><b>principal:</b> ${esc(t.ask)}</div>`;
  for (const s of steps) h += stepHtml(t, s, flagStep);
  if (t.report && !only) h += `<div class="report"><b>→ principal:</b> ${esc(t.report)}</div>`;
  h += "</div></details>";
  return h;
}
const postStepOf = t => t.steps.find(s=>s.calls.some(c=>c.post_ts));
function peekText(t){
  if (!t.steps.length) return "";
  if (opts.peek==="end"){ const s=[...t.steps].reverse().find(s=>s.reasoning); return s ? "…"+s.reasoning.slice(-220) : ""; }
  if (opts.peek==="post"){ const s=postStepOf(t); if (s && s.reasoning) return "…"+s.reasoning.slice(-220); }
  const s = t.steps.find(s=>s.reasoning); return s ? s.reasoning.slice(0,220) : "";
}
const msgByTs = Object.fromEntries(D.msgs.map(m=>[m.ts, m]));
const convLabel = Object.fromEntries(D.convs.map(c=>[c.id, c.label]));
const IMPORTANT = new Set(D.important_convs || []);
function pmsg(m, tag){
  const who = m.user in (D.id_names||{}) ? D.id_names[m.user] : m.user;
  const conv = convLabel[m.conv] || m.conv;
  const star = IMPORTANT.has(m.conv) ? " ★" : "";
  return `<div class="pmsg${IMPORTANT.has(m.conv)?" imp":""}"><span class="pm">${esc(tag)} ${esc(conv)}${star} · <b>${esc(who)}</b>${m.time?" · "+esc(m.time):""}</span><div>${esc(m.text)}</div></div>`;
}
function perspectiveBody(t){
  let h = "";
  // 1. what woke it
  if (["wake","added"].includes(t.kind)){
    const ms = (t.wake_batch||[]).map(ts=>msgByTs[ts]).filter(Boolean);
    h += `<div class="psec">saw · ${ms.length} event${ms.length!==1?"s":""}</div>` + ms.map(m=>pmsg(m,"⚡")).join("");
  } else {
    h += `<div class="psec">${esc(t.kind)} from principal</div><div class="askfull">${esc(t.ask)}</div>`;
  }
  // 2. steps: reasoning, then what each call showed it / did
  for (const s of t.steps){
    h += `<div class="psec">step ${s.step}</div>`;
    if (s.reasoning) h += `<div class="reason">${esc(s.reasoning)}</div>`;
    for (const c of s.calls){
      if (c.fetched !== undefined){
        const byConv = {};
        for (const m of c.fetched) (byConv[m.conv] ??= []).push(m);
        const convs = Object.keys(byConv);
        h += `<div class="pcall">${esc(c.line)}${c.seen_n?` · ${c.seen_n} already seen`:""}${convs.length?"":" · nothing new"}</div>`;
        for (const cv of convs) h += byConv[cv].map(m=>pmsg({...m, time:""},"👁")).join("");
      } else if (c.post_ts && msgByTs[c.post_ts]){
        h += pmsg(msgByTs[c.post_ts], "✎ posted");
      } else {
        h += `<div class="pcall">${esc(c.line)}</div>`;
      }
    }
    if (s.text) h += `<div class="stext">${esc(s.text)}</div>`;
  }
  const nh = notesHtml(t);
  if (nh) h += `<div class="psec">→ ${esc(t.agent)} (private)</div>` + nh;
  return h;
}
function turnCard(t){
  if (persp[t.agent]){
    return `<div class="card turnc persp" id="turn-${t.i}" data-tid="${t.i}"
      data-msgs="${[t.wake_ts, ...t.posts].filter(Boolean).join(" ")}" data-reads="${t.read_ts.join(" ")}"
      style="border-left-color:${agentColor(t.agent)}">
      <div class="hd"><b style="color:${agentColor(t.agent)}">${esc(t.agent)}</b>
        <span>${esc(t.src)}</span><span class="t">${t.time}–${t.end}</span></div>
      ${perspectiveBody(t)}</div>`;
  }
  const silent = !t.posts.length && !t.reads.length && t.steps.length<=1 &&
                 !["ask","debrief"].includes(t.kind);
  const relMsgs = [t.wake_ts, ...t.posts].filter(Boolean).join(" ");
  const pk = peekText(t);
  const peek = pk ? `<div class="peek">${esc(pk)}</div>` : "";
  const rline = t.reads.length ? `<div class="rline">${esc(t.reads.join(" · "))}</div>` : "";
  const rep = opts.steprows ? "" : notesHtml(t);
  // steps-as-rows: the turn card carries only its first step; later steps get their own
  // cards at their own clock rows (stepCard)
  const split = opts.steprows && t.steps.length > 1;
  return `<div class="card turnc${silent?" silent":""}" id="turn-${t.i}" data-tid="${t.i}"
    data-msgs="${relMsgs}" data-reads="${t.read_ts.join(" ")}"
    style="border-left-color:${agentColor(t.agent)}">
    <div class="hd"><b style="color:${agentColor(t.agent)}">${esc(t.agent)}</b>
      <span>${esc(t.src)}</span><span class="t">${t.time}${split?" · step 1/"+t.steps.length:""}</span></div>
    ${rline}${split?"":peek}${rep}${cotHtml(t, "t"+t.i, -1, split ? [t.steps[0]] : null)}</div>`;
}
// every private message this turn sent its employee, in order: the notify_user pushes and
// the text the assistant wrote outside a tool call. Full text, no truncation — this is the
// only surface the employee sees, so it is on by default in the agent column.
function notesHtml(t){
  const parts = [], n = t.steps.length;
  t.steps.forEach((s, k) => {
    for (const c of s.calls) if (c.tool === "notify_user" && (c.push||"").trim())
      parts.push({k, text:c.push, push:true});
    if ((s.text||"").trim()) parts.push({k, text:s.text, push:false});
  });
  if (!parts.length && (t.report||"").trim()) parts.push({k:-1, text:t.report, push:false});
  return parts.map(p => {
    const step = n > 1 && p.k >= 0 ? ` · step ${p.k+1}/${n}` : "";
    const tag = p.push ? `\ud83d\udcf2 push \u2192 ${esc(t.agent)}` : `\u2192 ${esc(t.agent)}`;
    return `<div class="report${p.push?" push":""}" title="${p.push?"notify_user: a push to the employee's phone":"text written outside a tool call: a private message only the employee sees"}"><b>${tag}${step}:</b> ${esc(p.text)}</div>`;
  }).join("");
}
function postMark(t, k, st, m){
  const n = t.steps.length, tail = st && st.reasoning ? (st.reasoning.length>240 ? "…"+st.reasoning.slice(-240) : st.reasoning) : "";
  const lbl = convLabel[m.conv] || m.conv;
  return `<div class="card postmark" data-tid="${t.i}" data-msgs="${m.ts}" style="border-left-color:${agentColor(t.agent)}"
    onclick="if(!event.target.closest('.jump'))this.classList.toggle('full')" title="click: full reasoning of this step">
    <div class="hd">✎ <b style="color:${agentColor(t.agent)}">${esc(t.agent)}</b><span>step ${k+1}/${n} · posted → ${esc(lbl)}</span>
      <span class="t">${esc(m.time)}</span><span class="jump" data-jump="${t.i}">#${t.i} ↗</span></div>
    ${tail ? `<div class="tail">${esc(tail)}</div><div class="fulltxt">${esc(st.reasoning)}</div>` : `<div class="tail" style="opacity:.6">(no reasoning recorded for this step)</div>`}</div>`;
}
function stepCard(t, k){
  const s = t.steps[k], last = k===t.steps.length-1;
  const posts = s.calls.filter(c=>c.post_ts && msgByTs[c.post_ts]).map(c=>c.post_ts);
  const lines = s.calls.map(c=>`<div class="cl${c.post_ts?" post":""}">${c.post_ts?"✎":c.fetched!==undefined?"👁":"⚙"} ${esc(c.line)}</div>`).join("");
  const rep = "";   // notes render inline below, per step, in the same prominent style
  return `<div class="card turnc stepc" id="turn-${t.i}-s${k}" data-tid="${t.i}" data-msgs="${posts.join(" ")}"
    style="border-left-color:${agentColor(t.agent)}">
    <div class="hd"><b style="color:${agentColor(t.agent)}">${esc(t.agent)}</b>
      <span>step ${k+1}/${t.steps.length}${last?" · ends turn":""}</span><span class="t">${esc(s.time||"")}</span>
      <span class="jump" data-jump="${t.i}" title="to the turn's first card">#${t.i} ↗</span></div>
    ${s.reasoning?`<div class="reason">${esc(s.reasoning)}</div><span class="more" onclick="this.closest('.stepc').classList.toggle('full');this.textContent=this.closest('.stepc').classList.contains('full')?'less':'more'">more</span>`:""}
    ${lines}${notesHtml({...t, steps:[s], report:""})}${rep}</div>`;
}
function msgCard(m, mi){
  const t = m.src ? D.turns[m.src.turn] : null;
  const cls = "card msg"+(t?"":" scripted");
  const color = t ? agentColor(t.agent) : (AGENTS.has(m.user)?agentColor(m.user):"var(--human)");
  const secs = s => s.split(":").reduce((a,x)=>a*60+ +x, 0);
  // every other assistant that saw this message: woken by it, or fetched it via history.
  // Each gets an expandable CoT of that turn (filled lazily on first open), so you can read
  // what Nadia thought when Priya's post reached her.
  const seenBy = [
    ...m.wakes.map(i=>({i, kind:"⚡", time:D.turns[i].time, wake:true})),
    ...(m.reads||[]).map(x=>({i:x.turn, kind:"👁", time:x.time, wake:false})),
  ].filter(w=>D.turns[w.i].agent!==m.user);
  // one chip per person: the first time its assistant saw the message; later re-fetches
  // are listed inside the chip ("also in #30 #33") — 2026-09-03
  seenBy.sort((a,b)=>secs(a.time)-secs(b.time) || a.i-b.i);
  const firstBy = {}, laterBy = {};
  for (const w of seenBy){ const a=D.turns[w.i].agent; if (!firstBy[a]) firstBy[a]=w; else if (w.i!==firstBy[a].i) (laterBy[a] ??= new Set()).add(w.i); }
  const seenU = Object.values(firstBy);
  const others = seenU.length
    ? `<div class="wline seen">${seenU.map(w=>{
        const t2 = D.turns[w.i], oid = `x${mi}-${w.i}`, later = [...(laterBy[t2.agent]||[])];
        const lag = secs(w.time)-secs(m.time);
        const lagTxt = lag>=90 ? `+${Math.round(lag/60)}m` : lag>=5 ? `+${lag}s` : w.wake ? "" : w.time.slice(0,5);
        return `<details class="cot xcot" data-oid="${oid}" data-turn="${w.i}" data-conv="${esc(m.conv||"")}" data-later="${later.join(" ")}" ${isOpen(oid)?"open":""}>`+
          `<summary style="border-color:${agentColor(t2.agent)}">${w.kind} ${esc(t2.agent)} ${lagTxt} · ${t2.steps.length} step${t2.steps.length!==1?"s":""}${later.length?` <span class="again">+${later.length}×</span>`:""}</summary>`+
          `<div class="cotbody"></div></details>`;
      }).join(" ")}</div>`
    : `<div class="wline nobody">👁 read by nobody</div>`;
  // what each woken assistant then did in that turn: its posts, or nothing
  let react = "";
  if (opts.reactions && m.wakes.length){
    react = [...new Set(m.wakes)].map(i=>{
      const t2 = D.turns[i];
      const acts = [];
      for (const s of t2.steps) for (const c of s.calls){
        if (c.post_ts && msgByTs[c.post_ts]){ const pm = msgByTs[c.post_ts];
          acts.push(`✎ ${esc(convLabel[pm.conv]||pm.conv)} ${pm.time.slice(0,5)} <span class="q">“${esc(pm.text.replace(/\s+/g," ").slice(0,110))}${pm.text.length>110?"…":""}”</span>`); }
        else if (c.tool==="board_assign") acts.push(`⚙ ${esc(c.line)}`);
      }
      const col = agentColor(t2.agent);
      if (!acts.length) return `<div class="react none">↳ <b style="color:${col}">${esc(t2.agent)}</b> · posted nothing <span class="jump" data-jump="${i}">↗</span></div>`;
      return acts.map((a,k)=>`<div class="react">↳ <b style="color:${col}">${esc(t2.agent)}</b> ${a}${k===0?` <span class="jump" data-jump="${i}">↗</span>`:""}</div>`).join("");
    }).join("");
  }
  let cot = "", thought = "";
  if (t){
    const step = t.steps.find(s=>s.calls.some(c=>c.post_ts===m.ts)) || t.steps[t.steps.length-1];
    const k = t.steps.indexOf(step)+1, after = t.steps.length - k;
    if (opts.thoughts && step && step.reasoning){
      const tail = step.reasoning.length > 320 ? "…"+step.reasoning.slice(-320) : step.reasoning;
      thought = `<div class="thought" onclick="if(!event.target.closest('.jump'))this.classList.toggle('full')" title="click: full reasoning of this step">`+
        `<div class="th">💭 <b>${esc(t.agent)}</b> · step ${k}/${t.steps.length} · ${esc(step.time||"")} · ${after?`+${after} step${after!==1?"s":""} after`:"ends turn"} <span class="jump" data-jump="${t.i}">↗</span></div>`+
        `<div class="tail">${esc(tail)}</div><div class="fulltxt">${esc(step.reasoning)}</div></div>`;
    }
    cot = `<details class="cot" data-oid="m${mi}" ${isOpen("m"+mi)?"open":""}>`+
      `<summary>CoT · step ${k}/${t.steps.length}</summary><div class="cotbody">`+
      `<div class="sh" style="font-size:10.5px;color:var(--dim)">${esc(t.agent)}'s turn ` +
      `<span class="jump" data-jump="${t.i}">#${t.i} ↗</span> · ${esc(t.time)}–${esc(t.end)}` +
      (t.wake_batch?.length ? ` · woken by ${t.wake_batch.length} event${t.wake_batch.length!==1?"s":""}` : "") + `</div>`+
      (t.ask ? `<div class="askfull"><b>principal:</b> ${esc(t.ask)}</div>` : "")+
      stepsFocused(t, step, "post")+
      (t.report?`<div class="report"><b>→ principal:</b> ${esc(t.report)}</div>`:"")+
      `</div></details>`;
  }
  return `<div class="${cls}" data-mid="${m.ts}" data-src="${t?t.i:""}"
    data-wakes="${m.wakes.join(" ")}" style="border-left-color:${color}">
    <div class="hd"><b style="color:${color}">${esc(m.user)}</b>${t?"":"<span>scripted</span>"}
      <span class="t">${m.time}</span></div>
    <div class="body">${esc(m.text)}</div>${thought}${others}${react}${cot}</div>`;
}

/* ---------- grid ---------- */
function renderGrid(){
  const grid = document.getElementById("grid");
  const cols = colDefs.filter(c=>c.on);
  const colIdx = Object.fromEntries(cols.map((c,i)=>[c.id, i+2]));

  // assemble logical rows: spacers + markers interleaved by epoch
  const items = [];
  let markers = [...D.markers].sort((a,b)=>a.epoch-b.epoch);
  let prev = null;
  // steps-as-rows: every step after the first becomes an atom of its own at the clock it ended
  let rows = D.rows;
  if (opts.steprows){
    const byEp = {};
    for (const r of D.rows) byEp[r.epoch] = {...r, steps: []};
    for (const t of D.turns){
      if (persp[t.agent]) continue;
      t.steps.forEach((s, k)=>{
        if (k===0 || s.epoch===undefined) return;
        (byEp[s.epoch] ??= {epoch:s.epoch, time:s.time, turns:[], msgs:[], marks:[], steps:[]}).steps.push([t.i, k]);
      });
    }
    rows = Object.values(byEp).sort((a,b)=>a.epoch-b.epoch);
  }
  for (const r of rows){
    const visible = r.turns.some(ti=>colIdx["a:"+D.turns[ti].agent]) ||
                    (r.steps||[]).some(([ti])=>colIdx["a:"+D.turns[ti].agent]) ||
                    r.msgs.some(mi=>colIdx["c:"+D.msgs[mi].conv]) ||
                    (r.marks||[]).some(m=>colIdx["c:"+m.conv]);
    if (!visible) continue;   // row lives entirely in toggled-off columns
    while (markers.length && markers[0].epoch <= r.epoch)
      items.push({marker: markers.shift()});
    if (prev!==null && r.epoch - prev > D.gap_s)
      items.push({gap: Math.round((r.epoch-prev)/60)});
    items.push({row: r}); prev = r.epoch;
  }
  for (const m of markers) items.push({marker: m});

  const nrows = items.length + 2;   // header + backlog
  grid.style.gridTemplateColumns = "76px " + cols.map(c=>c.kind==="board" ? "60px" : "minmax(250px, 340px)").join(" ");
  let h = "";
  for (const c of cols)
    h += `<div class="stripe" style="grid-column:${colIdx[c.id]};grid-row:1/${nrows+1}"></div>`;
  h += `<div class="colhead rule" style="grid-row:1;grid-column:1">clock</div>`;
  for (const c of cols){
    if (c.kind==="board"){
      h += `<div class="colhead bhead" style="grid-row:1;grid-column:${colIdx[c.id]}" title="board claims: T1 lane left, T2 lane right; one bar per person, in their colour, from claim to release/replacement">T1 · T2</div>`;
      continue;
    }
    const lbl = c.kind==="conv" && c.label.startsWith("dm:")
      ? c.label.slice(3).replace("+"," ⇄ ") : c.label;
    const col = c.kind==="agent" ? `color:${agentColor(c.label.split(" ")[0])};` : "";
    h += `<div class="colhead" style="${col}grid-row:1;grid-column:${colIdx[c.id]}">${esc(lbl)}</div>`;
  }
  // backlog row
  h += `<div class="rule" style="grid-row:2;grid-column:1">history</div>`;
  for (const c of cols){
    if (c.kind!=="conv") continue;
    const bl = D.backlog[c.id.slice(2)] || [];
    if (!bl.length) continue;
    h += `<div class="cell bkcell" style="grid-row:2;grid-column:${colIdx[c.id]}">
      <details><summary>${bl.length} earlier message${bl.length!==1?"s":""}</summary>
      ${bl.map(m=>msgCard(m,"b"+m.ts)).join("")}</details></div>`;
  }
  // body rows
  const rowIdx = [];                       // rendered data rows: {gr, epoch}
  const grByTurn = {};                     // turn i -> grid row of its cell
  items.forEach((it, k)=>{
    const gr = k+3;
    if (it.gap){ h += `<div class="spacer" style="grid-row:${gr}">+${it.gap} min</div>`; return; }
    if (it.marker){
      h += `<div class="marker" style="grid-row:${gr}">${esc(it.marker.label)} · ${it.marker.time}</div>`;
      return;
    }
    const r = it.row;
    rowIdx.push({gr, epoch: r.epoch});
    for (const ti of r.turns) grByTurn[ti] = gr;
    h += `<div class="rule" style="grid-row:${gr};grid-column:1">${r.time}</div>`;
    const byCol = {};
    for (const ti of r.turns){
      const id = "a:"+D.turns[ti].agent;
      if (colIdx[id]) (byCol[id] ??= []).push(turnCard(D.turns[ti]));
    }
    for (const [ti, k] of (r.steps||[])){
      const id = "a:"+D.turns[ti].agent;
      if (colIdx[id]) (byCol[id] ??= []).push(stepCard(D.turns[ti], k));
    }
    for (const m of (r.marks||[])){
      const id = "c:"+m.conv;
      if (colIdx[id]) (byCol[id] ??= []).push(
        `<div class="card readmark${m.important?" imp":""}" data-tid="${m.turn}" title="turn ${m.turn}: ${esc(m.agent)}'s assistant fetched ${m.n} messages here${m.important?" — layered material lives in this conversation":""}" onclick="document.getElementById('turn-${m.turn}')?.scrollIntoView({block:'center'})" style="border-left-color:${agentColor(m.agent)}">👁 <b style="color:${agentColor(m.agent)}">${esc(m.agent)}</b> read ${m.n}${m.important?" ★":""}</div>`);
    }
    for (const mi of r.msgs){
      const id = "c:"+D.msgs[mi].conv;
      if (colIdx[id]) (byCol[id] ??= []).push(msgCard(D.msgs[mi], mi));
    }
    // post marks: turn cards sit at turn start, so a post made in step 8 of a 20-minute
    // turn would otherwise leave the agent column empty at its own height. One mark per
    // (turn, step) per row; "steps as rows" mode has the step card there instead.
    if (!opts.steprows){
      const done = new Set();
      for (const mi of r.msgs){
        const m = D.msgs[mi]; if (!m.src) continue;
        const t = D.turns[m.src.turn], key = t.i+"/"+m.src.step;
        if (done.has(key) || r.turns.includes(t.i) || persp[t.agent]) continue;
        done.add(key);
        const id = "a:"+t.agent; if (!colIdx[id]) continue;
        const k = t.steps.findIndex(s=>s.step===m.src.step), st = t.steps[k];
        (byCol[id] ??= []).push(postMark(t, k, st, m));
      }
    }
    for (const [id, cards] of Object.entries(byCol))
      h += `<div class="cell" style="grid-row:${gr};grid-column:${colIdx[id]}">${cards.join("")}</div>`;
  });
  // active-period bars: one per turn, spanning its lane from start row to the last
  // rendered row that falls inside [t0, t1]
  for (const t of D.turns){
    const col = colIdx["a:"+t.agent], gr0 = grByTurn[t.i];
    if (!col || !gr0) continue;
    let gr1 = gr0;
    for (const {gr, epoch} of rowIdx)
      if (epoch > t.t0 && epoch <= t.t1 && gr > gr1) gr1 = gr;
    h += `<div class="tbar" data-jump="${t.i}" style="grid-column:${col};`+
      `grid-row:${gr0}/${gr1+1};background:${agentColor(t.agent)}" `+
      `title="${esc(t.agent)} · ${t.time} → ${t.end}"></div>`;
  }
  // board lanes: for each person, intervals between successive claims; T1/T2 get a bar,
  // "skip"/anything else ends the previous bar
  const bcol = colIdx["board"];
  if (bcol && rowIdx.length){
    const lastGr = rowIdx[rowIdx.length-1].gr;
    h += `<div class="bdiv" style="grid-column:${bcol};grid-row:2/${nrows+1};margin-left:29px"></div>`;
    const byAgent = {};
    for (const e of (D.board_events||[])) (byAgent[e.agent] ??= []).push(e);
    for (const [agent, evs] of Object.entries(byAgent)){
      const p = Math.max(0, D.roster.indexOf(agent));
      for (let k = 0; k < evs.length; k++){
        const e = evs[k], nxt = evs[k+1];
        if (e.task !== "T1" && e.task !== "T2") continue;
        const r0 = rowIdx.find(x=>x.epoch >= e.epoch);
        const gr0 = r0 ? r0.gr : lastGr;
        let gr1 = lastGr;
        if (nxt){ const before = rowIdx.filter(x=>x.epoch < nxt.epoch); gr1 = before.length ? before[before.length-1].gr : gr0; }
        if (gr1 < gr0) gr1 = gr0;
        const lane = e.task === "T1" ? 0 : 1;
        h += `<div class="bbar" style="grid-column:${bcol};grid-row:${gr0}/${gr1+1};margin-left:${3 + lane*31 + p*5}px;background:${agentColor(agent)}" `+
             `title="${esc(agent)} on ${e.task} ${e.time}${nxt ? " → " + nxt.time + (nxt.task==="T1"||nxt.task==="T2" ? " (→ "+nxt.task+")" : " (released)") : " → end"}"></div>`;
      }
    }
  }
  grid.innerHTML = h;
}

/* ---------- cross-highlight + interactions ---------- */
function related(el){
  const ids = new Set([el]);
  const sel = [];
  if (el.dataset.tid !== undefined && el.dataset.tid !== ""){
    for (const ts of (el.dataset.msgs||"").split(" ").filter(Boolean))
      sel.push(`[data-mid="${ts}"]`);
    for (const ts of (el.dataset.reads||"").split(" ").filter(Boolean))
      sel.push(`[data-mid="${ts}"]`);
  }
  if (el.dataset.mid){
    if (el.dataset.src) sel.push(`#turn-${el.dataset.src}`);
    for (const t of (el.dataset.wakes||"").split(" ").filter(Boolean))
      sel.push(`#turn-${t}`);
  }
  for (const s of sel) document.querySelectorAll(s).forEach(x=>ids.add(x));
  return ids;
}
let hovered = [];
document.addEventListener("mouseover", e=>{
  const el = e.target.closest?.(".card"); if (!el) return;
  hovered.forEach(x=>x.classList.remove("hl")); hovered=[];
  for (const x of related(el)){ x.classList.add("hl"); hovered.push(x); }
});
document.addEventListener("mouseout", e=>{
  if (!e.target.closest?.(".card")) return;
  hovered.forEach(x=>x.classList.remove("hl")); hovered=[];
});
document.addEventListener("click", e=>{
  const j = e.target.closest?.("[data-jump]");
  if (j){
    const col = colById["a:"+D.turns[+j.dataset.jump].agent];
    if (!col.on){ col.on=true; renderChips(); renderGrid(); writeHash(); }
    const t = document.getElementById("turn-"+j.dataset.jump);
    if (t){ t.scrollIntoView({block:"center"});
      t.querySelector("details.cot")?.setAttribute("open","");
      t.classList.add("pin"); setTimeout(()=>t.classList.remove("pin"), 2500); }
    return;
  }
  if (e.target.closest?.("details")) return;   // let expanders behave
  const el = e.target.closest?.(".card"); if (!el) return;
  const on = !el.classList.contains("pin");
  document.querySelectorAll(".pin").forEach(x=>x.classList.remove("pin"));
  if (on) for (const x of related(el)) x.classList.add("pin");
});
function fillX(det){
  const body = det.querySelector(".cotbody");
  if (!body || body.dataset.filled) return;
  body.dataset.filled = "1";
  const t = D.turns[+det.dataset.turn], conv = det.dataset.conv;
  const hit = t.steps.find(s=>s.calls.some(c=>c.conv && c.conv===conv)) || t.steps[0];
  let h = `<div class="sh" style="font-size:10.5px;color:var(--dim)">${esc(t.agent)}'s turn ` +
          `<span class="jump" data-jump="${t.i}">#${t.i} ↗</span> · ${esc(t.time)}` +
          (t.wake_batch?.length ? ` · woken by ${t.wake_batch.length} event${t.wake_batch.length!==1?"s":""}` : "") +
          ((det.dataset.later||"").split(" ").filter(Boolean).length ? ` · fetched again in ` + det.dataset.later.split(" ").filter(Boolean).map(i=>`<span class="jump" data-jump="${i}">#${i} ↗</span>`).join(" ") : "") + `</div>`;
  if (t.ask) h += `<div class="askfull"><b>principal:</b> ${esc(t.ask)}</div>`;
  h += stepsFocused(t, hit, "hit");
  if (t.report) h += `<div class="report"><b>→ principal:</b> ${esc(t.report)}</div>`;
  body.innerHTML = h;
  if (namesOn) applyNames();
  const el = body.querySelector(".step.hit"); if (el && hit!==t.steps[0]) el.scrollIntoView({block:"nearest"});
}
document.addEventListener("toggle", e=>{
  if (e.target.matches?.("details.xcot") && e.target.open) fillX(e.target);
  if (e.target.matches?.("details.cot")) writeHash();
}, true);

// ids → names: wrap known Slack ids in text nodes (TreeWalker, so attributes and scripts
// are never touched), then swap id ⟷ ⟨name⟩. The grid is re-rendered by chip toggles and
// expanders, so the overlay is re-applied after every render while it is on.
const SLACK_IDS = D.id_names || {};
let namesOn = false;
function wrapIds(root){
  const keys = Object.keys(SLACK_IDS); if (!keys.length) return;
  const re = new RegExp('\\b(' + keys.join('|') + ')\\b', 'g');
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const nodes = []; let n;
  while (n = w.nextNode()) { re.lastIndex = 0; if (n.nodeValue && re.test(n.nodeValue)) nodes.push(n); }
  for (const node of nodes) {
    const p = node.parentElement; if (!p || p.closest('script,style') || p.classList.contains('slackid')) continue;
    const s = node.nodeValue, frag = document.createDocumentFragment(); let last = 0, m; re.lastIndex = 0;
    while (m = re.exec(s)) {
      frag.appendChild(document.createTextNode(s.slice(last, m.index)));
      const sp = document.createElement('span'); sp.className = 'slackid';
      sp.dataset.id = m[0]; sp.dataset.name = SLACK_IDS[m[0]]; sp.title = m[0]; sp.textContent = m[0];
      frag.appendChild(sp); last = m.index + m[0].length;
    }
    frag.appendChild(document.createTextNode(s.slice(last))); node.parentNode.replaceChild(frag, node);
  }
}
function applyNames(){
  if (namesOn) wrapIds(document.body);
  document.querySelectorAll('.slackid').forEach(sp => {
    sp.textContent = namesOn ? '\u27e8' + sp.dataset.name + '\u27e9' : sp.dataset.id;
    sp.classList.toggle('named', namesOn); });
  document.getElementById('btn-names').textContent = namesOn ? 'names → ids' : 'ids → names';
}
function toggleNames(){ namesOn = !namesOn; applyNames(); }
const _renderGrid = renderGrid;
renderGrid = function(){ _renderGrid(); if (namesOn) applyNames(); };
document.addEventListener("toggle", () => { if (namesOn) applyNames(); }, true);

renderChips(); renderGrid();
</script></body></html>
"""


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        out = render(arg)
        print(out)


if __name__ == "__main__":
    main()
