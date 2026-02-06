"""
MCP Tasks Client using Official SDK Pattern

Uses the experimental MCP Tasks API:
- session.experimental.call_tool_as_task()
- session.experimental.poll_task()
- session.experimental.get_task_result()

Reference: https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/experimental/tasks-client.md
"""

import asyncio
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import CallToolResult

MCP_URL = "http://localhost:3000/sse"


async def main():
    print(f"Connecting to {MCP_URL}...")
    
    async with sse_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✅ Connected\n")
            
            # List tools
            tools = await session.list_tools()
            print(f"Tools: {[t.name for t in tools.tools]}")
            for tool in tools.tools:
                if hasattr(tool, 'execution') and tool.execution:
                    print(f"  - {tool.name}: taskSupport={tool.execution.taskSupport}")
            print()
            
            # Call tool as task using the official SDK pattern
            print("🚀 Calling long_running_task as task...")
            result = await session.experimental.call_tool_as_task(
                "long_running_task",
                {"description": "Process important data", "steps": 5},
                ttl=60000,  # 60 second TTL
            )
            
            task_id = result.task.taskId
            print(f"   Task ID: {task_id}")
            print(f"   Initial status: {result.task.status}")
            print(f"   Poll interval: {result.task.pollInterval}ms")
            print()
            
            # Poll using the official SDK poll_task iterator
            print("🔄 Polling for status...")
            async for status in session.experimental.poll_task(task_id):
                msg = f"   {status.status}"
                if status.statusMessage:
                    msg += f" - {status.statusMessage}"
                print(msg)
                
                # Handle terminal states
                if status.status == "completed":
                    print("\n✅ Task completed!")
                    final = await session.experimental.get_task_result(task_id, CallToolResult)
                    print(f"Result: {final.content[0].text if final and final.content else 'None'}")
                    break
                
                if status.status == "failed":
                    print(f"\n❌ Task failed: {status.statusMessage}")
                    break
                
                if status.status == "cancelled":
                    print("\n⚠️ Task was cancelled")
                    break


if __name__ == "__main__":
    asyncio.run(main())
