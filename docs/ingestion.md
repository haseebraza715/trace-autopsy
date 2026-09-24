# Trace Ingestion

Agent Autopsy supports multiple trace formats with automatic detection and normalization.

## Format Detection

The system automatically detects trace format based on structure and field presence.
LangGraph, LangChain, and OpenTelemetry each have dedicated parsers. When an input shape is only partially compatible, the parser falls back to generic handling for unknown fields.

1. **LangGraph**: Detects `thread_id`, `checkpoint`, or `runs` fields
2. **LangChain**: Detects `run_type` or `callbacks` fields  
3. **OpenTelemetry**: Detects `resourceSpans` or `traceId` fields
4. **Generic**: Fallback for any JSON structure

Format detection happens automatically when parsing a trace file. If a dedicated parser cannot confidently map the payload, the system falls back to the generic parser.

## Supported Formats

### LangGraph Format

Detected by presence of:
- `thread_id` field
- `checkpoint` field
- `runs` array with LangGraph-specific structure

**Example structure:**
```json
{
  "thread_id": "abc123",
  "runs": [
    {
      "name": "node_name",
      "type": "llm",
      "inputs": {...},
      "outputs": {...}
    }
  ]
}
```

### LangChain Format

Detected by presence of:
- `run_type` field
- `callbacks` array
- LangChain-specific event structure
Dedicated parser available; unsupported/unknown structures are handled by the generic fallback path.

**Example structure:**
```json
{
  "run_type": "chain",
  "callbacks": [...],
  "events": [...]
}
```

### OpenTelemetry Format

Detected by presence of:
- `resourceSpans` array
- `traceId` field
- OTEL span structure
Dedicated parser available; unsupported/unknown structures are handled by the generic fallback path.

**Example structure:**
```json
{
  "resourceSpans": [
    {
      "traceId": "abc123",
      "spans": [...]
    }
  ]
}
```

**GenAI semantic conventions.** The OpenTelemetry parser speaks the GenAI semantic conventions natively, plus the OpenInference and traceloop/openllmetry dialects. Spans from any of the three SDKs collapse to the same canonical event:

| Canonical field | OTel GenAI | OpenInference | traceloop |
|---|---|---|---|
| `event.type` | `gen_ai.operation.name` (chat, completion, tool_use, …) | `openinference.span.kind` | span name (`openai.chat`) |
| `event.name` (model) | `gen_ai.request.model` / `gen_ai.response.model` | `llm.model_name` | `ai.model.id` |
| `event.name` (tool) | `gen_ai.tool.name` | `tool.name` | `function.name` |
| `event.input` | `gen_ai.prompt` / `gen_ai.request.messages` | `input.value` / `llm.input_messages` | `ai.prompt` |
| `event.output` | `gen_ai.completion` / `gen_ai.response.messages` | `output.value` / `llm.output_messages` | `ai.completion` |
| input tokens | `gen_ai.usage.input_tokens` | `llm.token_count.prompt` | `ai.prompt_tokens` |
| output tokens | `gen_ai.usage.output_tokens` | `llm.token_count.completion` | `ai.completion_tokens` |

Both input and output token counts are preserved in `event.metadata` (as `input_tokens`/`output_tokens` plus the original keys) so the cost detector can compute accurate per-direction pricing instead of approximating from a single total.

Reference: [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/). Fixtures for all three SDK dialects live in [`tests/fixtures/otel_genai/`](../tests/fixtures/otel_genai/) and have a parametrized cross-SDK test in [`tests/test_otel_genai.py`](../tests/test_otel_genai.py).

### Generic Format

Fallback format for any JSON structure. Attempts to extract:
- Events from common field names (`events`, `steps`, `actions`)
- Metadata from top-level fields
- Timestamps from various formats

## Normalization

All formats are normalized to a unified schema:

**Event Normalization:**
- Sequential event IDs (0, 1, 2, ...)
- Standardized event types (llm_call, tool_call, error, etc.)
- Consistent timestamp format (ISO 8601)
- Unified input/output structure

**Statistics Calculation:**
- Total tokens (sum of all LLM token usage)
- Total latency (sum of all event latencies)
- Event counts (LLM calls, tool calls, errors)
- Duration (start to end time)

**Validation:**
- Structure validation (required fields present)
- Reference validation (parent_event_id references exist)
- Type validation (event types match schema)
- Timestamp validation (chronological order)

**Missing Data Handling:**
- Fills missing timestamps with calculated values
- Provides default values for optional fields
- Handles truncated or incomplete traces

## Trace Schema

After normalization, all traces conform to the unified schema defined in [`src/schema/trace_v2.py`](../src/schema/trace_v2.py):

- **Trace**: Top-level container with run_id, status, events
- **TraceEvent**: Individual events with type, input, output, metadata
- **TraceStats**: Aggregate statistics
- **EnvironmentInfo**: Framework, model, available tools
- **TaskContext**: Goal, success criteria, expected output

See [Architecture](architecture.md) for more details on the ingestion pipeline.
