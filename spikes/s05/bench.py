import asyncio, time, faulthandler, resource
faulthandler.dump_traceback_later(120, exit=True)
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static, TextArea
import textual
print("textual", textual.__version__, "| Markdown.get_stream:", hasattr(Markdown,"get_stream"))

class Bench(App):
    def compose(self)->ComposeResult:
        yield VerticalScroll(id="log")
        yield TextArea(id="prompt")
        yield Static("status", id="status")

async def main():
    app=Bench()
    async with app.run_test(size=(120,40)) as pilot:
        log=app.query_one("#log")
        # 1) mount 5000 finished blocks
        t=time.perf_counter()
        for i in range(0,5000,250):
            await log.mount_all([Static(f"⏺ tool.call_{j}(args)\n  ⎿  result {j} · 0.{j%9}s") for j in range(i,i+250)])
        await pilot.pause()
        print(f"mount 5000 blocks: {time.perf_counter()-t:.2f}s")
        # 2) scroll to end + page up/down latency
        t=time.perf_counter(); log.scroll_end(animate=False); await pilot.pause(); 
        for _ in range(20): log.scroll_page_up(animate=False); await pilot.pause()
        print(f"20 page-ups over 5000 blocks: {time.perf_counter()-t:.2f}s")
        # 3) stream markdown: 6000 chars in 40-char chunks, one flush per 16ms tick
        md=Markdown(""); await log.mount(md); log.scroll_end(animate=False)
        text=("Here is **bold** text, `code`, and a list:\n- item one\n- item two\n\n```python\nprint('x')\n```\n")*40
        t=time.perf_counter(); lat=[]
        stream=Markdown.get_stream(md) if hasattr(Markdown,"get_stream") else None
        async def feed():
            for i in range(0,len(text),40):
                t0=time.perf_counter()
                if stream: await stream.write(text[i:i+40])
                else: await md.append(text[i:i+40])
                await pilot.pause(); lat.append(time.perf_counter()-t0)
                await asyncio.sleep(0.016)
            if stream: await stream.stop()
        await feed()
        lat.sort()
        print(f"stream {len(text)} chars: total {time.perf_counter()-t:.2f}s | per-flush p50 {lat[len(lat)//2]*1000:.0f}ms p95 {lat[int(len(lat)*.95)]*1000:.0f}ms max {lat[-1]*1000:.0f}ms")
        # 4) multiline prompt typing
        ta=app.query_one("#prompt"); ta.focus()
        t=time.perf_counter(); await pilot.press(*list("hello world "*20)); print(f"type 240 keys: {time.perf_counter()-t:.2f}s")
    print("peak RSS MB:", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e6)
asyncio.run(main())
