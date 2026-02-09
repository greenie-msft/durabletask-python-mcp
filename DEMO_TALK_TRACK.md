# Demo Talk Track: MCP Tasks with Durable Task SDK

**Duration:** ~3 minutes

---

## Opening (15 seconds)

> "Today I want to show you how we can build **durable, long-running tools** for MCP using the **Durable Task SDK**."
>
> "The problem we're solving: MCP tools are typically synchronous - they block until complete. But what about tasks that take minutes or hours? What if the server restarts mid-execution?"

---

## The Solution (30 seconds)

> "The MCP SDK now has an experimental **Tasks protocol**. Instead of blocking, a tool can return a task ID, and the client polls until completion."
>
> "But here's the key question: **where does the task state live?** That's where Durable Task Scheduler comes in."
>
> "Durable Task Scheduler persists every step of your workflow. If the server crashes, work resumes exactly where it left off. It handles retries, timeouts, and gives you a dashboard to monitor everything."

---

## Show the Code (45 seconds)

> "Let me show you how simple it is."

*[Open `examples/server.py`]*

> "Here's a DurableTasks server. I create it with a connection to the DTS emulator on port 8080."
>
> "This `@task` decorator defines an **orchestration** - it coordinates the workflow. Notice it uses `yield` to call activities. Each `yield` is a checkpoint - if we restart, we resume from here."
>
> "The `@activity` decorator defines the actual work - in this case, a 1-second analysis step."
>
> "That's it. About 30 lines of code for a fully durable, long-running MCP tool."

---

## Live Demo (60 seconds)

> "Let's run it."

*[Terminal 1: Show DTS emulator is running]*

> "The DTS emulator is running in Docker - that's our workflow engine."

*[Terminal 2: Start the server]*

```bash
python server.py
```

> "Server's up. It's connected to DTS and ready for MCP clients."

*[Terminal 3: Run the client]*

```bash
python client.py
```

> "The client calls `call_tool_as_task`, gets back a task ID immediately, then polls."
>
> "Watch the status... working... working... and completed!"
>
> "The result shows all 5 steps completed successfully."

*[Open http://localhost:8082 in browser]*

> "And here's the dashboard. We can see our orchestration, each activity that ran, inputs, outputs, timing - full observability out of the box."

---

## Key Takeaways (30 seconds)

> "So what did we just see?"
>
> "**One:** Long-running MCP tools that don't block the client."
>
> "**Two:** Durable execution - if the server restarts, work continues."
>
> "**Three:** Built-in observability with the DTS dashboard."
>
> "**Four:** A simple decorator-based API that feels natural for Python developers."

---

## Closing (15 seconds)

> "This is just a prototype, but imagine this as the default for MCP Tasks in Azure - automatic persistence, retry policies, progress reporting - all handled by Durable Task Scheduler."
>
> "The code is on GitHub. Try it out and let us know what you think."

---

## Quick Reference

| What to Show | When |
|--------------|------|
| `server.py` code | 0:45 - 1:30 |
| Start server | 1:30 - 1:40 |
| Run client | 1:40 - 2:10 |
| DTS Dashboard | 2:10 - 2:30 |
| Wrap up | 2:30 - 3:00 |

## Key Points to Emphasize

1. **Problem:** MCP tools block; no persistence for long-running work
2. **Solution:** Tasks protocol + Durable Task SDK for state persistence
3. **Demo:** 30 lines of code → fully durable workflow
4. **Value:** Reliability, observability, simple API
