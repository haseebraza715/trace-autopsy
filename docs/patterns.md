# Deterministic pattern detectors

All detectors live in `src/preanalysis/patterns.py` (`PatternDetector.detect_all`) and run before any LLM step.

| Pattern type | Enum value | Summary |
|--------------|------------|---------|
| Infinite loop | `infinite_loop` | Same tool+input signature repeated consecutively |
| Retry storm | `retry_storm` | Same tool many times inside a short window |
| Redundant tool call | `redundant_tool_call` | Duplicate tool calls with similar inputs |
| Empty response | `empty_response` | Blank or useless LLM/tool outputs |
| Error cascade | `error_cascade` | Chained failures across events |
| Hallucinated tool | `hallucinated_tool` | Calls to tools not in the allowed set |
| Auth / permission | `auth_permission_failure` | Auth-related error text or 401/403 signals |
| Timeout pattern | `timeout_pattern` | Timeout-related errors or very slow calls |
| Goal drift | `goal_drift` | Similarity to the task goal drops over the run (embeddings or lexical fallback) |
| Stale context | `stale_context` | Old context reused while the task moved on |
| Token waste | `token_waste` | High token use with little progress |
| Inter-agent failure | `inter_agent_failure` | Handoff / multi-agent issues |
| Context overflow | `context_overflow` | Token totals vs model or trace limits |
| Tool contract mismatch | `tool_contract_mismatch` | Outputs that violate declared contracts |

Plugin detectors registered on the plugin manager are invoked after the built-in list; failures are logged and skipped.

## Evaluation

`scripts/eval_detectors.py` reports per-pattern TP/FP/FN against the hand-labeled corpus in `tests/fixtures/real_traces/_manifest.yaml`. Labels are hand-specified per scenario (not derived from detector output): `must_include` marks positive controls, `must_not_include` marks negative controls that must produce no detection, and `clean` traces must be silent. These are corpus-relative regression results, not a measure of accuracy on unseen production traces; a forbidden detection on a `must_not_include` trace fails CI.

## Per-pattern reference

For each detector: **what it catches**, **false-positive risk**, **tuning**.

### `infinite_loop`

- **Catches:** Identical tool name + normalized input repeated consecutively at least `threshold` times (default 3).
- **False positives:** Legitimate polling or idempotent “check status” loops; reduce sensitivity by raising `threshold` in `detect_loops` or narrowing tool names.
- **Tune:** `PatternDetector.detect_loops(threshold=...)`.

### `retry_storm`

- **Catches:** Many calls to the same tool within `retry_window_seconds` (config).
- **False positives:** Batch workloads that intentionally hammer an API; compare with `infinite_loop` (consecutive vs windowed).
- **Tune:** `get_config().retry_window_seconds`, cluster size in `detect_retry_storms`.

### `redundant_tool_call`

- **Catches:** Near-duplicate tool inputs within a sliding window.
- **False positives:** Intentionally repeated reads; tune similarity thresholds in implementation.
- **Tune:** Adjust heuristics inside `detect_redundant_tool_calls`.

### `empty_response`

- **Catches:** Blank or whitespace-only LLM/tool outputs.
- **False positives:** Short but valid answers; may co-occur with real failures. Use manifest expectations in eval, not as sole root cause.
- **Tune:** Stricter empty checks vs minimum content length (code change).

### `error_cascade`

- **Catches:** Chains of error-typed events or error substrings in outputs.
- **False positives:** Handled errors that recover; inspect event types in the trace schema.
- **Tune:** `detect_error_cascades` window and substring list.

### `hallucinated_tool`

- **Catches:** Tool calls whose names are not in the trace-declared tool set.
- **False positives:** Dynamic tools not listed in metadata; ensure trace ingestion lists all tools.
- **Tune:** Tool registry completeness in normalized trace.

### `auth_permission_failure`

- **Catches:** Regex on auth/forbidden/401/403 style messages, gated on failure evidence (any matched event errored, the run is not a success, or an error summary exists) — auth language inside healthy-run prompts/outputs stays silent.
- **Tune:** `_AUTH_PERMISSION_RE` and `_AUTH_STATUS_CODE_RE` in `patterns.py`.

### `timeout_pattern`

- **Catches:** Timeout keywords or very large latencies vs peers.
- **False positives:** Slow but successful calls; latency threshold sensitivity.
- **Tune:** `detect_timeout_patterns` duration multiplier.

### `goal_drift` / `stale_context`

- **Catches:** Embedding or lexical drift from task goal; stale markers in context fields.
- **False positives:** Task pivots that are intentional; requires embeddings (`--no-embeddings` skips drift).
- **Tune:** `semantic_drift_*` settings in `src/utils/config.py`.

### `token_waste`

- **Catches:** High token totals with low “progress” heuristics.
- **False positives:** Legitimately long reasoning traces.
- **Tune:** Thresholds inside `detect_token_waste`.

### `inter_agent_failure`

- **Catches:** Broken handoffs / multi-agent signals in trace metadata.
- **False positives:** Sparse instrumentation of handoffs.
- **Tune:** Event patterns in `detect_inter_agent_failures`.

### `context_overflow`

- **Catches:** Cumulative tokens vs model limits from `model_context_limits.json` or trace caps.
- **False positives:** Under-estimated limits for custom models.
- **Tune:** `context_overflow_threshold`, model limits path.

### `tool_contract_mismatch`

- **Catches:** Contract validation (`ContractValidator`) against declared schemas.
- **False positives:** Over-strict schemas; fix contracts vs detector.
- **Tune:** Tool contracts in trace / `ContractValidator` rules.
