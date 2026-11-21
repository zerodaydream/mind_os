import os, sys, re, pathlib, time
os.environ["LANGSMITH_TRACING"]="false"
env={}
for l in pathlib.Path("../.env").read_text().splitlines():
    m=re.match(r'^([A-Z_]+)=(.*)$', l)
    if m: env[m[1]]=m[2].strip().strip('"').strip("'")
os.environ["GOOGLE_API_KEY"]=env["GOOGLE_API_KEY"]
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

@tool
def add(a: int, b: int) -> int:
    """Add two ints."""
    return a+b

strict=JsonPlusSerializer(allowed_msgpack_modules=[("langchain_core","messages","ai","AIMessage"),("langchain_core","messages","human","HumanMessage"),("langchain_core","messages","tool","ToolMessage")])
for mid in sys.argv[1:]:
    print("== model", mid)
    try:
        llm=ChatGoogleGenerativeAI(model=mid, thinking_level="high", include_thoughts=True).bind_tools([add])
        msgs=[HumanMessage("Think step by step: if a train leaves at 3:40pm and travels 2h55m, when does it arrive? Then use the add tool for 17+25.")]
        ai=llm.invoke(msgs)
        blocks=[b.get("type") for b in ai.content] if isinstance(ai.content,list) else ["str"]
        print(" first blocks:", blocks, "| tool_calls:", [c["name"] for c in ai.tool_calls],
              "| kwargs keys:", [k for k in ai.additional_kwargs][:4], "| usage:", ai.usage_metadata and ai.usage_metadata.get("output_token_details"))
        # checkpoint round-trip (strict), then continue the tool loop
        ai2=strict.loads_typed(strict.dumps_typed(ai))
        same = ai2.additional_kwargs==ai.additional_kwargs and ai2.content==ai.content
        msgs2=[msgs[0], ai2]+[add.invoke(c) for c in ai2.tool_calls]
        final=llm.invoke(msgs2)
        print(" roundtrip identical:", same, "| continued OK:", bool(final.content), "| final blocks:", [b.get("type") for b in final.content] if isinstance(final.content,list) else "str")
    except Exception as e:
        print(" ERR", type(e).__name__, str(e)[:200])
