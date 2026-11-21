from langchain_core.tools import tool
from langchain_core.messages import AIMessage
from deepagents import create_deep_agent
from fake import FakeToolModel

@tool
def calendar_search(q: str) -> str:
    """Search calendar."""
    return "ok"

def tool_names(agent):
    out=set()
    for n,node in agent.nodes.items():
        b=getattr(node,"bound",None)
        t=getattr(b,"tools_by_name",None)
        if t: out|=set(t)
    return out

m=FakeToolModel(messages=iter([AIMessage(content="hi")]))
a=create_deep_agent(model=m, tools=[calendar_search])
print("DEFAULT effective tools:", sorted(tool_names(a)))
