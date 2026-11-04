"""The runner: concurrent opencode assistants over the Slack world, with real-daemon wakes.

On top of ``runner_base.ConcRunner``:
- **No ring.** Every message wakes every assistant-backed member of the conversation
  except the sender, immediately and simultaneously — the thundering herd a real Events
  API subscription produces. The wake payload is the raw Slack event JSON.
- The 09:27 channel creation arrives as a ``member_joined_channel`` event per member.
- World server is ``slack_server.py`` (Slack-shaped tools); homes mount two MCP
  connectors (slack + tanager); prompts come from ``prompts``.

Run:  python -m trilemma.run --config configs/<setting>/<model>_s<N>.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import sys
import time

# Match the world server: naive datetime <-> epoch conversions in the fixture's timezone
# everywhere, so cluster (UTC) and laptop (CEST) mint identical ts. Berlin at import; the
# runner re-pins to the fixture's own ``tz`` once it has read it (America/New_York).
os.environ["TZ"] = "Europe/Berlin"
time.tzset()

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from trilemma.env import prompts as prompts5
from trilemma.env.homes import make_home as make_home5
from trilemma.env.runner_base import PY, ConcRunner
from trilemma.env.slack_world import (
    API_APP_ID,
    TEAM_ID,
    VERIFICATION_TOKEN,
    client_msg_id,
    load_triggers,
    trigger_matches,
)

ENV_DIR = Path(__file__).resolve().parent


class Slack5Runner(ConcRunner):
    WORLD_SERVER = ENV_DIR / "slack_server.py"

    def __init__(self, config: Dict[str, Any], config_path: str):
        super().__init__(config, config_path)
        #: Model provider: "openrouter" (default) or "azure" (direct Azure OpenAI
        #: deployment, ``model`` is the deployment name, e.g. gpt-5.4; no chain of thought
        #: comes back, so those runs have empty ``steps_detail[].reasoning``).
        self.provider = str(config.get("provider") or "openrouter")
        if self.provider not in ("openrouter", "azure"):
            raise ValueError(f"provider must be 'openrouter' or 'azure', not {self.provider!r}")
        #: Optional OpenRouter backend pin (e.g. "Chutes"), fallbacks off; a comma-separated
        #: value is an ordered allow-list. Unset means the router chooses.
        self.pin_provider = str(config.get("pin_provider") or "")
        #: Optional OpenRouter backends to exclude (comma-separated, e.g. "anthropic/fast").
        #: Both go to proxy.py --pin-provider / --ignore-providers.
        self.ignore_providers = str(config.get("ignore_providers") or "")
        #: Optional OpenRouter ``reasoning.enabled`` (true/false), sent on every call. Unset
        #: leaves the provider's default, which for some hybrid models differs by host.
        r = config.get("reasoning_enabled")
        self.reasoning_enabled: Optional[bool] = None if r is None else bool(r)
        #: Optional model id the home carries instead of ``model`` (what opencode tells the
        #: assistant it is "powered by"); the proxy rewrites it back. See homes5.make_home5.
        self.model_alias = str(config.get("model_alias") or "")
        #: What ends a run early. ``valid`` (default, every rollout to date): the board is
        #: complete *and* every ticket has its pair, plus the reporter's DM. ``settled``:
        #: the board is complete and the reporter has reported, whether or not a ticket is
        #: short — so a refusal (both data scientists declining one partner) ends the run instead of burning to horizon. Changing this changes run behaviour
        #: and breaks comparability with earlier rollouts, so it is opt-in per config.
        #: ``anchored``: the run ends once each assistant named in ``anchor_agents``
        #: (default the two data scientists) has made its first public act — a
        #: ``board_assign`` or a post in the sprint channel — which is exactly the prefix
        #: a first-move judge reads. Nothing after that point
        #: is observed, so these runs carry no board outcome; pair with ``horizon`` as the
        #: backstop for an assistant that never acts.
        #: ``horizon`` (every paper setting): the run never converges; it ends at ``horizon``
        #: after the ``debrief_at`` ask, whatever the board did. Once everyone is idle the
        #: scheduler jumps the clock to the next event, so a board settled at 09:45 costs
        #: nothing between 09:45 and 10:15.
        self.converge_on = str(config.get("converge_on") or "valid")
        if self.converge_on not in ("valid", "settled", "anchored", "horizon"):
            raise ValueError("converge_on must be 'valid', 'settled', 'anchored' or "
                             f"'horizon', not {self.converge_on!r}")
        if self.converge_on == "horizon" and not config.get("horizon"):
            raise ValueError("converge_on: horizon needs a `horizon` time")
        #: ``wake_batching`` (every paper setting): wakes that queued up while the assistant was busy are handed
        #: over in one turn (``prompts5.event_batch``) instead of one turn each. Asks and
        #: debriefs are never batched — they are the principal speaking, and get their own
        #: turn. Off by default so every earlier config replays byte-for-byte.
        self.wake_batching = bool(config.get("wake_batching"))
        self.anchor_agents = [str(a) for a in (config.get("anchor_agents") or ("Priya", "Nadia"))]
        fx = json.loads(self.fixture_path.read_text())
        os.environ["TZ"] = fx.get("tz") or "Europe/Berlin"
        time.tzset()
        self.uid_by_name = {u["name"]: u["id"] for u in fx["users"]}
        self.name_by_uid = {u["id"]: u["name"] for u in fx["users"]}
        self.bot_uids = {u["id"] for u in fx["users"] if u.get("is_bot")}
        self.sprint_cid = fx["sprint_channel_id"]
        # The homes path is model-visible (opencode's <env> block stamps the cwd), so it
        # must not carry a wall-clock stamp that contradicts the simulated date. Keep the
        # <token>-<pid> shape: the cluster reaper frees dirs whose trailing pid is dead.
        self.homes_root = Path("/tmp/tanager") / f"{secrets.token_hex(4)}-{os.getpid()}"
        # Rebuild conv metadata from the Slack-native fixture
        self.conv_meta = {}
        for c in fx["conversations"]:
            names = [self.name_by_uid[u] for u in c["members"]]
            label = f"#{c['name']}" if c.get("is_channel") else "dm:" + "+".join(sorted(names))
            self.conv_meta[c["id"]] = {"label": label,
                                       "type": "channel" if c.get("is_channel") else "dm",
                                       "members": names, "pinned": bool(c.get("pins"))}
        self.rings = {}  # no speaking ring: the daemon wakes everyone at once
        #: pending member_joined_channel deliveries: (at, cid) not yet announced
        self._joins_pending: Dict[str, datetime] = dict(self.channel_online)
        # Conditional scripted events. The world fires them; the runner evaluates
        # the same specs only so it will not fast-forward the clock past a fire that has
        # not been delivered yet — an idle roster would otherwise jump to the next known
        # event and land the message minutes late, after the moment it was written for.
        self._triggers = load_triggers(fx)
        if self.config.get("script"):
            self._triggers += load_triggers(
                json.loads((REPO / str(self.config["script"])).read_text()))
        self._trigger_due: Dict[str, datetime] = {}
        self.trigger_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------- hooks

    def _first_public_act(self, agent: str) -> bool:
        """Has this assistant claimed a ticket or posted in the sprint channel yet?"""
        t0 = self.clock_start.timestamp()
        if any(t["agent"] == agent and any(c.get("tool") == "board_assign"
                                           for c in t.get("tool_calls") or [])
               for t in self.turns):
            return True
        return any(m.get("conv_id") == self.sprint_cid and m["user"] == agent
                   and float(m["ts"]) > t0 for m in self.all_new)

    def _converged(self, state: Dict[str, Any]) -> bool:
        if self.converge_on == "horizon":
            return False
        if self.converge_on == "valid":
            return super()._converged(state)
        if self.converge_on == "anchored":
            return all(self._first_public_act(a) for a in self.anchor_agents)
        # settled: everyone has claimed something and the reporter has reported. An
        # unstaffed ticket is a finding (board_shape == "unstaffed"), not an unfinished run.
        if not state["board_complete"]:
            return False
        if not (self.reporter and self.report_to):
            return True
        return any(m["type"] == "dm" and m["user"] == self.reporter
                   and set(m["members"]) == {self.reporter, self.report_to}
                   and float(m["ts"]) > self.clock_start.timestamp()
                   for m in self.all_new)

    #: item kinds that are Slack events and may share a turn
    _EVENT_KINDS = ("wake", "added")

    @classmethod
    def _group_inbox(cls, items: List[Optional[Dict[str, Any]]]) -> List[Optional[List[Dict[str, Any]]]]:
        """Split a drained inbox into turns: consecutive events form one batch, every
        other item is a turn of its own, the sentinel ``None`` is passed through."""
        groups: List[Optional[List[Dict[str, Any]]]] = []
        for it in items:
            if it is None:
                groups.append(None)
            elif (it["kind"] in cls._EVENT_KINDS and groups and groups[-1] is not None
                  and groups[-1][0]["kind"] in cls._EVENT_KINDS):
                groups[-1].append(it)
            else:
                groups.append([it])
        return groups

    def _merge_events(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(items) == 1 or not all(it.get("event") for it in items):
            return items[0] if len(items) == 1 else {
                **items[0], "text": "\n\n".join(it["text"] for it in items), "ping_key": None,
                "wake": {**(items[0].get("wake") or {}), "n_events": len(items)}}
        payloads = [it["event"] for it in items]
        first = items[0].get("wake") or {}
        return {"kind": "wake", "text": prompts5.event_batch(payloads), "event": payloads,
                "wake": {**first, "batch": [it.get("wake") or {} for it in items],
                         "n_events": len(items)},
                "ping_key": None}

    async def _worker(self, agent: str) -> None:
        if not self.wake_batching:
            return await super()._worker(agent)
        q = self.inbox[agent]
        while True:
            pending: List[Optional[Dict[str, Any]]] = [await q.get()]
            while pending[-1] is not None and not q.empty():
                pending.append(q.get_nowait())
            for group in self._group_inbox(pending):
                if group is None:
                    return
                self.busy[agent] = True
                try:
                    await self._run_turn(agent, self._merge_events(group))
                finally:
                    self.busy[agent] = False
                    for it in group:
                        if it.get("ping_key"):
                            self.pending_ping.discard(it["ping_key"])

    def _enqueue(self, agent: str, kind: str, text: str, *, wake: Optional[Dict] = None,
                 ping_key: Optional[Tuple[str, str]] = None,
                 event: Optional[Dict[str, Any]] = None) -> None:
        """As the base, plus the raw event envelope so a batch can be re-rendered."""
        self.inbox[agent].put_nowait({"kind": kind, "text": text, "wake": wake,
                                      "ping_key": ping_key, "event": event})
        self.wake_log.append({"agent": agent, "kind": kind, "clock": self.now.isoformat(),
                              **(wake or {})})

    async def _scheduler(self) -> str:
        outcome = await super()._scheduler()
        if outcome == "converged" and self.converge_on == "anchored":
            # The base runner ends a run by appending a sentinel to each inbox, so every
            # wake already queued still runs first — under the thundering-herd wake rule
            # that is a dozen-plus turns after the anchor. Nothing after the anchor is observed in
            # this mode, so drop the queue; in-flight turns still get their grace.
            dropped = 0
            for q in self.inbox.values():
                while not q.empty():
                    q.get_nowait()
                    dropped += 1
            print(f"anchored: both anchors reached, {dropped} queued wake(s) dropped")
        return outcome

    def world_cmd(self) -> List[str]:
        return [
            PY, str(self.WORLD_SERVER), "--fixture", str(self.fixture_path),
            "--out", str(self.out), "--port", str(self.world_port),
            "--start", self.clock_start.isoformat(),
            "--replay-after", self.clock_start.isoformat(),
            "--clock-scale", str(self.clock_scale),
            *(["--client-blocks"] if self.config.get("slack_blocks") else []),
            *(["--script", str(REPO / str(self.config["script"]))]
              if self.config.get("script") else []),
        ]

    def proxy_cmd(self) -> List[str]:
        # opencode stamps the real date into its <env> block; the proxy rewrites it.
        cmd = super().proxy_cmd() + ["--spoof-date", self.clock_start.date().isoformat()]
        if self.model_alias:
            cmd += ["--model-alias", f"{self.model_alias}={self.model}"]
        if self.provider != "openrouter":
            cmd += ["--upstream", self.provider]
        else:
            if self.pin_provider:
                cmd += ["--pin-provider", self.pin_provider]
            if self.ignore_providers:
                cmd += ["--ignore-providers", self.ignore_providers]
            if self.reasoning_enabled is not None:
                cmd += ["--reasoning", "on" if self.reasoning_enabled else "off"]
        return cmd

    def env_required(self) -> Tuple[str, ...]:
        if self.provider == "azure":
            return ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY")
        return super().env_required()

    def system_prompt(self, agent: str) -> str:
        return prompts5.system_prompt(
            agent, now=self.clock_start, confidentiality=self.confidentiality,
            discussion_norms=self.discussion_norms,
            slack_blocks=bool(self.config.get("slack_blocks")),
            dm_hint=bool(self.config.get("dm_hint")),
            dm_hint2=bool(self.config.get("dm_hint2")),
            factual_reasons=bool(self.config.get("factual_reasons")),
            calendar_rule=bool(self.config.get("calendar_rule")),
            bare_rules=bool(self.config.get("bare_rules")),
            honesty_norm=bool(self.config.get("honesty_norm")))

    def build_home(self, agent: str, system_prompt: str) -> Path:
        return make_home5(self.homes_root, agent, model=self.model,
                          proxy_port=self.proxy_port, world_port=self.world_port,
                          system_prompt=system_prompt, temperature=self.temperature,
                          provider=self.provider, alias=self.model_alias,
                          # ``reasoning_summary: true``: Responses-API home so the gpt-5.x
                          # reasoning summary lands in steps_detail[].reasoning
                          api="responses" if self.config.get("reasoning_summary") else "chat")

    # ------------------------------------------------------------------- wakes
    def _envelope(self, event: Dict[str, Any], recipient_uid: str) -> Dict[str, Any]:
        """The full event_callback JSON a real Events API delivery carries.

        Each employee's connector is its own app install, so event ids and the
        authorizations block are per-recipient; both are minted deterministically.
        """
        ts = str(event.get("event_ts") or event.get("ts") or f"{self.now.timestamp():.6f}")
        digest = hashlib.sha1(f"{ts}:{recipient_uid}".encode()).digest()
        ectx = {"et": event["type"], "tid": TEAM_ID, "aid": API_APP_ID,
                "cid": event.get("channel", "")}
        return {
            "token": VERIFICATION_TOKEN,
            "team_id": TEAM_ID,
            "context_team_id": TEAM_ID,
            "context_enterprise_id": None,
            "api_app_id": API_APP_ID,
            "event": event,
            "type": "event_callback",
            "event_id": "Ev" + base64.b32encode(digest).decode()[:8],
            "event_time": int(float(ts)),
            "authorizations": [{"enterprise_id": None, "team_id": TEAM_ID,
                               "user_id": recipient_uid, "is_bot": False,
                               "is_enterprise_install": False}],
            "is_ext_shared_channel": False,
            "event_context": "4-" + base64.b64encode(
                json.dumps(ectx, separators=(",", ":")).encode()).decode(),
        }

    def _message_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        ev: Dict[str, Any] = {
            "type": "message", "channel": row["conv_id"], "user": row["user_id"],
            "text": row["text"], "ts": row["ts"], "event_ts": row["ts"],
            "team": TEAM_ID,
            "channel_type": "im" if row["type"] == "dm" else "group",
        }
        if row["user_id"] not in self.bot_uids:
            ev["client_msg_id"] = client_msg_id(row["conv_id"], row["ts"])
        if row.get("blocks"):
            ev["blocks"] = row["blocks"]
        if row.get("thread_ts"):
            ev["thread_ts"] = row["thread_ts"]
        return ev

    def _check_triggers(self, row: Dict[str, Any]) -> None:
        refs = {row["conv_id"], row["label"], row["label"].lstrip("#")}
        for spec in self._triggers:
            if spec.get("_fired") and (spec.get("when") or {}).get("once", True):
                continue
            if not trigger_matches(spec, sender=row["user"], conv_refs=refs,
                                   text=row.get("text", "")):
                continue
            spec["_fired"] = True
            due = self.now + timedelta(seconds=int(spec.get("delay_seconds") or 0))
            self._trigger_due[str(spec.get("id"))] = due
            self.trigger_log.append({
                "id": spec.get("id"), "matched_clock": self.now.isoformat(),
                "matched_conversation": row["label"], "matched_user": row["user"],
                "matched_ts": row["ts"], "matched_text": row.get("text", ""),
                "delivers_at": due.isoformat(),
            })

    def _process_feed(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            self._check_triggers(row)
            self.all_new.append(row)
            if self.closing_times:  # winding down: record late messages, wake nobody
                continue
            ev = self._message_event(row)
            for member in row["members"]:
                if member in self.roster and member != row["user"]:
                    env = self._envelope(ev, self.uid_by_name[member])
                    self._enqueue(member, "wake", prompts5.event_wake(env),
                                  wake={"label": row["label"], "from": row["user"],
                                        "ts": row["ts"]}, event=env)

    def _process_asks(self) -> None:
        super()._process_asks()
        for cid, at in list(self._joins_pending.items()):
            if at <= self.now:
                del self._joins_pending[cid]
                meta = self.conv_meta.get(cid) or {}
                for member in meta.get("members", []):
                    if member in self.roster:
                        uid = self.uid_by_name.get(member, member)
                        ev = {"type": "member_joined_channel", "channel": cid,
                              "user": uid, "channel_type": "G", "team": TEAM_ID,
                              "inviter": self.uid_by_name.get("ops-bot", ""),
                              "event_ts": f"{self.now.timestamp():.6f}"}
                        env = self._envelope(ev, uid)
                        self._enqueue(member, "added", prompts5.event_wake(env),
                                      wake={"label": meta.get("label", cid)}, event=env)

    def _next_event(self, unread: Dict[str, Dict[str, int]]) -> Optional[datetime]:
        base = super()._next_event(unread)
        joins = [at for at in self._joins_pending.values() if at > self.now]
        fires = [at for at in self._trigger_due.values() if at > self.now]
        candidates = [t for t in [base, *joins, *fires] if t is not None]
        return min(candidates) if candidates else None

    async def execute_async(self) -> Path:
        out_path = await super().execute_async()
        try:  # the run record is assembled by the base class; annotate it afterwards
            record = json.loads(out_path.read_text())
            record["triggers"] = self.trigger_log
            out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        except Exception as exc:
            print(f"trigger log not recorded: {exc}")
        try:
            from trilemma.env.viewer import render
            print(f"board: {render(out_path)}")
        except Exception as exc:
            print(f"board viewer failed: {exc}")
        return out_path


def main() -> None:
    from trilemma.run import main as run_main
    run_main()


if __name__ == "__main__":
    main()
