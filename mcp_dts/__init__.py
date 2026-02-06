"""
mcp_dts - MCP Server Library with Durable Task SDK Backend

This library provides a simple way to create MCP servers where tools are
backed by Durable Task SDK orchestrations for reliable, long-running tasks.

What it does:
- Starts a DTS Worker to execute orchestrations and activities
- Uses a DTS Client to schedule and monitor orchestrations  
- Runs an MCP Server (SSE transport) that exposes tools to AI agents
- Implements MCP Tasks protocol for async tool execution with polling

Architecture:
    AI Agent <--MCP--> DurableTaskServer <--gRPC--> DTS Backend
                       (MCP Server)         (Worker + Client)

Usage:
    from mcp_dts import DurableTaskServer

    mcp = DurableTaskServer("my-server", dts_host="localhost:8080")

    @mcp.durable_task(name="long_running_task", description="Process data")
    def my_orchestration(ctx, input: dict):
        result = yield ctx.call_activity(do_step, input={"data": input})
        return {"success": True, "result": result}

    @mcp.activity
    def do_step(ctx, input: dict):
        return {"completed": True}

    mcp.run()  # Starts both DTS worker and MCP server
"""

from .server import DurableTaskServer
from .store import DurableTaskStore

__all__ = ["DurableTaskServer", "DurableTaskStore"]
