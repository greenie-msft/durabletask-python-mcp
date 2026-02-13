"""
MCP Tasks Client using Official SDK Pattern

Uses the experimental MCP Tasks API:
- session.experimental.call_tool_as_task()
- session.experimental.poll_task()
- session.experimental.get_task_result()

Reference: https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/experimental/tasks-client.md

Usage:
    python client.py              # Run basic analysis (no approval needed)
    python client.py --approval   # Run analysis that requires human approval

Human-in-the-loop demo (--approval):
    1. Task will reach "input_required" status
    2. Kill the server while waiting
    3. Client will timeout/fail polling
    4. Restart server and run: python client.py --approve <task_id>
    5. Task resumes and completes!
"""

import asyncio
import sys
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import CallToolResult

MCP_URL = "http://localhost:3000/sse"


async def main():
    # Check for --approve mode (send approval to existing task)
    if len(sys.argv) >= 3 and sys.argv[1] == "--approve":
        task_id = sys.argv[2]
        await send_approval(task_id)
        return
    
    # Check for --approval mode (run approval workflow)
    use_approval = "--approval" in sys.argv
    
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
            
            if use_approval:
                await run_approval_workflow(session)
            else:
                await run_basic_workflow(session)


async def run_basic_workflow(session: ClientSession):
    """Run the basic long_running_analysis tool."""
    tools = await session.list_tools()
    task_tool = next((t for t in tools.tools if t.name == "long_running_analysis"), None)
    if not task_tool:
        print("long_running_analysis tool not found!")
        return
    
    print(f"🚀 Calling long_running_analysis as task...")
    result = await session.experimental.call_tool_as_task(
        "long_running_analysis",
        {"query": "Process important data", "steps": 5},
        ttl=60000,
    )
    
    task_id = result.task.taskId
    print(f"   Task ID: {task_id}")
    print(f"   Initial status: {result.task.status}")
    print()
    
    await poll_and_complete(session, task_id)


async def run_approval_workflow(session: ClientSession):
    """Run the analysis_with_approval tool that requires human approval."""
    tools = await session.list_tools()
    task_tool = next((t for t in tools.tools if t.name == "analysis_with_approval"), None)
    if not task_tool:
        print("analysis_with_approval tool not found!")
        return
    
    print(f"🚀 Calling analysis_with_approval as task...")
    print("   (This task will pause for human approval)")
    print()
    result = await session.experimental.call_tool_as_task(
        "analysis_with_approval",
        {"query": "Important analysis", "steps": 3},
        ttl=300000,  # 5 minute TTL for approval workflow
    )
    
    task_id = result.task.taskId
    print(f"   Task ID: {task_id}")
    print(f"   Initial status: {result.task.status}")
    print()
    print(f"   💡 Save this task ID! If you kill the server, you can resume with:")
    print(f"      python client.py --approve {task_id}")
    print()
    
    await poll_and_complete(session, task_id, handle_input_required=True)


async def poll_and_complete(session: ClientSession, task_id: str, handle_input_required: bool = False):
    """Poll a task until completion, optionally handling input_required status."""
    print("🔄 Polling for status...")
    
    async for status in session.experimental.poll_task(task_id):
        msg = f"   {status.status}"
        if status.statusMessage:
            msg += f" - {status.statusMessage}"
        print(msg)
        
        # Handle input_required status
        if status.status == "input_required" and handle_input_required:
            print()
            print("⏸️  Task is waiting for approval!")
            print(f"   Status message: {status.statusMessage}")
            print()
            
            # Ask for approval
            response = input("   Approve? (y/n/skip): ").strip().lower()
            
            if response == "skip":
                print("   Skipping approval - task will remain waiting")
                print(f"   To approve later: python client.py --approve {task_id}")
                return
            
            approved = response in ("y", "yes")
            
            # Send approval using send_task_input tool
            print(f"   Sending {'approval' if approved else 'rejection'}...")
            approval_result = await session.call_tool(
                "send_task_input",
                {
                    "task_id": task_id,
                    "data": {
                        "approved": approved,
                        "approver": "demo_user",
                        "reason": "Approved via CLI" if approved else "Rejected via CLI"
                    }
                }
            )
            print(f"   {approval_result.content[0].text if approval_result.content else 'Input sent'}")
            print()
            continue
        
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


async def send_approval(task_id: str):
    """Send approval to a waiting task (for resuming after server restart)."""
    print(f"Connecting to {MCP_URL}...")
    
    async with sse_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✅ Connected\n")
            
            # Check task status first
            print(f"📋 Checking task {task_id}...")
            status = await session.experimental.get_task(task_id)
            print(f"   Status: {status.status}")
            if status.statusMessage:
                print(f"   Message: {status.statusMessage}")
            print()
            
            if status.status != "input_required":
                print(f"⚠️  Task is not waiting for input (status: {status.status})")
                if status.status == "completed":
                    final = await session.experimental.get_task_result(task_id, CallToolResult)
                    print(f"   Result: {final.content[0].text if final and final.content else 'None'}")
                return
            
            # Send approval
            print("✅ Sending approval...")
            result = await session.call_tool(
                "send_task_input",
                {
                    "task_id": task_id,
                    "data": {
                        "approved": True,
                        "approver": "demo_user_resumed",
                        "reason": "Approved after server restart"
                    }
                }
            )
            print(f"   {result.content[0].text if result.content else 'Input sent'}")
            print()
            
            # Poll for completion
            print("🔄 Polling for completion...")
            async for poll_status in session.experimental.poll_task(task_id):
                msg = f"   {poll_status.status}"
                if poll_status.statusMessage:
                    msg += f" - {poll_status.statusMessage}"
                print(msg)
                
                if poll_status.status == "completed":
                    print("\n✅ Task completed!")
                    final = await session.experimental.get_task_result(task_id, CallToolResult)
                    print(f"Result: {final.content[0].text if final and final.content else 'None'}")
                    break
                
                if poll_status.status in ("failed", "cancelled"):
                    print(f"\n❌ Task ended: {poll_status.status}")
                    break


if __name__ == "__main__":
    asyncio.run(main())
