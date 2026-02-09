# MCP Tasks with Durable Task SDK

A library for building **durable, long-running MCP tools** backed by **Durable Task Scheduler**.

## 🎯 Why Durable Task SDK?

MCP's [Tasks protocol](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/experimental/tasks.md) enables tools to run asynchronously - the client starts a task, polls for status, and retrieves results when complete. But **where does the task state live?**

That's where **Durable Task SDK** comes in:

| Challenge | How Durable Task SDK Solves It |
|-----------|-------------------------------|
| **State Persistence** | Orchestration state is stored in Durable Task Scheduler, surviving server restarts |
| **Long-Running Work** | Break work into activities that can take minutes, hours, or days |
| **Reliability** | Automatic retry and checkpointing - work resumes exactly where it left off |
| **Scalability** | Workers can scale horizontally; scheduler distributes work |
| **Observability** | Built-in dashboard to view running orchestrations and their history |

### What is Durable Task Scheduler?

[Durable Task Scheduler](https://learn.microsoft.com/azure/azure-functions/durable/durable-functions-overview) is a managed Azure service (also available as a local emulator) that:

- **Persists orchestration state** - Every step is checkpointed
- **Manages the work queue** - Distributes activities to workers
- **Handles replay** - When an orchestration resumes, it replays from the beginning using saved state
- **Provides a dashboard** - Visual UI to monitor running tasks

For local development, we use the **DTS Emulator** - a Docker container that provides the same functionality.

## ✨ Quick Example

```python
from mcp_dts import DurableTasks

# Create MCP server with DTS backend
durable_mcp = DurableTasks("my-server", dts_host="localhost:8080")

@durable_mcp.task(name="process_data", description="Process data in steps")
def my_orchestration(ctx, input: dict):
    """Orchestration: coordinates the workflow."""
    results = []
    for i in range(input.get("steps", 5)):
        # Each activity is checkpointed - if server restarts, it resumes here
        result = yield ctx.call_activity(do_step, input={"step": i})
        results.append(result)
    return {"success": True, "results": results}

@durable_mcp.activity
def do_step(ctx, input: dict):
    """Activity: does the actual work."""
    import time
    time.sleep(1)  # Simulate work
    return f"Completed step {input['step']}"

durable_mcp.run()  # Starts MCP server + DTS worker
```

## 🔄 How It Works

```
Client                          MCP Server                    DTS Scheduler
  │                                 │                              │
  │  call_tool_as_task()            │                              │
  ├────────────────────────────────▶│  schedule_new_orchestration()│
  │                                 ├─────────────────────────────▶│
  │  CreateTaskResult(task_id)      │                              │
  │◀────────────────────────────────┤                              │
  │                                 │                              │
  │  poll_task(task_id)             │  get_instance_state()        │
  ├────────────────────────────────▶├─────────────────────────────▶│
  │  status: "working"              │◀─────────────────────────────┤
  │◀────────────────────────────────┤                              │
  │         ...                     │         ...                  │
  │                                 │                              │
  │  poll_task(task_id)             │  get_instance_state()        │
  ├────────────────────────────────▶├─────────────────────────────▶│
  │  status: "completed"            │◀─────────────────────────────┤
  │◀────────────────────────────────┤                              │
  │                                 │                              │
  │  get_task_result(task_id)       │  get serialized output       │
  ├────────────────────────────────▶├─────────────────────────────▶│
  │  {"success": true, ...}         │◀─────────────────────────────┤
  │◀────────────────────────────────┤                              │
```

### The Key Components

| Component | Role |
|-----------|------|
| **MCP Server** | Handles MCP protocol, exposes tools to clients |
| **DurableTasks** | Bridges MCP Tasks API to Durable Task SDK |
| **DTS Worker** | Executes orchestrations and activities |
| **DTS Scheduler** | Persists state, manages work queue, handles replay |
| **DurableTaskStore** | Implements MCP's `TaskStore` interface using DTS |

## 🚀 Running the Example

### Prerequisites

- Python 3.10+
- Docker (for the DTS Emulator)

### 1. Start the DTS Emulator

```bash
docker run -d -p 8080:8080 -p 8082:8082 \
  --name dts-emulator \
  cgillum/durabletask-emulator
```

- **Port 8080**: gRPC endpoint (workers connect here)
- **Port 8082**: Dashboard UI

Open the dashboard: http://localhost:8082

### 2. Install the Library

```bash
cd /path/to/mcp-dts
pip install -e .
```

### 3. Run the Server

```bash
cd examples
python server.py
```

You'll see:
```
Starting existing server with DTS support...
Dashboard: http://localhost:8082
MCP: http://localhost:3000/sse
DTS Worker connecting to localhost:8080...
```

### 4. Run the Client

In another terminal:

```bash
cd examples
python client.py
```

Output:
```
Connecting to http://localhost:3000/sse...
✅ Connected

Tools: ['long_running_analysis']
  - long_running_analysis: taskSupport=required

🚀 Calling long_running_analysis as task...
   Task ID: aba6e209-8322-4a44-8f61-b55b41c4bc5a
   Initial status: working
   Poll interval: 1000ms

🔄 Polling for status...
   working - RUNNING
   working - RUNNING
   working - RUNNING
   completed - COMPLETED

✅ Task completed!
Result: {"analysis_complete": true, "steps": 5, "results": [...]}
```

### 5. View in Dashboard

Open http://localhost:8082 to see:
- Running and completed orchestrations
- Execution history for each orchestration
- Activity inputs and outputs

## 📦 Two Usage Patterns

### Option 1: Create a New Server

```python
from mcp_dts import DurableTasks

durable_mcp = DurableTasks("my-server", dts_host="localhost:8080")

@durable_mcp.task(name="analyze", description="Run analysis")
def analyze(ctx, input: dict):
    result = yield ctx.call_activity(do_work, input=input)
    return {"done": True, "result": result}

@durable_mcp.activity
def do_work(ctx, input: dict):
    return {"processed": True}

durable_mcp.run()  # Starts MCP server + DTS worker
```

### Option 2: Add to an Existing Server

```python
from mcp.server import Server
from mcp_dts import DurableTasks

# Your existing MCP server
server = Server("my-server")

# Wrap with DTS support - automatically chains to existing handlers
dts = DurableTasks(server, dts_host="localhost:8080")

@dts.task(name="analyze", description="Run analysis")
def analyze(ctx, input: dict):
    result = yield ctx.call_activity(do_work, input=input)
    return {"done": True}

@dts.activity
def do_work(ctx, input: dict):
    return {"processed": True}

# Start worker, then run your server however you normally do
dts.start_worker()
# ... your normal server startup
```

## ⚠️ Critical: Orchestration Determinism

**Orchestrations MUST be deterministic.** DTS replays orchestrations from the beginning after each activity completes, using saved state. Non-deterministic code causes infinite replay loops.

### ❌ Don't Do This

```python
@durable_mcp.task(name="bad_task")
def bad_orchestration(ctx, input: dict):
    import random
    steps = random.randint(5, 10)  # ❌ Different value on each replay!
    for i in range(steps):
        yield ctx.call_activity(do_work, input={"step": i})
```

### ✅ Do This Instead

```python
@durable_mcp.task(name="good_task")
def good_orchestration(ctx, input: dict):
    steps = input.get("steps", 5)  # ✅ Deterministic from input
    for i in range(steps):
        yield ctx.call_activity(do_work, input={"step": i})
```

**Rule of thumb:** Anything non-deterministic (`random`, `datetime.now()`, `uuid.uuid4()`) should be:
1. Passed as input to the orchestration, or
2. Generated inside an activity (activities run once, not replayed)

## 🏗️ Architecture

```
┌─────────────────┐     MCP Protocol      ┌─────────────────────────────┐
│   MCP Client    │ ────────────────────▶ │                             │
│   (client.py)   │ ◀──────────────────── │     DurableTasks class      │
└─────────────────┘                       │                             │
                                          │   • MCP SDK + Tasks         │
                                          │   • DurableTaskStore        │
                                          │   • Durable Task SDK        │
                                          └──────────────┬──────────────┘
                                                         │ gRPC
                                                         ▼
                                          ┌─────────────────────────────┐
                                          │   DTS Emulator (Docker)     │
                                          │   • Persists workflow state │
                                          │   • Manages work queue      │
                                          │   • Dashboard UI            │
                                          └─────────────────────────────┘
```

### How DurableTaskStore Bridges MCP and DTS

| MCP Operation | DurableTaskStore Implementation |
|---------------|--------------------------------|
| `create_task(metadata)` | Schedules a DTS orchestration |
| `get_task(task_id)` | Queries orchestration state, maps to MCP status |
| `get_result(task_id)` | Returns serialized orchestration output |
| `cancel_task(task_id)` | Terminates the orchestration |

### State Mapping

| DTS State | MCP Status |
|-----------|------------|
| `PENDING` | `working` |
| `RUNNING` | `working` |
| `COMPLETED` | `completed` |
| `FAILED` | `failed` |
| `TERMINATED` | `cancelled` |

## 📁 Project Structure

```
mcp-dts/
├── mcp_dts/                    # The library
│   ├── __init__.py             # Exports DurableTasks, DurableTaskStore
│   ├── server.py               # DurableTasks class with decorators
│   └── store.py                # DurableTaskStore (TaskStore → DTS)
│
├── examples/                   # Working examples
│   ├── server.py               # MCP server with task-backed tools
│   └── client.py               # MCP client demonstrating Tasks API
│
├── pyproject.toml              # Package configuration
└── README.md
```

## 📊 Task States

| Status | Meaning |
|--------|---------|
| `working` | Task is running or pending |
| `completed` | Done successfully |
| `failed` | Error occurred |
| `cancelled` | Terminated by user |

## 🔗 Connect to VS Code / GitHub Copilot

Add to your VS Code settings:

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
- [Durable Task Scheduler](https://learn.microsoft.com/azure/azure-functions/durable/durable-functions-overview)
- [DTS Emulator](https://github.com/cgillum/durabletask-emulator)

## 🔮 Future: Making DTS the Default for MCP Tasks in Azure

If Durable Task SDK and Scheduler were to become the default for long-running MCP tasks in Azure:

### SDK Improvements

| Area | Improvement |
|------|-------------|
| **Ready-to-use TaskStore** | Provide `DurableTaskStore` in MCP SDK or as a package |
| **Auto-start worker** | Start DTS worker automatically when MCP server initializes |
| **Environment-based config** | Auto-discover from `DTS_ENDPOINT`, `DTS_TASKHUB` |

### Azure Integration

| Area | Improvement |
|------|-------------|
| **Managed Identity** | Auto-detect Azure identity for DTS auth |
| **One-click provisioning** | Deploy DTS alongside MCP server resources |
| **Built-in monitoring** | Correlate MCP requests with DTS orchestrations in App Insights |

### Developer Experience

| Area | Improvement |
|------|-------------|
| **Local dev** | `azd up` auto-starts DTS emulator |
| **Simplified decorators** | `@mcp.orchestration` that auto-registers with DTS |
| **Progress reporting** | Standard `task.progress` field with percentage and message |

### Example: Ideal Future API

```python
from mcp.server import Server
from mcp.durable import durable_task  # Future package

mcp = Server("my-server")
mcp.enable_durable_tasks()  # One line to enable

@mcp.tool(task_required=True)
@durable_task
async def process_data(ctx, data: str, steps: int = 5):
    """A long-running task backed by DTS."""
    for i in range(steps):
        result = await ctx.call_activity(do_step, step=i)
        await ctx.report_progress(i / steps, f"Step {i+1}/{steps}")
    return {"success": True}
```

