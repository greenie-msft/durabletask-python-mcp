"""
mcp_dts - Add Durable Task SDK support to MCP servers

This library provides a simple way to add reliable, long-running task support
to MCP servers using the Durable Task SDK as the orchestration backend.

Works two ways:

1. CREATE A NEW SERVER - Pass a name string:

    from mcp_dts import DurableTasks

    durable_mcp = DurableTasks("my-server", dts_host="localhost:8080")

    @durable_mcp.task(name="long_task", description="Process data")
    def my_workflow(ctx, input: dict):
        result = yield ctx.call_activity(do_step, input=input)
        return {"success": True, "result": result}

    @durable_mcp.activity
    def do_step(ctx, input: dict):
        return {"completed": True}

    durable_mcp.run()  # Starts MCP server + DTS worker


2. ADD TO EXISTING SERVER - Pass a Server instance:

    from mcp.server import Server
    from mcp_dts import DurableTasks

    server = Server("my-server")
    durable_mcp = DurableTasks(server, dts_host="localhost:8080")
    
    @server.list_tools()
    async def list_tools():
        return [...] + durable_mcp.get_task_tools()
    
    @server.call_tool()
    async def call_tool(name, arguments):
        if name in [t.name for t in durable_mcp.get_task_tools()]:
            return await durable_mcp.handle_task_tool(name, arguments)
        # handle other tools...
    
    @durable_mcp.task(name="long_task", description="Process data")
    def my_workflow(ctx, input: dict):
        result = yield ctx.call_activity(do_step, input=input)
        return {"success": True}

    @durable_mcp.activity
    def do_step(ctx, input: dict):
        return {"completed": True}

    durable_mcp.start_worker()  # Start worker, run server separately


Components:
- DTS Worker: Executes orchestrations and activities (background thread)
- DTS Client: Schedules orchestrations and queries their state
- DTS TaskStore: Enables MCP Tasks protocol for async execution

Architecture:
    AI Agent <--MCP--> DurableTasks <--gRPC--> DTS Backend
                       (MCP Server)    (Worker + Client)
"""

from .server import DurableTasks, DurableTaskServer
from .store import DurableTaskStore

__all__ = ["DurableTasks", "DurableTaskServer", "DurableTaskStore"]
