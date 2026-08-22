"""MCP server wiring: bearer-token verification and server construction.

The service layer has its own suite; this file covers mcp/server.py itself,
which routes every tool call in production.
"""

from __future__ import annotations

import pytest

from agent_autopsy.mcp.server import _StaticMcpTokenVerifier, create_mcp_server


@pytest.mark.asyncio
async def test_missing_or_wrong_tokens_are_rejected():
    verifier = _StaticMcpTokenVerifier("secret-token")
    assert await verifier.verify_token(None) is None
    assert await verifier.verify_token("") is None
    assert await verifier.verify_token("wrong") is None


@pytest.mark.asyncio
async def test_matching_token_grants_mcp_scope():
    verifier = _StaticMcpTokenVerifier("secret-token")
    access = await verifier.verify_token("  secret-token  ")
    assert access is not None
    assert access.client_id == "agent-autopsy-mcp"
    assert access.scopes == ["mcp"]


def test_create_server_stdio_builds_and_registers_tools():
    mcp = create_mcp_server("stdio")
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 13


def test_http_transports_refuse_to_start_without_token(monkeypatch):
    monkeypatch.delenv("MCP_SSE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="MCP_SSE_TOKEN"):
        create_mcp_server("sse")
    with pytest.raises(RuntimeError, match="MCP_SSE_TOKEN"):
        create_mcp_server("streamable-http")


def test_http_transport_starts_when_token_set(monkeypatch):
    monkeypatch.setenv("MCP_SSE_TOKEN", "secret-token")
    mcp = create_mcp_server("sse")
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 13
