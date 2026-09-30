# TraceAutopsy portfolio summary

TraceAutopsy reads recorded agent traces and reports suspected failure patterns with cited events. Its first release candidate is the offline CLI on `fix/truthful-deterministic-analysis`, plus the local review patch. This work is not yet published on main.

The [README](README.md) gives the complete setup and explicit offline controls. The base install needs Python >=3.10. With `--no-llm --no-embeddings` for analyze and `AUTOPSY_NO_EMBEDDINGS=1` for diff, the bundled synthetic examples need no credentials or model downloads after installation.

The [hallucinated-tool report](examples/outputs/hallucinated_tool.md) shows a failed run at 77/100 with five findings and event evidence. The [loop comparison](examples/outputs/loop_diff.txt) removes four detected patterns in an authored corrected trace. Fewer patterns do not prove real task success. The health score summarizes heuristic detector penalties; it is not a calibrated success or safety measure.

The parser normalizes generic JSON, LangGraph, LangChain, and OpenTelemetry shapes into one event model. Detectors, reports, and comparisons share that model through [api.py](src/agent_autopsy/api.py). This architecture is implemented; the bundled examples do not verify live framework integrations.

The lexical detector evaluation uses 29 of 30 manifest entries and reports 45 true-positive labels, 0 false positives, and 0 false negatives. It is a corpus regression check, not an accuracy estimate on unseen production traces. Provenance is unknown for corpus files without a verified origin. Optional LLM interpretation, embeddings, MCP, and Streamlit remain separate, unverified release claims. Historical demo media uses an older score and is labeled in the README.
