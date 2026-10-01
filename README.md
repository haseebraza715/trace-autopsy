# TraceAutopsy

Read a recorded agent trace, find suspected failure patterns, and inspect the events behind each finding. Compare a failing trace with a corrected example without uploading either file.

This release candidate leads with the deterministic offline CLI. The four bundled examples are synthetic. Optional LLM interpretation, embedding-based detection, live framework integrations, and the Streamlit UI are preserved but outside this release's verification claim.

## Install and run offline

Requires Python **3.10 or newer**. Local verification used Python 3.11.16 on macOS; the other supported versions have not been rechecked for this candidate.

These instructions target the `fix/truthful-deterministic-analysis` branch, which contains the reviewed release candidate. They do not describe `main`, which still lags this branch.

```bash
git clone --branch fix/truthful-deterministic-analysis https://github.com/haseebraza715/trace-autopsy.git
cd trace-autopsy
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .

export AUTOPSY_NO_EMBEDDINGS=1
autopsy validate examples/traces/hallucinated_tool.json
autopsy analyze examples/traces/hallucinated_tool.json --no-llm --no-embeddings
autopsy fixes examples/traces/hallucinated_tool.json
```

Use an installed Python >=3.10 if `python3.11` is unavailable. On Windows, activate with `.venv\Scripts\activate` and set `$env:AUTOPSY_NO_EMBEDDINGS="1"` in PowerShell. Clone and installation require network access unless you already have the source and dependency wheels. The base install above needs no optional model packages. Do not create a `.env` or configure a provider for this walkthrough.

After installation these commands need no network, credentials, or model downloads, including with an empty model cache. `--no-llm` disables provider calls for analyze. `--no-embeddings` selects lexical goal-drift detection for analyze; `AUTOPSY_NO_EMBEDDINGS=1` applies the same setting to diff and the whole session. The global defaults remain unchanged. Without these controls, an installed embedding extra may load or download a model.

Expected exits are `0` for validate, `1` for analyze, and `0` for fixes. Analyze's `1` means findings or a non-success run status, not an installation failure. `2` means a tool or parse error. Fixes prints template suggestions; it does not apply them.

## Read the result

[Saved hallucinated-tool report](examples/outputs/hallucinated_tool.md) shows status `failed`, health score **77/100**, and five findings: `empty_response`, `error_cascade`, `hallucinated_tool`, and two `contract_unknown_tool` findings for separate invalid calls. Findings cite event IDs and trace excerpts.

The health score is a heuristic summary of detector findings, not a calibrated probability, task success rate, or safety guarantee. The [scoring code](src/agent_autopsy/output/report.py) starts at 100, applies severity penalties with overlap damping, then subtracts an evidence-coverage penalty. Run status is separate: a failed run can still score 77. A score of 100 means these detectors found no penalized signals; it does not establish that the task succeeded. The report's confidence field is also not a measured accuracy rate.

## Compare a failing trace with a corrected example

Keep `AUTOPSY_NO_EMBEDDINGS=1` set for diff.

```bash
autopsy analyze examples/traces/loop_failure.json --no-llm --no-embeddings
autopsy diff examples/traces/loop_failure.json examples/traces/loop_fixed.json
```

The [failing loop report](examples/outputs/loop_failure.md) has status `failed`, score **59/100**, and four patterns: `empty_response`, `error_cascade`, `infinite_loop`, and `timeout_pattern`. The [corrected report](examples/outputs/loop_fixed.md) has status `success`, score **100/100**, and no detected patterns. The [saved diff](examples/outputs/loop_diff.txt) shows those four patterns only in the failing example. These are authored traces demonstrating detector behavior, not evidence of a real agent succeeding after a fix.

The fourth example, [successful_run](examples/outputs/successful_run.md), also scores 100 with no patterns. See the [example index](examples/README.md) for inputs and walkthroughs.

## Run the demo

```bash
bash scripts/demo/demo_body.sh
```

The script uses the same offline controls and prints validate, analyze, and fixes output. It includes typing and reading pauses, so its duration depends on the machine. The [historical GIF](assets/demo/demo.gif) and [historical video](assets/demo/demo.mp4) show an older scoring implementation, including 24/100. They remain archived references, not current-output evidence. New recordings are deferred.

## Follow the implementation

1. [Parser](src/agent_autopsy/ingestion/parser.py) selects a reader for generic JSON, LangGraph, LangChain, or OpenTelemetry shapes.
2. [Normalizer](src/agent_autopsy/ingestion/normalizer.py) maps input onto the [Trace/Event schema](src/agent_autopsy/schema/trace_v2.py).
3. [PatternDetector](src/agent_autopsy/preanalysis/patterns.py) checks the event stream; [tool contracts](src/agent_autopsy/preanalysis/contracts.py) check declared tool names.
4. [Deterministic renderer](src/agent_autopsy/output/deterministic_report.py) cites evidence. [ReportGenerator](src/agent_autopsy/output/report.py) adds the score and exports text, Markdown, or JSON.
5. [CLI](src/agent_autopsy/cli.py) prints output and chooses an exit code. [api.py](src/agent_autopsy/api.py) provides the shared pipeline used by the CLI and optional interfaces.

See [patterns](docs/patterns.md) for detector descriptions and [architecture](docs/architecture.md) for more detail. Format support in code does not establish fidelity on every real framework export.

## Verify the detector corpus

For development verification only, install the heavier extras and run existing checks:

```bash
python -m pip install -e ".[dev]"
export AUTOPSY_NO_EMBEDDINGS=1
python scripts/eval_detectors.py --json-out /tmp/detector-eval.json
python -m pytest -q
ruff check src scripts tests
```

The [manifest](tests/fixtures/real_traces/_manifest.yaml) has 30 entries, of which 29 are evaluated and one is excluded. The lexical-backend regression check currently reports 45 true-positive pattern labels, 0 false positives, and 0 false negatives. These are corpus-relative counts, not general detector accuracy or 45 independent traces. Labels are hand-specified for fixture scenarios; there is no held-out production benchmark here.

Despite the directory name `real_traces`, provenance varies. Scenario generators in [generate_test_traces.py](scripts/generate_test_traces.py) and [generate_more_traces.py](scripts/generate_more_traces.py) establish synthetic generation mechanisms. They do not establish the origin of every checked-in file; files without a verified origin remain of unknown provenance. The directory name alone is not evidence of real user runs.

## Limits

Detectors can produce false positives and miss quiet failures or failures absent from the trace. Fix suggestions need review. Optional embeddings require `sentence-transformers` and may download weights on first use. Optional LLM analysis needs provider configuration and can send normalized traces to that provider. Neither optional path, the UI, nor live integrations is verified by the offline examples above. Recovery branches remain preserved for separate review.
