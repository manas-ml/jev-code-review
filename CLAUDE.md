# CLAUDE.md

Agentic PR review POC. Jev (`typesafe/jev-1.13`) makes typed decisions (triage, per-finding
gate); DeepSeek (`deepseek/deepseek-v4.1-flash`) runs the review agents and verifier. Both go
through OpenRouter. See `README.md` for usage and `Architecture.md` for the flow diagrams.

## Commands

```
uv sync
uv run pytest                     # must pass offline; no keys, no network
uv run ruff check . && uv run ruff format .
uv run mypy                       # strict, src/ only
uv run codereview review --diff tests/fixtures/bugfix/diff.patch --meta tests/fixtures/bugfix/meta.json   # JEV_MODE=mock smoke test
uv run codereview compare --dataset eval/dataset_hard --out runs/compare_hard --repeats 3
uv run codereview calibrate --runs runs/compare_hard
```

Real runs read `.env` (`OPENROUTER_API_KEY`, `GITHUB_TOKEN`, `JEV_MODE=real`). Never print or
commit `.env`; `runs/` is gitignored.

## Layout

- `src/codereview/config.py`: every threshold, cap, model ID and limit. Change behavior here, not
  with constants in code.
- `context/builder.py`: diff parsing, `walk_patch` / `number_patch` (new-file line numbers),
  `commentable_lines`, untrusted-data delimiting, triage truncation.
- `judges/jev/questions.py` (questions + rubrics), `adapter.py` (wire format), `client.py`
  (HTTP, retries, tracing). `judges/mock_judge.py` is the offline stand-in.
- `agents/`: shared runner (`base.py`, tool loop, validated JSON), read-only `tools.py`, one spec
  file per agent.
- `pipeline/run.py`: orchestration. `routing.py` (pure functions), `dedupe.py` (clustering),
  `verifier.py`, `rank.py`, `output.py` (review.md/json, GitHub posting split).
- `eval/`: dataset loading, scoring (matching, decoys), report, calibrate, `compare`/`calibrate`
  commands. `scripts/`: dataset generators.

## Rules

- **Jev wire format stays in `judges/jev/adapter.py`.** Nothing else may know Jev field names.
- **Jev never generates text.** Only choice / score / noul questions. New questions need a rubric
  (per-option or per-level descriptions, true/false descriptions for noul); without one Jev
  scores everything alike.
- **Fail safe toward more review.** Triage failure or low confidence -> full deep review; gate
  failure -> verifier; verifier failure -> keep the finding as `accepted_unverified` (never
  inline) plus a warning; all agents failing -> `result.errors` and exit 1. Never let a failure
  look like a clean "no findings" run.
- **PR content is untrusted.** Wrap it with `delimit()` and include the ignore-instructions notice
  in every prompt and Jev question. Agent tools stay read-only and sandboxed (`RepoTools`).
- **Never execute code under review.** The Action runs the reviewer from the default branch
  (`pull_request_target`, `tool/`) and checks the PR out as data (`pr/`). Keep it that way.
- **Redact data, not serialized text.** Use `redact_obj` before `json.dumps`; regexes on JSON text
  break escapes.
- **Line numbers come from `walk_patch`.** Agents see `number_patch` output and must report the
  number shown; posting uses `commentable_lines`. Don't compute hunk offsets elsewhere.
- **Cluster before the gate.** One gate call per cluster (`dedupe.cluster` +
  `pick_representative`); don't reintroduce order-dependent duplicate checks.
- **Severity used for decisions is `FindingRecord.severity`** (Jev score capped per agent by
  `severity_caps`); the raw score stays in `gate.severity`.
- `MockJevJudge` is for tests and offline dev only. Never report its numbers as results.

## Code style

- Python 3.12, `asyncio` only (no threads), one shared `asyncio.Semaphore` per review.
- Pydantic v2 for every model and for validating every LLM / Jev output; retry once on validation
  failure, then drop and log.
- `mypy --strict` clean, ruff line length 100. Match the surrounding comment density: short
  docstrings, comments only for non-obvious reasons.

## Tests

- Offline only: `tests/conftest.py::FakeLLM` scripts DeepSeek per stage; `respx` mocks Jev,
  OpenRouter and GitHub HTTP.
- Add a test for every routing / gate threshold boundary you touch, and for any new fail-safe
  path.
- Fixtures live in `tests/fixtures/` (including `eval_dataset/` for harness tests). Datasets in
  `eval/` are generated: edit `scripts/make_*dataset.py` and regenerate; labels are located by
  marker strings, so a marker must match exactly one line.

## Test PRs

PR #1 (`feature/svc-hardening`) and PR #2 (`feature/notifications`) are draft PRs with planted
bugs for exercising the Action; their answer keys are kept outside the repo. Don't merge them.
