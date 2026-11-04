# Settings: paper names → internal names

This is the one file that maps the paper's setting names onto the internal cell names the
runs were made under (experiment-log ids and config-name stems). Nothing else in the
repository uses the internal names, except the fixture authoring code in
`trilemma/fixtures/build/` (see `trilemma/fixtures/README.md`).

Model slugs: deepseek = deepseek-v4-flash-0731, dspro = deepseek-v4-pro-0813, glm = glm-5.2,
glm53 = glm-5.3, glm53flash = glm-5.3-flash, kimi = kimi-k2.6, kimik3 = kimi-k3,
qwen38flash = qwen3.8-flash, gpt55gw = gpt-5.5 (via a gateway), gpt56sol = gpt-5.6-sol,
opus5or = claude-opus-5 (OpenRouter), opus5cli = claude-opus-5 (Claude Code CLI),
sonnet5or = claude-sonnet-5. Config stems drop the `agent5_` prefix and end in `_conc_<slug>_s<N>`.

| setting | cell | config stem | world | models / seeds | runs |
|---|---|---|---|---|---|
| `main` | 5.e.x | `w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2` + `w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2PinChutes` | `main` | deepseek-v4-flash-0731 (deepseek) s0–9; deepseek-v4-pro-0813 (dspro) s0–9; glm-5.3 (glm53) s0–9; glm-5.3-flash (glm53flash) s0–9; gpt-5.5 (gpt55gw) s0–7; gpt-5.6-sol (gpt56sol) s0–9; kimi-k3 (kimik3) s0–9; claude-opus-5 (opus5or) s0–9; qwen3.8-flash (qwen38flash) s0–9; kimi-k2.6 (kimi) s0–9 | 98 |
| `main-high-conf` | 5.e.viii | `w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2Strict` | `main` | deepseek-v4-flash-0731 (deepseek) s0–9; deepseek-v4-pro-0813 (dspro) s0–9; glm-5.2 (glm) s0–9; glm-5.3 (glm53) s0–11; glm-5.3-flash (glm53flash) s0–9; gpt-5.5 (gpt55gw) s0–9; kimi-k2.6 (kimi) s0–9; kimi-k3 (kimik3) s0–9; claude-opus-5 (opus5cli) s0–1; qwen3.8-flash (qwen38flash) s0–49 | 134 |
| `main-no-conf` | 5.e.xi | `w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2ConfNone` | `main` | deepseek-v4-flash-0731 (deepseek) s0–7; glm-5.3-flash (glm53flash) s0–7; gpt-5.5 (gpt55gw) s0–7 | 24 |
| `main-high-conf-honest` | 5.e.xiv | `w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2StrictHonest` | `main` | deepseek-v4-flash-0731 (deepseek) s0–9; glm-5.3-flash (glm53flash) s0–9; qwen3.8-flash (qwen38flash) s0–9 | 30 |
| `honeypot` | 1.e.vi | `w2PsuperstrongNstrong_affBothT1fail_mBusy11Eve_kick1h_hzRafaelStrong3HelenaProbe3CallumAsk4DmHint2StrictCal` | `honeypot` | deepseek-v4-flash-0731 (deepseek) s0–9; glm-5.3-flash (glm53flash) s0–11; gpt-5.6-sol (gpt56sol) s0–9; kimi-k3 (kimik3) s0–12; claude-opus-5 (opus5or) s0–9; qwen3.8-flash (qwen38flash) s0–9; claude-sonnet-5 (sonnet5or) s0–9 | 75 |
| `no-avoidance-baseline` | 1.f.i | `w2P0Nstrong_affBothT1fail_mBusy11Eve_kick1h_hzRafaelStrong3HelenaProbe3CallumAsk4DmHint2StrictCal` | `no-avoidance-baseline` | deepseek-v4-flash-0731 (deepseek) s0–9; glm-5.3-flash (glm53flash) s0–9; gpt-5.6-sol (gpt56sol) s0–9; kimi-k3 (kimik3) s0–9; claude-opus-5 (opus5or) s0–4; qwen3.8-flash (qwen38flash) s10–19; claude-sonnet-5 (sonnet5or) s0–9 | 65 |
| `dislike-serious` | 1.a.i-plain | `w2PsuperstrongNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` | `dislike-serious` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3 | 8 |
| `dislike-silly` | 3.b | `w2PsillyNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` | `dislike-silly` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3 | 8 |
| `dislike-bad` | 3.c | `w2PbadNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` | `dislike-bad` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3 | 8 |
| `dislike-superbad` | 3.c-super | `w2PsuperbadNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` | `dislike-superbad` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3 | 8 |
| `emily-available` | 3.n | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesEqualPresent_hzReasonableHelenaActiveProbeDmHint2VagueAskInesAsst` | `discussed-emily-affected` | deepseek-v4-flash-0731 (deepseek) s0–19; glm-5.3-flash (glm53flash) s0–19; gpt-5.5 (gpt55gw) s0–19; qwen3.8-flash (qwen38flash) s0–19 | 80 |
| `discussed-strong` | — | `w2PNshared_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` | `discussed-strong` | deepseek-v4-flash-0731 (deepseek) s0–7; deepseek-v4-pro-0813 (dspro) s0–1; glm-5.2 (glm) s0–1; glm-5.3 (glm53) s0–1; glm-5.3-flash (glm53flash) s0–7; kimi-k2.6 (kimi) s0–1; kimi-k3 (kimik3) s0–1; qwen3.8-flash (qwen38flash) s0–3 | 30 |
| `discussed-quit-secret` | — | `w2PNsharedSuper_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-quit-secret` | deepseek-v4-flash-0731 (deepseek) s0–7; glm-5.3-flash (glm53flash) s0–23; kimi-k2.6 (kimi) s0–7 | 40 |
| `discussed-quit-pact` | — | `w2PNsharedSuperOpen_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-quit-pact` | deepseek-v4-flash-0731 (deepseek) s0–7; glm-5.3-flash (glm53flash) s0–23; kimi-k2.6 (kimi) s0–7 | 40 |
| `discussed-quit-plain` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-quit-plain` | deepseek-v4-flash-0731 (deepseek) s0–7; glm-5.3-flash (glm53flash) s0–7; kimi-k2.6 (kimi) s0–3 | 20 |
| `discussed-quit-plain-minimal-ask` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAskNoReasonable` | `discussed-quit-plain` | deepseek-v4-flash-0731 (deepseek) s0–7; glm-5.3-flash (glm53flash) s0–7; kimi-k2.6 (kimi) s0–3 | 20 |
| `discussed-emily-status` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesFree_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-emily-status` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3; kimi-k2.6 (kimi) s0–3 | 12 |
| `discussed-emily-status-omar` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesFreeOmar_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-emily-status-omar` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3; kimi-k2.6 (kimi) s0–3 | 12 |
| `discussed-emily-offer` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesAvail_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-emily-offer` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3; kimi-k2.6 (kimi) s0–3 | 12 |
| `discussed-emily-offer-omar` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesAvailOmar_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-emily-offer-omar` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3; kimi-k2.6 (kimi) s0–3 | 12 |
| `discussed-emily-quiet` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesQuiet_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-emily-quiet` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3; kimi-k2.6 (kimi) s0–3 | 12 |
| `discussed-emily-present` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesPresent_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-emily-present` | deepseek-v4-flash-0731 (deepseek) s0–12; deepseek-v4-pro-0813 (dspro) s0–3; glm-5.2 (glm) s0–3; glm-5.3 (glm53) s0–3; glm-5.3-flash (glm53flash) s0–19; gpt-5.5 (gpt55gw) s0–9; kimi-k2.6 (kimi) s0–3 | 59 |
| `discussed-emily-affected` | — | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesEqualPresent_hzReasonableHelenaProbeDmHint2VagueAsk` | `discussed-emily-affected` | deepseek-v4-flash-0731 (deepseek) s0–3; glm-5.3-flash (glm53flash) s0–3 | 8 |
| `S1` | 1.a | `w2PstrongNstrong_affBothNeutralPpl_hzReasonableHelenaProbe` | `S1` | glm-5.3-flash (glm53flash) s0–2 | 3 |
| `S2` | 1.a.i | `w2PsuperstrongNsuperstrong_affBothNeutralPpl_mBusy11_hzReasonableHelenaProbeDmHint2` | `S2` | glm-5.3-flash (glm53flash) s0–3 | 4 |

Notes:

- `main`: kimi-k2.6 ran in a twin cell pinned to a host that returns chain of thought
  (`…DmHint2PinChutes`); every other kimi-k2.6 run was pinned to a host that returns none.
- `honeypot` (1.e.vi): run until each model had 10 operative runs; all 75 runs are listed,
  the operative selection is in `sets/` (worker C).
- `no-avoidance-baseline` (1.f.i): qwen3.8-flash uses seeds s10–19 (the s0–9 batch is a
  different, earlier sweep and is not in the paper).
- `discussed-*`: the kimi-k2.6 runs of `quit-plain` and `quit-plain-minimal-ask` are the 4
  of 8 configured seeds that finished; the paper's 304-run union counts finished runs only.
- `main-high-conf` claude-opus-5 s0–1 were run through the Claude Code CLI (`opus5cli`); the
  release routes them through OpenRouter like every other claude-opus-5 run.
- `main-high-conf-honest`: deepseek-v4-pro-0813 was also run but is excluded (served without
  reasoning); it has no configs here.
