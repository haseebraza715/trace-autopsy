# Agent Autopsy

Local-first debugging for AI agent traces.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)
[![GitHub stars](https://img.shields.io/github/stars/haseebraza715/agent-autopsy?style=social)](https://github.com/haseebraza715/agent-autopsy)

Agent Autopsy analyzes trace files from agent runs, normalizes them into a common schema, detects common failure patterns, and generates reports with evidence and fix suggestions.

It is built for local debugging first:

- Run deterministic analysis fully offline
- Keep traces on your machine
- Use it from the CLI, Streamlit UI, or MCP
- Add optional LLM synthesis only when you want it

![Agent Autopsy demo](docs/images/autopsy-demo.gif)

**[Live demo](https://autopsyagent.streamlit.app/)** ·
**[Architecture](ARCHITECTURE.md)** ·
**[Quick start doc](docs/quickstart.md)** ·
**[Examples](examples/README.md)**

## What it does

Agent Autopsy follows a deterministic-first pipeline:

1. Ingest a trace from LangGraph, LangChain, OpenTelemetry, or generic JSON.
2. Normalize it into a shared trace schema.
3. Detect patterns like loops, retry storms, hallucinated tools, timeouts, error cascades, token waste, and contract mismatches.
4. Generate a report with findings, evidence, and likely causes.
5. Optionally run LLM-assisted synthesis for a stronger narrative and fix guidance.

Supported interfaces:

- CLI for local runs and CI gates
- Streamlit app for interactive inspection
- MCP server for programmatic use
- Trace capture helpers for LangChain and LangGraph style workflows

## Install

Requirements:

- Python 3.10+
- `pip`

Base install:

```bash
git clone https://github.com/haseebraza715/agent-autopsy.git
cd agent-autopsy

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Optional extras:

| Target | Command | Includes |
|---|---|---|
| LLM | `python -m pip install -e ".[llm]"` | OpenAI / OpenRouter / Anthropic / Ollama integrations |
| GUI | `python -m pip install -e ".[gui]"` | Streamlit UI |
| MCP | `python -m pip install -e ".[mcp]"` | MCP server |
| Embeddings | `python -m pip install -e ".[embeddings]"` | Semantic drift embeddings |
| Full | `python -m pip install -e ".[full]"` | Everything above |

## Quick start

Run the deterministic path first. It is the simplest way to confirm the project works in your environment.

```bash
autopsy summary examples/traces/successful_run.json
autopsy validate examples/traces/loop_failure.json
autopsy analyze examples/traces/loop_failure.json --no-llm --no-embeddings
```

Useful follow-ups:

```bash
autopsy fixes examples/traces/loop_failure.json
autopsy diff examples/traces/loop_failure.json examples/traces/hallucinated_tool.json
autopsy benchmark --traces-dir tests/fixtures/real_traces --limit 8
```

What you should expect from `analyze`:

- A trace summary
- Deterministic findings with evidence
- Root-cause hypotheses
- Cost estimates when token usage is present
- Non-zero exit code when actionable findings are detected

## Verified local commands

The commands below were exercised against the bundled sample and fixture traces:

```bash
autopsy summary examples/traces/successful_run.json
autopsy validate examples/traces/loop_failure.json
autopsy analyze examples/traces/loop_failure.json --no-llm --no-embeddings --quiet
autopsy analyze tests/fixtures/real_traces/fail_timeout_12c9776c.json --no-llm --no-embeddings --quiet
autopsy diff examples/traces/loop_failure.json examples/traces/hallucinated_tool.json
autopsy fixes examples/traces/loop_failure.json
autopsy benchmark --traces-dir tests/fixtures/real_traces --limit 8
streamlit run app.py
```

Representative results from those runs:

- `summary` reported the sample success trace correctly
- `validate` accepted the bundled failure trace
- `analyze` produced detailed deterministic reports for loop and timeout cases
- `benchmark` processed the real trace fixtures and produced a summary table
- Streamlit started successfully from `app.py`

## Common workflows

### Analyze one trace

```bash
autopsy analyze trace.json
autopsy analyze trace.json --no-llm
autopsy analyze trace.json --no-llm --no-embeddings
autopsy analyze trace.json -f json
autopsy analyze trace.json -o report.md
autopsy analyze trace.json -o report.md --artifacts ./patches
autopsy analyze trace.json --artifacts ./patches --code-root ./agent
```

### Compare two runs

```bash
autopsy diff baseline.json candidate.json
autopsy diff baseline.json candidate.json -f json
autopsy diff baseline.json candidate.json --fail-on-regression
```

### Gate traces in CI

```bash
autopsy analyze trace.json --no-llm --no-embeddings -q \
  --fail-on infinite_loop,token_waste,context_overflow
```

`--fail-on` changes the exit rule from "fail on any finding" to "fail only on these patterns".

### Watch a trace directory

```bash
autopsy watch ./traces
```

### Replay events

```bash
autopsy replay trace.json --from 42 --speed 2
autopsy replay trace.json --step
autopsy replay trace.json --reproduce --reproduce-n 5
```

### Generate fix suggestions

```bash
autopsy fixes trace.json
```

## LLM mode

Install the LLM extra and configure one provider:

```bash
python -m pip install -e ".[llm]"
cp .env.example .env
```

Example `.env`:

```env
PROVIDER=openrouter
OPENROUTER_API_KEY=your_key_here
DEFAULT_MODEL=google/gemma-4-31b-it:free
```

Example commands:

```bash
autopsy analyze trace.json
autopsy analyze trace.json --stream
autopsy analyze trace.json --provider ollama --model llama3.1:8b
autopsy analyze trace.json --no-cache
```

If credentials are missing or the provider call fails, the CLI falls back to deterministic analysis.

## Streamlit UI

```bash
python -m pip install -e ".[gui]"
streamlit run app.py
```

The UI includes:

- Single trace analysis
- Batch analysis
- Trace viewer
- Reports
- Settings

## MCP server

```bash
python -m pip install -e ".[mcp]"

autopsy-mcp --transport stdio
autopsy-mcp --transport streamable-http --mount-path /mcp
```

For HTTP transports, bearer token auth can be enabled with `MCP_SSE_TOKEN`.

More detail: [docs/mcp.md](docs/mcp.md)

## Capturing traces

```python
from src.tracing import start_trace, end_trace

trace_handler, run_id = start_trace()
result = graph.invoke(state, config={"callbacks": [trace_handler]})
end_trace(trace_handler)
```

Relevant environment variables:

```env
TRACE_ENABLED=1
TRACE_DIR=./traces
TRACE_MAX_CHARS=5000
```

See [src/tracing/trace_saver.py](src/tracing/trace_saver.py).

## Telemetry

Telemetry is opt-in and local-only.

```bash
autopsy telemetry status
autopsy telemetry on
autopsy telemetry off
```

When enabled, telemetry is written to a local JSONL file under the cache directory. No trace payloads are sent anywhere by default.

## Documentation

| Topic | Link |
|---|---|
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Quick start | [docs/quickstart.md](docs/quickstart.md) |
| Ingestion formats | [docs/ingestion.md](docs/ingestion.md) |
| Analysis pipeline | [docs/analysis.md](docs/analysis.md) |
| Pattern catalog | [docs/patterns.md](docs/patterns.md) |
| MCP | [docs/mcp.md](docs/mcp.md) |
| Plugins and extensions | [docs/plugins.md](docs/plugins.md), [docs/extensions.md](docs/extensions.md) |
| Examples and walkthroughs | [examples/README.md](examples/README.md) |
| Demo guide | [docs/demo.md](docs/demo.md) |

## Contributing

Start here:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [docs/good-first-issues.md](docs/good-first-issues.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

Built by [Haseeb Raza](https://github.com/haseebraza715) · MIT licensed
