# Walkthrough: Loop Failure

Synthetic trace: `examples/traces/loop_failure.json`

## Goal

See how loop and retry-related failures are surfaced.

## What to run

```bash
export AUTOPSY_NO_EMBEDDINGS=1
python -m agent_autopsy.cli summary examples/traces/loop_failure.json
python -m agent_autopsy.cli analyze examples/traces/loop_failure.json --no-llm --no-embeddings -o /tmp/loop_report.md
```

## Expected interpretation

- Status should be `failed`.
- Pattern output should include loop and/or retry-like behavior.
- Fix recommendations should include iteration guards and retry control.
- Health score should be significantly lower than a successful run.
