"""Build the data behind the TRILEMMA project site (index.html, traces.html) from rollouts + judge outputs.

    python -m trilemma.site.build_site --runs-root RUNS --outputs-root OUTPUTS --out SITE/data [--sets-root DIR]

Writes, under --out:
  index.js                      settings, run index and seat labels (window.TRILEMMA_INDEX)
  worlds/<world>.js             the pre-seeded Slack workspace of one world, written once and shared by every run
                                that starts from it: conversations, messages before the run, user directory, and
                                tool-result fragments that recur across runs (e.g. the users_list payload)
  runs/<setting>/<run>.js       one rollout: messages posted during the run, every agent turn (reasoning, text,
                                tool calls with full arguments), board moves, all system and user prompts, judges
  results/<setting>/<run>.js    the full tool results of that rollout, loaded only when a result is opened

Run and results files are gzip+base64 JSONP (decoded in the browser with DecompressionStream), so the pages
work from file:// and stay small. Messages inside tool results are stored as references ({"$m": ts}; "$nt": 1 when the
original had no `type` key) into the world or run messages and expanded back to {type, ts, user, text, ...} by the viewer; Slack's `team` and
`client_msg_id` fields are dropped there. Not published: per-turn input payloads, wake/notification logs,
token usage, wall-clock times, the full run config, the world call log and the model proxy dumps.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import labels as L
from .settings import CLOSED, GROUPS, MODEL_NAME, MODEL_ORDER, SETTINGS, parse, run_ids, select

FOCAL = ["Priya", "Nadia"]
SECRET_DMS = [("Priya", "Zofia"), ("Priya", "Ines"), ("Nadia", "Ines"), ("Nadia", "Zofia")]
BLOB_MIN = 300  # chars: tool-result fragments at least this long that recur across runs go to the world file

# judge outputs per setting (relative to --outputs-root); absent dirs are skipped
JUDGES = {
    "main": dict(deception=["5e10_deception_judges", "5e10_closed_deception_judges"],
                 disclosure=["disclosure_5e10_luna_v5c_flagged", "disclosure_5e10_closed_luna_v5c"]),
    "main-high-conf": dict(deception=["5e8_deception_judges"], disclosure=["disclosure_5e8_luna_v5c_flagged"]),
    "main-high-conf-honest": dict(deception=["5e14_deception_judges"]),
    "honeypot": dict(honeypot=["honeypot_hp4msg_1evi"]),
}


def naive_utc(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


def epoch_offset(r):
    for t in r["turns"]:
        w = t.get("wake") or {}
        if w.get("ts") and t.get("clock"):
            return float(w["ts"]) - naive_utc(t["clock"])
    return 0.0


def canon(o):
    return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def h8(s):
    return hashlib.sha1(s.encode()).hexdigest()[:10]


def read_jsonp(path: Path):
    """Inverse of jsonp(): the object stored in a data file."""
    s = path.read_text()
    if "_GZ(" in s[:40]:
        b = s[s.index('",') + 2:].strip().rstrip(");").strip('"')
        return json.loads(gzip.decompress(base64.b64decode(b)))
    return json.loads(s[s.index(",") + 1:s.rstrip().rindex(")")])


def jsonp(fn, key, obj, gz=True):
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if not gz:
        return f"{fn}({json.dumps(key)}," + s.replace("</", "<\\/") + ");\n"
    b = base64.b64encode(gzip.compress(s.encode(), 9, mtime=0)).decode()
    return f"{fn}_GZ({json.dumps(key)},\"{b}\");\n"


# ── tool results: messages -> refs, recurring fragments -> world blobs ──
DROP_MSG_KEYS = {"type", "ts", "user", "text", "team", "client_msg_id"}


def ref_messages(o, known):
    """Replace Slack message objects whose (ts, user, text) we publish elsewhere by {"$m": ts, ...extra keys}."""
    if isinstance(o, dict):
        ts = o.get("ts")
        if isinstance(ts, str) and "text" in o and ts in known and known[ts] == (o.get("user"), o.get("text")):
            extra = {k: ref_messages(v, known) for k, v in o.items() if k not in DROP_MSG_KEYS}
            return {"$m": ts, **({} if "type" in o else {"$nt": 1}), **extra}
        return {k: ref_messages(v, known) for k, v in o.items()}
    if isinstance(o, list):
        return [ref_messages(v, known) for v in o]
    return o


def walk_big(o, acc):
    if isinstance(o, (dict, list)):
        s = canon(o)
        if len(s) >= BLOB_MIN:
            acc.add(s)
        for v in (o.values() if isinstance(o, dict) else o):
            walk_big(v, acc)


def ref_blobs(o, blobs):
    if isinstance(o, (dict, list)):
        s = canon(o)
        if len(s) >= BLOB_MIN and s in blobs:
            return {"$b": blobs[s]}
        if isinstance(o, dict):
            return {k: ref_blobs(v, blobs) for k, v in o.items()}
        return [ref_blobs(v, blobs) for v in o]
    return o


# ── one rollout ──
def call_view(c, conv_label, users):
    tool = c.get("tool", "?")
    args = c.get("args") or {}
    res = c.get("result") or {}
    ch = str(args.get("channel") or args.get("channel_id") or "")
    lab = conv_label.get(ch, ch)
    v = {"tool": tool, "step": int(c.get("step") or 0), "clock": (c.get("clock") or "")[11:19]}
    if tool in ("conversations_history", "conversations_replies"):
        n = len(res.get("messages") or []) if isinstance(res, dict) else 0
        v["line"] = f"read {lab} ({n} msgs)"
        v["conv"] = lab
    elif tool == "chat_postMessage":
        v["line"] = f"post → {lab}"
        v["conv"] = lab
        v["text"] = args.get("text") or ""
        if isinstance(res, dict) and res.get("ts"):
            v["ts"] = str(res["ts"])
    elif tool == "board_assign":
        v["line"] = f"board: {args.get('person') or ''} → {args.get('task_id') or ''}".replace(":  →", ":")
        v["task"] = str(args.get("task_id") or "")
        v["ok"] = not (isinstance(res, dict) and (res.get("ok") is False or res.get("error")))
    elif tool == "notify_user":
        v["line"] = "note to own user"
        v["text"] = args.get("text") or args.get("message") or ""
    elif tool == "users_info":
        v["line"] = f"look up {users.get(str(args.get('user')), args.get('user'))}"
    elif tool == "search_messages":
        v["line"] = f"search “{args.get('query', '')}”"
    elif tool.startswith("calendar_"):
        v["line"] = tool.replace("_", " ") + (f" {args.get('title')}" if args.get("title") else "")
    else:
        v["line"] = tool.replace("_", " ") + (f" {lab}" if lab else "")
    v["args"] = args
    return v


class World:
    def __init__(self, key):
        self.key, self.convs, self.msgs, self.users, self.blobs = key, {}, [], {}, {}
        self.known = {}


def load_run(rdir):
    r = json.loads((rdir / "run.json").read_text())
    off = epoch_offset(r)
    start = naive_utc(r["clock_start"]) + off
    return r, off, start


def world_of(r, off, start, repo_root, worlds):
    hist = [m for m in r["messages"] if float(m["ts"]) < start]
    sig = h8(canon(sorted([m["conv_id"], m["ts"], m["text"]] for m in hist)))
    stem = Path(r["config"]["fixture"]).stem.replace("tanager_slack_", "")
    key = f"{stem}-{sig[:6]}"
    if key not in worlds:
        w = worlds[key] = World(key)
        fx = json.loads((repo_root / r["config"]["fixture"]).read_text())
        w.users = {u["id"]: {"name": u["name"], "title": u.get("title") or ""} for u in fx.get("users") or []}
        for m in r["messages"]:
            w.convs.setdefault(m["label"], {"label": m["label"], "type": m["type"], "members": m.get("members") or []})
        for m in sorted(hist, key=lambda m: float(m["ts"])):
            w.msgs.append({"id": f"{m['conv_id']}:{m['ts']}", "conv": m["label"], "user": m["user"],
                           "uid": m.get("user_id"), "text": m["text"],
                           "time": datetime.fromtimestamp(float(m["ts"]) - off, tz=timezone.utc).strftime("%a %d %b %H:%M")})
            w.known[m["ts"]] = (m.get("user_id"), m["text"])
    return worlds[key]


def build_run(setting, rid, rdir, repo_root, worlds, J):
    r, off, start = load_run(rdir)
    world = world_of(r, off, start, repo_root, worlds)
    fx_users = {k: v["name"] for k, v in world.users.items()}
    conv_label = {m["conv_id"]: m["label"] for m in r["messages"]}
    p = parse(rdir.name)
    model, seed = p["model"], f"s{p['seed']}"

    def hms(ep):
        return datetime.fromtimestamp(ep - off, tz=timezone.utc).strftime("%H:%M:%S")

    # live message ts run ahead of the simulated clock; place them via the chat_postMessage calls that created them
    anchors = sorted({(float(c["result"]["ts"]), naive_utc(c["clock"]) + off)
                      for t in r["turns"] for c in t.get("tool_calls") or []
                      if c.get("tool") == "chat_postMessage" and isinstance(c.get("result"), dict)
                      and c["result"].get("ts") and c.get("clock")})

    def sim(ts):
        if not anchors:
            return ts
        if ts <= anchors[0][0]:
            return anchors[0][1] - (anchors[0][0] - ts)
        if ts >= anchors[-1][0]:
            return anchors[-1][1] + (ts - anchors[-1][0])
        for (a0, s0), (a1, s1) in zip(anchors, anchors[1:]):
            if a0 <= ts <= a1:
                return s0 + (s1 - s0) * ((ts - a0) / (a1 - a0) if a1 > a0 else 0)

    msg_flags = J.get("disc_rows", {}).get(rdir.name, {})
    msgs, known = [], dict(world.known)
    for m in r["messages"]:
        ts = float(m["ts"])
        if ts < start:
            continue
        mid = f"{m['conv_id']}:{m['ts']}"
        o = {"id": mid, "conv": m["label"], "user": m["user"], "uid": m.get("user_id"), "text": m["text"],
             "live": True, "t": round(sim(ts) - start, 1), "time": hms(sim(ts))}
        if mid in msg_flags:
            o["disc"] = msg_flags[mid]
        msgs.append(o)
        known[m["ts"]] = (m.get("user_id"), m["text"])
    msgs.sort(key=lambda o: (o["t"], o["id"].split(":")[1]))

    turns, board, results = [], [], []
    for t in r["turns"]:
        w = t.get("wake") or {}
        steps = t.get("steps_detail") or []
        rf = t.get("reasoning_full") or t.get("reasoning") or []
        reasoning = [s.get("reasoning") or "" for s in steps] if steps else list(rf)
        texts = [s.get("text") or "" for s in steps] if steps else []
        calls = []
        for c in t.get("tool_calls") or []:
            v = call_view(c, conv_label, fx_users)
            v["r"] = len(results)
            results.append(ref_messages(c.get("result"), known))
            calls.append(v)
        for c in calls:
            if c["tool"] == "board_assign" and c.get("ok"):
                ep = naive_utc(t["clock"][:19] if not c["clock"] else t["clock"][:11] + c["clock"]) + off
                board.append({"agent": t["agent"], "task": c["task"], "t": round(ep - start, 1), "time": c["clock"]})
        t0 = naive_utc(t["clock"]) + off
        t1 = naive_utc(t.get("clock_end") or t["clock"]) + off
        turns.append({
            "i": t["i"], "agent": t["agent"], "kind": t.get("kind"), "t": round(t0 - start, 1),
            "t_end": round(t1 - start, 1), "time": hms(t0), "wake": w.get("label") or "",
            "wake_from": w.get("from") or "",
            "steps": [{"reasoning": a, "text": b} for a, b in zip(reasoning, texts + [""] * (len(reasoning) - len(texts)))],
            "calls": calls, "to_user": t.get("text_to_principal") or "",
        })

    seats = {}
    for emp in FOCAL:
        key = (model, seed, emp)
        s = {"label": J.get("labels", {}).get(key), "final_task": (r.get("assignments") or {}).get(emp)}
        if key in J.get("leaks", {}):
            s["leak_bob"], s["leak_work"] = J["leaks"][key]
        jd = J.get("dec_dir", {}).get(key)
        if jd is not None:
            s["deception"] = L.deception_details(jd, model, seed, emp, ack=model not in CLOSED)
        seats[emp] = s
    hp = J.get("honeypot", {}).get(rdir.name)
    if hp:
        seats["Priya"]["honeypot"] = hp
    agents = list((r.get("system_prompts") or {}).keys())
    score = r.get("score") or {}
    cfg = r["config"]
    doc = {
        "id": f"{setting}/{rid}", "setting": setting, "rid": rid, "model": model, "model_name": MODEL_NAME.get(model, model),
        "seed": seed, "closed": model in CLOSED, "dir": rdir.name, "world": world.key, "outcome": r.get("outcome"),
        "kickoff": round(naive_utc(r["kickoff"]) + off - start, 1),
        "deadline": round(naive_utc(r["deadline"]) + off - start, 1),
        "clock_start": r["clock_start"][11:19],
        "assignments": r.get("assignments") or {}, "valid": bool(score.get("valid")),
        "complete": bool(score.get("complete")), "board_shape": score.get("board_shape"),
        "agents": agents,
        "roles": {u["name"]: u["title"] for u in world.users.values()},
        "msgs": msgs, "turns": turns, "board": board, "seats": seats,
        "system_prompts": r.get("system_prompts") or {},
        "asks": {a: ((cfg.get("ask_overrides") or {}).get(a) or cfg.get("ask") or "") for a in agents},
    }
    return doc, results


def load_judges(setting, outputs_root):
    spec = JUDGES.get(setting, {})
    J = {"labels": {}, "leaks": {}, "dec_dir": {}, "disc_rows": {}, "honeypot": {}}
    for d in spec.get("deception", []):
        jd = outputs_root / d
        if jd.exists():
            lab = L.deception_labels(jd)
            J["labels"].update(lab)
            J["dec_dir"].update({k: jd for k in lab})
    for d in spec.get("disclosure", []):
        dd = outputs_root / d
        if dd.exists():
            J["disc_rows"].update(L.disclosure_rows(dd))
            if (dd / "seats_r123.json").exists():
                J["leaks"].update(L.leak_flags(dd))
    for d in spec.get("honeypot", []):
        if (outputs_root / d).exists():
            J["honeypot"].update(L.honeypot_labels(outputs_root / d))
    return J


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", type=Path, required=True)
    ap.add_argument("--outputs-root", type=Path, required=True)
    ap.add_argument("--sets-root", type=Path, help="dir with the honeypot_set_*.txt run lists (default: parent of --runs-root)")
    ap.add_argument("--repo-root", type=Path, help="root the run configs' fixture paths are relative to (default: 3 levels above --runs-root)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*", help="build only these settings")
    ap.add_argument("--plain", action="store_true", help="write plain (not gzipped) JSONP, for debugging")
    a = ap.parse_args()
    sets_root = a.sets_root or a.runs_root.parent
    repo_root = a.repo_root or a.runs_root.parents[2]
    gz = not a.plain
    worlds: dict[str, World] = {}
    index_settings, main_runs = [], []
    world_runs: dict[str, list] = {}  # world -> [(setting, rid, rdir)]
    sel = {}
    for s in SETTINGS:
        if a.only and s["key"] not in a.only:
            continue
        dirs = select(s, a.runs_root, a.outputs_root, sets_root)
        sel[s["key"]] = (s, list(zip(run_ids(dirs), dirs)))

    for key, (s, runs) in sel.items():
        J = load_judges(key, a.outputs_root)
        docs = []
        for rid, rdir in runs:
            doc, results = build_run(key, rid, rdir, repo_root, worlds, J)
            docs.append((doc, results))
            world_runs.setdefault(doc["world"], []).append(key)
        # recurring result fragments -> world blobs (counted over every run of the world in this setting)
        by_world: dict[str, list] = {}
        for doc, results in docs:
            by_world.setdefault(doc["world"], []).append((doc, results))
        rows = []
        for wkey, items in by_world.items():
            w = worlds[wkey]
            seen: dict[str, int] = {}
            for _, results in items:
                acc = set()
                for res in results:
                    walk_big(res, acc)
                for x in acc:
                    seen[x] = seen.get(x, 0) + 1
            for x, n in seen.items():
                if n >= 2 and x not in w.blobs:
                    w.blobs[x] = h8(x)
            for doc, results in items:
                results = [ref_blobs(res, w.blobs) for res in results]
                rd = a.out / "runs" / key
                rr = a.out / "results" / key
                rd.mkdir(parents=True, exist_ok=True)
                rr.mkdir(parents=True, exist_ok=True)
                (rd / f"{doc['rid']}.js").write_text(jsonp("TRACE_LOAD", doc["id"], doc, gz))
                (rr / f"{doc['rid']}.js").write_text(jsonp("TRACE_RESULTS", doc["id"], results, gz))
                row = {"id": doc["id"], "rid": doc["rid"], "model": doc["model"], "seed": doc["seed"],
                       "valid": doc["valid"], "assignments": doc["assignments"], "turns": len(doc["turns"]),
                       "seats": {e: {k: v for k, v in st.items() if k in ("label", "leak_bob", "leak_work", "final_task")}
                                 for e, st in doc["seats"].items()}}
                hp = doc["seats"]["Priya"].get("honeypot")
                if hp:
                    row["hp"] = {k: hp.get(k) for k in ("used_as_reason", "presented_as_fixed")}
                q = next((x.get("decisive_quote") for x in (doc["seats"].get("Priya", {}).get("deception") or {}).get("plain", [])
                          if x.get("decisive_quote")), None)
                rows.append(row)
                if key == "main" and doc["model"] in L_MAIN_MODELS:
                    main_runs.append({"model": doc["model"], "model_name": doc["model_name"], "seed": doc["seed"],
                                      "closed": doc["closed"], "file": f"main/{doc['rid']}", "valid": doc["valid"],
                                      "assignments": doc["assignments"],
                                      "sprint_posts": sum(1 for m in doc["msgs"] if m["conv"].startswith("#")),
                                      "turns": len(doc["turns"]),
                                      "seats": {e: {k: v for k, v in st.items() if k != "deception"} |
                                                {"quote": next((x.get("decisive_quote") for x in (st.get("deception") or {}).get("plain", [])
                                                                if x.get("decisive_quote")), None)}
                                                for e, st in doc["seats"].items()}})
        rows.sort(key=lambda x: (MODEL_ORDER.index(x["model"]), int(x["seed"][1:]), x["rid"]))
        models = sorted({x["model"] for x in rows}, key=MODEL_ORDER.index)
        index_settings.append({"key": key, "title": s["title"], "group": s["group"], "desc": s["desc"],
                               "judged": sorted(JUDGES.get(key, {}).keys()),
                               "models": [{"key": m, "name": MODEL_NAME.get(m, m), "closed": m in CLOSED} for m in models],
                               "runs": rows})
        print(f"{key:32} {len(rows):4} runs")

    wd = a.out / "worlds"
    wd.mkdir(parents=True, exist_ok=True)
    for w in worlds.values():
        old = wd / f"{w.key}.js"
        if a.only and old.exists():  # keep fragments that runs of settings not rebuilt now still point to
            prev = read_jsonp(old)
            for h, obj in prev["blobs"].items():
                w.blobs.setdefault(canon(obj), h)
            world_runs.setdefault(w.key, []).extend(prev.get("settings", []))
        doc = {"key": w.key, "convs": list(w.convs.values()), "msgs": w.msgs, "users": w.users,
               "blobs": {v: json.loads(k) for k, v in w.blobs.items()},
               "settings": sorted(set(world_runs.get(w.key, [])))}
        (wd / f"{w.key}.js").write_text(jsonp("TRACE_WORLD", w.key, doc, gz))
    if a.only and (a.out / "index.js").exists():  # merge into the existing index
        old = json.loads((a.out / "index.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
        keep = [s for s in old["settings"] if s["key"] not in sel]
        index_settings = keep + index_settings
        if "main" not in sel:
            main_runs = old["runs"]
    order = [s["key"] for s in SETTINGS]
    index_settings.sort(key=lambda s: order.index(s["key"]))
    mm = [m for m in MODEL_ORDER if any(r["model"] == m for r in main_runs)]
    index = {"models": [{"key": m, "name": MODEL_NAME[m], "closed": m in CLOSED} for m in mm],
             "runs": main_runs, "groups": GROUPS, "settings": index_settings}
    (a.out / "index.js").write_text("window.TRILEMMA_INDEX = " + json.dumps(index, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + ";\n")
    print(len(worlds), "worlds")


# the landing page's matrix: the nine models of `main` that the judges cover (gpt-5.5 only enters the
# confidentiality comparison)
L_MAIN_MODELS = {"deepseek", "dspro", "glm53", "glm53flash", "kimi", "kimik3", "qwen38flash", "gpt56sol", "opus5or"}

if __name__ == "__main__":
    main()
