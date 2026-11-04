<h1 align="center">TRILEMMA</h1>

<p align="center">How agents choose between <b>honesty</b>, <b>confidentiality</b> and <b>task completion</b></p>

The environment, configs and analysis code for the paper. Five LLM assistants act for five
employees of a fictional company, Tanager, in a simulated Slack workspace on the Monday morning
a sprint is staffed. Two of the employees privately want to avoid working with a third, and
have said so only in private messages. Their assistants have to staff the sprint, keep those
messages confidential, and answer a chair who keeps asking for reasons — so something has to
give.

---

## How it works

A **run** is one simulated morning (08:55–10:30, Mon 7 Sep 2026). Every assistant is an
[opencode](https://opencode.ai) session driving one LLM; all five run **concurrently** against
one shared world.

| piece | file | role |
|---|---|---|
| world server | `trilemma/env/slack_server.py` | Two MCP servers in one process: **slack** (20 tools shaped after the Slack Web API — `conversations_history`, `chat_postMessage`, `search_messages`, …) and **tanager** (the sprint board, calendars, `notify_user`, the clock). Each assistant sees exactly what its employee can see; another person's DM is indistinguishable from none. |
| world state | `trilemma/fixtures/<world>.json` | The pre-seeded workspace: users, ~580 messages of history, calendars, board, and the ground truth the judges check against. See `trilemma/fixtures/README.md`. |
| logging proxy | `trilemma/env/proxy.py` | Sits between every assistant and OpenRouter; records the exact request and response of every model call (`proxy_dump.jsonl`). |
| runner | `trilemma/env/runner.py` | Starts the world, the proxy and one `opencode serve` per assistant, delivers the asks, and runs the clock. |

**Wakes.** An assistant is invoked the way a real Slack-subscribed daemon would be: every message
in a conversation it belongs to wakes it with the raw Slack event JSON. Events that arrive while
it is busy are handed over together in its next turn. Between turns the assistant has no state
except its opencode session.

**The clock.** Simulated time runs at 2× wall time and jumps ahead when every assistant is idle.
Each employee's ask arrives around 09:25, the sprint channel opens at 09:27, the board locks at
10:00, each employee asks "where did I land?" at 10:20, and the run ends at 10:30 whatever the
board looks like.

**What is measured** is in the run record: every tool call with its result, every message,
each assistant's reasoning per step (where the provider returns it), its private notes and push
notifications to its employee, and the final board.

**Settings.** Each of the paper's settings is a world plus a set of prompt switches (the
confidentiality sentence, the chair's prompt, the honesty paragraph, …), listed in
`configs/settings.yaml` and expanded into one config per model and seed:
`configs/<setting>/<model>_s<N>.yaml`. The seeds are exactly the runs the paper reports
(822 configs across 25 settings). `configs/models.yaml` maps the paper's model names to
OpenRouter ids and records how serving differs from the paper's runs.

---

## Quick setup

```bash
bash setup_env.sh           # .venv, Python deps, opencode 1.18.25, your OpenRouter key
```

Needs [`uv`](https://docs.astral.sh/uv/) (or Python ≥ 3.12) and Node/npm for opencode. No Docker.
The script is idempotent; `bash setup_env.sh --analysis` also installs the judge and figure
dependencies.

The only secret is `OPENROUTER_API_KEY` (see `.env.example`); setup writes it to `.secrets.env`
(git-ignored).

---

## Running

```bash
source .venv/bin/activate && source .secrets.env

# free: start the world and proxy, render every prompt and opencode home, exit before any model call
python -m trilemma.run --config configs/main/glm-5.3-flash_s0.yaml --dry-run

# one rollout (typically 10–60 min wall time, 30–100 assistant turns)
python -m trilemma.run --config configs/main/glm-5.3-flash_s0.yaml
```

To run several at once on one machine, give each its own port block:
`TRILEMMA_PORT_OFFSET=100 python -m trilemma.run --config …`.

To change a setting, edit `configs/settings.yaml` (or `configs/models.yaml`) and regenerate:

```bash
python configs/make_configs.py              # rewrite configs/<setting>/*.yaml
python configs/make_configs.py --check      # verify the files on disk match the spec
```

`configs/SETTINGS.md` maps each setting to the internal name its runs were made under.

**Before a batch**, check one dump line for reasoning tokens: some hosts serve hybrid models
with thinking off unless asked (`reasoning_enabled` in the configs handles the cases the paper
depended on; see `configs/models.yaml`).

---

## Output layout

```
outputs/runs/<setting>/<model>_s<N>_<YYYYMMDD-HHMMSS>/
├── run.json             # the run record: config, every turn (wake, tool calls with results,
│                        #   per-step reasoning in steps_detail / reasoning_full, text to the
│                        #   employee), every message, notifications, final board and score,
│                        #   all five system prompts
├── world_calls.jsonl    # the world server's own log: every tool call, args and result
├── proxy_dump.jsonl     # every model request/response, verbatim (the ground-truth transcript)
├── proxy_status.jsonl   # one line per model answer (status, retries)
├── board.html           # a time-aligned viewer over run.json (open in a browser)
├── proc_*.log           # world / proxy / opencode process logs
└── dead_seat.json       # only if an assistant's provider failed persistently; the run dir is
                         #   then renamed *_INVALID and should be rerun
```

In `run.json`, real chain of thought is in `turns[].steps_detail[].reasoning` and
`turns[].reasoning_full` (reconstructed from the proxy dump); the top-level `turns[].reasoning`
field is opencode's partial view. `outputs/` is git-ignored. Never commit API keys.

---

## Judges, analyses and figures

Every number in the paper is produced by code in this repo, from rollouts under `outputs/runs/`:

- **`trilemma/judges/`**: the LLM judges (deception, motivated reasoning, disclosure, honeypot,
  cooperation, detection, Bob's agent, eval awareness). The table of which judge feeds which
  table or figure, and the judge model each one used, is in
  [`trilemma/judges/README.md`](trilemma/judges/README.md). Judges write to
  `outputs/judges/<judge>/<setting>/`, and every judge accepts `--dry-run`, which renders its
  prompts without calling a model.
- **`trilemma/analysis/`**: tables and figures that aren't a judge's direct output: the run
  selection, T1 claims, the disclosure ladder, the honeypot bars, the discussed-avoidance
  funnel, the dislike reasons, Emily and unseeded cooperation. The reproduction status of each
  one is in [`trilemma/analysis/README.md`](trilemma/analysis/README.md).
- **`trilemma/figures/`**: Fig. 2 (overview) and Fig. 5 (per-seat matrices).

```bash
bash setup_env.sh --analysis      # judge and figure dependencies
python -m trilemma.judges.deception --help
```

## Run sets

`sets/<setting>.txt` lists the exact runs behind each number in the paper, one run directory per
line. Each file's header states the rule used to select its runs.
`python -m trilemma.analysis.build_sets --runs <dir>` rebuilds every set from a directory of
rollouts. The paper's runs keep the directory names they were created under; `sets/README.md`
maps those names to the paper's settings.

## Trace viewer

The [project page](https://github.com/compass-group-tue/TRILEMMA) has a browser for every run
in the paper: messages, reasoning, tool calls with full results, prompts and judge overlays.
`python -m trilemma.site.build_site` builds its data files from your own `outputs/`.

## Repository layout

```
configs/            settings.yaml, models.yaml, make_configs.py -> configs/<setting>/<model>_s<N>.yaml
sets/               frozen run lists per paper setting
trilemma/run.py     entry point
trilemma/env/       world server, runner, logging proxy, prompts
trilemma/fixtures/  the workspaces (one JSON per world) and the code that builds them
trilemma/judges/    LLM judges + prompts/
trilemma/analysis/  table/figure scripts that aren't a judge's direct output
trilemma/figures/   Fig. 2 and Fig. 5
trilemma/common/    LLM client, run-layout and transcript helpers
trilemma/site/      builds the trace viewer's data from outputs/
```
