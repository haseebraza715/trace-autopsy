# Curated Examples

This folder contains synthetic starter traces and walkthroughs for evaluating TraceAutopsy quickly.

## Traces

- [`traces/successful_run.json`](traces/successful_run.json)
- [`traces/loop_failure.json`](traces/loop_failure.json)
- [`traces/loop_fixed.json`](traces/loop_fixed.json)
- [`traces/hallucinated_tool.json`](traces/hallucinated_tool.json)

`loop_failure.json` and `loop_fixed.json` are the same task before and after a
fix: run `autopsy diff` on them to see the failing-run patterns disappear.

## Walkthroughs

- [`walkthroughs/successful_run.md`](walkthroughs/successful_run.md)
- [`walkthroughs/loop_failure.md`](walkthroughs/loop_failure.md)
- [`walkthroughs/hallucinated_tool.md`](walkthroughs/hallucinated_tool.md)

## Try it offline

```bash
export AUTOPSY_NO_EMBEDDINGS=1
python -m agent_autopsy.cli summary examples/traces/loop_failure.json
python -m agent_autopsy.cli analyze examples/traces/loop_failure.json --no-llm --no-embeddings -o /tmp/loop_report.md
autopsy diff examples/traces/loop_failure.json examples/traces/loop_fixed.json
```

See [current saved reports](outputs/) and the [README setup](../README.md). Analyze exits 1 for the failing examples. Fewer findings in the authored corrected trace do not prove real agent success.
