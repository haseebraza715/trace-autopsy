# Run the CLI demo

Complete the [offline setup](../README.md#install-and-run-offline), then run:

```bash
bash scripts/demo/demo_body.sh
```

The script sets `AUTOPSY_NO_EMBEDDINGS=1` and analyzes with `--no-llm --no-embeddings`. It validates the synthetic hallucinated-tool trace, prints its failed status, score 77/100 and five findings, then prints fix suggestions. Analyze exits 1 for findings; the demo itself finishes with exit 0. Typing and reading pauses affect duration.

Inspect the [saved report](../examples/outputs/hallucinated_tool.md) and [loop comparison](../examples/outputs/loop_diff.txt). The health score is a heuristic summary and fewer detected patterns do not prove task success.

The preserved [GIF](../assets/demo/demo.gif) and [video](../assets/demo/demo.mp4) are historical recordings of older scores. They are not current evidence. Recording new media and demonstrating optional MCP or UI features are deferred.
