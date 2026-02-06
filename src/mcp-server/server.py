"""
MCP Tasks Server with Durable Task SDK

This server demonstrates long-running tasks using the MCP Tasks protocol
backed by Durable Task SDK for durable orchestration.
"""

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Generator

from durabletask import task, client, worker
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.shared.experimental.tasks.store import TaskStore
from mcp.types import (
    CallToolResult, CreateTaskResult, TextContent, Tool, ToolExecution,
    Task, TaskStatus, TaskMetadata, TASK_REQUIRED,
)
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response
import uvicorn

# Configuration
DTS_HOST = "localhost:8080"
TASKHUB = "default"
executor = ThreadPoolExecutor(max_workers=4)

# Global state
_client: client.TaskHubGrpcClient | None = None
_worker: worker.TaskHubGrpcWorker | None = None
_task_metadata: dict[str, dict] = {}  # Store task metadata
_task_arguments: dict[str, dict] = {}  # Store tool arguments for tasks


# =============================================================================
# DURABLE TASK ORCHESTRATION
# =============================================================================

def long_running_orchestration(ctx: task.OrchestrationContext, input: dict) -> Generator:
    """Orchestration that processes data in steps."""
    steps = input.get("steps", 5)
    
    results = []
    for i in range(1, steps + 1):
        result = yield ctx.call_activity(do_step, input={"step": i, "total": steps})
        results.append(result)
    
    return {"success": True, "results": results}


def do_step(ctx: task.ActivityContext, input: dict) -> dict:
    """Activity that performs one step of work."""
    time.sleep(1)  # Simulate work
    return {"step": input["step"], "completed_at": datetime.now(timezone.utc).isoformat()}


# =============================================================================
# DURABLE TASK CLIENT
# =============================================================================

def get_client() -> client.TaskHubGrpcClient:
    global _client
    if _client is None:
        _client = client.TaskHubGrpcClient(host_address=DTS_HOST, metadata=[("taskhub", TASKHUB)])
    return _client


def start_worker():
    global _worker
    _worker = worker.TaskHubGrpcWorker(host_address=DTS_HOST, metadata=[("taskhub", TASKHUB)])
    _worker.add_orchestrator(long_running_orchestration)
    _worker.add_activity(do_step)
    print(f"Worker connecting to {DTS_HOST}...")
    _worker.start()


# =============================================================================
# MCP TASK STORE (backed by Durable Task SDK)
# =============================================================================

class DurableTaskStore(TaskStore):
    """TaskStore that uses DTS orchestrations as tasks."""
    
    async def create_task(self, metadata: TaskMetadata, task_id: str | None = None) -> Task:
        """Start a DTS orchestration as the task."""
        task_id = task_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        arguments = _task_arguments.pop(task_id, {})
        
        _task_metadata[task_id] = {"created_at": now}
        
        # Start the orchestration
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(executor, lambda: get_client().schedule_new_orchestration(
            long_running_orchestration, input=arguments, instance_id=task_id))
        
        return Task(taskId=task_id, status="working", createdAt=now, lastUpdatedAt=now, pollInterval=1000)
    
    async def get_task(self, task_id: str) -> Task | None:
        """Get task status from DTS orchestration state."""
        loop = asyncio.get_event_loop()
        state = await loop.run_in_executor(executor, 
            lambda: get_client().get_orchestration_state(task_id, fetch_payloads=True))
        
        meta = _task_metadata.get(task_id, {})
        now = datetime.now(timezone.utc).isoformat()
        
        if state is None:
            return Task(taskId=task_id, status="working", createdAt=meta.get("created_at", now), 
                       lastUpdatedAt=now, pollInterval=1000) if meta else None
        
        # Map DTS status to MCP Task status
        status_name = state.runtime_status.name if state.runtime_status else ""
        status: TaskStatus = "completed" if status_name == "COMPLETED" else \
                            "failed" if status_name == "FAILED" else "working"
        
        return Task(taskId=task_id, status=status, statusMessage=status_name,
                   createdAt=meta.get("created_at", now), lastUpdatedAt=now, pollInterval=1000)
    
    async def get_result(self, task_id: str) -> CallToolResult | None:
        """Get DTS orchestration result."""
        loop = asyncio.get_event_loop()
        state = await loop.run_in_executor(executor,
            lambda: get_client().get_orchestration_state(task_id, fetch_payloads=True))
        
        if state is None or state.serialized_output is None:
            return None
        return CallToolResult(content=[TextContent(type="text", text=state.serialized_output)])
    
    async def cancel_task(self, task_id: str) -> bool:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(executor, lambda: get_client().terminate_orchestration(task_id))
            return True
        except Exception:
            return False
    
    # Required interface methods (minimal implementations)
    async def list_tasks(self, cursor=None, limit=100): return [], None
    async def delete_task(self, task_id: str) -> bool: return await self.cancel_task(task_id)
    async def update_task(self, task_id: str, **kwargs): return await self.get_task(task_id)
    async def store_result(self, task_id: str, result) -> None: pass
    async def notify_update(self, task_id: str) -> None: pass
    async def wait_for_update(self, task_id: str, timeout=None) -> bool: 
        await asyncio.sleep(min(timeout or 1, 1))
        return True


# =============================================================================
# MCP SERVER
# =============================================================================

mcp = Server("mcp-dts")
task_store = DurableTaskStore()
mcp.experimental.enable_tasks(store=task_store)


@mcp.list_tools()
async def list_tools():
    return [
        Tool(
            name="long_running_task",
            description="A long-running task that processes data in steps.",
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "steps": {"type": "integer", "default": 5},
                },
                "required": ["description"],
            },
            execution=ToolExecution(taskSupport=TASK_REQUIRED),
        ),
    ]


@mcp.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult | CreateTaskResult:
    """Handle tool calls. For task-required tools, start a DTS orchestration."""
    if name == "long_running_task":
        ctx = mcp.request_context
        ctx.experimental.validate_task_mode(TASK_REQUIRED)
        
        # Store arguments and create task (which starts the DTS orchestration)
        task_id = str(uuid.uuid4())
        _task_arguments[task_id] = arguments
        task = await task_store.create_task(metadata=ctx.experimental.task_metadata, task_id=task_id)
        
        return CreateTaskResult(task=task)
    
    return CallToolResult(content=[TextContent(type="text", text=f"Unknown tool: {name}")], isError=True)


# =============================================================================
# MAIN
# =============================================================================

async def main():
    import threading
    threading.Thread(target=start_worker, daemon=True).start()
    
    sse = SseServerTransport("/messages/")
    
    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp.run(streams[0], streams[1], mcp.create_initialization_options())
        return Response()
    
    app = Starlette(routes=[
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
    ])
    
    print(f"MCP Tasks Server - DTS: {DTS_HOST}, MCP: http://localhost:3000/sse")
    await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=3000)).serve()


if __name__ == "__main__":
    asyncio.run(main())
