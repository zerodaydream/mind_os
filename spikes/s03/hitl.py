import sys, os, pathlib, json
os.environ["LANGSMITH_TRACING"]="false"
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from deepagents import create_deep_agent
from fake import FakeToolModel

DSN="postgresql://spike@localhost:54329/spike"
LOG=pathlib.Path("/tmp/s03_send_log.txt")
SIG="EroRSIGNATURE=="
STRICT = "strict" in sys.argv

@tool
def send_email(to: str, body: str) -> str:
    """Send an email."""
    LOG.open("a").write(f"SENT to={to}\n"); return f"sent to {to}"

def serde():
    if STRICT:
        return JsonPlusSerializer(allowed_msgpack_modules=[("langchain_core","messages","ai","AIMessage"),
            ("langchain_core","messages","human","HumanMessage"),("langchain_core","messages","tool","ToolMessage")])
    return None

phase = sys.argv[1]
cfg={"configurable":{"thread_id":"s03-thread"}}
with PostgresSaver.from_conn_string(DSN) as cp:
    if serde(): cp.serde = serde()
    cp.setup()
    if phase=="A":
        LOG.unlink(missing_ok=True)
        ai = AIMessage(content=[{"type":"thinking","thinking":"plan"},{"type":"text","text":"sending","extras":{"signature":SIG}}],
              tool_calls=[{"name":"send_email","args":{"to":"a@b.c","body":"hi"},"id":"call1","type":"tool_call"}],
              additional_kwargs={"__gemini_function_call_thought_signatures__":{"call1":SIG}})
        msgs=[ai]
    else:
        msgs=[AIMessage(content="Sent.")]
    import itertools
    agent=create_deep_agent(model=FakeToolModel(messages=itertools.chain(msgs, itertools.repeat(AIMessage(content="Sent.")))),
        tools=[send_email], interrupt_on={"send_email":{"allowed_decisions":["approve","reject"]}}, checkpointer=cp)
    if phase=="A":
        r=agent.invoke({"messages":[HumanMessage("email a@b.c")]}, cfg)
        intr=r.get("__interrupt__")
        print("A interrupted:", bool(intr), "| sent so far:", LOG.exists())
    else:
        st=agent.get_state(cfg)
        last_ai=[m for m in st.values["messages"] if isinstance(m,AIMessage)][-1]
        print("B pending next:", st.next)
        print("B signature in extras kept:", last_ai.content[1]["extras"]["signature"]==SIG,
              "| in additional_kwargs kept:", last_ai.additional_kwargs.get("__gemini_function_call_thought_signatures__",{}).get("call1")==SIG)
        r=agent.invoke(Command(resume={"decisions":[{"type":"approve"}]}), cfg)
        print("B final:", r["messages"][-1].content, "| sends:", LOG.read_text().count("SENT"))
        try:
            agent.invoke(Command(resume={"decisions":[{"type":"approve"}]}), cfg)
        except Exception as e: print("B 2nd resume raised:", type(e).__name__)
        print("B sends after 2nd resume:", LOG.read_text().count("SENT"))
