# Scoring

Scoring is deterministic and needs no LLM judge. Every task has a typed answer schema. The model's `submit_answer` JSON is compared field by field with the answer key in `answers.jsonl`. The code lives in `fincrime_bench/scoring.py` and `fincrime_bench/report.py`.

## 1. Field scores

| Type | Model gives | Score |
|---|---|---|
| `id` | one id string | 1 if equal to the gold id after normalisation, else 0 |
| `id_set` | list of ids | **F1** between the predicted and gold sets |
| `number` | number (or a string like `"$1,234.56"`) | 1 within tolerance, then linear decay to 0 |
| `bool` | true/false (`"yes"`/`"no"` accepted) | 1 if correct, else 0 |
| `id_number_map` | list of `{"id", "value"}` (a dict or pairs are also accepted) | `(1 − v) · keyF1 + v · valueScore` |

**Normalisation.** Ids are compared case-insensitively with punctuation removed, so `ac1234567` matches `AC1234567`, and `912-00-1234` matches `912001234`. Phone numbers match with or without the `+1` prefix and separators.

**F1** = 2PR/(P+R), where P is precision (the share of the model's ids that are correct) and R is recall (the share of gold ids the model found). F1 is deliberately symmetric:

- Listing every plausible suspect drives precision down.
- Naming one obvious culprit drives recall down.

Example: gold has 4 people. A model that names 2 of them and nothing else scores 0.67. A model that names all 4 plus 20 innocents scores 0.29.

**Numbers.** Each numeric field has its own tolerance. With relative tolerance:

```
err = |pred − gold| / |gold|
score = 1                                   if err ≤ rel_tol
score = 1 − (err − rel_tol)/(zero_at − rel_tol)  if rel_tol < err < zero_at
score = 0                                   if err ≥ zero_at
```

Ownership percentages use absolute tolerance instead: `abs_tol` = 1 percentage point and `abs_zero_at` = 10 points.

| Field | rel_tol / abs_tol | zero_at |
|---|---|---|
| `tax_unreported_01` understated amounts | 2% | 25% |
| `tax_networth_01.unexplained_amount` | 1% | 20% |
| `tax_skimming_01` understated receipts | 5% | 50% |
| `own_kickback_*.total_paid` | 1% | 25% |
| capstone money fields | 1–2% | 25–30% |
| `own_ubo_*` ownership % | ±1 pt | ±10 pt |

**Id-to-number maps** (for example *taxpayer → understated amount*, or *owner → %*):

- `keyF1` is the F1 of the ids.
- `valueScore` is the mean number score over the **gold** ids. A missing id contributes 0, and extra ids affect only `keyF1`.
- `v` (the `value_weight`) is 0.4.

## 2. Task score

```
task_score = Σ_f weight_f · field_score_f / Σ_f weight_f      ∈ [0, 1]
```

Field weights are in `tasks.jsonl`. They favour the field that names the responsible person; for example, layering is accounts 0.5, exit account 0.2, beneficiary 0.3. A missing field scores 0. If the first submission lacks required fields, the harness tells the model once and lets it resubmit. A model that never submits scores 0 on the task.

## 3. Headline score

```
FinCrime Score = 100 · Σ_t W_t · S_t / Σ_t W_t        W = 1 (easy), 2 (medium), 3 (hard)
```

`S_t` is the task score averaged over trials. Tasks a run did not attempt count as 0 and appear in the **coverage** column. The **95% CI** comes from 2,000 bootstrap resamples of tasks (seed 0), so it reflects how much the score depends on which tasks are included.

Also reported:

- **Category scores**: AML, Tax, Ownership, Telecom and Investigation. Each is the unweighted mean task score × 100.
- **Difficulty scores**: easy, medium and hard.
- **Unweighted score**: the plain mean over tasks.
- **Trial scores / trial_std**: the headline score per trial and its standard deviation. This is run-to-run noise.

## 4. Diagnostic metrics

| Metric | Definition | Why it matters |
|---|---|---|
| `precision` | Σ true positives / Σ predicted ids, over all set-valued fields (micro) | How often the model's accusations are right |
| `recall` | Σ true positives / Σ gold ids, over all set-valued fields | How much wrongdoing it finds |
| `decoy_hit_rate` | decoys the model named / decoys planted | Planted look-alikes: travellers, a legitimate car purchase, nominee directors, a legitimately busy preparer, loan-funded deposits, and so on. A high rate means pattern-matching without verification |
| `avg_tool_calls`, `avg_turns`, `avg_total_tokens`, `avg_wall_time_s` | per task attempt | Cost and efficiency |
| `failures` | attempts with status `api_error`, `harness_error`, `no_submission` or `context_overflow` | Reliability |

## 5. Head-to-head

For every pair of models, the leaderboard reports:

- **Wins, losses and ties.** A win means one model scored at least 0.05 higher on that task.
- **Δ**: the difference in headline scores.
- A **paired bootstrap 95% CI** for Δ, resampling tasks and keeping each model's scores on the same task together. If the interval excludes 0, the gap is unlikely to come from task selection alone.

## 6. Reading results responsibly

- 27 tasks is a modest sample, so confidence intervals are wide. Use `--trials` ≥ 3 and, for important comparisons, also evaluate on a private seed (see the README).
- A model's score depends on the harness settings: tool budget, toolset, tool mode and output truncation. Compare models only under identical settings; they are recorded in each `run.json`.
- The `bool` field in `aml_roundtrip_01` has weight 0.2. Guessing it alone gives at most 0.2 on that task.

## 7. Longevity sessions

Each session round is scored exactly like a task: same field scorers, same answer keys for task rounds. Round weights are the source task's difficulty weight (1/2/3) for case-file and investigation rounds, 1 for recall rounds and 2 for the synthesis round.

The **longevity score** is the weighted mean round score × 100 over all session rounds, with a 95% bootstrap CI over rounds. Rounds never reached count as 0: for example, after a `context_overflow` the remaining rounds of that session are lost.

Reported in `summary.json` → `longevity` and in the leaderboard's longevity table:

| Metric | Definition |
|---|---|
| `by_kind` | Mean round score × 100 for `case_file` (tools off, read the case file), `investigation` (tools on), `recall` and `synthesis` |
| `by_context` | Mean score of rounds grouped by the prompt size at the start of the round: <32k, 32–64k, 64–96k, 96k+ tokens. Uses API-reported `prompt_tokens` when available, else an estimate (characters / 3) |
| `by_position` | Mean score for rounds 1–3, 4–6 and 7+ |
| `peak_context_tokens` | Largest prompt size reached in any session (`context_source` says whether it came from the API or the estimate) |
| `min_first_round_context` | Smallest context at the first question across sessions (should be ≥ 32k) |
| `rounds_lost` | Rounds ending in `api_error`, `context_overflow` or `harness_error` |
| Δ standalone (leaderboard) | Mean over tasks of (in-session score − standalone score) when the same run directory has both. Negative means degradation in long conversations |

Recall rounds score against the **true** value. A model that answered an earlier round wrongly and then restates its own wrong answer still scores 0 on the recall round. The synthesis round is an F1 over person ids, so it rewards complete and precise recall of several earlier findings.
