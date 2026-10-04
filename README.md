# codereview: agentic PR review POC (Jev gates + LLM agents)

Jev (`typesafe/jev-1.13`, via OpenRouter) makes the fast, constrained decisions: triage (which
agents, how deep) and the per-finding gate (real? severe? actionable? duplicate?). DeepSeek
(`deepseek/deepseek-v4.1-flash`, via OpenRouter) does the reasoning: five specialist review
agents and a verifier for uncertain findings. Jev never writes text; it returns typed answers
(choice, score, probability).

```
PR -> context (line-numbered diff) -> TRIAGE (Jev) -> route: skip | light | deep
   -> specialist agents (parallel, DeepSeek) -> cluster duplicates
   -> GATE (Jev, one call per cluster): accept | discard | uncertain -> verifier (DeepSeek)
   -> rank by effective severity -> review.md / review.json / trace.json  [-> GitHub review]
```

See [Architecture.md](Architecture.md) for diagrams of every stage, and [CLAUDE.md](CLAUDE.md)
for working on the code.

## Why Jev

Jev is used where the pipeline needs a quick, structured judgment about a piece of text (what
kind of change is this, is this finding real) and not where it needs reasoning or prose.

- **Typed answers, no parsing.** Questions are choice / score / noul (a calibrated yes
  probability), so the gate and triage never parse free text, retry on malformed JSON, or
  handle a model that "explains" instead of answering.
- **Fast and cheap.** Each call is a single small request, billed on input tokens only (output
  tokens are free), so asking many questions per finding stays inexpensive.
- **Probabilities you can threshold.** Answers carry numbers, so routing and gating are plain
  comparisons in `config.py` that can be tuned and tested at every boundary, and the calibration
  table in the eval report shows whether the probabilities are actually usable as thresholds.
- **Built-in uncertainty.** Choice and score answers include confidence. Jev returns 0 when its
  distribution is split, which the pipeline turns into a full deep review instead of a guess.
- **Steering resistance by construction.** Jev can only pick from the options we give it, so an
  injected "approve this" in a diff can shift an answer but cannot make it emit text. Guardrail
  paths are also enforced in code before the judge, and failures lead to more review.
- **Clean split of labor.** Jev makes the many small sorting decisions; DeepSeek, the expensive
  model, only reasons about what survives. Routing can skip trivial PRs and the gate can discard
  findings before the verifier runs.
- **Replaceable.** The wire format lives in one module (`judges/jev/adapter.py`), and
  `JEV_MODE=mock` runs everything offline.

Caveats: Jev needs a good rubric (a description per level or option); without one it scores
every finding alike. It has not been shown to be more accurate than a general LLM judge asking
the same questions, so the case for it rests on speed, cost and typed outputs, not accuracy.

## Setup

```
uv sync
cp .env.example .env       # set OPENROUTER_API_KEY; GITHUB_TOKEN for --pr / --post; JEV_MODE=real
uv run pytest              # offline: fake LLMs + respx-mocked HTTP, no network or keys
uv run ruff check . && uv run mypy
```

| env var | purpose |
|---------|---------|
| `OPENROUTER_API_KEY` | Jev, agents, verifier and the eval match judge all go through OpenRouter |
| `GITHUB_TOKEN` | fetch PRs (`--pr`), post reviews (`--post`), bootstrap datasets |
| `JEV_MODE` | `real` or `mock` (default `mock`: offline heuristic judge, dev/tests only) |
| `JEV_MODEL`, `AGENT_MODEL`, `VERIFIER_MODEL`, `MATCH_JUDGE_MODEL` | model overrides |
| `GATE_OVERRIDES_FILE` | JSON of calibrated gate thresholds (from `codereview calibrate`) |

Every threshold, cap and limit lives in `src/codereview/config.py`; nested values can be set via
env as JSON (pydantic-settings). `TYPESAFE_API_KEY` is accepted but unused. Vercel AI Gateway or
Cloudflare Workers AI could replace OpenRouter for Jev; only `judges/jev/adapter.py` (wire format)
and the URL in `judges/jev/client.py` would change.

## Commands

```
# review one PR (dry run by default)
uv run codereview review --pr https://github.com/o/r/pull/1 [--repo ./checkout] [--post]
uv run codereview review --diff x.patch --meta meta.json [--repo ./checkout]
#   --min-severity 3   inline-comment floor when posting (0 cosmetic .. 4 critical)
#   --run-id NAME      output folder under runs/

# evaluate and calibrate
uv run codereview compare --dataset eval/dataset_hard --out runs/compare_hard --repeats 3 --parallel 4
uv run codereview calibrate --runs runs/compare_hard
GATE_OVERRIDES_FILE=runs/compare_hard/calibrated_gate.json uv run codereview review ...

# datasets
uv run python scripts/make_hard_dataset.py          # regenerate eval/dataset_hard
uv run python scripts/make_synthetic_dataset.py     # regenerate eval/dataset
uv run python scripts/bootstrap_dataset.py OWNER/REPO 123 456   # best-effort labels from real PRs
```

`--repo` gives agents and the verifier read-only `read_file` / `grep` / `list_dir` over a
checkout (path traversal rejected; no writes, shell or network). Deep reviews use the tools;
light reviews don't.

Exit codes: `0` review completed; `1` every agent run failed (no trustworthy result); `2` missing
`OPENROUTER_API_KEY` with `JEV_MODE=real`. Partial failures still exit `0` but add a warning to the
review.

## Outputs

Each run writes `runs/<run-id>/`:

- `review.md`: summary (route, human-review flag, counts by severity), a ranking table (rank,
  severity, score, confidence, location, agent, verified), findings with suggested fixes, and
  every discarded finding with its reason.
- `review.json`: the full result; each finding has `rank`, effective `severity`, raw gate answers,
  `disposition` and `reason`.
- `trace.json`: per-stage latency, every Jev call (request-state hash, full answers with
  probabilities and confidence), every LLM call (model, tokens, cost), routing decision, warnings.

Secrets are redacted from all three before writing.

## How findings are filtered and ranked

1. Agents report at most 8 findings each, grounded in quoted evidence, with the line number shown
   in the diff.
2. Duplicates (same file, within 3 lines, overlapping titles) are clustered; one representative
   per cluster goes to the gate.
3. Jev answers four rubric questions per representative. Defaults: discard if duplicate > 0.7,
   is_real_defect < 0.35 or actionable < 0.3; accept if is_real_defect >= 0.8, actionable >= 0.6
   and severity >= 2; otherwise the verifier decides.
4. Effective severity = Jev's severity capped per agent (`tests` <= 1, `architecture` <= 2).
5. Ranked by effective severity, then is_real_defect; top 10 are inline, the rest overflow.

Nothing is fixed automatically; `suggested_fix` is text.

## GitHub Action

`.github/workflows/code-review.yml` reviews every non-draft PR from this repo (opened,
synchronize, reopened, ready_for_review) and can be run by hand: Actions > "Jev code review" >
Run workflow > PR number.

- Setup: `gh secret set OPENROUTER_API_KEY`. `GITHUB_TOKEN` comes from Actions.
- Security: `pull_request_target` runs the workflow and reviewer from the default branch; the PR
  is checked out separately (`pr/`) and only read as data, never installed or executed. Fork PRs
  are skipped.
- Posting: inline comments only for findings with effective severity >= `REVIEW_MIN_SEVERITY`
  (repo variable, default 3), on lines visible in the diff, verified or gate-accepted, max 10.
  Everything else is listed, collapsed, in the review body with the ranking table. If GitHub
  rejects inline comments the review is reposted with all findings in the body. Nothing is
  posted when there are no findings, no warnings and no human-review flag.
- New pushes cancel the in-flight run; each run posts a new review. Run artifacts
  (`tool/runs/`) are kept 14 days.

## Evaluation

| dataset | cases | contents |
|---------|-------|----------|
| `eval/dataset` | 30 | small single-file PRs, 20 with one planted bug, 10 clean |
| `eval/dataset_hard` | 16 | multi-file PRs, 14 subtle bugs, 18 decoys, some clean-but-scary |

`labels.json`: `issues` (true bugs), `must_not_flag` (decoys: code that looks wrong but is
correct), `nitpicks` (unused). Labels are located by marker strings in the generators, so they
stay correct when the cases change.

Matching: a comment matches an issue if it is in the same file within 2 lines of the labeled
range, else if the LLM match judge (`src/codereview/eval/prompts/match_judge.md`, cached in
`match_cache.json`, off with `--no-use-match-judge`) says they describe the same issue. A comment
that matches no issue but lands on a decoy is a `decoy_hit`.

`compare` writes `report.md` / `report.json` plus CSVs: cost per PR, latency p50/p95, comments,
precision, recall, decoy hits, skip rate, and missed-issue risk from triage
(`skipped_with_labels`, `downgraded_with_labels`). With `--repeats`, metrics are means with
min/max across repeats. The calibration table buckets the gate's is_real_defect probability
against labeled matches; if the matched fraction does not rise across buckets, the probability
is not usable as a threshold.

`calibrate` replays recorded gate answers (no model calls), grid-searches the auto-accept
thresholds on a train split, reports a held-out split next to the defaults, and writes
`calibrated_gate.json`. It cannot replay the verifier, so it tunes auto-accept only.

Limits: datasets are synthetic and small; LLM output varies between runs (use repeats);
`bootstrap_dataset.py` labels over- and under-count.

## Safety

- PR title, description and diff are delimited untrusted data in every prompt; every prompt and
  Jev question says to ignore embedded instructions.
- Guardrail paths (auth, secrets, migrations, `.github/workflows/`) force a deep review and
  human review; judges cannot lower this.
- Fail-safe: triage failure or low confidence (< 0.5) -> full deep review; gate failure ->
  verifier; verifier failure -> finding kept as unverified (not inline) with a warning; all
  agents failing -> exit 1.

## Jev API notes

Verified against OpenRouter's docs and live calls: `POST https://openrouter.ai/api/alpha/decisions`
with `{model, state, questions}`; each question is `{type, instructions, criteria}` (choice:
`{key: description}`, score: ordered level descriptions, noul: `{true, false}` descriptions).
Answers: noul `{noul}` (no confidence field, treated as 1.0), choice `{choice, confidence,
probabilities}`, score `{score, confidence, probabilities, legend}` with a continuous zero-based
score. Cost is `usage.cost`. Jev reports confidence 0 when its score distribution is split, which
triggers the fail-safe deep review. The model is pinned to `typesafe/jev-1.13`.

DeepSeek is called through OpenRouter chat completions (JSON mode and tool calls). Prices are
config (`prices`); the response's `usage.cost` is used when present.

All routing and gate thresholds are uncalibrated defaults from the original spec until
`calibrate` is run on a representative labeled set.
