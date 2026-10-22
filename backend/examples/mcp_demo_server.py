"""
mcp_demo_server.py — a tiny MCP tool server for testing Orion's MCP support
============================================================================
Part of UPDATE-014. This file is NOT imported by the backend; it is a demo
you can point ``.orion/mcp.json`` at to verify the MCP integration end to
end without downloading any third-party server:

    {
      "mcpServers": {
        "demo": {
          "command": "<path to backend venv python>",
          "args": ["<path to repo>/backend/examples/mcp_demo_server.py"]
        }
      }
    }

Requires only the ``mcp`` package (already in backend/requirements.txt).
Works with mcp 1.x (FastMCP) and 2.x (MCPServer) — the SDK renamed the
high-level server class between major versions.

Tools:
  echo(text)        -> returns the text verbatim          (happy path)
  add(a, b)         -> returns the sum of two numbers     (arguments + schema)
  fail(reason)      -> always raises                      (tool-level error:
                       Orion must show [MCP TOOL ERROR] and keep working)
"""

import sys

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _ServerImpl
except ImportError:  # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as _ServerImpl
    except ImportError:
        sys.stderr.write(
            "orion demo mcp server: the 'mcp' package is not installed in this "
            "interpreter. Run: pip install -r backend/requirements.txt\n"
        )
        raise

server = _ServerImpl("orion-demo")


@server.tool()
def echo(text: str) -> str:
    """Return the given text verbatim. Use it to test the MCP connection."""
    return f"echo: {text}"


@server.tool()
def add(a: float, b: float) -> float:
    """Add two numbers and return the sum."""
    return a + b


@server.tool()
def fail(reason: str = "no reason given") -> str:
    """This tool always fails. Use it to test Orion's MCP error handling."""
    raise RuntimeError(f"demo failure on purpose: {reason}")


if __name__ == "__main__":
    server.run(transport="stdio")
