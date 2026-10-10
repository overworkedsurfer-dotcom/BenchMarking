# FinCrimeBench

A benchmark that tests how well LLM agents analyse tabular and graph data to catch financial crime and identify the people behind it. Models follow stolen money through layered accounts, chain call records to a hidden boss, unmask beneficial owners behind offshore shells, and catch tax evasion by reconciling returns against third-party records.

Everything is **synthetic** and generated deterministically from a seed: a small world of ~2,500 people and ~480 companies with a year (2025) of banking, telecom, corporate-registry and tax data. Into that background the generator plants **27 investigations**. Each one comes with a machine-checkable answer key, **decoys** built to catch false accusations, and a **reference solver** that proves the answer can be derived from the data alone.

A **longevity suite** tests endurance in long conversations. It has 4 sessions of 9–10 rounds each. Every session opens with a case file of at least 32k tokens, then asks its questions one at a time in a single, ever-growing conversation.

You plug in any model that speaks the **OpenAI Chat Completions API** (OpenAI, OpenRouter, Together, Groq, DeepSeek, vLLM, Ollama, LM Studio, LiteLLM, …). Give it a model name, base URL and key, and the harness runs it as a tool-using agent, scores it, and ranks it against other models.

```
 generator (seed) ──► 17 CSV tables ──► read-only SQLite warehouse ──► agent tools
        │                                                                  │
        └──► tasks.jsonl + answers.jsonl            model (OpenAI-compatible API)
                          │                                                │
                          └──────────► scorer ◄──────── submit_answer(JSON)┘
                                         │
                                 results/ + LEADERBOARD.md
```

## Quickstart

```bash
git clone https://github.com/overworkedsurfer-dotcom/BenchMarking && cd BenchMarking
pip install -e .                 # no third-party dependencies, Python >= 3.10
fincrime-bench validate          # must report 27/27 tasks and 39/39 longevity rounds

# run one model (key read from an env var so it never lands in shell history or results)
export OPENAI_API_KEY=sk-...
fincrime-bench run --model gpt-4.1 --base-url https://api.openai.com/v1 --api-key-env OPENAI_API_KEY
```

Without installing, use `python -m fincrime_bench <command>` instead of `fincrime-bench`.

By default `run` executes both the standalone tasks and the longevity sessions. Use `--suite tasks` or `--suite longevity` to run only one.

Smoke-test the pipeline without any API key using the built-in pseudo-models: `oracle` submits the answer key (100), `reference` runs the reference solvers (100), and `null` submits nothing (0):

```bash
fincrime-bench run --model reference
```

### Any OpenAI-compatible endpoint

```bash
# OpenRouter / Together / Groq / DeepSeek ... : same flags, different base URL
fincrime-bench run --model meta-llama/llama-3.3-70b-instruct \
  --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY --param temperature=0

# local vLLM / Ollama / LM Studio. Use --tool-mode text if the server's function calling is unreliable
fincrime-bench run --model qwen2.5:32b --base-url http://localhost:11434/v1 --api-key ollama --tool-mode text
```

Extra request-body fields go through `--param key=value`, with values parsed as JSON. Examples: `--param max_tokens=4096`, `--param reasoning_effort='"high"'`, `--param max_completion_tokens=32000`. Extra HTTP headers go through `--header key=value`.

### Pit models against each other

Copy [`models.example.toml`](models.example.toml) to `models.toml`, list your models, then:

```bash
fincrime-bench run --models-config models.toml --trials 3 --concurrency 6
fincrime-bench leaderboard results/ --out results/LEADERBOARD.md
```

Each model gets `results/<name>/`. The leaderboard ranks models by score with 95% confidence intervals, breaks scores down by category, shows precision, recall, decoy hits, tool calls, tokens and failures, gives a per-task score matrix, and adds a **head-to-head** table with paired-bootstrap confidence intervals so you can tell real gaps from noise.

Runs are **resumable**: re-running the same command skips finished tasks and retries only API failures. `--no-resume` starts over.

## What the model sees

The system prompt describes the role and lists every table with its columns. The user message holds the task (for example *"payment TX… was stolen in a business-email compromise; trace the funds and identify the beneficiary"*) plus the exact answer fields. The model then calls tools until it calls `submit_answer`.

| Tool | Purpose |
|---|---|
| `list_tables`, `describe_table` | Schema, column meanings, sample rows |
| `sql_query` | One read-only SQLite `SELECT` (CTEs and window functions allowed); 50 rows by default, up to 200 |
| `entity_profile` | Everything directly linked to an id: person, company, account, phone, device, return, … |
| `graph_neighbors` | Adjacent edges in a derived graph (holds, signer, owns, control, relationship, transfer, called, uses_device, …) |
| `find_paths` | Shortest connection paths between two nodes |
| `submit_answer` | Final JSON answer, validated against the task's fields |

Defaults: 40 investigative tool calls, 60 model turns, and tool output truncated at 8,000 characters. Use `--toolset sql` to drop the graph helpers and test pure SQL analysis. The sandbox is read-only (SQLite authorizer plus a read-only connection), and queries time out after 20 s.

`--tool-mode native` (default) uses OpenAI function calling. `--tool-mode text` asks the model to emit `` ```tool {"tool": ..., "arguments": ...}``` `` blocks, which works with any chat model.

## The tasks

| Category | Task | Difficulty | What it tests |
|---|---|---|---|
| AML | `aml_layering_01..04` | easy → hard | Trace business-email-compromise proceeds hop by hop (3–7 hops, splits and merges, intermediary cuts, timing distractors) to a cash withdrawal or offshore wire; resolve the beneficiary through signers or offshore ownership |
| AML | `aml_structuring_01` | medium | Population scan for cash structured just under the $10k CTR threshold; decoys include reported large cash, isolated near-threshold deposits and businesses |
| AML | `aml_smurfing_01` | easy | Several depositors feed one account; find them and the real controller (not the nominee owner of record) |
| AML | `aml_mule_01/02` | medium/hard | From one romance-scam complaint, map mule accounts, the collector and all victims, then identify the operator through a shared device (01) or a shared home IP (02) in the online-banking log |
| AML | `aml_ato_01` | medium | Account takeovers: new device, foreign IP, password reset, new payee, fast transfer. Decoys: travellers, phone upgrades, a legitimate car purchase |
| AML | `aml_roundtrip_01` | hard | An "investment" that is the company's own money cycled through US and offshore shells; find the cycle and the investor's beneficial owner |
| Ownership | `own_ubo_01..03` | easy → hard | Effective beneficial ownership through holding chains, trusts, expired stakes and nominee directors; in 03 one owner crosses 25% only by summing two paths |
| Ownership | `own_kickback_01/02` | medium/hard | A vendor that siphons company money offshore to an insider (directly, or via the insider's spouse); decoy vendors are genuinely new businesses |
| Telecom | `tel_chain_01/02` | medium/hard | Contact chaining from a crew to its coordinator and boss; in 02 the boss uses an anonymous burner, found by cell-tower co-location with their personal phone (a relative is a weaker co-location decoy) |
| Telecom | `tel_burner_01/02` | medium/hard | Identify the replacement number(s) after a suspect drops their phone, using contact-set overlap; decoys include a relative switching phones at the same time |
| Tax | `tax_unreported_01` | medium | Match returns against W-2/1099s: omitted income and non-filers. Traps: joint returns, income reported on a different line, small omissions under the threshold |
| Tax | `tax_lifestyle_01` | medium | Expenditure method: asset purchases that reported income and documented sources (loans, asset sales) cannot explain |
| Tax | `tax_networth_01` | medium | Precise net-worth computation for one taxpayer (joint return, financed purchase, asset sale) |
| Tax | `tax_preparer_01` | hard | A refund-mill preparer diverting clients' refunds into accounts they control; decoy preparer with legitimately large refunds |
| Tax | `tax_dependents_01` | easy | Duplicate dependents and dependents who died before the tax year; decoy: died during the year (valid) |
| Tax | `tax_skimming_01` | hard | Bank-deposits method on cash businesses; exclude loan proceeds and owner contributions |
| Investigation | `x_capstone_01` | hard | From a scam call to the organizer: call records → victims → mules → collector LLC → offshore parent → organizer → income missing from their tax return |
| Investigation | `x_capstone_02` | hard | From a cash home purchase back to an employer, a conduit LLC fronted by a relative, and an offshore account |

Run `fincrime-bench tasks` to list them, or `fincrime-bench tasks --show <id>` to print the exact prompt a model receives. [`docs/TASKS.md`](docs/TASKS.md) describes each family in depth.

## Longevity suite: long conversations past 32k tokens

Standalone tasks start from an empty conversation. The longevity suite checks whether a model keeps performing as one conversation grows long.

| Session | Rounds | Case file | Theme |
|---|---|---|---|
| `long_01` | 10 | ≥ 32k tokens | Laundering desk: stolen wires, smurfing, offshore investors |
| `long_02` | 9 | ≥ 32k tokens | Telecom and fraud-ring desk |
| `long_03` | 10 | ≥ 32k tokens | Tax desk |
| `long_04` | 10 | ≥ 32k tokens | Corporate registry and account-security desk |

Each session is one conversation:

1. The first message is a **case file**: an extract of warehouse records with the evidence for some questions buried among unrelated records. It is at least 32k tokens by a conservative estimate (about 40–55k with real tokenizers), so even the first question sits past 32k.
2. Then come 9–10 **rounds**, one question at a time, and the context keeps growing (typically 60–100k tokens by the end):
   - **case_file** rounds, tools disabled: answer by reading the case file. Some of these come many rounds after it was shown, which tests long-range retrieval.
   - **investigation** rounds, tools enabled: a benchmark task asked mid-conversation.
   - **recall** rounds, tools disabled: restate an item from the round-1 question, or a finding from an earlier round.
   - **synthesis** round, tools disabled: list the people identified across several earlier rounds.

Every one of the 27 tasks appears in exactly one session, so the leaderboard can compare each model's **in-session vs standalone** score on the same tasks. A negative Δ means the model degrades as the conversation grows. The longevity table also reports:

- scores by round kind
- scores by context size at the start of each round (<32k, 32–64k, 64–96k, 96k+)
- late-round performance
- peak context reached; this uses the API's reported token counts, or an estimate when the API reports none
- rounds lost when a conversation stops fitting a model's context window. That is recorded as `context_overflow` and scores 0, which is itself a longevity result.

`fincrime-bench validate` proves the suite is sound. It re-parses every case file from the exact text the model sees and runs the reference solvers on that extract alone.

`fincrime-bench tasks --show long_01` prints a full session outline.

Per-round settings: `--session-max-tool-calls` (20), `--session-max-turns` (30), `--session-tool-output-chars` (6,000). `generate --dossier-tokens` sets the case-file size.

**Cost.** Every model call in a session resends the whole conversation, so the suite is input-heavy: roughly 8–12M input tokens per model per trial before prompt caching. Providers that cache repeated prefixes (most major APIs) cut this substantially, because the case file stays at the front of the conversation. Models with context windows under about 64k tokens will lose late rounds.

## Scoring (summary)

Each answer field has a type-specific scorer:

- **id**: exact match after normalisation.
- **id_set**: F1, so precision punishes false accusations and recall punishes missed culprits.
- **number**: full credit within tolerance, decaying linearly to zero.
- **id→number map**: key F1 blended with value accuracy.
- **bool**: exact match.

A task score in [0, 1] is the weighted mean of its fields. The headline **FinCrime Score** is the difficulty-weighted mean task score × 100 (easy 1, medium 2, hard 3), with a 95% bootstrap confidence interval. Secondary metrics:

- per-category and per-difficulty scores
- micro **precision** and **recall** over all set answers
- **decoy hit rate**: share of planted look-alike innocents the model accused
- efficiency: tool calls, tokens and wall time
- failure counts

Full definitions are in [`docs/SCORING.md`](docs/SCORING.md).

## Avoiding contamination: private test sets

`data/public/` (seed 20251) is committed **with its answer key**, for development, debugging and comparing harness settings. For evaluations whose numbers matter, generate a fresh world from a secret seed. It has the same task families with different people, ids, amounts and chain shapes:

```bash
fincrime-bench generate --seed 918273 --out data/private   # keep the seed secret
fincrime-bench validate --data data/private               # must print 27/27 and 39/39
fincrime-bench run --models-config models.toml --data data/private --out results-private
```

Generation is deterministic: the same seed and generator version always produce byte-identical files, recorded with SHA-256 hashes in `manifest.json`. The generator refuses to emit a world whose rule-derived answer keys differ from the planted cases. This was checked across 25 seeds.

## Fair comparisons

Compare models only on the same data split and the same settings: `--max-tool-calls`, `--max-turns`, `--toolset`, `--tool-mode`, `--max-tool-output-chars` and the `--session-*` limits are recorded in each run's `run.json`. In-session rounds get a smaller default tool budget (20 vs 40 calls) to keep conversations within common context windows. To make the Δ-vs-standalone comparison measure context alone, pass `--session-max-tool-calls 40`. Prefer `--trials 3` or more at temperature 0 or the provider default, and report the confidence interval. A model that needs `--tool-mode text` is using a different interface, so note it next to the score.

## Outputs

```
results/<model>/
  run.json          model config (no API key), agent settings, dataset seed/version
  results.jsonl     one line per task x trial: score, per-field scores, decoy hits, status, tokens, submission
  session_results.jsonl  one line per session x trial: per-round scores, statuses, context size per round
  summary.json      aggregate metrics, including a "longevity" block (see docs/SCORING.md)
  transcripts/      full conversation, every tool call and result, per task / session x trial
```

`fincrime-bench rescore results/<model>` re-scores stored submissions after a scorer change, without calling the model again.

## Repository layout

```
fincrime_bench/
  generator/   world.py (population, banks, phones, background activity), tax.py (returns, 1099s),
               aml.py, taxfraud.py, ownership.py, telecom.py, capstone.py (planted schemes + tasks),
               sessions.py (longevity sessions: case files, rounds, recall/synthesis)
  schema.py    data dictionary (drives CSV columns, SQLite types, describe_table, prompts)
  db.py        CSV -> cached SQLite warehouse, read-only sandbox, derived graph_edges table
  tools.py     model-facing tools          agent.py    native/text tool-calling agent loop
  llm.py       stdlib OpenAI-compatible client with retries
  scoring.py   field scorers               report.py   summaries, leaderboard, head-to-head
  solvers.py   reference solvers (data-only solutions used by `validate`)
  sessions.py  longevity sessions at run time: case-file warehouses, validation, round scoring
  runner.py    concurrency, resume, transcripts           cli.py      command-line interface
data/public/   17 CSV tables, tasks.jsonl, answers.jsonl, sessions.jsonl, session_answers.jsonl, manifest.json
docs/          DATASET.md (data dictionary), TASKS.md, SCORING.md
tests/         dataset integrity, scorer unit tests, reference solvers, longevity sessions, end-to-end runs vs a fake API
```

`python -m pytest` runs the test suite: about 60 tests in about 35 s, including end-to-end task and multi-round session runs against a scripted fake OpenAI server in both tool modes. Explore the data by hand with `fincrime-bench tool sql_query '{"query": "SELECT ..."}'`.

## Adding a task family

1. Write a planter in `fincrime_bench/generator/` that creates the scheme on top of background data, picks actors with `w.pick(...)` (which reserves them), plants decoys, and registers the task with `add_task(...)`. Rule-based population tasks pass a `deferred=` gold function and call `w.expect(...)` with the planted positives.
2. Make sure every feature your scheme relies on also occurs in legitimate background activity, so models must reason rather than filter on a rare value.
3. Add a data-only reference solver to `solvers.py`, then call the planter from `build_world()`.
4. Run `fincrime-bench validate` and the slow multi-seed test (`pytest -m slow`).

## Limitations

The world is simplified:

- Tax rules are approximate and there are no account balances.
- Each person has one employer at most.
- Background activity is generated from statistical patterns, not real behaviour.

Scores measure investigative reasoning over this kind of data. They are not a certification for real compliance work. All names, ids, companies and records are fictional, and any resemblance to real entities is coincidental.
