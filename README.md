# MCP Tasks with Durable Task SDK

A library and demo for building **durable, long-running MCP tools** backed by **Durable Task Scheduler**.

## ✨ Quick Example

```python
from mcp_dts import DurableTaskServer

mcp = DurableTaskServer("my-server", dts_host="localhost:8080")

@mcp.durable_task(name="process_data", description="Process data in steps")
def my_orchestration(ctx, input: dict):
    for i in range(input.get("steps", 5)):
        yield ctx.call_activity(do_step, input={"step": i})
    return {"success": True}

@mcp.activity
def do_step(ctx, input: dict):
    import time; time.sleep(1)
    return {"completed": input["step"]}

mcp.run()  # That's it!
```

## 🎯 What This Does

This project provides:
1. **`mcp_dts` library** - Simple decorators to add durable tasks to any MCP server
2. **Working demo** - Complete server and client examples

The [MCP Tasks protocol](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/experimental/tasks.md) enables async tool execution with polling:

```
Client: call_tool_as_task("long_running_task", {...})
    ↓
Server: CreateTaskResult(task_id="abc-123")
    ↓
Client: poll_task(task_id)  → status: "working"
Client: poll_task(task_id)  → status: "working"
Client: poll_task(task_id)  → status: "completed"
    ↓
Client: get_task_result(task_id)
    ↓
Result: {"success": true, "results": [...]}
```

## 🏗️ Architecture

```
┌─────────────────┐     MCP Protocol      ┌─────────────────────────────┐
│   MCP Client    │ ────────────────────▶ │                             │
│   (client.py)   │ ◀──────────────────── │        server.py            │
└─────────────────┘                       │                             │
                                          │   • MCP SDK + Tasks         │
                                          │   • Custom TaskStore        │
                                          │   • Durable Task SDK        │
                                          └──────────────┬──────────────┘
                                                         │ gRPC
                                                         ▼
                                          ┌─────────────────────────────┐
                                          │   DTS Emulator (Docker)     │
                                          │   Persists workflow state   │
                                          └─────────────────────────────┘
```

### How It Works

The `DurableTaskStore` implements the MCP SDK's `TaskStore` interface, bridging MCP Tasks to Durable Task orchestrations:

| MCP Operation | DurableTaskStore Implementation |
|---------------|--------------------------------|
| `create_task(metadata)` | Starts a DTS orchestration |
| `get_task(task_id)` | Queries DTS orchestration state |
| `get_result(task_id)` | Returns DTS orchestration output |
| `cancel_task(task_id)` | Terminates the DTS orchestration |

## 📁 Files

```
src/
├── mcp_dts/                # The library
│   ├── __init__.py         # Exports DurableTaskServer, DurableTaskStore
│   ├── server.py           # DurableTaskServer with decorators
│   ├── store.py            # DurableTaskStore (TaskStore implementation)
│   └── requirements.txt
├── examples/
│   └── simple_server.py    # Simple example using the library
├── mcp-server/
│   ├── server.py           # Full demo server (manual implementation)
│   └── requirements.txt
└── client/
    ├── client.py           # MCP Tasks client demo
    └── requirements.txt
```

## 🚀 Quick Start

### 1. Start DTS Emulator

```bash
docker run -d -p 8080:8080 -p 8082:8082 \
  --name dts-emulator \
  mcr.microsoft.com/dts/dts-emulator:latest
```

Dashboard: http://localhost:8082

### 2. Install Dependencies

```bash
cd src/mcp-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Start the Server

```bash
python server.py
```

Output:
```
Worker connecting to localhost:8080...
MCP Tasks Server - DTS: localhost:8080, MCP: http://localhost:3000/sse
```

### 4. Run the Client

```bash
cd src/client
python client.py
```

Output:
```
Connecting to http://localhost:3000/sse...
✅ Connected

Tools: ['long_running_task']
  - long_running_task: taskSupport=required

🚀 Calling long_running_task as task...
   Task ID: abc-123
   Initial status: working
   Poll interval: 1000ms

🔄 Polling for status...
   working - RUNNING
   working - RUNNING
   working - RUNNING
   completed - COMPLETED

✅ Task completed!
Result: {"success": true, "description": "...", "results": [...]}
```

## 🔧 MCP Tasks SDK Usage

### Server Side

```python
from mcp.server import Server
from mcp.types import Tool, ToolExecution, TASK_REQUIRED, CreateTaskResult

# Enable tasks with custom store
mcp = Server("my-server")
mcp.experimental.enable_tasks(store=my_task_store)

@mcp.list_tools()
async def list_tools():
    return [
        Tool(
            name="long_running_task",
            inputSchema={...},
            execution=ToolExecution(taskSupport=TASK_REQUIRED),  # <-- Required for tasks
        ),
    ]

@mcp.call_tool()
async def call_tool(name: str, arguments: dict):
    ctx = mcp.request_context
    ctx.experimental.validate_task_mode(TASK_REQUIRED)
    
    # Create task in store (starts the orchestration)
    task = await task_store.create_task(
        metadata=ctx.experimental.task_metadata,
        task_id=str(uuid.uuid4()),
    )
    
    return CreateTaskResult(task=task)
```

### Client Side

```python
from mcp.client.session import ClientSession
from mcp.types import CallToolResult

async with ClientSession(read, write) as session:
    await session.initialize()
    
    # Call tool as task (returns immediately)
    result = await session.experimental.call_tool_as_task(
        "long_running_task",
        {"description": "Process data", "steps": 5},
        ttl=60000,
    )
    task_id = result.task.taskId
    
    # Poll until complete
    async for status in session.experimental.poll_task(task_id):
        print(f"Status: {status.status}")
    
    # Get result
    final = await session.experimental.get_task_result(task_id, CallToolResult)
```

## 📊 Task States

| Status | Meaning |
|--------|---------|
| `working` | Task is running |
| `completed` | Done successfully |
| `failed` | Error occurred |
| `cancelled` | Terminated by user |

## 🔗 Connect to VS Code / GitHub Copilot

```json
{
  "mcp.servers": {
    "durable-tasks": {
      "url": "http://localhost:3000/sse"
    }
  }
}
```

## 📚 References

- [MCP Tasks Overview](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/experimental/tasks.md)
- [MCP Tasks Server Guide](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/experimental/tasks-server.md)
- [MCP Tasks Client Guide](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/experimental/tasks-client.md)
- [Durable Task Python SDK](https://github.com/microsoft/durabletask-python)
- [DTS Emulator](https://learn.microsoft.com/azure/azure-functions/durable/durable-functions-create-first-python-v2)

## 🔮 Making DTS the Default for MCP Tasks in Azure

If Durable Task SDKs and Durable Task Scheduler were to become the default implementation for long-running MCP tasks in Azure, here are areas that would need to change or be made easier:

### 1. SDK Integration

| Area | Current State | Improvement |
|------|---------------|-------------|
| **TaskStore boilerplate** | Developers implement `TaskStore` interface manually | Provide a ready-to-use `DurableTaskStore` in the MCP SDK or as a package |
| **Worker management** | Manual thread/process management for DTS worker | Auto-start worker when MCP server initializes |
| **Connection configuration** | Manual gRPC client setup | Environment-based auto-discovery (`DTS_ENDPOINT`, `DTS_TASKHUB`) |

### 2. Azure Integration

| Area | Current State | Improvement |
|------|---------------|-------------|
| **Managed Identity** | Manual auth configuration | Auto-detect Azure identity and configure DTS client |
| **DTS provisioning** | Separate resource creation | One-click DTS provisioning when creating MCP server resources |
| **Monitoring** | Separate Application Insights setup | Built-in correlation between MCP requests and DTS orchestrations |

### 3. Developer Experience

| Area | Current State | Improvement |
|------|---------------|-------------|
| **Local development** | Run Docker emulator separately | `azd up` or `func start` auto-starts DTS emulator |
| **Orchestration definition** | Write Python generator functions | Decorators like `@mcp.orchestration` that auto-register with DTS |
| **Error handling** | Manual status mapping | Standard error types that map consistently to MCP task states |

### 4. MCP Protocol Enhancements

| Area | Current State | Improvement |
|------|---------------|-------------|
| **Task metadata** | Limited to `ttl` and custom fields | Support for retry policies, timeout, and priority |
| **Progress reporting** | No standard progress mechanism | `task.progress` field with percentage and message |
| **Sub-orchestrations** | Not exposed in MCP | Allow tasks to spawn child tasks with parent-child tracking |
| **Human-in-the-loop** | Custom implementation | Standard `approval_required` flag and approval endpoints |

### 5. Deployment Templates

| Area | Current State | Improvement |
|------|---------------|-------------|
| **Container Apps** | Manual Bicep/ARM setup | `azd` template for MCP + DTS on Container Apps |
| **Azure Functions** | Separate Durable Functions config | MCP trigger binding that auto-provisions DTS backend |
| **Kubernetes** | No standard Helm chart | Helm chart with DTS sidecar and KEDA scaler |

### Example: Simplified Future API

```python
from mcp.server import Server
from mcp.durable import durable_task  # New package

mcp = Server("my-server")

# One line to enable DTS-backed tasks (auto-connects to DTS_ENDPOINT)
mcp.enable_durable_tasks()

@mcp.tool(task_required=True)
@durable_task
async def process_data(ctx, data: str, steps: int = 5):
    """A long-running task backed by DTS."""
    results = []
    for i in range(steps):
        result = await ctx.call_activity(do_step, step=i)
        await ctx.report_progress(i / steps, f"Step {i+1}/{steps}")
        results.append(result)
    return {"success": True, "results": results}
```

