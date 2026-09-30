# Walkthrough: Hallucinated Tool

Synthetic trace: `examples/traces/hallucinated_tool.json`

## Goal

Validate tool-allowlist and contract-failure diagnostics.

## What to run

```bash
export AUTOPSY_NO_EMBEDDINGS=1
python -m agent_autopsy.cli summary examples/traces/hallucinated_tool.json
python -m agent_autopsy.cli analyze examples/traces/hallucinated_tool.json --no-llm --no-embeddings -o /tmp/hallucinated_tool_report.md
```

## Expected interpretation

- Pattern output should include unknown/hallucinated tool usage.
- Suggested fixes should include stricter tool validation/guardrails.
- Evidence should cite specific event IDs where invalid tools are called.
