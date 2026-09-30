# Run the offline example

Follow the [README setup](../README.md#install-and-run-offline) for the candidate branch, Python >=3.10, a virtual environment, and the base install. Installation needs network access; the documented CLI session after installation does not.

```bash
export AUTOPSY_NO_EMBEDDINGS=1
autopsy validate examples/traces/hallucinated_tool.json
autopsy analyze examples/traces/hallucinated_tool.json --no-llm --no-embeddings -o /tmp/hallucinated_tool_report.md
autopsy fixes examples/traces/hallucinated_tool.json
autopsy diff examples/traces/loop_failure.json examples/traces/loop_fixed.json
```

Expected exits are 0, 1, 0, and 0. Analyze's exit 1 means findings or a non-success run status. A trace whose status is missing, still running or unrecognised is reported as `unknown` and exits 1, because it is not a verified completion. Read the [current saved output](../examples/outputs/hallucinated_tool.md) and [comparison](../examples/outputs/loop_diff.txt). These examples are synthetic. The score summarizes detector findings and does not prove task success.

Optional model interpretation and live integrations require separate setup and verification. They are outside this offline walkthrough. See [architecture](architecture.md), [patterns](patterns.md), and [examples](../examples/README.md).
