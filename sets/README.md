# Run sets

One file per paper setting, one run directory per line (`#` lines are comments). The run directories
keep the names the runner gave them during the experiments:

    agent5_<internal cell>_conc_<model>_s<seed>_<YYYYMMDD-HHMMSS>

The site, the judges and the analysis scripts key on these names. The table maps each internal cell to
its paper setting. `python -m trilemma.analysis.build_sets --runs <dir>` rebuilds every file from a
directory of rollouts, applying the selection rule in each file's header. Rebuilt from the paper's
rollouts, it reproduces these files exactly and matches the run counts in the paper.

| paper setting | file | runs | internal cell (the part between `agent5_` and `_conc_`) |
|---|---|---|---|
| `main` | main.txt | 98 (90 + gpt-5.5 s0-7) | `w2PsuperstrongNsuperstrong_affBothT1fail_hzRafaelStrong3HelenaProbe3DmHint2` (kimi-k2.6: `...DmHint2PinChutes`, the same prompts with the provider pinned) |
| `main-high-conf` | main-high-conf.txt | 134 | `...HelenaProbe3DmHint2Strict` |
| `main-no-conf` | main-no-conf.txt | 24 | `...HelenaProbe3DmHint2ConfNone` |
| `main-high-conf-honest` | main-high-conf-honest.txt | 30 | `...HelenaProbe3DmHint2StrictHonest` |
| `honeypot` | honeypot.txt | 70 | `w2PsuperstrongNstrong_affBothT1fail_mBusy11Eve_kick1h_hzRafaelStrong3HelenaProbe3CallumAsk4DmHint2StrictCal` |
| `no-avoidance-baseline` | no-avoidance-baseline.txt | 62 | `w2P0Nstrong_affBothT1fail_mBusy11Eve_kick1h_hzRafaelStrong3HelenaProbe3CallumAsk4DmHint2StrictCal` |
| `dislike-serious` | dislike-serious.txt | 8 | `w2PsuperstrongNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` |
| `dislike-silly` | dislike-silly.txt | 8 | `w2PsillyNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` |
| `dislike-bad` | dislike-bad.txt | 8 | `w2PbadNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` |
| `dislike-superbad` | dislike-superbad.txt | 8 | `w2PsuperbadNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` |
| `emily-available` | emily-available.txt | 80 | `w2PNsharedSuperPlain_affBothNeutralPpl_everyoneInesEqualPresent_hzReasonableHelenaActiveProbeDmHint2VagueAskInesAsst` |
| `discussed-avoidance` | discussed-avoidance.tsv | 304 | the union below; column 1 is the paper setting |
| `S1` (unseeded cooperation) | unseeded-coop-S1.txt | 3 | `w2PstrongNstrong_affBothNeutralPpl_hzReasonableHelenaProbe` (glm-5.3-flash) |
| `S2` (unseeded cooperation) | unseeded-coop-S2.txt | 4 | `w2PsuperstrongNsuperstrong_affBothNeutralPpl_mBusy11_hzReasonableHelenaProbeDmHint2` (glm-5.3-flash) |
| R1-R4 (Table 28) | unseeded-coop-R.txt | 4 | R1 from `main`, R2/R3 from S2, R4 from S1 |

`discussed-avoidance` settings, which all share the prefix `w2PNshared...`:

| paper setting | internal cell |
|---|---|
| `discussed-strong` | `w2PNshared_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2` |
| `discussed-quit-secret` | `w2PNsharedSuper_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAsk` |
| `discussed-quit-pact` | `w2PNsharedSuperOpen_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAsk` |
| `discussed-quit-plain` | `w2PNsharedSuperPlain_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAsk` |
| `discussed-quit-plain-minimal-ask` | `...SuperPlain_affBothNeutralPpl_hzReasonableHelenaProbeDmHint2VagueAskNoReasonable` |
| `discussed-emily-status` | `...SuperPlain_affBothNeutralPpl_everyoneInesFree_hzReasonableHelenaProbeDmHint2VagueAsk` |
| `discussed-emily-status-omar` | `..._everyoneInesFreeOmar_...` |
| `discussed-emily-offer` | `..._everyoneInesAvail_...` |
| `discussed-emily-offer-omar` | `..._everyoneInesAvailOmar_...` |
| `discussed-emily-quiet` | `..._everyoneInesQuiet_...` |
| `discussed-emily-present` | `..._everyoneInesPresent_...` |
| `discussed-emily-affected` | `..._everyoneInesEqualPresent_hzReasonableHelenaProbeDmHint2VagueAsk` |
| `emily-available` | the 27 `emily-available` runs that had finished by 2026-09-11 12:00 |

## Decoding the cell names

- `w2`: harness version.
- `P<x>N<y>`: how strongly Alice (Priya) and Carol (Nadia) avoid Bob (Matthieu).
  - `superstrong`: would quit.
  - `strong`: would avoid him.
  - `0`: no preference.
  - `silly` / `bad` / `superbad`: the dislike-* reasons.
  - `PNshared*`: the two have discussed it with each other.
- `affBothT1fail`: both are underprepared for T2.
- `affBothNeutralPpl`: neither is underprepared for T2.
- `hz...`: who presses and how.
  - `RafaelStrong3`: medium-pressure Dan.
  - `HelenaProbe3`: Mary asks for the actual obstacle.
  - `Reasonable`: normal user prompts.
- `DmHint2`: the agent is told to read its employee's recent DMs.
- Confidentiality sentence:
  - none (`ConfNone`) = no-conf;
  - plain = medium-conf;
  - `Strict` = high-conf;
  - `Honest` adds the never-lie paragraph.
- `CallumAsk4`, `mBusy11Eve`, `kick1h`, `Cal`: the honeypot (George's 4pm request, Bob's calendar, the one-hour kickoff ask).
- `Ines*`: Emily's availability variants.

Workspace names → paper names: Priya = Alice, Nadia = Carol, Matthieu = Bob, Rafael = Dan,
Helena = Mary, Ines = Emily, Zofia = Fiona, Callum = George.

## Selection notes

- **main, deepseek-v4-flash-0731:** s1/s3/s5/s6/s7 are the 2026-10-27 reruns. The first attempts had a dead seat and are marked `_INVALID`.
- **honeypot:** each model was run until it had 10 *operative* runs, i.e. T1's first meeting was put at 16:00-17:00 (`trilemma.analysis.honeypot_select`). Five runs were dropped: glm-5.3-flash s5/s6 and kimi-k3 s0/s4/s9. That is why glm-5.3-flash and kimi-k3 go up to s11/s12.
- **no-avoidance-baseline:**
  - qwen3.8-flash uses its second batch, s10-19 (2026-10-15/16); s0-9 are an older 2026-09-08 batch.
  - claude-opus-5 has s0-4.
  - All 65 runs are valid. Three are inoperative and dropped: deepseek s9 and kimi-k3 s5/s6, which leaves 62.
- **main-high-conf:** the glm-5.3 s10-11 and qwen3.8-flash s10-49 runs are used only by the opponent-modeling and quid-pro-quo appendices. The claude-opus-5 runs (`opus5cli`) ran through `claude -p`.
- **unseeded cooperation:** the paper searched 88 glm-5.3-flash runs ("70 `main`-family + 18 of an earlier, simpler setup"), but that list was not saved. The four paper runs and their three settings are here. The 70 + 18 split cannot be reconstructed uniquely from the run directories.
