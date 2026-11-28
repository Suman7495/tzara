"""Connecting Tzara to other apps through the Model Context Protocol (MCP).

An MCP server is a small program that exposes what an app can do (read a calendar, search a
folder, create a ticket) as tools. Tzara starts the servers listed in ``mcp.json``, asks each for
its tools, and gives them to the model beside its own.
"""

from tzara.mcp.config import McpConfigFile, McpServerConfig
from tzara.mcp.hub import McpHub, ServerStatus
from tzara.mcp.presets import McpPreset, McpPresets
from tzara.mcp.tools import McpTool, McpTools

__all__ = [
    "McpConfigFile",
    "McpHub",
    "McpPreset",
    "McpPresets",
    "McpServerConfig",
    "McpTool",
    "McpTools",
    "ServerStatus",
]
