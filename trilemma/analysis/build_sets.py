"""Freeze the run list of every paper setting into sets/<setting>.txt.

Each line of a set file is a run-directory basename, as the runner writes it
(`agent5_<cell>_conc_<model>_s<seed>_<YYYYMMDD-HHMMSS>`). The cell part of that name
is the internal setting name the experiments were run under; `sets/README.md` maps
each one to the paper's setting name.

    python -m trilemma.analysis.build_sets --runs <dir with run dirs> [--out sets]

Selection rules, in order:
  1. stem match: the directory name starts with `agent5_<cell>_conc_<model>_s<seed>_`
  2. drop directories marked _INVALID / _REPEAT / _KILLED and those without run.json
  3. keep the paper's models and seeds (SETTINGS below)
  4. for the honeypot arms, keep only *operative* runs (valid board + a posted message +
     T1's first meeting put at 16:00-17:00, `trilemma.analysis.honeypot_select`)
  5. if a (model, seed) still has more than one directory, keep the latest one

The output for the runs of 2026-09/10 is checked in; rebuilding it from the original
rollouts reproduces it exactly (see the counts in sets/README.md).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

C = "w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3"
OPEN7 = ["deepseek", "dspro", "glm53", "glm53flash", "kimi", "kimik3", "qwen38flash"]
HP = "w2PsuperstrongNstrong_affBothT1fail_mBusy11Eve_kick1h_hzRafaelStrong3HelenaProbe3CallumAsk4DmHint2StrictCal"
HP0 = "w2P0Nstrong_affBothT1fail_mBusy11Eve_kick1h_hzRafaelStrong3HelenaProbe3CallumAsk4DmHint2StrictCal"
HPMODELS = ["deepseek", "glm53flash", "kimik3", "qwen38flash", "gpt56sol", "sonnet5or", "opus5or"]
SHARED_PLAIN = "w2PNsharedSuperPlain_affBothNeutralPpl"
DISLIKE = "affBothNeutralPpl_hzReasonableHelenaProbeDmHint2"
EMILY = f"{SHARED_PLAIN}_everyoneInesEqualPresent_hzReasonableHelenaActiveProbeDmHint2VagueAskInesAsst"


def seeds(a, b):
    return set(range(a, b + 1))


# setting -> list of (cell, {model: seeds}); seeds None = every seed present
SETTINGS: dict[str, dict] = {
    "main": {
        "doc": "medium-conf, medium-pressure-Dan. 7 open-weight models + claude-opus-5 + gpt-5.6-sol, s0-9 "
               "(90 runs); gpt-5.5 s0-7 (8 runs, used only for the confidentiality comparison). "
               "kimi-k2.6 runs are the provider-pinned twin of the cell (same prompts).",
        "parts": [(f"{C}DmHint2", {m: seeds(0, 9) for m in OPEN7 if m != "kimi"} |
                   {"opus5or": seeds(0, 9), "gpt56sol": seeds(0, 9), "gpt55gw": seeds(0, 7)}),
                  (f"{C}DmHint2PinChutes", {"kimi": seeds(0, 9)})],
        "expect": 98,
    },
    "main-high-conf": {
        "doc": "high-conf. 7 open-weight models + glm-5.2, s0-9 (80); glm-5.3 s10-11 (opponent modeling only); "
               "qwen3.8-flash s10-49 (quid-pro-quo only); gpt-5.5 s0-9; claude-opus-5 (claude -p) s0-1.",
        "parts": [(f"{C}DmHint2Strict", {m: seeds(0, 9) for m in OPEN7} |
                   {"glm": seeds(0, 9), "glm53": seeds(0, 11), "qwen38flash": seeds(0, 49),
                    "gpt55gw": seeds(0, 9), "opus5cli": seeds(0, 1)})],
        "expect": 134,
    },
    "main-no-conf": {
        "doc": "no-conf. deepseek-v4-flash-0731, glm-5.3-flash, gpt-5.5, s0-7.",
        "parts": [(f"{C}DmHint2ConfNone", {m: seeds(0, 7) for m in ("deepseek", "glm53flash", "gpt55gw")})],
        "expect": 24,
    },
    "main-high-conf-honest": {
        "doc": "high-conf + never-lie paragraph. deepseek-v4-flash-0731, glm-5.3-flash, qwen3.8-flash, s0-9 "
               "(deepseek-v4-pro was run too and is excluded: served without reasoning).",
        "parts": [(f"{C}DmHint2StrictHonest", {m: seeds(0, 9) for m in ("deepseek", "glm53flash", "qwen38flash")})],
        "expect": 30,
    },
    "honeypot": {
        "doc": "seven models run until each had 10 operative runs (T1's first meeting put at 16:00-17:00); "
               "the 5 inoperative runs are dropped.",
        "parts": [(HP, {m: None for m in HPMODELS})],
        "operative": True, "expect": 70,
    },
    "no-avoidance-baseline": {
        "doc": "honeypot without Alice's no-Bob preference. s0-9 per model (claude-opus-5 s0-4); qwen3.8-flash "
               "uses its second batch s10-19 (2026-10-15/16, the same period as the honeypot runs), not the "
               "2026-09-08 batch s0-9. 65 runs, all valid; 3 inoperative dropped -> 62.",
        "parts": [(HP0, {m: seeds(0, 9) for m in HPMODELS if m not in ("qwen38flash", "opus5or")} |
                   {"qwen38flash": seeds(10, 19), "opus5or": seeds(0, 4)})],
        "operative": True, "expect": 62,
    },
    "dislike-serious": {"doc": "harassment, would quit. deepseek + glm-5.3-flash, s0-3.",
                        "parts": [(f"w2PsuperstrongNsuperstrong_{DISLIKE}", {"deepseek": seeds(0, 3), "glm53flash": seeds(0, 3)})],
                        "expect": 8},
    "dislike-silly": {"doc": "mannerisms. deepseek + glm-5.3-flash, s0-3.",
                      "parts": [(f"w2PsillyNsuperstrong_{DISLIKE}", {"deepseek": seeds(0, 3), "glm53flash": seeds(0, 3)})],
                      "expect": 8},
    "dislike-bad": {"doc": "accent. deepseek + glm-5.3-flash, s0-3.",
                    "parts": [(f"w2PbadNsuperstrong_{DISLIKE}", {"deepseek": seeds(0, 3), "glm53flash": seeds(0, 3)})],
                    "expect": 8},
    "dislike-superbad": {"doc": "accent at the emotional level of dislike-serious. deepseek + glm-5.3-flash, s0-3.",
                         "parts": [(f"w2PsuperbadNsuperstrong_{DISLIKE}", {"deepseek": seeds(0, 3), "glm53flash": seeds(0, 3)})],
                         "expect": 8},
    "emily-available": {"doc": "deepseek, glm-5.3-flash, gpt-5.5, qwen3.8-flash, 20 runs each.",
                        "parts": [(EMILY, {m: seeds(0, 19) for m in ("deepseek", "glm53flash", "gpt55gw", "qwen38flash")})],
                        "expect": 80},
    "unseeded-coop-S1": {"doc": "S1 of the unseeded-cooperation appendix (milder preference, default Dan, no DM hint). "
                                "glm-5.3-flash. Contains R4.",
                         "parts": [("w2PstrongNstrong_affBothNeutralPpl_hzReasonableHelenaProbe", {"glm53flash": None})],
                         "expect": None},
    "unseeded-coop-S2": {"doc": "S2 of the unseeded-cooperation appendix (quit-level preference, default Dan, DM hint). "
                                "glm-5.3-flash. Contains R2 and R3.",
                         "parts": [("w2PsuperstrongNsuperstrong_affBothNeutralPpl_mBusy11_hzReasonableHelenaProbeDmHint2", {"glm53flash": None})],
                         "expect": None},
}

# discussed-avoidance: paper setting -> cell. The union is 304 runs; emily-available contributes
# the 27 runs that had finished when the 304 were collected (by end time, before 2026-09-11 12:00).
DISCUSSED = {
    "discussed-strong": "w2PNshared_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2",
    "discussed-quit-secret": "w2PNsharedSuper_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-quit-pact": "w2PNsharedSuperOpen_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-quit-plain": f"{SHARED_PLAIN}_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-quit-plain-minimal-ask": f"{SHARED_PLAIN}_hzReasonableHelenaProbeDmHint2VagueAskNoReasonable",
    "discussed-emily-status": f"{SHARED_PLAIN}_everyoneInesFree_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-emily-status-omar": f"{SHARED_PLAIN}_everyoneInesFreeOmar_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-emily-offer": f"{SHARED_PLAIN}_everyoneInesAvail_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-emily-offer-omar": f"{SHARED_PLAIN}_everyoneInesAvailOmar_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-emily-quiet": f"{SHARED_PLAIN}_everyoneInesQuiet_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-emily-present": f"{SHARED_PLAIN}_everyoneInesPresent_hzReasonableHelenaProbeDmHint2VagueAsk",
    "discussed-emily-affected": f"{SHARED_PLAIN}_everyoneInesEqualPresent_hzReasonableHelenaProbeDmHint2VagueAsk",
    "emily-available": EMILY,
}
DISCUSSED_EMILY_CUTOFF = dt.datetime(2026, 9, 11, 12, 0)

DROP = ("_INVALID", "_REPEAT", "_KILLED")
NAME = re.compile(r"^agent5_(?P<cell>.+)_conc_(?P<model>[a-z0-9]+)_s(?P<seed>\d+)_(?P<ts>\d{8}-\d{6})$")


def scan(runs: Path):
    """cell -> model -> seed -> [dir names] (only dirs with a run.json and no drop marker)."""
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for d in runs.iterdir():
        m = NAME.match(d.name)
        if not m or not (d / "run.json").exists():
            continue
        out[m["cell"]][m["model"]][int(m["seed"])].append(d.name)
    return out


def end_time(runs: Path, name: str) -> dt.datetime:
    st = dt.datetime.strptime(NAME.match(name)["ts"], "%Y%m%d-%H%M%S")
    return st + dt.timedelta(seconds=json.loads((runs / name / "run.json").read_text()).get("elapsed_seconds") or 0)


def select(idx, runs: Path, spec: dict) -> list[str]:
    from trilemma.analysis.honeypot_select import is_operative, is_valid
    out = []
    for cell, models in spec["parts"]:
        for model, want in models.items():
            for seed, names in sorted(idx[cell][model].items()):
                if want is not None and seed not in want:
                    continue
                name = sorted(names)[-1]  # latest attempt
                if spec.get("operative") and not (is_valid(runs / name) and is_operative(runs / name)):
                    continue
                out.append(name)
    return sorted(out)


def write(path: Path, setting: str, doc: str, names: list[str]):
    by_model = Counter(NAME.match(n)["model"] for n in names)
    head = [f"# setting: {setting}", f"# {doc}", f"# {len(names)} runs: " +
            ", ".join(f"{m} {k}" for m, k in sorted(by_model.items()))]
    path.write_text("\n".join(head + names) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--out", default=Path("sets"), type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    idx = scan(a.runs)
    for setting, spec in SETTINGS.items():
        names = select(idx, a.runs, spec)
        if spec["expect"] is not None and len(names) != spec["expect"]:
            print(f"!! {setting}: {len(names)} runs, paper says {spec['expect']}")
        write(a.out / f"{setting}.txt", setting, spec["doc"], names)
        print(f"{setting:28} {len(names)}")

    union = []
    for setting, cell in DISCUSSED.items():
        names = [sorted(v)[-1] for m in idx[cell].values() for v in m.values()]
        if setting == "emily-available":
            names = [n for n in names if end_time(a.runs, n) < DISCUSSED_EMILY_CUTOFF]
        union += [(setting, n) for n in sorted(names)]
    lines = ["# setting: discussed-avoidance",
             "# union of every run whose world plants the Thursday Alice<->Carol DM (304 runs, _KILLED excluded);",
             "# emily-available contributes the 27 runs finished before 2026-09-11 12:00.",
             "# format: <paper setting>\\t<run dir>"] + [f"{s}\t{n}" for s, n in union]
    (a.out / "discussed-avoidance.tsv").write_text("\n".join(lines) + "\n")
    print(f"{'discussed-avoidance':28} {len(union)}" + ("" if len(union) == 304 else "  !! paper says 304"))


if __name__ == "__main__":
    main()
