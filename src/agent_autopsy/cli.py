"""
CLI interface for Agent Autopsy.

Provides commands for analyzing traces and generating reports.

Exit codes: 0 = no issues detected, 1 = issues or analysis findings, 2 = tool / parse error.
"""

import fnmatch
import json
import logging
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from agent_autopsy import api
from agent_autopsy.advanced import LiveTraceMonitor, benchmark_trace_directory
from agent_autopsy.advanced.comparison import trace_diff_detail
from agent_autopsy.errors import ParseError, PluginError, SchemaValidationError
from agent_autopsy.ingestion import TraceNormalizer
from agent_autopsy.output import ArtifactGenerator, FixSuggestionGenerator
from agent_autopsy.schema import TraceStatus
from agent_autopsy.utils.config import get_config

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="autopsy",
    help="Agent Autopsy - Debug and analyze agent execution traces",
    add_completion=True,
)
console = Console()


def _trace_has_findings(trace, preanalysis) -> bool:
    """Whether the run should be treated as having actionable findings (non-zero exit).

    A run with a recovered error but no detected signals exits cleanly; the
    gate fires on detected signals, on a non-success status, or when a failed
    run recorded an error summary.
    """
    if preanalysis.signals:
        return True
    if trace.status != TraceStatus.SUCCESS:
        return True
    if getattr(trace.stats, "num_errors", 0) > 0 and trace.error_summary:
        return True
    return False


@app.command()
def analyze(
    trace_file: Path = typer.Argument(
        ...,
        help="Path to the trace JSON file",
        exists=True,
        readable=True,
    ),
    output: Path | None = typer.Option(
        None,
        "-o",
        "--output",
        help="Output file path for the report",
    ),
    artifacts: Path | None = typer.Option(
        None,
        "--artifacts",
        help="Output directory for patch artifacts",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Model to use for analysis (overrides default)",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="LLM provider override: openrouter | openai | anthropic | ollama",
    ),
    verbose: bool = typer.Option(
        False,
        "-v",
        "--verbose",
        help="Show detailed output including tool traces",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Run only deterministic analysis without LLM",
    ),
    no_embeddings: bool = typer.Option(
        False,
        "--no-embeddings",
        help="Do not load sentence-transformers for semantic drift (saves memory)",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass LLM response disk cache (only applies with LLM analysis)",
    ),
    stream: bool = typer.Option(
        False,
        "--stream",
        help="Stream LLM output to the terminal (LLM path only)",
    ),
    quiet: bool = typer.Option(
        False,
        "-q",
        "--quiet",
        help="Minimal output (no spinners/banners); faster for scripts",
    ),
    format: str = typer.Option(
        "text",
        "-f",
        "--format",
        help="Output format: text | markdown | json",
    ),
):
    """
    Analyze an agent execution trace and generate an autopsy report.

    Example:
        autopsy analyze ./traces/run_001.json
        autopsy analyze ./traces/run_001.json -o report.md --artifacts ./patches/
    """
    config = get_config()
    prev_skip = config.skip_embeddings
    prev_provider = config.llm_provider
    if no_embeddings:
        config.skip_embeddings = True
    if provider:
        config.llm_provider = provider.strip().lower()

    # Validate options before touching the trace file so bad flags fail fast.
    fmt = format.lower().strip()
    if fmt not in ("text", "markdown", "json"):
        console.print(f"[red]Unknown format:[/red] {format} (use text, markdown, or json)")
        raise typer.Exit(2)

    exit_code = 0
    trace = None
    preanalysis = None
    t0 = time.perf_counter()
    try:
        if not quiet:
            console.print(
                Panel.fit(
                    "[bold blue]Agent Autopsy[/bold blue]\nAnalyzing agent execution trace...",
                    border_style="blue",
                )
            )

        def _progress_ctx():
            if quiet:
                from contextlib import nullcontext

                return nullcontext()
            return Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            )

        with _progress_ctx() as progress:
            if not quiet:
                task = progress.add_task("Parsing trace file...", total=None)
            try:
                trace = api.load_trace(trace_file)
                api.apply_embedding_defaults_for_trace(trace)
            except (ParseError, SchemaValidationError, PluginError) as e:
                console.print(f"[red]Error parsing trace:[/red] {e}")
                exit_code = 2
                raise typer.Exit(exit_code)
            except Exception:
                logger.exception("Unexpected error while parsing trace file")
                console.print("[red]Error parsing trace (see logs for details).[/red]")
                exit_code = 2
                raise typer.Exit(exit_code)
            if not quiet:
                progress.update(task, description="Trace parsed successfully")

        summary = api.trace_summary(trace)
        if not quiet:
            _print_trace_summary(summary)

        with _progress_ctx() as progress:
            if not quiet:
                task = progress.add_task("Running pre-analysis...", total=None)
            preanalysis = api.run_preanalysis(trace)
            if not quiet:
                progress.update(task, description="Pre-analysis complete")

        if verbose and not quiet:
            _print_preanalysis(preanalysis)

        if no_llm or not api.llm_credentials_configured(config):
            if not no_llm and not quiet:
                console.print(
                    "[yellow]Warning:[/yellow] No API key configured for the selected LLM provider. "
                    "Running without LLM."
                )
            result = api.run_deterministic_analysis(trace)
        elif stream:
            result_holder: dict = {}
            try:
                for chunk in api.stream_llm_analysis_text(
                    trace,
                    result_holder,
                    model=model,
                    verbose=verbose,
                    use_cache=not no_cache,
                ):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                result = result_holder.get("result") or api.run_deterministic_analysis(trace)
            except Exception as e:
                logger.exception("Streaming LLM analysis failed")
                console.print(f"\n[yellow]Streaming failed:[/yellow] {e}")
                result = api.run_deterministic_analysis(trace)
        else:
            with _progress_ctx() as progress:
                if not quiet:
                    task = progress.add_task(
                        f"Running LLM analysis with {model or config.default_model}...",
                        total=None,
                    )
                try:
                    result = api.run_llm_analysis(
                        trace,
                        model=model,
                        verbose=verbose,
                        use_cache=not no_cache,
                    )
                except Exception as e:
                    logger.exception("LLM analysis failed")
                    console.print(f"[yellow]LLM analysis failed:[/yellow] {e}")
                    console.print("Falling back to deterministic analysis...")
                    result = api.run_deterministic_analysis(trace)
                if not quiet:
                    progress.update(task, description="Analysis complete")

        report_generator = api.generate_report(trace, result)

        if output:
            save_fmt = fmt if fmt in ("json", "markdown", "text") else "markdown"
            saved_path = report_generator.save(output, format=save_fmt)
            if not quiet:
                console.print(f"\n[green]Report saved to:[/green] {saved_path}")
        else:
            console.print("\n")
            if fmt == "json":
                sys.stdout.write(json.dumps(report_generator.to_json(), indent=2, default=str))
                console.print()
            elif fmt == "markdown":
                console.print(Markdown(report_generator.to_markdown()))
            else:
                console.print(report_generator.render("text"))

        if artifacts:
            artifact_generator = ArtifactGenerator(trace, preanalysis)
            saved_artifacts = artifact_generator.save_all(artifacts)
            if not quiet:
                console.print(f"\n[green]Artifacts saved to:[/green] {artifacts}")
                for path in saved_artifacts:
                    console.print(f"  - {path.name}")

        if not quiet:
            console.print("\n")
            _print_result_summary(result, preanalysis)

        if _trace_has_findings(trace, preanalysis):
            exit_code = 1
    finally:
        config.skip_embeddings = prev_skip
        config.llm_provider = prev_provider
        try:
            from agent_autopsy.utils.telemetry import record_event

            record_event(
                "analyze",
                exit_code=exit_code,
                signal_count=len(preanalysis.signals) if preanalysis else 0,
                run_id=trace.run_id if trace else None,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        except Exception:
            logger.debug("telemetry record failed", exc_info=True)

    raise typer.Exit(exit_code)


@app.command()
def summary(
    trace_file: Path = typer.Argument(
        ...,
        help="Path to the trace JSON file",
        exists=True,
        readable=True,
    ),
):
    """
    Show a quick summary of a trace without full analysis.

    Example:
        autopsy summary ./traces/run_001.json
    """
    try:
        trace = api.load_trace(trace_file)
    except (ParseError, SchemaValidationError, PluginError) as e:
        console.print(f"[red]Error parsing trace:[/red] {e}")
        raise typer.Exit(2)
    except Exception:
        logger.exception("Unexpected error parsing trace")
        console.print("[red]Error parsing trace (see logs for details).[/red]")
        raise typer.Exit(2)

    summary = api.trace_summary(trace)
    _print_trace_summary(summary)

    # Quick pre-analysis
    preanalysis = api.run_preanalysis(trace)
    _print_preanalysis(preanalysis)


@app.command()
def validate(
    trace_file: Path = typer.Argument(
        ...,
        help="Path to the trace JSON file",
        exists=True,
        readable=True,
    ),
):
    """
    Validate a trace file format without running analysis.

    Example:
        autopsy validate ./traces/run_001.json
    """
    try:
        trace = api.load_trace(trace_file)
        issues = TraceNormalizer.validate(trace)

        if issues:
            console.print("[yellow]Validation issues found:[/yellow]")
            for issue in issues:
                console.print(f"  - {issue}")
        else:
            console.print("[green]Trace is valid![/green]")

        # Print basic info
        console.print(f"\nRun ID: {trace.run_id}")
        console.print(f"Events: {len(trace.events)}")
        console.print(f"Status: {trace.status.value}")

    except (ParseError, SchemaValidationError, PluginError) as e:
        console.print(f"[red]Invalid trace file:[/red] {e}")
        raise typer.Exit(2)
    except Exception:
        logger.exception("Unexpected error validating trace")
        console.print("[red]Invalid trace file (see logs for details).[/red]")
        raise typer.Exit(2)


@app.command()
def config():
    """
    Show current configuration.
    """
    cfg = get_config()

    table = Table(title="Agent Autopsy Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    for key, value in cfg.to_dict().items():
        table.add_row(key, str(value))

    console.print(table)


@app.command("compare")
@app.command("diff")
def compare_traces(
    trace_a: Path = typer.Argument(..., exists=True, readable=True, help="First trace file"),
    trace_b: Path = typer.Argument(..., exists=True, readable=True, help="Second trace file"),
    out_format: str = typer.Option(
        "text",
        "-f",
        "--format",
        help="text (human) or json (pipe to jq)",
    ),
):
    """Compare two traces: patterns, tool deltas, timing (alias: diff)."""
    try:
        a = api.load_trace(trace_a)
        b = api.load_trace(trace_b)
    except (ParseError, SchemaValidationError, PluginError) as e:
        console.print(f"[red]Error parsing traces:[/red] {e}")
        raise typer.Exit(2)
    except Exception:
        logger.exception("Unexpected error parsing traces for compare")
        console.print("[red]Error parsing traces (see logs for details).[/red]")
        raise typer.Exit(2)

    detail = trace_diff_detail(a, b)
    fmt = out_format.lower().strip()
    if fmt == "json":
        sys.stdout.write(json.dumps(detail, indent=2, default=str))
        console.print()
        return

    adv = detail["advanced"]
    table = Table(title="Trace comparison")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Trace A", detail["run_id_a"])
    table.add_row("Trace B", detail["run_id_b"])
    table.add_row("Event IDs only in A", str(detail["event_ids_only_in_a"][:20]))
    table.add_row("Event IDs only in B", str(detail["event_ids_only_in_b"][:20]))
    table.add_row("Patterns only in A", ", ".join(detail["patterns_only_in_a"]) or "None")
    table.add_row("Patterns only in B", ", ".join(detail["patterns_only_in_b"]) or "None")
    table.add_row("New tool signatures", str(len(adv["new_tool_signatures"])))
    table.add_row("Removed tool signatures", str(len(adv["removed_tool_signatures"])))
    table.add_row("Tool arg/name diffs", str(len(detail["tool_call_arg_or_name_diffs"])))
    console.print(table)


@app.command("watch")
def watch_traces(
    directory: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        help="Directory to watch for new .json traces",
    ),
    pattern: str = typer.Option("*.json", "--pattern", help="Filename glob"),
    bell: bool = typer.Option(False, "--bell", help="Terminal bell on critical findings"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Only print analysis lines"),
):
    """Watch a directory and analyze new trace JSON files as they appear."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers.polling import PollingObserver

    seen: set[str] = set()

    class Handler(FileSystemEventHandler):
        def _handle(self, path: Path) -> None:
            if not fnmatch.fnmatch(path.name, pattern):
                return
            key = str(path.resolve())
            if key in seen:
                return
            try:
                trace = api.load_trace(path)
            except Exception as exc:
                console.print(f"[red]watch:[/red] failed to load {path}: {exc}")
                return
            # Only mark as seen after a successful load so traces that were
            # still being written when the create event fired get retried
            # via the subsequent modify event.
            seen.add(key)
            pre = api.run_preanalysis(trace)
            crit = any(s.severity == "critical" for s in pre.signals)
            if not quiet:
                console.print(f"[cyan]new[/cyan] {path.name} run={trace.run_id} signals={len(pre.signals)}")
            else:
                console.print(f"{path.name}\tsignals={len(pre.signals)}")
            if crit and bell:
                sys.stdout.write("\a")
                sys.stdout.flush()
            if pre.signals and not quiet:
                for s in pre.signals[:8]:
                    sev = "red" if s.severity == "critical" else "yellow"
                    console.print(f"  [{sev}]{s.severity}[/{sev}] {s.type}: {s.evidence[:120]}")

        def on_created(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            self._handle(Path(str(event.src_path)))

        def on_modified(self, event):  # type: ignore[override]
            if event.is_directory:
                return
            self._handle(Path(str(event.src_path)))

    # The native macOS FSEvents observer can fail asynchronously in containers
    # and restricted CI hosts, leaving the command alive but unable to observe
    # anything. Polling is portable and, for a local trace directory, cheap.
    obs = PollingObserver(timeout=0.25)
    obs.schedule(Handler(), str(directory), recursive=False)
    obs.start()
    console.print(f"[green]Watching[/green] {directory} for {pattern} (Ctrl+C to stop)")
    try:
        while obs.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        obs.stop()
        obs.join()


@app.command("replay")
def replay_trace(
    trace_file: Path = typer.Argument(..., exists=True, readable=True, help="Trace JSON"),
    start_from: int = typer.Option(0, "--from", help="Only show events with id >= this"),
    delay: float = typer.Option(0.35, "--delay", help="Seconds between steps (before speed)"),
    speed: float = typer.Option(1.0, "--speed", help="Delay divisor (2 = twice as fast)"),
    step: bool = typer.Option(False, "--step", help="Wait for Enter after each event"),
    until_regex: str | None = typer.Option(None, "--until", help="Stop when event text matches regex"),
):
    """Print trace events step-by-step like a debugger."""
    import re as re_mod

    try:
        trace = api.load_trace(trace_file)
    except (ParseError, SchemaValidationError, PluginError) as e:
        console.print(f"[red]Error parsing trace:[/red] {e}")
        raise typer.Exit(2)
    until_c = re_mod.compile(until_regex) if until_regex else None
    sp = max(0.01, speed)
    for ev in trace.events:
        if ev.event_id < start_from:
            continue
        line = f"[{ev.event_id:04d}] {ev.type.value}"
        if ev.name:
            line += f" {ev.name}"
        if ev.agent_id:
            line += f" @{ev.agent_id}"
        console.print(line)
        blob = f"{ev.input!s} {ev.output!s}"
        if until_c and until_c.search(blob):
            console.print("[green]--until matched, stopping--[/green]")
            break
        if step:
            input()
        else:
            time.sleep(delay / sp)


@app.command()
def benchmark(
    traces_dir: Path = typer.Option(
        Path("./traces"),
        "--traces-dir",
        help="Directory containing trace JSON files",
    ),
    limit: int = typer.Option(100, "--limit", help="Maximum number of traces to include"),
):
    """Run benchmark/evaluation metrics across traces."""
    result = benchmark_trace_directory(traces_dir, limit=limit).to_dict()
    table = Table(title="Benchmark Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Total Runs", str(result["total_runs"]))
    table.add_row("Success Rate", f"{result['success_rate']:.0%}")
    table.add_row("Average Tokens", str(result["average_tokens"]))
    table.add_row("Average Latency (ms)", str(result["average_latency_ms"]))
    table.add_row("Average Errors", str(result["average_errors"]))
    table.add_row(
        "Top Patterns",
        ", ".join(f"{p['pattern']}({p['count']})" for p in result["top_failure_patterns"]) or "None",
    )
    table.add_row(
        "Degradation Alerts",
        "; ".join(result["degradation_alerts"]) if result["degradation_alerts"] else "None",
    )
    console.print(table)


@app.command()
def monitor(
    traces_dir: Path = typer.Option(Path("./traces"), "--traces-dir", help="Trace directory to monitor"),
    duration: float = typer.Option(10.0, "--duration", help="Monitor duration in seconds"),
    poll_interval: float = typer.Option(1.0, "--poll-interval", help="Polling interval in seconds"),
    max_alerts: int = typer.Option(50, "--max-alerts", help="Maximum alerts to print"),
):
    """Monitor traces in near real-time and print pattern alerts."""
    mon = LiveTraceMonitor(traces_dir, poll_interval_seconds=poll_interval)
    alert_count = 0
    for alert in mon.stream(duration_seconds=duration):
        console.print(
            f"[yellow]alert[/yellow] {alert.severity.upper()} "
            f"{alert.pattern_type} run={alert.run_id} events={alert.event_ids} "
            f"file={alert.trace_file}"
        )
        alert_count += 1
        if alert_count >= max_alerts:
            break
    console.print(f"Monitoring complete. Alerts emitted: {alert_count}")


@app.command()
def fixes(
    trace_file: Path = typer.Argument(..., exists=True, readable=True, help="Trace file path"),
):
    """Generate advanced fix suggestions for a trace."""
    try:
        trace = api.load_trace(trace_file)
    except (ParseError, SchemaValidationError, PluginError) as e:
        console.print(f"[red]Error parsing trace:[/red] {e}")
        raise typer.Exit(2)
    except Exception:
        logger.exception("Unexpected error parsing trace for fixes")
        console.print("[red]Error parsing trace (see logs for details).[/red]")
        raise typer.Exit(2)

    preanalysis = api.run_preanalysis(trace)
    suggestions = FixSuggestionGenerator(trace, preanalysis).to_dict()
    if not suggestions:
        console.print("No advanced fix suggestions generated.")
        return

    for idx, suggestion in enumerate(suggestions, 1):
        console.print(
            Panel.fit(
                f"[bold]{suggestion['title']}[/bold]\n"
                f"Category: {suggestion['category']}\n"
                f"Events: {suggestion['event_ids']}\n\n"
                f"Rationale: {suggestion['rationale']}\n\n"
                f"Patch snippet:\n{suggestion['patch_snippet']}",
                title=f"Fix Suggestion {idx}",
                border_style="blue",
            )
        )


@app.command("agent-flow")
def agent_flow(
    trace_file: Path = typer.Argument(..., exists=True, readable=True, help="Trace file path"),
):
    """Show inter-agent communication and handoff flow."""
    try:
        trace = api.load_trace(trace_file)
    except (ParseError, SchemaValidationError, PluginError) as e:
        console.print(f"[red]Error parsing trace:[/red] {e}")
        raise typer.Exit(2)
    except Exception:
        logger.exception("Unexpected error parsing trace for agent-flow")
        console.print("[red]Error parsing trace (see logs for details).[/red]")
        raise typer.Exit(2)

    agent_ids = trace.get_agent_ids()
    handoffs = trace.get_agent_handoffs()
    table = Table(title="Agent Flow")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Agents", ", ".join(agent_ids) if agent_ids else "None")
    table.add_row("Handoffs", str(len(handoffs)))
    console.print(table)

    if handoffs:
        detail = Table(title="Handoff Details")
        detail.add_column("Event ID", style="cyan")
        detail.add_column("From", style="white")
        detail.add_column("To", style="white")
        for event_id, src, dst in handoffs:
            detail.add_row(str(event_id), src, dst)
        console.print(detail)


def _print_trace_summary(summary: dict):
    """Print trace summary table."""
    table = Table(title="Trace Summary", show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Run ID", summary.get("run_id", "N/A"))
    table.add_row("Status", summary.get("status", "N/A"))
    table.add_row("Framework", summary.get("framework", "N/A"))
    table.add_row("Model", summary.get("model", "N/A"))
    table.add_row("Total Events", str(summary.get("total_events", 0)))
    table.add_row("LLM Calls", str(summary.get("llm_calls", 0)))
    table.add_row("Tool Calls", str(summary.get("tool_calls", 0)))
    table.add_row("Errors", str(summary.get("errors", 0)))
    table.add_row("Total Tokens", str(summary.get("total_tokens", "N/A")))
    table.add_row("Duration (ms)", str(summary.get("duration_ms", "N/A")))
    table.add_row("Agent Count", str(summary.get("agent_count", 0)))

    console.print(table)


def _print_preanalysis(preanalysis):
    """Print pre-analysis results."""
    console.print("\n[bold]Pre-Analysis Results[/bold]")
    console.print(f"Summary: {preanalysis.summary}")

    if preanalysis.signals:
        console.print("\n[bold]Signals Detected:[/bold]")
        for signal in preanalysis.signals:
            severity_color = {
                "critical": "red",
                "high": "yellow",
                "medium": "blue",
                "low": "white",
            }.get(signal.severity, "white")

            console.print(
                f"  [{severity_color}]{signal.severity.upper()}[/{severity_color}] {signal.type}: {signal.evidence}"
            )
            console.print(f"    Events: {signal.event_ids}")

    if preanalysis.hypotheses:
        console.print("\n[bold]Top Hypotheses:[/bold]")
        for i, hyp in enumerate(preanalysis.hypotheses[:3], 1):
            console.print(f"  {i}. {hyp.description}")
            console.print(f"     Confidence: {hyp.confidence:.0%} | Category: {hyp.category}")


def _print_preanalysis_summary(preanalysis):
    """Backward-compatible alias for concise pre-analysis output."""
    _print_preanalysis(preanalysis)


def _print_result_summary(result, preanalysis):
    """Print analysis result summary."""
    status = "[green]SUCCESS[/green]" if result.success else "[red]FAILED[/red]"
    console.print(
        Panel.fit(
            f"Analysis Status: {status}\n"
            f"Signals Found: {len(preanalysis.signals)}\n"
            f"Hypotheses Generated: {len(preanalysis.hypotheses)}",
            title="Analysis Complete",
            border_style="green" if result.success else "red",
        )
    )


@app.command("autopsy-run")
def autopsy_run(
    trace_file: Path = typer.Argument(
        ...,
        help="Path to the trace JSON file to analyze",
        exists=True,
        readable=True,
    ),
    output: Path | None = typer.Option(
        None,
        "-o",
        "--output",
        help="Output file path for the report (default: ./reports/<trace_name>.md)",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Run only deterministic analysis without LLM",
    ),
    no_embeddings: bool = typer.Option(
        False,
        "--no-embeddings",
        help="Do not load sentence-transformers for semantic drift (saves memory)",
    ),
    verbose: bool = typer.Option(
        False,
        "-v",
        "--verbose",
        help="Show detailed output",
    ),
):
    """
    Run full autopsy analysis on a captured trace file.

    This command loads a trace JSON file (captured by TraceSaver),
    runs the complete analysis pipeline (parsing, normalization,
    pattern detection, and LLM analysis), and generates a report.

    Example:
        python -m src.cli autopsy-run traces/20241231_123456_abc123.json
        python -m src.cli autopsy-run traces/my_trace.json -o report.md --no-llm
    """
    config = get_config()
    prev_skip = config.skip_embeddings
    if no_embeddings:
        config.skip_embeddings = True

    try:
        console.print(
            Panel.fit(
                "[bold blue]Agent Autopsy[/bold blue]\nRunning full analysis pipeline...",
                border_style="blue",
            )
        )

        # Step 1: Parse and normalize trace
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Parsing trace file...", total=None)

            try:
                trace = api.load_trace(trace_file)
                api.apply_embedding_defaults_for_trace(trace)
            except (ParseError, SchemaValidationError, PluginError) as e:
                console.print(f"[red]Error parsing trace:[/red] {e}")
                raise typer.Exit(2)
            except Exception:
                logger.exception("Unexpected error parsing trace for autopsy-run")
                console.print("[red]Error parsing trace (see logs for details).[/red]")
                raise typer.Exit(2)

            progress.update(task, description="Trace parsed successfully")

        # Print trace summary
        table = Table(title="Trace Summary", show_header=False)
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("Trace File", str(trace_file))
        table.add_row("Run ID", trace.run_id)
        table.add_row("Status", trace.status.value)
        table.add_row("Total Events", str(len(trace.events)))
        table.add_row("LLM Calls", str(trace.stats.num_llm_calls))
        table.add_row("Tool Calls", str(trace.stats.num_tool_calls))
        table.add_row("Errors", f"[red]{trace.stats.num_errors}[/red]" if trace.stats.num_errors else "0")

        console.print(table)

        # Step 2: Run pre-analysis
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running pre-analysis...", total=None)

            preanalysis = api.run_preanalysis(trace)

            progress.update(
                task,
                description=f"Pre-analysis complete: {len(preanalysis.signals)} signals, {len(preanalysis.hypotheses)} hypotheses",
            )

        if verbose:
            _print_preanalysis_summary(preanalysis)

        # Step 3: Run analysis (LLM or deterministic)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            if no_llm or not api.llm_credentials_configured(config):
                task = progress.add_task("Running deterministic analysis...", total=None)
                result = api.run_deterministic_analysis(trace)
            else:
                task = progress.add_task("Running LLM analysis...", total=None)
                try:
                    result = api.run_llm_analysis(trace, verbose=verbose)
                except Exception as e:
                    logger.exception("LLM analysis failed in autopsy-run")
                    console.print(f"[yellow]LLM analysis failed, falling back to deterministic:[/yellow] {e}")
                    result = api.run_deterministic_analysis(trace)

            progress.update(task, description="Analysis complete")

        # Step 4: Generate report
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Generating report...", total=None)

            # Determine output path
            if output is None:
                reports_dir = Path("./reports")
                reports_dir.mkdir(parents=True, exist_ok=True)
                output = reports_dir / f"{trace_file.stem}.md"

            # Generate and save the report
            report_gen = api.generate_report(trace, result)
            output_format = "json" if output.suffix.lower() == ".json" else "markdown"
            output = report_gen.save(output, format=output_format)

            progress.update(task, description="Report generated")

        console.print(f"\n[green]Report saved to:[/green] {output}")

        # Print result summary
        _print_result_summary(result, preanalysis)

        # Exit codes: 1 = findings, 0 = clean (match `analyze`)
        if _trace_has_findings(trace, preanalysis):
            console.print("\n[yellow]Issues detected in trace - review report for details[/yellow]")
            raise typer.Exit(1)
        console.print("\n[green]No issues detected in trace[/green]")
    finally:
        config.skip_embeddings = prev_skip


@app.command("telemetry")
def telemetry_cmd(
    action: str = typer.Argument(
        ...,
        help="on | off | status — opt-in anonymous usage metrics (off by default)",
    ),
):
    """
    Control opt-in telemetry (command names, exit codes, signal counts — no trace payloads).

    Enable with ``on`` or environment ``AUTOPSY_TELEMETRY=1``. Events append to
    ``~/.cache/agent-autopsy/telemetry-events.jsonl``.
    """
    from agent_autopsy.utils import telemetry as tel

    a = action.lower().strip()
    if a == "on":
        tel.set_enabled(True)
        console.print(f"[green]Telemetry enabled.[/green] Log: {tel.events_path()}")
    elif a == "off":
        tel.set_enabled(False)
        console.print("[dim]Telemetry disabled.[/dim]")
    elif a == "status":
        on = tel.is_enabled()
        console.print(f"Telemetry: {'[green]on[/green]' if on else '[dim]off[/dim]'}")
        console.print(f"State: {tel.state_path()}")
        console.print(f"Events: {tel.events_path()}")
    else:
        console.print("[red]Unknown action. Use:[/red] on | off | status")
        raise typer.Exit(2)


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
