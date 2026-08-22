# TraceAutopsy MCP server

## Running

- **stdio** (default, local IDE integration): `python -m agent_autopsy.mcp --transport stdio`
- **SSE / HTTP** (remote or LAN): `python -m agent_autopsy.mcp --transport sse` (see MCP SDK / FastMCP docs for ports and paths)

## Security: SSE and streamable HTTP

When you use `sse` or `streamable-http`, any process that can reach the listener can invoke analysis tools on traces you expose through the server.

The server refuses to start on these transports without a token. Set a shared secret and send it as a **Bearer** token:

1. Export `MCP_SSE_TOKEN` to a long random string on the server.
2. Configure your MCP client to pass `Authorization: Bearer <same token>` on HTTP requests to the MCP endpoint.

**stdio** transport remains process-local and does not use `MCP_SSE_TOKEN`.

## Environment

See the project `.env.example` for `PROVIDER`, API keys, and trace capture settings.
