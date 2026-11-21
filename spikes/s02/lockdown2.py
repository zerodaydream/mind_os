import signal, faulthandler, sys
faulthandler.dump_traceback_later(25, exit=True)
import os, tempfile, pathlib, itertools
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain.agents.middleware.types import AgentMiddleware
from deepagents import create_deep_agent
from fake import FakeToolModel

ALLOW = frozenset({"calendar_search"})

class AllowlistMiddleware(AgentMiddleware):
    """Fail-closed: model sees only ALLOW; any other tool call is rejected at execution."""
    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=[t for t in request.tools if getattr(t,"name",None) in ALLOW]))
    def wrap_tool_call(self, request, handler):
        name = request.tool_call["name"]
        if name not in ALLOW:
            return ToolMessage(content=f"REJECTED: tool '{name}' not allowed", tool_call_id=request.tool_call["id"], status="error")
        return handler(request)

@tool
def calendar_search(q: str) -> str:
    """Search calendar."""
    return "ok"

canary = pathlib.Path(tempfile.gettempdir())/"deepagents_canary.txt"
canary.unlink(missing_ok=True)
calls = [
  AIMessage(content="", tool_calls=[{"name":"write_file","args":{"file_path":str(canary),"content":"pwn"},"id":"1"}]),
  AIMessage(content="", tool_calls=[{"name":"execute","args":{"command":f"echo pwn > {canary}"},"id":"2"}]),
  AIMessage(content="", tool_calls=[{"name":"calendar_search","args":{"q":"x"},"id":"3"}]),
  AIMessage(content="done"),
]
def run(mw):
    a = create_deep_agent(model=FakeToolModel(messages=itertools.chain(list(calls), itertools.repeat(AIMessage(content='done')))), tools=[calendar_search], middleware=mw)
    r = a.invoke({"messages":[HumanMessage("go")]})
    return [(m.name if hasattr(m,"name") else None, str(m.content)[:90]) for m in r["messages"] if isinstance(m, ToolMessage)]

print("--- NO allowlist (default backend = StateBackend)")
for x in run([]): print(x)
print("canary on disk:", canary.exists())
canary.unlink(missing_ok=True)
print("--- WITH AllowlistMiddleware")
calls_copy = calls
for x in run([AllowlistMiddleware()]): print(x)
print("canary on disk:", canary.exists())
