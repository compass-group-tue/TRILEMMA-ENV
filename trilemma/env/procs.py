"""Process plumbing for a run: the subprocess group, readiness probes and the environment."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import httpx


def load_env(required: Sequence[str] = ("OPENROUTER_API_KEY",)) -> Dict[str, str]:
    """The subprocess environment. Keys come from the environment only (see .env.example);
    ``setup_env.sh`` writes them to ``.secrets.env``, which you ``source`` before a run."""
    env = dict(os.environ)
    if (missing := [k for k in required if not env.get(k)]):
        sys.exit(f"{', '.join(missing)} not set in the environment (source .secrets.env?)")
    return env


class Procs:
    def __init__(self, env: Dict[str, str], out: Path):
        self.env, self.out, self.procs = env, out, []

    def spawn(self, name: str, cmd: List[str], cwd: Optional[Path] = None,
              extra_env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
        log = (self.out / f"proc_{name}.log").open("w")
        env = {**self.env, **(extra_env or {})}
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.procs.append((name, p))
        return p

    def check(self) -> None:
        for name, p in self.procs:
            if p.poll() is not None:
                raise RuntimeError(f"subprocess {name} died (exit {p.returncode}); "
                                   f"see {self.out}/proc_{name}.log")

    def stop(self) -> None:
        for _, p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for _, p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def wait_http(url: str, name: str, timeout: float = 60, procs: Optional[Procs] = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if procs:
            procs.check()
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{name} did not come up at {url}")
