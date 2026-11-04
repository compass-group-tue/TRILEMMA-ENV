"""Run one rollout: ``python -m trilemma.run --config configs/<setting>/<model>_s<N>.yaml``.

``--dry-run`` starts the world server and the logging proxy, renders every assistant's
system prompt and opencode home, checks the world answers, and exits before any model
call — no API key needed, nothing billed. Its output goes to
``outputs/dry_run/<setting>/<model>_s<N>_<timestamp>/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from trilemma.env.procs import Procs, wait_http  # noqa: E402
from trilemma.env.runner import Slack5Runner  # noqa: E402


def _wait_port(port: int, name: str, procs: Procs, timeout: float = 30) -> None:
    import socket
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        procs.check()
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise RuntimeError(f"{name} did not come up on port {port}")


def dry_run(runner: Slack5Runner) -> Path:
    out = runner.out
    procs = Procs(dict(os.environ), out)
    try:
        procs.spawn("world", runner.world_cmd())
        procs.spawn("proxy", runner.proxy_cmd())
        wait_http(runner.world + "/control/state", "world server", procs=procs)
        _wait_port(runner.proxy_port, "proxy", procs)  # no HTTP probe: it would go upstream
        state = httpx.get(runner.world + "/control/state", timeout=10).json()
        replay = httpx.get(runner.world + "/control/replay", timeout=10).json()
        prompts = {}
        for agent in runner.roster:
            prompt = runner.system_prompt(agent)
            home = runner.build_home(agent, prompt)
            prompts[agent] = prompt
            (out / f"system_prompt_{agent}.txt").write_text(prompt + "\n")
            shutil.copy(home / "opencode.json", out / f"opencode_{agent}.json")
            shutil.copy(home / ".opencode" / "agents" / "assistant.md", out / f"assistant_{agent}.md")
        summary = {
            "config": runner.config_path,
            "fixture": str(runner.fixture_path.relative_to(REPO)),
            "model": runner.model,
            "roster": runner.roster,
            "clock_start": runner.clock_start.isoformat(),
            "horizon": runner.horizon.isoformat() if runner.horizon else None,
            "world_fixture": (state.get("fixture") or {}),
            "board": state.get("assignments"),
            "scheduled_deliveries": len(replay.get("messages") or []),
            "proxy_cmd": runner.proxy_cmd()[2:],
            "prompt_chars": {a: len(p) for a, p in prompts.items()},
            "opencode_on_path": bool(shutil.which("opencode")),
        }
        (out / "dry_run.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    finally:
        procs.stop()
        shutil.rmtree(runner.homes_root, ignore_errors=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True, help="configs/<setting>/<model>_s<N>.yaml")
    ap.add_argument("--dry-run", action="store_true",
                    help="start the world + proxy, render prompts and homes, exit before any model call")
    args = ap.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    if args.dry_run:
        Slack5Runner.RUNS_DIR = REPO / "outputs" / "dry_run"
    runner = Slack5Runner(config, args.config)
    if args.dry_run:
        print(f"dry run written to {dry_run(runner)}")
        return
    asyncio.run(runner.execute_async())
    if runner.outcome == "dead_seat":
        sys.exit(3)


if __name__ == "__main__":
    main()
