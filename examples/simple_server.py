"""
Example: Simple MCP Server with DTS-backed tasks.

This demonstrates how to use the mcp_dts library to create an MCP server
where tools are backed by Durable Task SDK orchestrations.

When mcp.run() is called, it starts:
- DTS Worker (background thread) - executes orchestrations and activities
- DTS Client - schedules orchestrations and queries state
- MCP Server (SSE) - exposes tools to AI agents

Usage:
    1. Start DTS emulator: docker run -d -p 8080:8080 -p 8082:8082 cgillum/durabletask-emulator
    2. Run this server: python simple_server.py
    3. Run the client: python client.py
"""

from mcp_dts import DurableTaskServer

# Create server - connects to DTS backend via gRPC
mcp = DurableTaskServer("my-server", dts_host="localhost:8080")


@mcp.durable_task(
    name="process_data",
    description="Process data through multiple steps with progress tracking",
    input_schema={
        "type": "object",
        "properties": {
            "data": {"type": "string", "description": "Data to process"},
            "steps": {"type": "integer", "description": "Number of steps", "default": 5}
        }
    }
)
def process_data_orchestration(ctx, input: dict):
    """
    Orchestration that processes data in steps.
    Each step is an activity that runs durably.
    """
    results = []
    steps = input.get("steps", 5)
    
    for i in range(steps):
        # Call activity - if this crashes mid-way, it resumes from last completed step
        step_result = yield ctx.call_activity(
            process_step,
            input={"step": i + 1, "total": steps, "data": input.get("data", "")}
        )
        results.append(step_result)
    
    return {"success": True, "steps_completed": len(results), "results": results}


@mcp.activity
def process_step(ctx, input: dict):
    """Activity that processes a single step."""
    import time
    time.sleep(1)  # Simulate work
    return {
        "step": input["step"],
        "status": f"Completed step {input['step']}/{input['total']}"
    }


if __name__ == "__main__":
    print("Starting MCP Server with DTS backend...")
    print("Dashboard: http://localhost:8082")
    mcp.run(port=3000)
