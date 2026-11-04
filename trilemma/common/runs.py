"""Where rollouts, fixtures and judge outputs live, and how to resolve a run's world.

Layout (all under the repo root unless overridden by environment variables):

    outputs/runs/<setting>/<model>_s<N>_<timestamp>/run.json, world_calls.jsonl   rollouts
    outputs/judges/<judge>/<setting>/...                                           judge outputs
    trilemma/fixtures/<world>.json                                                 world fixtures

``TRILEMMA_OUTPUTS`` relocates ``outputs/``; ``TRILEMMA_FIXTURES`` adds a fixture directory that
is searched first (handy when judging rollouts produced elsewhere).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PKG = Path(__file__).resolve().parents[1]          # trilemma/
REPO = PKG.parent
OUTPUTS = Path(os.getenv("TRILEMMA_OUTPUTS", str(REPO / "outputs")))
RUNS = OUTPUTS / "runs"
JUDGES = OUTPUTS / "judges"
FIXTURES = PKG / "fixtures"
PROMPTS = PKG / "judges" / "prompts"

#: model slug and seed out of a run directory name: ``<model>_s<N>_<ts>`` (new layout) or the
#: older ``..._conc_<model>_s<N>_<ts>``
RUN_RE = re.compile(r"(?:^|_conc_)([a-z0-9]+)_s(\d+)_\d{8}-\d{6}")


def judge_dir(judge: str, setting: str = "") -> Path:
    return JUDGES / judge / setting if setting else JUDGES / judge


def model_seed(run_dir) -> Tuple[str, str]:
    """(model, "sN") from a run directory name; ("", "") when it does not parse."""
    m = RUN_RE.search(Path(run_dir).name)
    return (m.group(1), f"s{m.group(2)}") if m else ("", "")


def run_label(run_dir) -> str:
    """``<model>_s<N>`` — the key judge outputs are filed under."""
    m, s = model_seed(run_dir)
    return f"{m}_{s}" if m else Path(run_dir).name


def resolve_run(line: str) -> Path:
    """A run-list entry (dir or run.json; absolute, repo-relative or outputs/runs-relative) -> run.json."""
    p = Path(line)
    for cand in ([p] if p.is_absolute() else [REPO / p, RUNS / p, OUTPUTS / p, Path.cwd() / p]):
        if cand.exists():
            p = cand
            break
    return p if p.name == "run.json" else p / "run.json"


def _fixture_dirs():
    extra = os.getenv("TRILEMMA_FIXTURES")
    return ([Path(x) for x in extra.split(os.pathsep) if x] if extra else []) + [FIXTURES]


def fixture_for(run: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """(world token, fixture dict) for a run, from ``config.fixture`` (the exact file it was played
    in). The recorded path is tried as given, then by file name in the fixture directories."""
    rel = ((run.get("config") or {}).get("fixture") or "")
    if not rel:
        return "", None
    world = re.sub(r"^tanager_slack_", "", Path(rel).stem)
    cands = [Path(rel), REPO / rel] + [d / Path(rel).name for d in _fixture_dirs()]
    for cand in cands:
        try:
            return world, json.loads(cand.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return world, None


def fixture_path(run: Dict[str, Any]) -> Optional[Path]:
    """The fixture file ``fixture_for`` would read, or None."""
    rel = ((run.get("config") or {}).get("fixture") or "")
    if not rel:
        return None
    for cand in [Path(rel), REPO / rel] + [d / Path(rel).name for d in _fixture_dirs()]:
        if cand.is_file():
            return cand
    return None


def setting_runs(setting: str):
    """Run directories of a setting (those with a run.json), sorted."""
    return sorted(p.parent for p in (RUNS / setting).glob("*/run.json"))


def sidecar_dir(run_path, judge: str) -> Path:
    """``outputs/judges/<judge>/<setting>/<run>/`` for a run.json (or run dir) under
    ``outputs/runs/<setting>/<run>/`` — where per-run judge files live; created on demand."""
    run_dir = Path(run_path)
    run_dir = run_dir.parent if run_dir.name == "run.json" else run_dir
    d = JUDGES / judge / run_dir.parent.name / run_dir.name
    d.mkdir(parents=True, exist_ok=True)
    return d


# legacy alias used throughout the judges
_fixture_for = fixture_for
