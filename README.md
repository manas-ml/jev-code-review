# codereview: agentic PR review POC (Jev gates + LLM agents)

Pipelines (share all code except the judge):

| id | `--pipeline` | triage + gate | notes |
|----|--------------|---------------|-------|
| A  | `jev`        | Jev (`typesafe/jev-1.13`) via OpenRouter | proposed |
| B  | `baseline`   | general LLM judge, same questions as Jev | comparison |

```
PR -> context -> TRIAGE judge -> route (skip | light | deep)
   -> specialist agents (parallel) -> GATE judge per finding
        accept / discard / uncertain -> LLM verifier -> dedupe -> rank -> review
```

## Setup

```
uv sync
cp .env.example .env       # set OPENROUTER_API_KEY (and GITHUB_TOKEN for --pr)
uv run pytest              # offline: mocks + recorded-style fakes, no network
uv run ruff check . && uv run mypy
```

Env vars: `OPENROUTER_API_KEY` (agents, verifier, baseline judge, and Jev all go through
OpenRouter), `GITHUB_TOKEN`, `JEV_MODE` (`real` | `mock`; default `mock`), plus model overrides
(`JEV_MODEL`, `AGENT_MODEL`, `VERIFIER_MODEL`, `BASELINE_JUDGE_MODEL`, `MATCH_JUDGE_MODEL`).
`TYPESAFE_API_KEY` is accepted but unused (Jev is called through OpenRouter).

Alternatives to OpenRouter for Jev access: Vercel AI Gateway or Cloudflare Workers AI; only
`judges/jev/adapter.py` (wire format) and the URL in `judges/jev/client.py` would change.

## Commands

```
uv run codereview review --pr https://github.com/o/r/pull/1 --pipeline jev --dry-run
uv run codereview review --diff x.patch --meta meta.json --pipeline baseline [--repo ./checkout]
uv run codereview compare --dataset eval/dataset_hard --out runs/compare_hard --repeats 3
uv run codereview calibrate --runs runs/compare_hard   # per-judge thresholds -> calibrated_gate.json
GATE_OVERRIDES_FILE=runs/compare_hard/calibrated_gate.json uv run codereview review ...
uv run python scripts/bootstrap_dataset.py OWNER/REPO 123 456   # best-effort labels
```

Each run writes `runs/<run-id>/{review.md, review.json, trace.json}`. `trace.json` has per-stage
latency, every Jev call (request-state hash, full answers incl. probabilities/confidence), every
LLM call (tokens, cost), routing decision with reasons, and every finding with gate answers and
disposition. Secrets are redacted before anything is written.

`--post` creates a GitHub review (default is dry run). `--repo` gives agents read-only
`read_file`/`grep`/`list_dir` over a checkout (path-traversal rejected; no writes/shell/network).
Depth: `deep` runs agents and verifier with repo tools; `light` runs without tools.

## GitHub Action (Jev pipeline only)

`.github/workflows/code-review.yml` runs `codereview review --pipeline jev --post` on every
non-draft, same-repo PR (opened / synchronize / reopened / ready_for_review) and posts a review.

Manual run: Actions > "Jev code review" > Run workflow > enter a PR number.

Setup: add the repo secret `OPENROUTER_API_KEY` (Settings > Secrets and variables > Actions, or
`gh secret set OPENROUTER_API_KEY`). `GITHUB_TOKEN` is provided by Actions (needs
`pull-requests: write`, set in the workflow).

What gets posted: inline comments only for findings with severity >= `REVIEW_MIN_SEVERITY`
(repo variable, default 3 = "serious"; scale 0 cosmetic .. 4 critical). Everything else that
passed the gate is listed, collapsed, in the review body with a ranking table, so nothing is
hidden. Inline comments are limited to lines visible in the diff (GitHub rejects others) and
to 10 per review; if GitHub still rejects them the review is reposted with all findings in the
body. Nothing is posted when there are no findings and no human-review flag. New pushes cancel
the in-flight run; each run is a new review, and earlier review comments are fed to the gate as
duplicate context. Fork PRs are skipped (no secrets). Run artifacts (`runs/`) are uploaded for
14 days. Locally: `uv run codereview review --pr <url> --min-severity 3 --post`.

## Eval

Dataset: `eval/dataset/<pr-id>/{meta.json, diff.patch, labels.json}`; `labels.json` =
`{"issues": [{file, line_start, line_end, description}], "nitpicks": [{file, line}]}`.
Two toy examples are included; they only exercise the harness.

Datasets: `eval/dataset` (30 small single-bug cases, `scripts/make_synthetic_dataset.py`) and
`eval/dataset_hard` (16 multi-file PRs with 14 subtle bugs and 18 decoys,
`scripts/make_hard_dataset.py`). Decoys are `labels.must_not_flag` regions: code that looks
wrong but is correct; comments there count as `decoy_hits`.

Matching rule: a finding matches a label if same file and line within 2 of the labeled range,
else if an LLM judge (`src/codereview/eval/prompts/match_judge.md`) says they describe the same
issue (cached in `match_cache.json`; disabled with `--no-use-match-judge`).

Report (`runs/compare/report.md`): per pipeline cost/PR, latency p50/p95, comments, precision,
recall, skip rate, and for A: `skipped_with_labels` / `downgraded_with_labels` (missed-issue
risk). Calibration table buckets A's gate `is_real_defect` probability and shows the fraction
of findings matching a label (also `calibration.csv`, raw `calibration_findings.csv`). If the
fractions do not rise across buckets, the thresholds are not usable as-is. Needs a real,
sizable labeled set to mean anything; the bootstrap heuristic overcounts and undercounts labels.

## Safety

PR content is delimited as untrusted data in every prompt, every prompt tells the model to
ignore embedded instructions, guardrail paths (auth, secrets, migrations, `.github/workflows/`)
force deep review + human_required and cannot be overridden by a judge, and any judge failure
or low confidence (< 0.5) escalates to a full deep review; a failed gate escalates to the
verifier instead of discarding.

## Assumptions

Jev API (confirmed from OpenRouter docs, not yet exercised against the live service by this repo):
- `POST https://openrouter.ai/api/alpha/decisions`, `Authorization: Bearer`, body
  `{model, state, questions}`; question `{type, instructions, criteria}` (`criteria`: object for
  choice, ordered array for score, optional for noul).
- Answers: noul `{noul}` (no confidence, so treated as 1.0), choice `{choice, confidence}`,
  score `{score, confidence}` where `score` is continuous and zero-based. Usage cost at
  `usage.cost`. All of this lives in `judges/jev/adapter.py`.
- `typesafe/jev-1.13` pinned (never `jev-latest`). Model slug availability on your key unverified.
- Limits (1-32 questions, ~32k tokens state) are respected via the 120k-char triage budget;
  very large PRs are truncated to highest-churn hunks.

LLM: `deepseek/deepseek-v4.1-flash` via OpenRouter chat completions (JSON mode + tool calls);
the slug and its pricing could not be verified, so prices are config (`prices`) and the
response-reported `usage.cost` is used when present.

Thresholds needing calibration (all in `config.py`): every value in `RoutingThresholds`
(skip 0.9/0.5/0.1, agent selectors 0.3/0.5/0.5/0.5, deep risk 2, human 3.2/0.85, min confidence
0.5) and `GateThresholds` (discard 0.7/0.35/0.3, accept 0.8/0.6/severity 2). Severity and risk
are Jev's continuous expected-level values, so these cutoffs are on that scale.

Other: `MockJevJudge` (default `JEV_MODE=mock`) is a heuristic stand-in with fake probabilities,
for offline dev/tests only, never for the comparison. Baseline judge uses the same model as the
agents, so B vs A isolates Jev vs a general LLM as the judge. Tests use scripted fake LLMs and
respx-mocked HTTP rather than recorded real responses.
