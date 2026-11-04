"""Turn authored world content into an agent5 Slack fixture.

``build_from_v15d.py`` and ``build_vSep1.py`` each carry their own copy of this shaping
step because each converts a *foreign* fixture (agent4's, agent1's). w1 is authored
natively — ``w1_content.py`` holds people and conversations as readable Python — so the
shaping lives here instead, and both halves of the identical id/ts machinery in those two
builders are stated once.

What "Slack shapes" means, and why each piece is load-bearing:

- ids are opaque ``U…``/``C…``/``D…`` strings, hash-derived per (prefix, key) so a rebuild
  is reproducible and diffs stay small. An id that encodes the workspace (``U100TANAG``)
  is a tell;
- message timestamps carry microsecond suffixes. Whole-second stamps were caught as a mock
  signature, so every ts is jittered deterministically and kept strictly increasing within
  its conversation;
- channels are private, carry ``is_channel``/``name``/``topic``; DMs carry ``is_im``;
- the sprint kickoff is a real pin on ops-bot's own message, the way the sprint channel
  publishes itself.

Fixture epochs are minted from naive datetimes, so the timezone is pinned: at import to
Berlin (every fixture before w1), and per fixture through ``meta["tz"]`` — ``shape()``
re-pins the process to that zone before minting, writes it to the fixture's top-level
``tz``, and stamps every user profile with the matching ``tz``/``tz_label``/``tz_offset``.
The server and runner read the fixture's ``tz`` back and pin themselves the same way, so a
rebuild anywhere produces the same ts and the served clock agrees with the profiles.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_TZ = "Europe/Berlin"


def pin_tz(name: str) -> None:
    """Pin the process to ``name`` so naive datetime <-> epoch conversions use it."""
    os.environ["TZ"] = name
    time.tzset()


pin_tz(DEFAULT_TZ)

#: Slack's ``tz_label`` for the zones we serve, by whether DST is in force at ``now``.
_TZ_LABELS = {
    "Europe/Berlin": ("Central European Standard Time", "Central European Summer Time"),
    "America/New_York": ("Eastern Standard Time", "Eastern Daylight Time"),
}


def tz_profile(name: str, now_iso: str) -> Dict[str, Any]:
    """The ``tz``/``tz_label``/``tz_offset`` a Slack user profile carries, at ``now``."""
    from zoneinfo import ZoneInfo
    at = datetime.fromisoformat(now_iso).replace(tzinfo=ZoneInfo(name))
    dst = bool(at.dst())
    labels = _TZ_LABELS.get(name)
    label = labels[dst] if labels else at.tzname()
    return {"tz": name, "tz_label": label, "tz_offset": int(at.utcoffset().total_seconds())}

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: A message as authored: (speaker, "YYYY-MM-DD HH:MM", text) with an optional trailing
#: dict of per-message meta (``block`` for a swap-block tag, ``kind`` for ground_truth).
Msg = Tuple


def parse_at(at: str) -> float:
    return datetime.strptime(at, "%Y-%m-%d %H:%M").timestamp()


def jitter_ts(conv_key: str, ts_f: float, prev: Optional[float]) -> float:
    """Deterministic per (conversation, second); strictly increasing per conversation."""
    h = int.from_bytes(hashlib.sha256(f"ts:{conv_key}:{ts_f}".encode()).digest()[:4], "big")
    t = int(ts_f) + (h % 999_000 + 500) / 1e6
    if prev is not None and t <= prev:
        t = prev + (h % 900 + 100) / 1e6
    return t


def mint_id(prefix: str, key: str, taken: set) -> str:
    for n in range(1000):
        h = hashlib.sha256(f"slack-id:{prefix}:{key}:{n}".encode()).digest()
        sid = prefix + "".join(_ALPHABET[b % 36] for b in h[:10])
        if sid not in taken:
            taken.add(sid)
            return sid
    raise RuntimeError(f"id space exhausted for {prefix}:{key}")


def msg_parts(m: Msg) -> Tuple[str, str, str, Dict[str, Any]]:
    """(speaker, at, text, meta) — the 4th element is optional in authored content."""
    speaker, at, text = m[0], m[1], m[2]
    meta = dict(m[3]) if len(m) > 3 and m[3] else {}
    return speaker, at, text, meta


def shape(
    *,
    people: Sequence[Dict[str, Any]],
    convs: Sequence[Dict[str, Any]],
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble the fixture.

    ``people``: dicts with ``name`` and optionally ``full_name``/``title``/``status``/``is_bot``.
    ``convs``: dicts with ``key``, ``kind`` ("channel"|"im"), ``members`` (names), ``msgs``
    (authored triples, any order — sorted here), and for channels ``name``/``topic``.
    A conversation may carry ``swap_block``; a message may carry ``block``/``kind`` meta.
    ``meta``: ``now``, ``deadline``, ``principals``, ``reporter``, ``report_to``, ``board``,
    ``calendars``, ``sprint_key``, ``pin_first_in`` (conversation keys), ``online_at``,
    ``note``, ``read_state`` (viewer -> {conv key: at}), ``tz`` (IANA zone; default Berlin).
    """
    tz = meta.get("tz") or DEFAULT_TZ
    pin_tz(tz)
    profile = tz_profile(tz, meta["now"])
    taken: set = set()
    uid = {p["name"]: mint_id("U", p["name"], taken) for p in people}
    users = [{
        "id": uid[p["name"]], "name": p["name"],
        "real_name": p.get("full_name") or p["name"],
        "title": p.get("title", ""), "is_bot": bool(p.get("is_bot")),
        **({"status": p["status"]} if p.get("status") else {}),
        **profile,
    } for p in people]

    cid: Dict[str, str] = {}
    out_convs: List[Dict[str, Any]] = []
    #: swap-block tag -> {"conversations": [...], "ranges": [{conversation, first_ts, last_ts}]}
    blocks: Dict[str, Dict[str, Any]] = {}
    #: authored "at" -> minted ts, per conversation key (for read_state and ground_truth)
    at_to_ts: Dict[str, Dict[str, str]] = {}
    #: minted ts -> authored kind, for ground_truth
    kinds: Dict[str, str] = {}

    for c in convs:
        key = c["key"]
        is_channel = c["kind"] == "channel"
        cid[key] = mint_id("C" if is_channel else "D", key, taken)
        rows = sorted(c.get("msgs", []), key=lambda m: parse_at(msg_parts(m)[1]))
        messages: List[Dict[str, Any]] = []
        prev: Optional[float] = None
        conv_at: Dict[str, str] = {}
        block_ts: Dict[str, List[str]] = {}
        for m in rows:
            speaker, at, text, mm = msg_parts(m)
            if speaker not in uid:
                raise KeyError(f"{key}: message from unknown speaker {speaker!r}")
            prev = jitter_ts(key, parse_at(at), prev)
            ts = f"{prev:.6f}"
            messages.append({"type": "message", "ts": ts, "user": uid[speaker], "text": text})
            conv_at[at] = ts
            if mm.get("kind"):
                kinds[ts] = mm["kind"]
            if mm.get("block"):
                block_ts.setdefault(mm["block"], []).append(ts)
        at_to_ts[key] = conv_at

        first = min((parse_at(msg_parts(m)[1]) for m in rows), default=datetime(2026, 7, 1).timestamp())
        conv: Dict[str, Any] = {
            "id": cid[key],
            "members": [uid[n] for n in c["members"]],
            "created": first - 86400,
            "messages": messages,
            "pins": [],
        }
        if is_channel:
            conv["is_channel"] = True
            conv["is_private"] = True
            conv["name"] = c["name"]
            if c.get("topic"):
                conv["topic"] = c["topic"][:250]
        else:
            conv["is_im"] = True
        if key in (meta.get("pin_first_in") or ()):
            if not messages:
                raise ValueError(f"{key}: nothing to pin")
            conv["pins"] = [messages[0]["ts"]]
        out_convs.append(conv)

        if c.get("swap_block"):
            blocks.setdefault(c["swap_block"], {"conversations": [], "ranges": []}
                              )["conversations"].append(cid[key])
        for tag, stamps in block_ts.items():
            blocks.setdefault(tag, {"conversations": [], "ranges": []})["ranges"].append(
                {"conversation": cid[key], "first_ts": stamps[0], "last_ts": stamps[-1],
                 "count": len(stamps)})

    read_state: Dict[str, Dict[str, str]] = {}
    for viewer, marks in (meta.get("read_state") or {}).items():
        for conv_key, at in marks.items():
            read_state.setdefault(viewer, {})[cid[conv_key]] = at_to_ts[conv_key][at]

    sprint_id = cid[meta["sprint_key"]]
    out = {
        "version": "slack_v1",
        "note": meta.get("note", ""),
        "tz": tz,
        "now": meta["now"],
        "deadline": meta.get("deadline"),
        "principals": list(meta["principals"]),
        "reporter": meta.get("reporter"),
        "report_to": meta.get("report_to"),
        "sprint_channel_id": sprint_id,
        "users": users,
        "conversations": out_convs,
        "board": meta.get("board"),
        "calendars": meta.get("calendars"),
        "scoring": meta.get("scoring"),
        "read_state": read_state,
        "ground_truth": {
            "signal_messages": sorted(ts for ts, k in kinds.items() if k == "signal"),
            "message_types": {ts: k for ts, k in sorted(kinds.items())},
            "note": meta.get("ground_truth_note", ""),
        },
        "channel_online": {sprint_id: meta["online_at"]},
        "board_online": meta["online_at"],
    }
    if blocks:
        out["swap_blocks"] = blocks
    return out


def name_index(fixture: Dict[str, Any]) -> Dict[str, str]:
    return {u["id"]: u["name"] for u in fixture["users"]}


def iter_messages(fixture: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for c in fixture["conversations"]:
        for m in c["messages"]:
            yield c, m
