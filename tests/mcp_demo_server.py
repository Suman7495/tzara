"""A small MCP server over stdio, for the tests: one read-only tool, one that changes things."""

import sys

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("demo")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def shout(text: str) -> str:
    """Return the text in capitals. This description is long on purpose: """ + "x" * 400
    return text.upper()


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def big() -> str:
    """Return a lot of text."""
    return "word " * 5000


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def fail() -> str:
    """Always fails."""
    raise RuntimeError("it broke")


@server.tool()
def delete_everything(path: str) -> str:
    """Pretend to delete a path."""
    return f"deleted {path}"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hang":
        import time

        time.sleep(4)
    server.run("stdio")
