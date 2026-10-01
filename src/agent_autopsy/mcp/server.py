"""
MCP server integration for Agent Autopsy.

Exposes Agent Autopsy capabilities as MCP tools, resources, and prompts.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from pydantic import AnyHttpUrl

from . import service


class _StaticMcpTokenVerifier:
    """Bearer token check for MCP HTTP/SSE when ``MCP_SSE_TOKEN`` is set."""

    def __init__(self, secret: str) -> None:
        self._secret = secret

    async def verify_token(self, token: str) -> Any:
        import secrets

        from mcp.server.auth.provider import AccessToken

        if not token or not self._secret:
            return None
        if secrets.compare_digest(token.strip(), self._secret):
            return AccessToken(token=token, client_id="agent-autopsy-mcp", scopes=["mcp"])
        return None


def create_mcp_server(transport: str = "stdio") -> Any:
    """Create and configure the FastMCP server instance."""
    try:
        from mcp.server.auth.settings import AuthSettings
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP SDK is not installed. Install dependency `mcp` to run the MCP server."
        ) from exc

    auth_settings = None
    token_verifier = None
    secret = os.getenv("MCP_SSE_TOKEN", "").strip()
    if transport in ("sse", "streamable-http"):
        if not secret:
            raise RuntimeError(
                "MCP_SSE_TOKEN must be set for the sse/streamable-http transports; "
                "they expose analysis tools over HTTP. Use stdio for local use, "
                "or set MCP_SSE_TOKEN to require bearer auth."
            )
        # Minimal resource-server metadata so FastMCP can install bearer auth on HTTP transports.
        auth_settings = AuthSettings(
            issuer_url=AnyHttpUrl("http://127.0.0.1:9/"),
            resource_server_url=AnyHttpUrl("http://127.0.0.1:9/"),
            required_scopes=["mcp"],
        )
        token_verifier = _StaticMcpTokenVerifier(secret)

    mcp = FastMCP(
        "Agent Autopsy MCP",
        instructions=(
            "Use these tools to analyze agent traces, detect failure patterns, "
            "validate trace structure, compare runs, and generate fix guidance."
        ),
        auth=auth_settings,
        token_verifier=token_verifier,
    )

    @mcp.tool()
    def analyze_trace(
        trace_file: str | None = None,
        trace_json: dict[str, Any] | str | None = None,
        deterministic_only: bool = False,
        model: str | None = None,
        output_format: str = "markdown",
    ) -> dict[str, Any]:
        """Analyze a trace and return report, preanalysis, and health data."""
        return service.analyze_trace(
            trace_file=trace_file,
            trace_json=trace_json,
            deterministic_only=deterministic_only,
            model=model,
            output_format=output_format,
        )

    @mcp.tool()
    def detect_patterns(
        trace_file: str | None = None,
        trace_json: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Run deterministic pattern detection on a trace."""
        return service.detect_patterns(trace_file=trace_file, trace_json=trace_json)

    @mcp.tool()
    def validate_trace(
        trace_file: str | None = None,
        trace_json: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Validate a trace and return parse/schema issues."""
        return service.validate_trace(trace_file=trace_file, trace_json=trace_json)

    @mcp.tool()
    def get_trace_summary(
        trace_file: str | None = None,
        trace_json: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Get lightweight summary stats for a trace."""
        return service.get_trace_summary(trace_file=trace_file, trace_json=trace_json)

    @mcp.tool()
    def compare_traces(
        trace_file_a: str | None = None,
        trace_json_a: dict[str, Any] | str | None = None,
        trace_file_b: str | None = None,
        trace_json_b: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Compare two traces and return metric/pattern deltas."""
        return service.compare_traces(
            trace_file_a=trace_file_a,
            trace_json_a=trace_json_a,
            trace_file_b=trace_file_b,
            trace_json_b=trace_json_b,
        )

    @mcp.tool()
    def capture_trace(
        trace_dir: str | None = None,
        enabled: bool = True,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        """Configure trace-capture settings for the running process."""
        return service.capture_trace(trace_dir=trace_dir, enabled=enabled, max_chars=max_chars)

    @mcp.tool()
    def list_traces(
        directory: str | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List available trace files with optional filters."""
        return service.list_traces(
            directory=directory,
            status=status,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    @mcp.tool()
    def get_event_details(
        trace_file: str | None = None,
        trace_json: dict[str, Any] | str | None = None,
        event_id: int | None = None,
        start_id: int | None = None,
        end_id: int | None = None,
    ) -> dict[str, Any]:
        """Fetch details for one event or an event range."""
        return service.get_event_details(
            trace_file=trace_file,
            trace_json=trace_json,
            event_id=event_id,
            start_id=start_id,
            end_id=end_id,
        )

    @mcp.tool()
    def suggest_fixes(
        trace_file: str | None = None,
        trace_json: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Get categorized fix suggestions from deterministic analysis."""
        return service.suggest_fixes(trace_file=trace_file, trace_json=trace_json)

    @mcp.tool()
    def health_check(
        trace_file: str | None = None,
        trace_json: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Return health score and one-line summary for a trace."""
        return service.health_check(trace_file=trace_file, trace_json=trace_json)

    @mcp.tool()
    def benchmark_runs(
        trace_files: list[str] | None = None,
        directory: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Run evaluation metrics across traces and detect degradation trends."""
        return service.benchmark_runs(trace_files=trace_files, directory=directory, limit=limit)

    @mcp.tool()
    def monitor_traces(
        trace_dir: str | None = None,
        duration_seconds: float = 5.0,
        poll_interval_seconds: float = 1.0,
        max_alerts: int = 100,
    ) -> dict[str, Any]:
        """Monitor traces in near-real-time and emit pattern alerts."""
        return service.monitor_traces(
            trace_dir=trace_dir,
            duration_seconds=duration_seconds,
            poll_interval_seconds=poll_interval_seconds,
            max_alerts=max_alerts,
        )

    @mcp.tool()
    def conversation_flow(
        trace_file: str | None = None,
        trace_json: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Build inter-agent communication and handoff flow from a trace."""
        return service.conversation_flow(trace_file=trace_file, trace_json=trace_json)

    @mcp.resource("agent-autopsy://traces/recent")
    def resource_recent_traces() -> str:
        """Resource: recently available traces."""
        return json.dumps(service.recent_traces_resource(), indent=2, default=str)

    @mcp.resource("agent-autopsy://reports/archive")
    def resource_report_archive() -> str:
        """Resource: historical report index archive."""
        return json.dumps(service.report_archive_resource(), indent=2, default=str)

    @mcp.resource("agent-autopsy://patterns/catalog")
    def resource_pattern_catalog() -> str:
        """Resource: pattern catalog with descriptions."""
        return json.dumps(service.pattern_catalog_resource(), indent=2, default=str)

    @mcp.resource("agent-autopsy://config/current")
    def resource_config() -> str:
        """Resource: current Agent Autopsy configuration."""
        return json.dumps(service.config_resource(), indent=2, default=str)

    @mcp.resource("agent-autopsy://plugins/active")
    def resource_plugins() -> str:
        """Resource: active plugin registry and load errors."""
        return json.dumps(service.plugin_resource(), indent=2, default=str)

    @mcp.prompt()
    def debug_my_agent(trace_reference: str = "") -> str:
        """Prompt template: guided trace debugging workflow."""
        return service.debug_my_agent_prompt(trace_reference=trace_reference)

    @mcp.prompt()
    def quick_health_check(trace_reference: str = "") -> str:
        """Prompt template: fast healthy/unhealthy verdict."""
        return service.quick_health_check_prompt(trace_reference=trace_reference)

    @mcp.prompt()
    def compare_runs(trace_a: str = "", trace_b: str = "") -> str:
        """Prompt template: compare two traces and summarize changes."""
        return service.compare_runs_prompt(trace_a=trace_a, trace_b=trace_b)

    @mcp.prompt()
    def explain_failure(trace_reference: str = "", event_id: int | None = None) -> str:
        """Prompt template: deep root-cause explanation workflow."""
        return service.explain_failure_prompt(trace_reference=trace_reference, event_id=event_id)

    return mcp


def main() -> None:
    """CLI entry point for running the MCP server."""
    parser = argparse.ArgumentParser(description="Run Agent Autopsy MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport mode",
    )
    parser.add_argument(
        "--mount-path",
        default=None,
        help="Optional mount path for HTTP/SSE transports",
    )
    args = parser.parse_args()

    mcp = create_mcp_server(transport=args.transport)
    run_kwargs: dict[str, Any] = {"transport": args.transport}
    if args.mount_path:
        run_kwargs["mount_path"] = args.mount_path

    try:
        mcp.run(**run_kwargs)
    except KeyboardInterrupt:
        # Graceful shutdown for interactive local runs.
        return


if __name__ == "__main__":
    main()
