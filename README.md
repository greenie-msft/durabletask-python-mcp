# Durable Task MCP Integration (Python)

A prototype implementation of the [MCP Task specification][mcp-tasks] using the
[Durable Task SDK for Python][dt-sdk] with the
[Durable Task Scheduler (DTS)][dts] as the backend.

[mcp-tasks]: https://modelcontextprotocol.io/specification/draft/basic/utilities/tasks
[dt-sdk]: https://github.com/microsoft/durabletask-python
[dts]: https://learn.microsoft.com/azure/azure-functions/durable/durable-task-scheduler/durable-task-scheduler

## Overview

This project bridges [MCP Tasks][mcp-tasks] and [Durable Task][dt-sdk]
orchestrations, enabling MCP server developers to build reliable, observable,
long-running tools backed by the Durable Task Scheduler. An MCP tool is mapped
to a Durable Task orchestration or activity, and the library handles all
protocol details automatically.

### Why Durable Task?

- **Reliability** — Task state is durably persisted in DTS. Orchestrations automatically resume
  after server restarts without losing progress.
- **Observability** — The built-in [DTS Dashboard][dts-dashboard] provides real-time visibility
  into task execution history, timelines, inputs/outputs, and failure details.
- **Scalability** — DTS is a managed service that scales independently of the MCP server.
- **Rich execution models** — Simple single-activity tools and complex multi-step orchestrations
  with human-in-the-loop approval, all using the same programming model.

[dts-dashboard]: https://learn.microsoft.com/azure/azure-functions/durable/durable-task-scheduler/durable-task-scheduler-dashboard

## How It Works

```text
MCP Client ──► MCP Server (Python)
                    │
               TaskStore
                    │
              DurableTaskStore ◄── maps MCP lifecycle to orchestrations
                    │
               DurableTaskClient
                    │
              ┌─────┴──────┐
              │    DTS      │  (Emulator or Azure)
              └────────────┘
```

Each MCP tool registered via `@durable_mcp.task()` gets an internal orchestration
that handles MCP protocol concerns (TTL, status reporting, human-in-the-loop).
Developers write plain Durable Task code with no MCP boilerplate.

### Concept Mapping

| MCP Task Concept    | Durable Task Concept                  |
| ------------------- | ------------------------------------- |
| Task ID             | Orchestration Instance ID             |
| Task Lifecycle      | Orchestration Lifecycle               |
| `working`           | `Pending` or `Running`                |
| `input_required`    | `Running` + custom status             |
| `completed`         | `Completed`                           |
| `failed`            | `Failed`                              |
| `cancelled`         | `Terminated`                          |
| Task Status Message | Custom Status                         |
| Elicitation         | `wait_for_external_event` + custom status |
| List Tasks          | List Orchestrations                   |
| Cancel Task         | Terminate Orchestration               |

## Programming Model

Register orchestrations and activities as MCP tools using decorators:

```python
from mcp.server import Server
from mcp_dts import DurableTasks

server = Server("my-server")
dts = DurableTasks(server, dts_host="localhost:8080")

# Create human-in-the-loop helper
wait_for_approval = dts.create_input_waiter("approval")


@dts.task(
    name="purchase_order",
    description="Submit a purchase order. Orders over $1,000 require approval.",
    input_schema={
        "type": "object",
        "properties": {
            "item": {"type": "string"},
            "amount": {"type": "number"},
            "item_count": {"type": "integer", "default": 1},
        },
    },
)
def purchase_order_orchestration(ctx, input: dict):
    amount = input.get("amount", 0)
    item_count = input.get("item_count", 1)

    # Validate the order
    yield ctx.call_activity(validate_order, input=input)

    if amount > 1000:
        # Pauses the task and sets status to "input_required"
        decision = yield from wait_for_approval(
            ctx,
            message=f"Approve order for ${amount}?",
        )
        if not decision.get("approved"):
            return "Order rejected."

    # Process items
    yield ctx.call_activity(process_items, input={"count": item_count})
    return "Order completed."


@dts.activity
def validate_order(ctx, input: dict):
    """Validate the order details."""
    return True


@dts.activity
def process_items(ctx, input: dict):
    """Process items in the order."""
    import time
    time.sleep(1)
    return input.get("count", 1)
```

Orchestrations use standard Durable Task APIs:

- `yield ctx.call_activity(fn, input={...})` — Call an activity and checkpoint.
- `yield from wait_for_approval(ctx, message="...")` — Set `input_required` status and durably wait for external input.
- Return a value to complete the task.

## Samples

The example server exposes an `analysis_with_approval` tool demonstrating the
full MCP Task lifecycle, including human-in-the-loop approval. See
[examples/](examples/) for step-by-step instructions on testing with the
[MCP Inspector][inspector].

[inspector]: https://modelcontextprotocol.io/docs/tools/inspector

| Sample | Transport | Description |
| --- | --- | --- |
| [examples/server.py](examples/server.py) | SSE | MCP server on `http://localhost:3000/sse` |
| [examples/client.py](examples/client.py) | SSE | CLI client demonstrating Tasks API and approval flow |

### Prerequisites

- [Python 3.10+](https://www.python.org/downloads/)
- [Docker](https://www.docker.com/) (for the DTS emulator)
- [Node.js](https://nodejs.org/) (for the MCP Inspector)

### Quick Start

```bash
# 1. Start the DTS emulator
docker run -d -p 8080:8080 -p 8082:8082 \
  --name dts-emulator \
  mcr.microsoft.com/dts/dts-emulator:latest

# 2. Install the library
pip install -e .

# 3. Run the server
cd examples
python server.py
```

Open the DTS Dashboard at http://localhost:8082 to observe orchestrations.

To test with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

Connect to `http://localhost:3000/sse`, then invoke the `analysis_with_approval`
tool from the Tools tab.

## Configuration

The DTS connection is configured via constructor arguments:

| Parameter | Default | Description |
| --- | --- | --- |
| `dts_host` | `localhost:8080` | DTS gRPC endpoint |
| `taskhub` | `default` | DTS task hub name |

| Environment | `dts_host` value |
| --- | --- |
| Local emulator | `localhost:8080` |
| Azure DTS | `<name>.westus2.durabletask.io` |

## Project Structure

```text
├── mcp_dts/                        # Core library (pip package)
│   ├── __init__.py                 # Exports DurableTasks, DurableTaskStore
│   ├── server.py                   # DurableTasks class — decorators, tool registration
│   └── store.py                    # DurableTaskStore — TaskStore → DTS mapping
├── examples/
│   ├── server.py                   # SSE server with human-in-the-loop demo
│   └── client.py                   # CLI client with approval flow
├── pyproject.toml                  # Package configuration
└── README.md
```

## References

- [MCP Task Specification](https://modelcontextprotocol.io/specification/draft/basic/utilities/tasks)
- [Durable Task Python SDK](https://github.com/microsoft/durabletask-python)
- [Durable Task Scheduler](https://learn.microsoft.com/azure/azure-functions/durable/durable-task-scheduler/durable-task-scheduler)
- [DTS Dashboard](https://learn.microsoft.com/azure/azure-functions/durable/durable-task-scheduler/durable-task-scheduler-dashboard)
- [.NET implementation of this same POC](https://github.com/cgillum/durabletask-dotnet-mcp)

## License

MIT

