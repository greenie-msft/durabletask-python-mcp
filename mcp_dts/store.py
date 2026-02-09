"""
DurableTaskStore - MCP TaskStore backed by Durable Task SDK
"""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from durabletask import client
from mcp.shared.experimental.tasks.store import TaskStore
from mcp.types import CallToolResult, Task, TaskStatus, TaskMetadata, TextContent


class DurableTaskStore(TaskStore):
    """
    MCP TaskStore implementation backed by Durable Task SDK.
    
    Orchestrations become tasks:
    - create_task() starts a DTS orchestration
    - get_task() queries DTS orchestration state  
    - get_result() returns DTS orchestration output
    """
    
    def __init__(self, dts_client: client.TaskHubGrpcClient, executor: ThreadPoolExecutor = None):
        self._client = dts_client
        self._executor = executor or ThreadPoolExecutor(max_workers=4)
        self._meta: dict[str, dict] = {}
        self._pending_args: dict[str, tuple] = {}  # task_id -> (orchestrator, args)
    
    def set_pending_orchestration(self, task_id: str, orchestrator, arguments: dict):
        """Store orchestration info to start when create_task is called."""
        self._pending_args[task_id] = (orchestrator, arguments)
    
    async def create_task(self, metadata: TaskMetadata, task_id: str | None = None) -> Task:
        task_id = task_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        
        # Get pending orchestration
        orchestrator, arguments = self._pending_args.pop(task_id, (None, {}))
        
        # Get ttl from metadata or use default
        ttl = getattr(metadata, 'ttl', None) or 60000  # 1 minute default
        
        self._meta[task_id] = {"created_at": now.isoformat(), "ttl": ttl}
        
        if orchestrator:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, lambda: self._client.schedule_new_orchestration(
                orchestrator, input=arguments, instance_id=task_id))
        
        # MCP TaskStatus only supports: working, input_required, completed, failed, cancelled
        # No 'created' status exists, so tasks start as 'working'
        return Task(taskId=task_id, status="working", createdAt=now, lastUpdatedAt=now, ttl=ttl, pollInterval=1000)
    
    async def get_task(self, task_id: str) -> Task | None:
        loop = asyncio.get_event_loop()
        state = await loop.run_in_executor(self._executor,
            lambda: self._client.get_orchestration_state(task_id, fetch_payloads=True))
        
        meta = self._meta.get(task_id, {})
        now = datetime.now(timezone.utc)
        created_at = meta.get("created_at", now.isoformat())
        # Convert string to datetime if needed
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        ttl = meta.get("ttl", 60000)
        
        if state is None:
            return Task(taskId=task_id, status="working", createdAt=created_at,
                       lastUpdatedAt=now, ttl=ttl, pollInterval=1000) if meta else None
        
        status_name = state.runtime_status.name if state.runtime_status else ""
        # Map DTS states to MCP task statuses
        # MCP TaskStatus only supports: working, input_required, completed, failed, cancelled
        if status_name == "COMPLETED":
            status: TaskStatus = "completed"
        elif status_name == "FAILED":
            status: TaskStatus = "failed"
        elif status_name == "TERMINATED":
            status: TaskStatus = "cancelled"
        else:  # PENDING, RUNNING, or other
            status: TaskStatus = "working"
        
        return Task(taskId=task_id, status=status, statusMessage=status_name,
                   createdAt=created_at, lastUpdatedAt=now, ttl=ttl, pollInterval=1000)
    
    async def get_result(self, task_id: str) -> CallToolResult | None:
        loop = asyncio.get_event_loop()
        state = await loop.run_in_executor(self._executor,
            lambda: self._client.get_orchestration_state(task_id, fetch_payloads=True))
        
        if state is None or state.serialized_output is None:
            return None
        return CallToolResult(content=[TextContent(type="text", text=state.serialized_output)])
    
    async def cancel_task(self, task_id: str) -> bool:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(self._executor, lambda: self._client.terminate_orchestration(task_id))
            return True
        except Exception:
            return False
    
    # Required interface methods
    async def list_tasks(self, cursor=None, limit=100): return [], None
    async def delete_task(self, task_id: str) -> bool: return await self.cancel_task(task_id)
    async def update_task(self, task_id: str, **kwargs): return await self.get_task(task_id)
    async def store_result(self, task_id: str, result) -> None: pass
    async def notify_update(self, task_id: str) -> None: pass
    async def wait_for_update(self, task_id: str, timeout=None) -> bool:
        await asyncio.sleep(min(timeout or 1, 1))
        return True
