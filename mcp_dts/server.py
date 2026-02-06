"""
DurableTaskServer - MCP Server with built-in Durable Task SDK support.

This class combines three components:
1. DTS Worker - Executes orchestrations and activities (background thread)
2. DTS Client - Schedules orchestrations and queries their state
3. MCP Server - Exposes tools to AI agents via SSE transport

When you call mcp.run():
- A DTS Worker thread starts and connects to the DTS backend
- An MCP Server (uvicorn) starts and listens for agent connections
- Tools decorated with @mcp.durable_task become MCP task-backed tools

When an agent calls a tool:
- The DTS Client schedules a new orchestration instance
- MCP Tasks protocol returns a task ID for polling
- The DTS Worker executes the orchestration asynchronously
- Agent polls until completion, then retrieves the result
"""

import asyncio
import threading
import uuid
from typing import Callable, Any

from durabletask import client, worker, task
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import (
    CallToolResult, CreateTaskResult, TextContent, Tool, ToolExecution, TASK_REQUIRED
)
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response
import uvicorn

from .store import DurableTaskStore


class DurableTaskServer:
    """
    MCP Server with Durable Task SDK integration.
    
    Components started by run():
    - DTS Worker: Connects to dts_host, executes orchestrations/activities
    - DTS Client: Schedules orchestrations, queries state (used by TaskStore)
    - MCP Server: SSE transport on specified port, exposes tools to agents
    
    Args:
        name: Server name (shown to MCP clients)
        dts_host: DTS backend address (e.g., "localhost:8080" for emulator)
        taskhub: DTS task hub name (default: "default")
    
    Example:
        mcp = DurableTaskServer("my-server", dts_host="localhost:8080")
        
        @mcp.durable_task(name="process_data", description="Process data")
        def my_orchestration(ctx, input: dict):
            result = yield ctx.call_activity(do_step, input=input)
            return {"success": True, "result": result}
        
        @mcp.activity
        def do_step(ctx, input: dict):
            return {"completed": True}
        
        mcp.run(port=3000)  # Starts DTS worker + MCP server
    """
    
    def __init__(self, name: str, dts_host: str = "localhost:8080", taskhub: str = "default"):
        self.name = name
        self.dts_host = dts_host
        self.taskhub = taskhub
        
        # DTS client and worker
        self._client = client.TaskHubGrpcClient(
            host_address=dts_host,
            metadata=[("taskhub", taskhub)]
        )
        self._worker = worker.TaskHubGrpcWorker(
            host_address=dts_host,
            metadata=[("taskhub", taskhub)]
        )
        
        # MCP server with DTS-backed task store
        self._server = Server(name)
        self._store = DurableTaskStore(self._client)
        self._server.experimental.enable_tasks(store=self._store)
        
        # Registry of tools and orchestrations
        self._tools: list[Tool] = []
        self._orchestrators: dict[str, Callable] = {}  # tool_name -> orchestrator
        
        # Register MCP handlers
        self._server.list_tools()(self._list_tools)
        self._server.call_tool()(self._call_tool)
    
    def durable_task(
        self,
        name: str,
        description: str = "",
        input_schema: dict = None
    ) -> Callable:
        """
        Decorator to register an orchestration as a task-backed MCP tool.
        
        @mcp.durable_task(name="my_task", description="Does something")
        def my_orchestration(ctx, input: dict):
            result = yield ctx.call_activity(do_work, input=input)
            return result
        """
        def decorator(func: Callable) -> Callable:
            # Register with DTS worker
            self._worker.add_orchestrator(func)
            
            # Store mapping
            self._orchestrators[name] = func
            
            # Create MCP tool
            schema = input_schema or {
                "type": "object",
                "properties": {},
            }
            
            self._tools.append(Tool(
                name=name,
                description=description,
                inputSchema=schema,
                execution=ToolExecution(taskSupport=TASK_REQUIRED),
            ))
            
            return func
        return decorator
    
    def activity(self, func: Callable) -> Callable:
        """
        Decorator to register an activity function.
        
        @mcp.activity
        def do_step(ctx, input: dict):
            return {"result": "done"}
        """
        self._worker.add_activity(func)
        return func
    
    async def _list_tools(self) -> list[Tool]:
        return self._tools
    
    async def _call_tool(self, name: str, arguments: dict) -> CallToolResult | CreateTaskResult:
        if name not in self._orchestrators:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True
            )
        
        ctx = self._server.request_context
        
        # Get task metadata (may be None if client doesn't support tasks)
        task_metadata = getattr(ctx.experimental, 'task_metadata', None) if hasattr(ctx, 'experimental') else None
        
        # Generate task ID and store orchestration info
        task_id = str(uuid.uuid4())
        self._store.set_pending_orchestration(task_id, self._orchestrators[name], arguments)
        
        # Create task (which starts the orchestration)
        try:
            from mcp.types import TaskMetadata
            metadata = task_metadata or TaskMetadata()
            task = await self._store.create_task(
                metadata=metadata,
                task_id=task_id
            )
        except Exception as e:
            import traceback
            return CallToolResult(
                content=[TextContent(type="text", text=f"Failed to create task: {e}\n{traceback.format_exc()}")],
                isError=True
            )
        
        return CreateTaskResult(task=task)
    
    def _start_worker(self):
        """Start the DTS worker in a background thread."""
        print(f"DTS Worker connecting to {self.dts_host}...")
        self._worker.start()
    
    def run(self, host: str = "0.0.0.0", port: int = 3000):
        """Run the MCP server with SSE transport."""
        asyncio.run(self._run_async(host, port))
    
    async def _run_async(self, host: str, port: int):
        # Start DTS worker
        threading.Thread(target=self._start_worker, daemon=True).start()
        
        # SSE transport
        sse = SseServerTransport("/messages/")
        
        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                await self._server.run(streams[0], streams[1], self._server.create_initialization_options())
            return Response()
        
        app = Starlette(routes=[
            Route("/sse", handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ])
        
        print(f"MCP Server '{self.name}' - DTS: {self.dts_host}, MCP: http://{host}:{port}/sse")
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port)).serve()
