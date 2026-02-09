"""
Example: Adding DTS support to an EXISTING MCP server.

This demonstrates how to add Durable Tasks support to an MCP server
that already has other tools/resources defined.

DurableTasks automatically registers handlers that chain to any existing ones,
so you don't need to manually combine tools - just wrap your server!

Usage:
    1. Start DTS emulator: docker run -d -p 8080:8080 -p 8082:8082 cgillum/durabletask-emulator
    2. Run this server: python server.py
    3. Run the client: python client.py
"""

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


# ----- Define task-backed tools -----

@durable_mcp_server.task(
    name="long_running_analysis",
    description="Run a long analysis job",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to analyze"},
            "steps": {"type": "integer", "description": "Number of steps (default: 5)", "default": 5}
        }
    }
)
def analysis_orchestration(ctx, input: dict):
    """Orchestration for long-running analysis."""
    # NOTE: Don't use random.randint() here - DTS orchestrations must be deterministic!
    # Each replay would get a different value, causing infinite loops.
    num_steps = input.get("steps", 5)  # Get from input instead
    results = []
    for i in range(num_steps):
        result = yield ctx.call_activity(do_analysis, input={
            "query": input.get("query", ""),
            "step": i + 1,
            "total": num_steps
        })
        results.append(result)
    return {"analysis_complete": True, "steps": num_steps, "results": results}


@durable_mcp_server.activity
def do_analysis(ctx, input: dict):
    """Activity that does the actual analysis work."""
    import time
    time.sleep(1)  # Simulate work
    step = input.get("step", 1)
    total = input.get("total", 1)
    return f"Step {step}/{total}: Analyzed '{input.get('query', 'nothing')}'"


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
