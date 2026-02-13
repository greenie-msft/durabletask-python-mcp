"""
DurableTaskStore - MCP TaskStore backed by Durable Task SDK
"""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

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
    
    Human-in-the-loop support:
    - mark_waiting_for_input() called by orchestration when waiting for external event
    - send_input() raises external event to resume orchestration
    - get_task() returns input_required status when waiting
    """
    
    def __init__(self, dts_client: client.TaskHubGrpcClient, executor: ThreadPoolExecutor = None):
        self._client = dts_client
        self._executor = executor or ThreadPoolExecutor(max_workers=4)
        self._meta: dict[str, dict] = {}
        self._pending_args: dict[str, tuple] = {}  # task_id -> (orchestrator, args)
        self._waiting_for_input: dict[str, dict] = {}  # task_id -> {event_name, message, schema}
    
    def set_pending_orchestration(self, task_id: str, orchestrator, arguments: dict):
        """Store orchestration info to start when create_task is called."""
        self._pending_args[task_id] = (orchestrator, arguments)
    
    def mark_waiting_for_input(self, task_id: str, event_name: str, message: str = "", schema: dict = None):
        """
        Mark a task as waiting for external input.
        
        Called from orchestration before yielding wait_for_external_event().
        This causes get_task() to return status='input_required'.
        
        Args:
            task_id: The task/orchestration instance ID
            event_name: The DTS external event name to wait for
            message: Human-readable message explaining what input is needed
            schema: Optional JSON schema describing expected input format
        """
        self._waiting_for_input[task_id] = {
            "event_name": event_name,
            "message": message,
            "schema": schema or {"type": "object"}
        }
    
    def clear_waiting_for_input(self, task_id: str):
        """Clear the waiting-for-input state after input is received."""
        self._waiting_for_input.pop(task_id, None)
    
    def get_waiting_info(self, task_id: str) -> dict | None:
        """Get info about what input a task is waiting for, if any."""
        return self._waiting_for_input.get(task_id)
    
    def send_input(self, task_id: str, data: Any) -> bool:
        """
        Send input to a waiting task by raising a DTS external event.
        
        Uses the convention that human-in-the-loop tasks wait for "approval" event.
        This works even after server restart since DTS tracks the orchestration state.
        """
        # Always try to raise the event - DTS will handle it if the orchestration is waiting
        # Use "approval" as the convention for human-in-the-loop event name
        event_name = self._waiting_for_input.get(task_id, {}).get("event_name", "approval")
        self._client.raise_orchestration_event(task_id, event_name, data=data)
        self.clear_waiting_for_input(task_id)
        return True
    
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
        
        # Check if task is waiting for input (human-in-the-loop)
        waiting_info = self._waiting_for_input.get(task_id)
        if waiting_info and status_name in ("RUNNING", "PENDING"):
            # Task is waiting for external input
            status_message = waiting_info.get("message", "Waiting for input")
            return Task(taskId=task_id, status="input_required", statusMessage=status_message,
                       createdAt=created_at, lastUpdatedAt=now, ttl=ttl, pollInterval=1000)
        
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
    async def list_tasks(self, cursor=None, limit=100):
        """List tasks by querying DTS orchestration instances."""
        import durabletask.internal.orchestrator_service_pb2 as pb
        from google.protobuf import wrappers_pb2

        def _query():
            req = pb.QueryInstancesRequest(
                query=pb.InstanceQuery(
                    maxInstanceCount=limit or 100,
                    fetchInputsAndOutputs=False,
                )
            )
            if cursor:
                req.query.continuationToken.CopyFrom(
                    wrappers_pb2.StringValue(value=cursor)
                )
            return self._client._stub.QueryInstances(req)

        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(self._executor, _query)
        except Exception:
            return [], None

        now = datetime.now(timezone.utc)
        tasks = []
        for s in resp.orchestrationState:
            instance_id = s.instanceId
            status_name = pb.OrchestrationStatus.Name(s.orchestrationStatus)

            # Check human-in-the-loop state
            waiting_info = self._waiting_for_input.get(instance_id)
            if waiting_info and status_name in ("ORCHESTRATION_STATUS_RUNNING",
                                                 "ORCHESTRATION_STATUS_PENDING"):
                status: TaskStatus = "input_required"
                status_message = waiting_info.get("message", "Waiting for input")
            elif status_name == "ORCHESTRATION_STATUS_COMPLETED":
                status = "completed"
                status_message = "Completed"
            elif status_name == "ORCHESTRATION_STATUS_FAILED":
                status = "failed"
                status_message = "Failed"
            elif status_name == "ORCHESTRATION_STATUS_TERMINATED":
                status = "cancelled"
                status_message = "Terminated"
            else:
                status = "working"
                status_message = status_name

            created_at = s.createdTimestamp.ToDatetime().replace(tzinfo=timezone.utc) \
                if s.HasField("createdTimestamp") else now
            updated_at = s.lastUpdatedTimestamp.ToDatetime().replace(tzinfo=timezone.utc) \
                if s.HasField("lastUpdatedTimestamp") else now

            meta = self._meta.get(instance_id, {})
            ttl = meta.get("ttl", 60000)

            tasks.append(Task(
                taskId=instance_id,
                status=status,
                statusMessage=status_message,
                createdAt=created_at,
                lastUpdatedAt=updated_at,
                ttl=ttl,
                pollInterval=1000,
            ))

        next_cursor = None
        if resp.HasField("continuationToken"):
            next_cursor = resp.continuationToken.value

        return tasks, next_cursor
    async def delete_task(self, task_id: str) -> bool: return await self.cancel_task(task_id)
    async def update_task(self, task_id: str, **kwargs): return await self.get_task(task_id)
    async def store_result(self, task_id: str, result) -> None: pass
    async def notify_update(self, task_id: str) -> None: pass
    async def wait_for_update(self, task_id: str, timeout=None) -> bool:
        await asyncio.sleep(min(timeout or 1, 1))
        return True
