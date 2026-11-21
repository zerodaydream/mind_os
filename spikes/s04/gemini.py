import os, sys, time, re, pathlib
os.environ["LANGSMITH_TRACING"]="false"
# read only GOOGLE_API_KEY + model ids from ../.env; never print them
env={}
for l in pathlib.Path("../.env").read_text().splitlines():
    m=re.match(r'^([A-Z_]+)=(.*)$', l)
    if m: env[m[1]]=m[2].strip().strip('"').strip("'")
os.environ["GOOGLE_API_KEY"]=env["GOOGLE_API_KEY"]
model_id=sys.argv[1] if len(sys.argv)>1 else env["MINDOS_SYNTH_MODEL"]
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, ToolMessage

@tool
def get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"Sunny in {city}"

print("model:", model_id)
for level in ["minimal","low","high"]:
    try:
        llm=ChatGoogleGenerativeAI(model=model_id, thinking_level=level, include_thoughts=True)
        if level=="minimal":
            prof=getattr(llm,"profile",None) or {}
            print("profile levels:", prof.get("reasoning_effort_levels"), "default:", prof.get("reasoning_effort_default"))
        m=llm.bind_tools([get_weather])
        t=time.time()
        msgs=[HumanMessage("What's the weather in Boston and Paris? Use the tool for each.")]
        ai=m.invoke(msgs); msgs.append(ai)
        calls=ai.tool_calls
        sig_in_kwargs=any("signature" in k for k in ai.additional_kwargs)
        sig_in_blocks=any(isinstance(b,dict) and (b.get("extras") or {}).get("signature") for b in (ai.content if isinstance(ai.content,list) else []))
        for c in calls: msgs.append(get_weather.invoke(c))
        final=m.invoke(msgs)
        dt=time.time()-t
        types=[b.get("type") for b in final.content] if isinstance(final.content,list) else ["str"]
        u=final.usage_metadata or {}
        print(f"[{level}] ok {dt:.1f}s calls={len(calls)} sig_kwargs={sig_in_kwargs} sig_blocks={sig_in_blocks} "
              f"final_blocks={types} reasoning_tokens={(u.get('output_token_details') or {}).get('reasoning')} out={u.get('output_tokens')}")
    except Exception as e:
        print(f"[{level}] ERR {type(e).__name__}: {str(e)[:160]}")
