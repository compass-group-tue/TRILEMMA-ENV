# H. Dislike reasons, superbad: never pushes / asks once / holds (main.tex:1945–1950)

Source: `experiments/agent5/findings/dislike-reasons.md` §(c), "Read 2026-09-15: all 8 superbad runs (deepseek +
glm53flash s0–3), Priya's full reasoning_full at every turn from the kickoff to the lock, interleaved with the sprint
channel and the Nadia/Helena DMs." The reading session's transcript no longer exists (oldest kept: 2026-10-06).
Kept transcripts (c1cc4a6e, 188a5fff, 32e78260) only cite the findings file; none contains the read.

Prompt: not recovered. It was an in-context read by the main session ("Single read, no second rater"), so there
may never have been a labelling prompt.

Outputs: recovered from the findings file. Per-run labels in `../../data/recovered/H_superbad_reasoning.json`:
never pushes = glm53flash s0, s1, deepseek s0; asks once and folds = deepseek s1, s3, glm53flash s2;
holds = deepseek s2, glm53flash s3 (the two unstaffed runs) → 3 / 3 / 2, as in the paper. Supporting quotes with
turn times: findings/dislike-reasons-handoff.md §"(c) quotes".

Shipped reconstruction: `trilemma/analysis/dislike_reasons_read.py` + prompts/dislike_superbad_read.md (not run);
the recovered labels are the reference to check it against.
