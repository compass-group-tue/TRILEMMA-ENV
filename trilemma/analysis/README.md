# Analyses

This folder holds the scripts behind the paper's tables and figures that are not a judge's direct output. The judges live in `trilemma/judges`, the figure scripts in `trilemma/figures`. Every script takes `--runs <dir>`, a directory of rollouts, and reads its run lists from `sets/` (see `sets/README.md`).

Kind:
- **scripted**: deterministic, computed from `run.json` / `world_calls.jsonl` or from judge outputs.
- **LLM read**: the paper's numbers came from a model reading transcripts by hand (Claude). The script is that read rewritten as a judge with the reconstructed prompt in `prompts/`. These were not run for the paper, so expect agreement on the categories rather than on exact quotes.

Status as checked against the paper's rollouts and judge outputs on 2026-11-04:
- **exact**: every number in the table reproduces.
- **close**: reproduces with the stated difference.
- **not run**: the LLM read has not been run yet.

| paper | what | script | kind | status |
|---|---|---|---|---|
| all settings | frozen run lists | `build_sets.py` (+ `honeypot_select.py`) | scripted | exact: every run count in the paper (main 90+8, main-high-conf 80/82/50, no-conf 24, honest 30, honeypot 70, no-avoidance-baseline 62, dislike-* 4×8, emily-available 80, discussed-avoidance 304) |
| Tab 2 / Fig 5, T1 columns | who holds T1 at the end | `t1_claims.py` | scripted | exact for main-high-conf. For main, every count matches; the deepseek row in `findings/t1-claims-5e10.md` has 9 cells, so its incomplete markers are shifted by one seed |
| Tab 3-7, 11, 12, 14, 15, 18, 29 | example rows (quotes) | `example_quotes.py` | scripted (locates each quote) | 63 of 64 quotes found verbatim. The miss is a paper error: Tab 15's glm-5.3-flash quote ("Carol wrote it to **Emily**", `main-no-conf` s7) reads "Nadia wrote it to **Zofia**", which is Fiona, not Emily |
| Tab 13 (`tab:disc-ladder`) | disclosure at three confidentiality levels | `disclosure_ladder.py` (+ `data/disclosure_seat_spans.md`) | scripted over judge rows | exact, all 36 cells |
| Fig 4a | honeypot "fixed reason" x/10 per model | `honeypot_fig4a.py` | scripted over hp4 verdicts | exact (8, 6, 5, 4, 2, 2, 1). The figure's "raised otherwise" / "never raised" split is our reading of its legend and has no numbers in the paper to check against |
| Fig 8 (heatmap) | hp4 gates per model | `honeypot_fig4a.py --heatmap` | scripted | exact: every non-zero cell of all 7 rows |
| Tab 17 / 30 / 31 and the funnel text | discussed-avoidance groups 1 / 2 | `discussed_funnel.py` | scripted | exact: 304 / 158 / 105 / 55, by setting and by model |
| Tab 17 categories, Tab 18 | COLLAB / PUSH / MIXED / NEUTRAL per DM | `discussed_dm_labels.py` → `discussed_funnel.py --labels` | LLM read (the paper's labeller prompt, verbatim) | not run (paper: 306 DMs, 159/78/22/47; runs 74/4/26/1) |
| Tab 24 (`tab:dislike-outcomes`) | dislike reasons: board, quit mentions, legitimacy | `dislike_reasons.py` | scripted | exact (0/8, 6/8, 6/8, 6/8; 6-50, 0-1, 0-1, 5-35; 1/8, 0/8, 0/8, 8/8) |
| App. dislike reasons, superbad reasoning | never pushes / asks once / holds | `dislike_reasons_read.py` | LLM read | not run (paper: 3 / 3 / 2) |
| Tab 27 (`tab:emily`), "Ends on T1" | Emily / Alice-or-Carol on T1 | `emily.py` | scripted | exact (1/1, 2/2, 1/0, 0/2) |
| Tab 27, "Emily asked" / "answer" | who asked Emily; her agent's answer | `emily.py --judge` | LLM read | not run. Without `--judge` it prints an upper bound: qwen 16 vs the paper's 12; glm 7 vs 5; gpt-5.5 2 = 2; deepseek 0 = 0 |
| Tab 28 / 29 | unseeded cooperation R1-R4 | `unseeded_coop.py` | scripted screen + LLM read | screen: all four paper runs pass (Alice<->Carol DM, both end on T2) in a 27-run pool of main + main-high-conf + S1 + S2. The 88-run pool was not saved and cannot be reconstructed uniquely. LLM read not run |

Inputs that are data, not code:
- `data/disclosure_seat_spans.md`: the hand labels ([matt] / [unprep] / [no] / [?]) of every direct-disclosure span. Table 13 uses them for every seat except those in the five deepseek `main` reruns, which use the per-item judge's majority label.
- `prompts/*.md`: the reconstructed LLM-read prompts. `discussed_dm_labels.md` is the paper's labeller prompt, with the file input/output replaced by inline JSON.
