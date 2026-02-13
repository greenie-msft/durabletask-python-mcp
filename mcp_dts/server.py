"""
DurableTasks - Add Durable Task SDK support to MCP servers.

This class can be used two ways:
1. Create a new MCP server with DTS support from scratch
2. Add DTS support to an existing MCP server

Components:
- DTS Worker: Executes orchestrations and activities (background thread)
- DTS Client: Schedules orchestrations and queries their state
- DTS TaskStore: Enables MCP Tasks protocol for async execution
"""

import asyncio
import threading
import uuid
from typing import Callable, Any, Union

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


class DurableTasks:
    """
    Add Durable Task SDK support to MCP servers.
    
    Works two ways:
    
    1. NEW SERVER - Pass a name string to create a new MCP server:
    
        durable_mcp = DurableTasks("my-server", dts_host="localhost:8080")
        
        @durable_mcp.task(name="long_task", description="Process data")
        def my_workflow(ctx, input: dict):
            result = yield ctx.call_activity(do_step, input=input)
            return {"done": True}
        
        @durable_mcp.activity
        def do_step(ctx, input: dict):
            return {"completed": True}
        
        durable_mcp.run()  # Starts MCP server + DTS worker
    
    2. EXISTING SERVER - Pass an existing Server instance:
    
        from mcp.server import Server
        
        server = Server("my-server")
        
        # Add your existing tools, resources, prompts...
        @server.list_tools()
        async def list_tools(): ...
        
        # Add DTS support
        durable_mcp = DurableTasks(server, dts_host="localhost:8080")
        
        @durable_mcp.task(name="long_task", description="Process data")
        def my_workflow(ctx, input: dict):
            result = yield ctx.call_activity(do_step, input=input)
            return {"done": True}
        
        @durable_mcp.activity
        def do_step(ctx, input: dict):
            return {"completed": True}
        
        # Start worker, then run your server as usual
        durable_mcp.start_worker()
        # ... your normal server startup code
    
    Args:
        server_or_name: Either a Server instance (existing server) or a string (new server name)
        dts_host: DTS backend address (e.g., "localhost:8080" for emulator)
        taskhub: DTS task hub name (default: "default")
    """
    
    def __init__(
        self, 
        server_or_name: Union[Server, str], 
        dts_host: str = "localhost:8080", 
        taskhub: str = "default"
    ):
        self.dts_host = dts_host
        self.taskhub = taskhub
        
        # Determine if we're wrapping an existing server or creating a new one
        if isinstance(server_or_name, Server):
            self._server = server_or_name
            self._owns_server = False
            self.name = server_or_name.name
        else:
            self._server = Server(server_or_name)
            self._owns_server = True
            self.name = server_or_name
        
        # DTS client and worker
        self._client = client.TaskHubGrpcClient(
            host_address=dts_host,
            metadata=[("taskhub", taskhub)]
        )
        self._worker = worker.TaskHubGrpcWorker(
            host_address=dts_host,
            metadata=[("taskhub", taskhub)]
        )
        
        # Enable MCP Tasks with DTS-backed store
        self._store = DurableTaskStore(self._client)
        self._server.experimental.enable_tasks(store=self._store)
        
        # Registry of task tools and orchestrations
        self._task_tools: list[Tool] = []
        self._orchestrators: dict[str, Callable] = {}  # tool_name -> orchestrator
        
        # Registry of regular (non-task) tools
        self._regular_tools: list[Tool] = []
        self._tool_handlers: dict[str, Callable] = {}  # tool_name -> async handler
        
        # Save any existing handlers before we register ours
        from mcp.types import ListToolsRequest, CallToolRequest
        self._existing_list_tools = self._server.request_handlers.get(ListToolsRequest)
        self._existing_call_tool = self._server.request_handlers.get(CallToolRequest)
        
        # Always register our handlers - they chain to existing ones if present
        self._server.list_tools()(self._list_tools)
        self._server.call_tool()(self._call_tool)
    
    @property
    def server(self) -> Server:
        """The underlying MCP server."""
        return self._server
    
    @property
    def store(self) -> DurableTaskStore:
        """The DTS-backed task store."""
        return self._store
    
    @property
    def dts_client(self) -> client.TaskHubGrpcClient:
        """The DTS client for scheduling orchestrations."""
        return self._client
    
    @property
    def dts_worker(self) -> worker.TaskHubGrpcWorker:
        """The DTS worker for executing orchestrations."""
        return self._worker
    
    def task(
        self,
        name: str,
        description: str = "",
        input_schema: dict = None
    ) -> Callable:
        """
        Decorator to register an orchestration as a task-backed MCP tool.
        
        @dts.task(name="my_task", description="Does something")
        def my_workflow(ctx, input: dict):
            result = yield ctx.call_activity(do_work, input=input)
            return result
        """
        def decorator(func: Callable) -> Callable:
            # Register with DTS worker
            self._worker.add_orchestrator(func)
            
            # Store mapping
            self._orchestrators[name] = func
            
            # Create MCP tool definition
            schema = input_schema or {"type": "object", "properties": {}}
            tool = Tool(
                name=name,
                description=description,
                inputSchema=schema,
                execution=ToolExecution(taskSupport=TASK_REQUIRED),
            )
            self._task_tools.append(tool)
            
            return func
        return decorator
    
    def activity(self, func: Callable) -> Callable:
        """
        Decorator to register an activity function.
        
        @dts.activity
        def do_step(ctx, input: dict):
            return {"result": "done"}
        """
        self._worker.add_activity(func)
        return func
    
    def send_input(self, func: Callable) -> Callable:
        """
        Decorator to register a send_task_input tool.
        
        Automatically creates an MCP tool that sends input to a waiting task,
        polls for completion, and returns the result. The function name becomes
        the tool name.
        
        @dts.send_input
        async def send_task_input(task_id: str, data: dict):
            \"\"\"Send input to a task that is waiting for human input.\"\"\"n            pass
        """
        import asyncio as _asyncio
        import inspect
        
        tool_name = func.__name__
        description = inspect.getdoc(func) or "Send input to a task waiting for human input."
        store = self._store
        
        async def handler(arguments: dict):
            task_id = arguments.get("task_id", "").strip()
            data = arguments.get("data", {})
            if not task_id:
                return CallToolResult(
                    content=[TextContent(type="text", text="Missing required parameter: task_id")],
                    isError=True
                )
            
            # Call the user's function for custom validation/logic
            result = func(task_id, data) if not _asyncio.iscoroutinefunction(func) else await func(task_id, data)
            # If user returns something, use it as a pre-check
            if result is not None and result is not True:
                return CallToolResult(
                    content=[TextContent(type="text", text=str(result))],
                    isError=True
                )
            
            try:
                store.send_input(task_id, data)
            except Exception as e:
                return CallToolResult(
                    content=[TextContent(type="text", text=f"Failed to send input: {e}")],
                    isError=True
                )
            
            # Poll for completion
            for _ in range(60):
                await _asyncio.sleep(0.5)
                task = await store.get_task(task_id)
                if task and task.status == "completed":
                    res = await store.get_result(task_id)
                    text = res.content[0].text if res and res.content else "Done"
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Task completed!\n\nResult: {text}")]
                    )
                if task and task.status == "failed":
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Task failed: {task.statusMessage}")],
                        isError=True
                    )
            return CallToolResult(
                content=[TextContent(type="text", text=f"Input sent to task {task_id}. Still processing.")]
            )
        
        tool = Tool(
            name=tool_name,
            description=description,
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The task ID waiting for input"},
                    "data": {"type": "object", "description": "The input data to send", "additionalProperties": True}
                },
                "required": ["task_id", "data"]
            },
        )
        self._regular_tools.append(tool)
        self._tool_handlers[tool_name] = handler
        
        return func
    
    def create_input_waiter(self, event_name: str = "user_input"):
        """
        Create a helper for human-in-the-loop patterns.
        
        Returns a function to use with ``yield from`` inside an orchestration.
        It marks the task as ``input_required`` in the MCP task store,
        then durably waits for an external event.
        
        Usage in orchestration::
        
            wait_for_approval = dts.create_input_waiter("approval")
            
            # Do some work...
            result = yield ctx.call_activity(do_analysis, input=data)
            
            # Request human approval (single call)
            approval = yield from wait_for_approval(
                ctx,
                message="Please approve the analysis results",
                data={"results": result},
            )
            
            if approval.get("approved"):
                ...
        """
        store = self._store
        
        @self.activity
        def _mark_waiting_for_input(ctx, input: dict):
            """Activity that marks the orchestration as waiting for input."""
            instance_id = ctx.orchestration_id
            message = input.get("message", f"Waiting for {event_name}")
            schema = input.get("schema", {"type": "object"})
            store.mark_waiting_for_input(instance_id, event_name, message, schema)
            return {"status": "waiting", "event_name": event_name}
        
        def _wait_for_input(ctx, message: str = "", data=None, schema=None):
            """Mark the task as waiting and suspend until input arrives.
            
            Use with ``yield from`` inside an orchestration.
            """
            yield ctx.call_activity(_mark_waiting_for_input, input={
                "message": message or f"Waiting for {event_name}",
                "data": data,
                "schema": schema or {"type": "object"},
            })
            result = yield ctx.wait_for_external_event(event_name)
            return result
        
        return _wait_for_input
    
    def get_task_tools(self) -> list[Tool]:
        """
        Get the list of task-backed tools.
        
        For existing servers, include these in your list_tools handler:
        
            @server.list_tools()
            async def list_tools():
                return [my_tool, ...] + dts.get_task_tools()
        """
        return self._task_tools
    
    async def handle_task_tool(self, name: str, arguments: dict) -> CallToolResult | CreateTaskResult:
        """
        Handle a task tool call.
        
        For existing servers, call this from your call_tool handler:
        
            @server.call_tool()
            async def call_tool(name, arguments):
                if name in [t.name for t in dts.get_task_tools()]:
                    return await dts.handle_task_tool(name, arguments)
                # ... handle other tools
        """
        if name not in self._orchestrators:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown task tool: {name}")],
                isError=True
            )
        
        ctx = self._server.request_context
        task_metadata = getattr(ctx.experimental, 'task_metadata', None) if hasattr(ctx, 'experimental') else None
        
        task_id = str(uuid.uuid4())
        self._store.set_pending_orchestration(task_id, self._orchestrators[name], arguments)
        
        try:
            from mcp.types import TaskMetadata
            metadata = task_metadata or TaskMetadata()
            task_obj = await self._store.create_task(metadata=metadata, task_id=task_id)
        except Exception as e:
            import traceback
            return CallToolResult(
                content=[TextContent(type="text", text=f"Failed to create task: {e}\n{traceback.format_exc()}")],
                isError=True
            )
        
        return CreateTaskResult(task=task_obj)
    
    def start_worker(self, blocking: bool = False):
        """
        Start the DTS worker.
        
        Args:
            blocking: If True, blocks forever. If False (default), runs in background thread.
        """
        if blocking:
            print(f"DTS Worker connecting to {self.dts_host}...")
            self._worker.start()
        else:
            threading.Thread(target=self._start_worker_internal, daemon=True).start()
    
    def _start_worker_internal(self):
        print(f"DTS Worker connecting to {self.dts_host}...")
        self._worker.start()
    
    # === Internal handlers that chain to existing ones ===
    
    async def _list_tools(self) -> list[Tool]:
        """List tools handler - chains to existing handler if present."""
        tools = list(self._task_tools) + list(self._regular_tools)
        
        # Chain to existing handler if there was one
        if self._existing_list_tools:
            from mcp.types import ListToolsRequest
            existing_result = await self._existing_list_tools(ListToolsRequest())
            # Result is wrapped in ServerResult with a root ListToolsResult
            if hasattr(existing_result, 'root') and hasattr(existing_result.root, 'tools'):
                tools.extend(existing_result.root.tools)
            elif hasattr(existing_result, 'tools'):
                tools.extend(existing_result.tools)
        
        return tools
    
    async def _call_tool(self, name: str, arguments: dict) -> CallToolResult | CreateTaskResult:
        """Call tool handler - handles task tools, chains to existing for others."""
        # Handle our task tools
        if name in self._orchestrators:
            return await self.handle_task_tool(name, arguments)
        
        # Handle regular tools
        if name in self._tool_handlers:
            result = await self._tool_handlers[name](arguments)
            if isinstance(result, CallToolResult):
                return result
            # Allow returning a list of content dicts for convenience
            if isinstance(result, list):
                return CallToolResult(
                    content=[TextContent(type="text", text=item["text"]) if isinstance(item, dict) else item for item in result]
                )
            return CallToolResult(
                content=[TextContent(type="text", text=str(result))]
            )
        
        # Chain to existing handler for other tools
        if self._existing_call_tool:
            from mcp.types import CallToolRequest
            existing_result = await self._existing_call_tool(CallToolRequest(
                method="tools/call",
                params={"name": name, "arguments": arguments}
            ))
            # Result is wrapped in ServerResult with a root CallToolResult
            if hasattr(existing_result, 'root'):
                return existing_result.root
            return existing_result
        
        # No existing handler and not our tool
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {name}")],
            isError=True
        )
    
    def run(self, host: str = "0.0.0.0", port: int = 3000):
        """
        Run the MCP server with SSE transport (standalone mode only).
        
        This starts both the DTS worker and the MCP server.
        Only available when DurableTasks was created with a name string.
        """
        if not self._owns_server:
            raise RuntimeError(
                "run() is only available for standalone servers. "
                "When using an existing server, call start_worker() and run your server separately."
            )
        asyncio.run(self._run_async(host, port))
    
    async def _run_async(self, host: str, port: int):
        # Start DTS worker in background
        self.start_worker()
        
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


# Backwards compatibility alias
DurableTaskServer = DurableTasks
