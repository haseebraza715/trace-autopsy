# Contributing to Agent Autopsy

Thanks for your interest in contributing.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[full]"
```

## Local Checks

Run tests before opening a PR:

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/eval_detectors.py
.venv/bin/python scripts/benchmark_no_llm.py examples/traces/loop_failure.json --repeat 3
.venv/bin/python scripts/render_demo_gif.py   # refresh README demo GIF (needs Pillow)
```

Smoke-check core entrypoints:

```bash
.venv/bin/python -m src.cli --help
.venv/bin/python -m src.mcp --help
```

## Branching and Commits

- Work on focused, logical changes.
- Prefer multiple small commits over one large commit.
- Use conventional commit prefixes:
  - `feat:`
  - `fix:`
  - `docs:`
  - `chore:`
  - `refactor:`
  - `test:`
- Keep commit messages single-line and high-signal.

## Pull Requests

Before opening a PR, ensure:

- Tests pass locally.
- Docs are updated for behavior changes.
- New public behavior includes at least one test.

In PR description, include:

- Problem statement
- What changed
- Validation steps and outputs
- Risk/rollback notes

## Adding New Parsers/Detectors

- Parser changes: update `src/ingestion` and add ingestion tests.
- Detector changes: update `src/preanalysis/patterns.py` and add preanalysis tests.
- If output changes, update report tests and docs.

See:

- `docs/ingestion.md`
- `docs/patterns.md`
- `docs/extensions.md`

### Add a new pattern in ~15 minutes

1. Add a value to `PatternType` in `src/preanalysis/patterns.py` and implement `detect_*` on `PatternDetector`, then append it from `detect_all()`.
2. Wire human-readable lines in `src/output/deterministic_report.py` (`PATTERN_DESCRIPTIONS` and `LIKELY_CAUSE`).
3. **Add at least 5 corpus entries** to `tests/fixtures/real_traces/`: at least 3 positives (traces where the pattern should fire, listed in `_manifest.yaml` under `must_include`) and at least 2 negatives (clean traces or traces with similar-but-not-quite patterns). This lets the eval script measure recall *and* precision for your detector.
4. Run `python scripts/eval_detectors.py` — your detector should appear in the table at the top of [docs/patterns.md](docs/patterns.md). Refresh that table with `python scripts/eval_detectors.py --markdown-out docs/_detector_metrics.md` and paste in the result.
5. Run `pytest tests/test_preanalysis.py` to cover edge cases the corpus doesn't.

PRs that add a detector without ≥5 corpus entries will be asked to add fixtures before review.

## Detector corpus

Real traces for regression testing live under `tests/fixtures/real_traces/` with `_manifest.yaml`. CI runs `scripts/eval_detectors.py` after unit tests. Per-detector accuracy is published in [docs/patterns.md](docs/patterns.md#measured-accuracy) — regenerate the table with `--markdown-out` after adding corpus entries.

## Code of Conduct

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
