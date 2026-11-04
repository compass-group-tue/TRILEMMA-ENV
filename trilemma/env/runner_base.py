"""The concurrent runner: true parallelism over opencode sessions, one shared world.

Mechanism:

- All assistants run **concurrently** — each is an opencode session with its own asyncio
  worker; turns overlap, and the world is live mid-turn (another agent's post is visible
  the moment it lands).
- **Clock**: the world server runs simulated time at ``clock_scale`` × wall time (parked
  until the runner starts it). When *everything* is idle — no in-flight turns, no queued
  pings — the runner fast-forwards straight to the next scheduled event: a pending
  delivery, an unsent ask, a ring slot that would fire, a closing, or the deadline.
- **The ring** (unused by ``trilemma.env.runner``, which wakes everyone at once): each
  >2-member channel has a fixed cyclic order over its assistant-backed members and a slot
  grid; at each slot boundary the slotted member is pinged iff the channel holds messages
  they haven't read.
- **Asks**: every principal gets its ask around ``preask_at``; extra assistants keep their
  own asks. With ``converge_on: horizon`` the run ends at ``horizon`` after the
  ``debrief_at`` ask.

The paper's runs use the subclass in ``trilemma.env.runner``; run that, not this module.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from trilemma.env import prompts_base as prompts4
from trilemma.env.homes_base import make_home
from trilemma.env.procs import Procs, load_env, wait_http
from trilemma.env.timeutil import parse_dt

ENV_DIR = Path(__file__).resolve().parent
PY = sys.executable
EPOCH = datetime.fromtimestamp(0)


class ConcRunner:
    EXPERIMENT = "trilemma"
    RUNS_DIR = REPO / "outputs" / "runs"
    WORLD_SERVER = ENV_DIR / "slack_server.py"

    def __init__(self, config: Dict[str, Any], config_path: str):
        self.config = config
        self.config_path = config_path
        self.name = str(config.get("name") or Path(config_path).stem)

        self.fixture_path = REPO / str(config["fixture"])
        fx = json.loads(self.fixture_path.read_text())
        self.principals: List[str] = list(fx["principals"])
        self.kickoff: datetime = parse_dt(fx["now"])
        self.deadline: Optional[datetime] = parse_dt(fx["deadline"]) if fx.get("deadline") else None
        self.reporter, self.report_to = fx.get("reporter"), fx.get("report_to")
        self.channel_online: Dict[str, datetime] = {
            cid: parse_dt(str(at)) for cid, at in (fx.get("channel_online") or {}).items()}
        self.conv_meta: Dict[str, Dict[str, Any]] = {}
        for c in fx.get("conversations") or []:
            label = (f"#{c.get('name') or c['id']}" if c.get("type", "channel") == "channel"
                     else "dm:" + "+".join(c.get("members") or []))
            self.conv_meta[c["id"]] = {"label": label, "type": c.get("type", "channel"),
                                       "members": list(c.get("members") or []),
                                       "pinned": bool(c.get("pinned"))}

        self.model = str(config["model"])
        self.harness = str(config.get("harness") or "full")
        self.confidentiality = str(config.get("confidentiality") or "audience")
        self.discussion_norms = str(config.get("discussion_norms") or "off")
        self.temperature = float(config.get("temperature", 0.7))
        self.clock_scale = float(config.get("clock_scale", 2.0))
        self.slot_seconds = int(config.get("slot_seconds", 60))
        self.turn_timeout = float(config.get("turn_timeout", 600))
        self.outcome = ""
        #: Proxy dead-seat marker threshold (consecutive non-200 final answers to one
        #: agent); 0 disables the marker. The pre-commit-400 retry ladder is always on.
        self.dead_after = int(config.get("dead_after", 6))
        self.max_turns = int(config.get("max_turns", 90))
        self.max_wall_seconds = float(config.get("max_wall_seconds", 5400))
        self.rng = random.Random(int(config.get("seed", 0)))

        # Horizon ending mode: no wind-down phase at the deadline — the world stays
        # uniformly live (every message wakes, no closing turns) until sim time reaches
        # `horizon`, then the run stops and whatever exists is the record (in-flight
        # turns get the usual grace; queued wakes are dropped mid-conversation, which is
        # accepted: the world does not end, the observation does). An optional debrief
        # ask at `debrief_at` replaces the closing turn as the report-to-principal
        # surface, delivered like any ordinary message.
        def _tod(key: str) -> Optional[datetime]:
            raw = str(config.get(key) or "")
            if not raw:
                return None
            hh, mm = raw.split(":")
            return self.kickoff.replace(hour=int(hh), minute=int(mm), second=0)
        self.horizon = _tod("horizon")
        self.debrief_at = _tod("debrief_at")
        self.debrief = str(config.get("debrief") or "")
        self.extra_debriefs: Dict[str, str] = {
            str(name): str(spec["debrief"])
            for name, spec in (config.get("extra_assistants") or {}).items()
            if isinstance(spec, dict) and spec.get("debrief")}
        self.debriefs_sent: Set[str] = set()

        start_raw = str(config.get("warmup_start") or "")
        hh, mm = start_raw.split(":")
        self.clock_start = self.kickoff.replace(hour=int(hh), minute=int(mm), second=0)

        self.ask = str(config["ask"])
        #: Per-principal replacements (the askStrong-style arms); absent names get `ask`.
        self.ask_overrides: Dict[str, str] = {
            str(k): str(v) for k, v in (config.get("ask_overrides") or {}).items()}
        if (stray := [n for n in self.ask_overrides if n not in self.principals]):
            raise ValueError(f"ask_overrides names non-principals {stray}")
        pa = str(config.get("preask_at") or "09:25").split(":")
        preask = self.kickoff.replace(hour=int(pa[0]), minute=int(pa[1]), second=0)
        self.ask_times: Dict[str, datetime] = {
            p: preask + timedelta(seconds=self.rng.uniform(0, 60)) for p in self.principals}
        #: Per-principal ask *times* (the honeypot settings: Priya is asked at 09:35, after
        #: the kickoff, everyone else at `preask_at`). Same jitter as the pre-ask.
        for who, at in (config.get("ask_at_overrides") or {}).items():
            if str(who) not in self.principals:
                raise ValueError(f"ask_at_overrides names non-principal {who!r}")
            ah, am = str(at).split(":")
            self.ask_times[str(who)] = (self.kickoff.replace(hour=int(ah), minute=int(am), second=0)
                                        + timedelta(seconds=self.rng.uniform(0, 60)))
        self.extra_asks: Dict[str, str] = {}
        for extra, spec in (config.get("extra_assistants") or {}).items():
            eh, em = str(spec["ask_at"]).split(":")
            self.ask_times[extra] = (self.kickoff.replace(hour=int(eh), minute=int(em), second=0)
                                     + timedelta(seconds=self.rng.uniform(0, 30)))
            self.extra_asks[extra] = str(spec["ask"])
        self.roster: List[str] = self.principals + list(self.extra_asks)

        # Rings: configured orders win; otherwise every channel with >2 assistant-backed
        # members rings in fixture member order.
        self.rings: Dict[str, Dict[str, Any]] = {}
        configured = {str(k): [str(n) for n in v] for k, v in (config.get("ring_orders") or {}).items()}
        for cid, meta in self.conv_meta.items():
            if meta["type"] != "channel":
                continue
            members = configured.get(cid) or [m for m in meta["members"] if m in self.roster]
            if len(members) > 2 or cid in configured:
                anchor = self.channel_online.get(cid, EPOCH)
                # First slot of interest: the first boundary after the run's clock start
                # (an epoch-anchored grid must not be walked from 1970).
                elapsed = (self.clock_start - anchor).total_seconds()
                first_k = max(1, int(elapsed // self.slot_seconds) + 1)
                self.rings[cid] = {"order": members, "anchor": anchor,
                                   "next_k": first_k, "added_sent": set()}

        ports = config.get("ports") or {}
        # Several runs on one machine: export TRILEMMA_PORT_OFFSET per run so fixed config
        # ports never collide.
        import os as _os
        off = int(_os.environ.get("TRILEMMA_PORT_OFFSET", 0))
        self.world_port = int(ports.get("world", 8975)) + off
        self.proxy_port = int(ports.get("proxy", 8910)) + off
        self.opencode_base = int(ports.get("opencode_base", 4250)) + off
        self.world = f"http://127.0.0.1:{self.world_port}"

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{self.name}_{stamp}"
        #: outputs/runs/<setting>/<model>_s<seed>_<timestamp>/
        self.out = self.RUNS_DIR / str(config.get("setting") or "") / self.run_id
        self.out.mkdir(parents=True, exist_ok=True)
        import os
        self.homes_root = Path("/tmp/tanager") / f"{stamp}-{os.getpid()}"

        self.http: httpx.AsyncClient = None  # type: ignore[assignment]
        self.sessions: Dict[str, str] = {}
        self.oc_ports: Dict[str, int] = {}
        self.inbox: Dict[str, asyncio.Queue] = {}
        self.busy: Dict[str, bool] = {p: False for p in self.roster}
        self.pending_ping: Set[Tuple[str, str]] = set()
        self.asks_sent: Set[str] = set()
        self.closings_sent: Set[str] = set()
        self.closing_times: Dict[str, datetime] = {}
        self.turns: List[Dict[str, Any]] = []
        self.wake_log: List[Dict[str, Any]] = []
        self.now: datetime = self.clock_start
        self.watermark: float = self.clock_start.timestamp() - 1e-6
        self.all_new: List[Dict[str, Any]] = []
        self.pending_deliveries: List[datetime] = []
        self.stop_reason: Optional[str] = None

    # ------------------------------------------------------------------- world
    #: Local-service calls (world control, opencode setup) are retried on a read timeout:
    #: the world server holds one lock across its synchronous log append, so a slow disk
    #: can block /control past the timeout of a server that answers the next request. Three attempts, two seconds apart; a server that is really gone still
    #: fails, ~90 s later. Nothing about the run's content changes (the sim clock is the
    #: world's and keeps running, as during any slow step).
    RETRIES = 3

    async def _request(self, method: str, url: str, **kw) -> "httpx.Response":
        for attempt in range(1, self.RETRIES + 1):
            try:
                return await self.http.request(method, url, **kw)
            except httpx.ReadTimeout:
                if attempt == self.RETRIES:
                    raise
                print(f"[runner] {method} {url.split('/', 3)[-1]} timed out "
                      f"(attempt {attempt}/{self.RETRIES}); retrying", flush=True)
                await asyncio.sleep(2)
        raise AssertionError("unreachable")

    async def _control(self, method: str, path: str, **kw) -> Dict[str, Any]:
        r = await self._request(method, self.world + path, timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    def _label(self, cid: str) -> str:
        return self.conv_meta.get(cid, {}).get("label", cid)

    # ----------------------------------------------------------- subclass hooks
    def world_cmd(self) -> List[str]:
        return [
            PY, str(self.WORLD_SERVER), "--fixture", str(self.fixture_path),
            "--out", str(self.out), "--port", str(self.world_port),
            "--harness", self.harness, "--start", self.clock_start.isoformat(),
            "--replay-after", self.clock_start.isoformat(),
            "--clock-scale", str(self.clock_scale),
            *(["--script", str(REPO / str(self.config["script"]))]
              if self.config.get("script") else []),
        ]

    def proxy_cmd(self) -> List[str]:
        return [PY, str(ENV_DIR / "proxy.py"), "--out", str(self.out),
                "--port", str(self.proxy_port), "--dead-after", str(self.dead_after)]

    def env_required(self) -> Tuple[str, ...]:
        """Credentials the run cannot start without — provider-dependent."""
        return ("OPENROUTER_API_KEY",)

    def system_prompt(self, agent: str) -> str:
        return prompts4.system_prompt(
            agent, now=self.clock_start, confidentiality=self.confidentiality,
            discussion_norms=self.discussion_norms)

    def build_home(self, agent: str, system_prompt: str) -> Path:
        return make_home(self.homes_root, agent, model=self.model,
                         proxy_port=self.proxy_port, world_port=self.world_port,
                         system_prompt=system_prompt, temperature=self.temperature)

    async def start_agents(self, procs: Procs) -> None:
        """One opencode server + session per assistant."""
        for i, p in enumerate(self.roster):
            home = self.build_home(p, self.system_prompt(p))
            port = self.opencode_base + i
            self.oc_ports[p] = port
            xdg = {f"XDG_{kind}_HOME": str(home / ".xdg" / kind.lower())
                   for kind in ("DATA", "CONFIG", "STATE", "CACHE")}
            procs.spawn(f"opencode_{p.lower()}",
                        ["opencode", "serve", "--port", str(port)], cwd=home, extra_env=xdg)
        for p in self.roster:
            base = f"http://127.0.0.1:{self.oc_ports[p]}"
            wait_http(base + "/agent", f"opencode({p})", procs=procs)
            agents = (await self._request("GET", base + "/agent", timeout=10)).json()
            if "assistant" not in [a.get("name") for a in agents]:
                raise RuntimeError(f"opencode({p}): agent 'assistant' missing")
            self.sessions[p] = (await self._request(
                "POST", base + "/session", json={"title": f"{p} assistant"},
                timeout=10)).json()["id"]

    def enrich_record(self, out_path: Path) -> None:
        """Per-step reasoning and tool-call steps, reconstructed from the proxy dump."""
        from trilemma.env.reasoning_extract import enrich
        enrich(out_path)

    # ------------------------------------------------------------------ worker
    async def _worker(self, agent: str) -> None:
        while True:
            item = await self.inbox[agent].get()
            if item is None:
                return
            self.busy[agent] = True
            try:
                await self._run_turn(agent, item)
            finally:
                self.busy[agent] = False
                if item.get("ping_key"):
                    self.pending_ping.discard(item["ping_key"])

    async def _run_turn(self, agent: str, item: Dict[str, Any]) -> None:
        state = await self._control("GET", "/control/state")
        clock0, seq0 = state["now"], state["calls"]
        wall0 = time.time()
        base = f"http://127.0.0.1:{self.oc_ports[agent]}/session/{self.sessions[agent]}"
        timed_out = False
        try:
            r = await self.http.post(
                base + "/message",
                json={"agent": "assistant", "parts": [{"type": "text", "text": item["text"]}]},
                timeout=self.turn_timeout)
            r.raise_for_status()
            reply = r.json()
        except (httpx.TimeoutException, httpx.HTTPError):
            timed_out = True
            reply = {"parts": [], "info": {}}
            try:
                await self.http.post(base + "/abort", timeout=15)
            except httpx.HTTPError:
                pass
        wall1 = time.time()
        state1 = await self._control("GET", "/control/state")

        texts, reasoning = [], []
        for part in reply.get("parts", []):
            if part.get("type") == "text":
                texts.append(part.get("text") or "")
            elif part.get("type") == "reasoning":
                reasoning.append(part.get("text") or "")
        tool_calls = []
        try:
            for line in (self.out / "world_calls.jsonl").read_text().splitlines():
                call = json.loads(line)
                if (call["agent"] == agent and call["seq"] > seq0
                        and wall0 - 0.5 <= call["wall"] <= wall1 + 0.5):
                    tool_calls.append({k: call[k] for k in ("seq", "tool", "args", "result", "clock")})
        except FileNotFoundError:
            pass
        info = reply.get("info") or {}
        # A turn whose last model call did not come back 200 is a dead turn, not a quiet
        # one (a streak of upstream 400s otherwise reads as a passive assistant).
        # proxy_status.jsonl is the proxy's one-line-per-answer
        # log; the full dump is too big to re-read per turn.
        failed: Dict[str, Any] = {}
        try:
            last = None
            for line in (self.out / "proxy_status.jsonl").read_text().splitlines():
                row = json.loads(line)
                if row["agent"] == agent and wall0 - 0.5 <= row["wall"] <= wall1 + 0.5:
                    last = row
            if last and last["status"] != 200:
                failed = {"status": last["status"], "attempts": last.get("attempts"),
                          "precommit_retries": last.get("precommit_retries", 0)}
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        self.turns.append({
            "i": len(self.turns), "kind": item["kind"], "agent": agent,
            "clock": clock0, "clock_end": state1["now"],
            "wall_start": wall0, "wall_end": wall1,
            "message_in": item["text"],
            **({"timed_out": True} if timed_out else {}),
            **({"failed": failed} if failed else {}),
            "text_to_principal": "\n".join(t for t in texts if t.strip()),
            "reasoning": reasoning, "tool_calls": tool_calls,
            "wake": item.get("wake") or {},
            "usage": {k: info.get(k) for k in ("tokens", "cost") if info.get(k) is not None},
            "elapsed_seconds": round(wall1 - wall0, 2),
        })

    def _enqueue(self, agent: str, kind: str, text: str, *, wake: Optional[Dict] = None,
                 ping_key: Optional[Tuple[str, str]] = None) -> None:
        self.inbox[agent].put_nowait({"kind": kind, "text": text, "wake": wake, "ping_key": ping_key})
        self.wake_log.append({"agent": agent, "kind": kind, "clock": self.now.isoformat(),
                              **(wake or {})})

    # --------------------------------------------------------------- scheduler
    def _slot_time(self, ring: Dict[str, Any], k: int) -> datetime:
        return ring["anchor"] + timedelta(seconds=k * self.slot_seconds)

    def _process_rings(self, unread: Dict[str, Dict[str, int]]) -> None:
        for cid, ring in self.rings.items():
            online = self.channel_online.get(cid)
            while self._slot_time(ring, ring["next_k"]) <= self.now:
                k = ring["next_k"]
                ring["next_k"] += 1
                slot_at = self._slot_time(ring, k)
                # A slot older than one slot interval is stale — it came and went while
                # the member had nothing unread (fast-forward only targets firing slots).
                # Evaluating it against post-hoc unread would ping several members at once.
                if (self.now - slot_at).total_seconds() >= self.slot_seconds:
                    continue
                member = ring["order"][(k - 1) % len(ring["order"])]
                count = (unread.get(member) or {}).get(cid, 0)
                key = (member, cid)
                if count and key not in self.pending_ping:
                    self.pending_ping.add(key)
                    if online is not None and member not in ring["added_sent"]:
                        ring["added_sent"].add(member)
                        text = prompts4.channel_added(
                            self.now, self._label(cid), count,
                            self.conv_meta.get(cid, {}).get("pinned", False))
                        kind = "added"
                    else:
                        text = prompts4.ring_ping(self.now, self._label(cid), count)
                        kind = "ring"
                    self._enqueue(member, kind, text, ping_key=key,
                                  wake={"label": self._label(cid), "slot": k})

    def _process_feed(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            self.all_new.append(row)
            # Once the closing phase has begun, the run is winding down: late messages
            # stay in the record but wake nobody, or post-deadline chatter sustains
            # itself indefinitely (each closing post waking the next assistant).
            if self.closing_times:
                continue
            if row["type"] == "dm":
                for member in row["members"]:
                    if member in self.roster and member != row["user"]:
                        self._enqueue(member, "wake",
                                      prompts4.wake(self.now, row["label"]),
                                      wake={"label": row["label"], "from": row["user"],
                                            "ts": row["ts"]})

    def _process_asks(self) -> None:
        for agent, at in self.ask_times.items():
            if agent not in self.asks_sent and at <= self.now:
                self.asks_sent.add(agent)
                text = self.extra_asks.get(agent) or self.ask_overrides.get(agent, self.ask)
                self._enqueue(agent, "ask", text)
        if self.debrief_at and self.debrief_at <= self.now:
            for agent in self.roster:
                if agent in self.debriefs_sent:
                    continue
                self.debriefs_sent.add(agent)
                text = (self.debrief if agent in self.principals
                        else self.extra_debriefs.get(agent, ""))
                if text:
                    self._enqueue(agent, "debrief", text)

    def _process_closings(self) -> None:
        if self.horizon:
            return  # horizon mode: no wind-down phase, the run ends at the horizon
        if not self.closing_times and self.deadline and self.now >= self.deadline:
            order = (self.rings.get("C-sprint", {}).get("order")
                     or self.principals) + [a for a in self.roster if a not in self.principals]
            for i, agent in enumerate(order):
                self.closing_times[agent] = self.deadline + timedelta(seconds=i * 60)
        for agent, at in self.closing_times.items():
            if agent not in self.closings_sent and at <= self.now:
                self.closings_sent.add(agent)
                cut = self._last_turn_end_ts(agent)
                counts: Dict[str, int] = {}
                for m in self.all_new:
                    if agent in m["members"] and m["user"] != agent and float(m["ts"]) > cut:
                        counts[m["label"]] = counts.get(m["label"], 0) + 1
                self._enqueue(agent, "closing", prompts4.closing(self.now, self.deadline, counts))

    def _last_turn_end_ts(self, agent: str) -> float:
        ends = [parse_dt(t["clock_end"]).timestamp() for t in self.turns if t["agent"] == agent]
        return max(ends) if ends else self.clock_start.timestamp()

    def _idle(self) -> bool:
        return (not any(self.busy.values())
                and all(q.empty() for q in self.inbox.values()))

    def _next_event(self, unread: Dict[str, Dict[str, int]]) -> Optional[datetime]:
        events: List[datetime] = []
        events += [at for a, at in self.ask_times.items() if a not in self.asks_sent]
        events += [at for a, at in self.closing_times.items() if a not in self.closings_sent]
        events += [d for d in self.pending_deliveries if d > self.now]
        if self.deadline and not self.closing_times:
            events.append(self.deadline)
        if self.debrief_at and self.debriefs_sent < set(self.roster):
            events.append(self.debrief_at)
        if self.horizon:
            events.append(self.horizon)
        for cid, ring in self.rings.items():
            order, n = ring["order"], len(ring["order"])
            for j in range(n):  # next slot for each member; keep the earliest that fires
                k = ring["next_k"] + j
                member = order[(k - 1) % n]
                if (unread.get(member) or {}).get(cid, 0) and (member, cid) not in self.pending_ping:
                    events.append(self._slot_time(ring, k))
                    break
        future = [e for e in events if e > self.now]
        return min(future) if future else None

    def _converged(self, state: Dict[str, Any]) -> bool:
        if not (state["board_complete"] and state["allocation_valid"]):
            return False
        if not (self.reporter and self.report_to):
            return True
        return any(m["type"] == "dm" and m["user"] == self.reporter
                   and set(m["members"]) == {self.reporter, self.report_to}
                   and float(m["ts"]) > self.clock_start.timestamp()
                   for m in self.all_new)

    async def _scheduler(self) -> str:
        t_start = time.time()
        await self._control("POST", "/control/start_clock")
        while True:
            if (self.out / "dead_seat.json").exists():
                # The proxy saw a seat die (pre-commit 400 ladder exhausted, or a streak
                # of failed answers). Nothing from here on would be a valid record.
                return "dead_seat"
            state = await self._control("GET", "/control/state")
            self.now = parse_dt(state["now"])
            feed = await self._control("GET", "/control/messages",
                                       params={"after": repr(self.watermark)})
            rows = feed["messages"]
            if rows:
                self.watermark = max(float(r["ts"]) for r in rows)
                self._process_feed(rows)
            unread = (await self._control("GET", "/control/unread"))["unread"]
            self._process_asks()
            self._process_rings(unread)
            self._process_closings()

            if self.closing_times and self.closings_sent == set(self.closing_times) and self._idle():
                return "deadline"
            if (not self.closing_times and self.asks_sent >= set(self.principals)
                    and self._converged(state)):
                return "converged"
            if len(self.turns) >= self.max_turns:
                return "cap"
            if time.time() - t_start > self.max_wall_seconds:
                return "wall_cap"
            if self.horizon and self.now >= self.horizon:
                return "horizon"

            if self._idle():
                nxt = self._next_event(unread)
                if nxt is not None and (nxt - self.now).total_seconds() > 2 * self.clock_scale:
                    await self._control("POST", "/control/set_time", json={"now": nxt.isoformat()})
                    continue
                if nxt is None and self.closing_times:
                    await asyncio.sleep(1.0)  # closings sent; exit comes from the idle check
                    continue
                if nxt is None and self.deadline is None:
                    return "stalled"
            await asyncio.sleep(1.0)

    # -------------------------------------------------------------------- main
    async def execute_async(self) -> Path:
        env = load_env(self.env_required())
        procs = Procs(env, self.out)
        started_at = datetime.now().isoformat(timespec="seconds")
        t0 = time.time()
        outcome = "error"
        try:
            procs.spawn("world", self.world_cmd())
            proxy_cmd = self.proxy_cmd()
            if proxy_cmd:
                procs.spawn("proxy", proxy_cmd)
            wait_http(self.world + "/control/state", "world server", procs=procs)
            if proxy_cmd:
                wait_http(f"http://127.0.0.1:{self.proxy_port}/", "proxy", procs=procs)

            self.http = httpx.AsyncClient()
            pending = (await self._control("GET", "/control/replay"))["messages"]
            self.pending_deliveries = sorted(parse_dt(e["at"]) for e in pending)

            await self.start_agents(procs)
            for p in self.roster:
                self.inbox[p] = asyncio.Queue()

            workers = [asyncio.create_task(self._worker(p)) for p in self.roster]
            outcome = await self._scheduler()
            self.outcome = outcome
            for p in self.roster:
                self.inbox[p].put_nowait(None)
            grace = self.turn_timeout + 30
            if outcome == "dead_seat":
                # Kill the in-flight turns now: opencode would otherwise keep retrying the
                # dead upstream for minutes. Partial turns still get recorded.
                dead = json.loads((self.out / "dead_seat.json").read_text())
                print(f"[runner] DEAD SEAT {dead.get('agent')}: {dead.get('reason')} "
                      f"(statuses {dead.get('attempts')}) — aborting the run", flush=True)
                for p in self.roster:
                    if p in self.sessions:
                        try:
                            await self.http.post(
                                f"http://127.0.0.1:{self.oc_ports[p]}/session/{self.sessions[p]}/abort",
                                timeout=15)
                        except httpx.HTTPError:
                            pass
                grace = 60
            # Let an in-flight turn finish (it can legitimately take minutes); if it
            # doesn't, cancel — the record must still be written.
            try:
                await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True),
                                       timeout=grace)
            except (asyncio.TimeoutError, TimeoutError):
                for w in workers:
                    w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
        finally:
            try:
                final_state = await self._control("GET", "/control/state")
                final_messages = (await self._control(
                    "GET", "/control/messages", params={"after": "0"}))["messages"]
            except Exception:
                final_state, final_messages = {}, []
            if self.http:
                await self.http.aclose()
            procs.stop()

        self.turns.sort(key=lambda t: t["wall_start"])
        for i, t in enumerate(self.turns):
            t["i"] = i
        record = {
            "experiment": self.EXPERIMENT,
            "mechanism": "concurrent",
            "config": {**self.config, "_path": self.config_path, "run_id": self.run_id},
            "outcome": outcome,
            "fixture": final_state.get("fixture"),
            "started_at": started_at,
            "elapsed_seconds": round(time.time() - t0, 1),
            "clock_start": self.clock_start.isoformat(),
            "kickoff": self.kickoff.isoformat(),
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "clock_scale": self.clock_scale,
            "rings": {cid: {"order": r["order"], "anchor": r["anchor"].isoformat()}
                      for cid, r in self.rings.items()},
            "turns": self.turns,
            "wake_log": self.wake_log,
            "notifications": final_state.get("notifications"),
            "messages": final_messages,
            "score": final_state.get("score"),
            "assignments": final_state.get("assignments"),
            "seen": final_state.get("seen"),
            "system_prompts": {p: self.system_prompt(p) for p in self.roster},
        }
        out_path = self.out / "run.json"
        out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        try:
            self.enrich_record(out_path)
        except Exception as exc:
            print(f"reasoning extraction failed: {exc}")
        print(f"outcome: {outcome}  turns: {len(self.turns)}  record: {out_path}")
        self.outcome = outcome
        if outcome == "dead_seat":
            # Same convention as the hand-marked partial dirs: the suffix keeps every index,
            # judge and batch glob away from a record with a seat that was not there.
            invalid = self.out.with_name(self.out.name + "_INVALID")
            self.out.rename(invalid)
            out_path = invalid / "run.json"
            print(f"DEAD SEAT — run marked invalid: {invalid}", flush=True)
        return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    runner = ConcRunner(config, args.config)
    asyncio.run(runner.execute_async())
    if runner.outcome == "dead_seat":
        sys.exit(3)


if __name__ == "__main__":
    main()
