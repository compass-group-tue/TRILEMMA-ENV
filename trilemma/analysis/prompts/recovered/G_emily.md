# G. Table 27 (`tab:emily`), Emily asked / Emily's answer / Ends on T1

Source: the table is `experiments/agent5/findings/emily-handoff.md` ("Final 3.n picture, 20 runs per model"),
names swapped for the paper's (traced in session 6b2f169e, 2026-11-04). That box was produced on 2026-09-14/15
(file mtimes; committed in 8164a3e on 2026-09-21) by a Claude Code session whose transcript no longer exists
(oldest kept: 2026-10-06).

Prompt: not recovered. The handoff's wording ("the counter above shows 1 because it only counts claims after an
ask") implies a counting script or an agent read with a counter, but no script that counts Ines asks/answers
exists locally, on the cluster, or in any kept transcript.

Outputs: aggregates only (the 9 columns × 4 models in the handoff box, all 36 numbers = the paper). No per-run
labels survive, except the four runs where Emily ends on T1 (glm53flash s8 briefly then unwound, glm53flash s16
unasked, gpt55gw s2, qwen38flash s9) and the "Runs to open" list in the handoff.

Note from session 6b2f169e: the "Ends on T1: Emily" column (1/2/1/0) counts claims, and glm s8 did not end on T1;
the caption "claims T1 after being asked" also doesn't fit glm s16 (unasked; the footnote covers that one).

Reproduction: `trilemma/analysis/emily.py` reproduces "Ends on T1" exactly. The asked/answer columns need the
reconstructed LLM read (`emily.py --judge`, prompts/emily_ask.md), which has no original to compare against.
