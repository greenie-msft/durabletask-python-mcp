import asyncio
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response
import uvicorn

from mcp_dts import DurableTasks

# Create a standard MCP server
mcp_server = Server("my-existing-server")

# Add DTS support
durable_mcp_server = DurableTasks(mcp_server, dts_host="localhost:8080")

# Create human-in-the-loop helper
wait_for_approval = durable_mcp_server.create_input_waiter("approval")


# ----- Define task-backed tools -----

@durable_mcp_server.task(
    name="analysis_with_approval",
    description="Run analysis that requires human approval before completing",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to analyze"},
            "steps": {"type": "integer", "description": "Number of steps (default: 5)", "default": 5}
        }
    }
)
def approval_orchestration(ctx, input: dict):
    """
    Orchestration that demonstrates human-in-the-loop.
    """
    num_steps = input.get("steps", 5)
    query = input.get("query", "data")
    
    # Phase 1: Initial analysis
    results = []
    for i in range(num_steps):
        result = yield ctx.call_activity(do_analysis, input={
            "query": query,
            "step": i + 1,
            "total": num_steps
        })
        results.append(result)
    
    # Phase 2: Request human approval
    # Marks the task as "input_required" and durably waits until input arrives
    approval = yield from wait_for_approval(
        ctx,
        message=f"Analysis complete. {num_steps} steps processed. Please approve to continue.",
        data={"results": results},
    )
    
    # Phase 3: Handle approval/rejection
    if approval and approval.get("approved"):
        # Run final processing
        final = yield ctx.call_activity(do_final_processing, input={
            "results": results,
            "approver": approval.get("approver", "unknown")
        })
        return {
            "status": "completed",
            "approved": True,
            "initial_results": results,
            "final_result": final
        }
    else:
        return {
            "status": "rejected",
            "approved": False,
            "reason": approval.get("reason", "No reason provided") if approval else "Approval timeout",
            "initial_results": results
        }


@durable_mcp_server.activity
def do_analysis(ctx, input: dict):
    """Activity that does the actual analysis work."""
    import time
    time.sleep(1)  # Simulate work
    step = input.get("step", 1)
    total = input.get("total", 1)
    return f"Step {step}/{total}: Analyzed '{input.get('query', 'nothing')}'"


@durable_mcp_server.activity
def do_final_processing(ctx, input: dict):
    """Activity that does final processing after approval."""
    import time
    time.sleep(1)  # Simulate work
    results = input.get("results", [])
    approver = input.get("approver", "unknown")
    return f"Final processing complete. {len(results)} results approved by {approver}."


# ----- Run the server -----

async def main():
    # Start the DTS worker (background thread)
    durable_mcp_server.start_worker()
    
    # Set up SSE transport
    sse = SseServerTransport("/messages/")
    
    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp_server.run(streams[0], streams[1], mcp_server.create_initialization_options())
        return Response()
    
    app = Starlette(routes=[
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
    ])
    
    print("Starting existing server with DTS support...")
    print("Dashboard: http://localhost:8082")
    print("MCP: http://localhost:3000/sse")
    await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=3000)).serve()


if __name__ == "__main__":
    asyncio.run(main())
