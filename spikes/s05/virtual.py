import asyncio, time, faulthandler, sys
faulthandler.dump_traceback_later(100, exit=True)
from textual.app import App, ComposeResult
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.geometry import Size
from rich.segment import Segment
from rich.style import Style
N=int(sys.argv[1])

class Transcript(ScrollView):
    """Virtualized transcript: renders only visible lines from a model (list[str])."""
    def __init__(self): super().__init__(); self.lines=[]
    def set_lines(self, lines):
        self.lines=lines; self.virtual_size=Size(self.size.width or 120, len(lines)); self.refresh()
    def append_to_last(self, s):
        self.lines[-1]+=s; self.refresh_line(len(self.lines)-1)
    def render_line(self, y:int)->Strip:
        _, sy = self.scroll_offset; i=y+sy
        if i>=len(self.lines): return Strip.blank(self.size.width, self.rich_style)
        return Strip([Segment(self.lines[i][:self.size.width].ljust(self.size.width), self.rich_style)])

class B(App):
    def compose(self)->ComposeResult: yield Transcript()

async def main():
    app=B()
    async with app.run_test(size=(120,40)) as pilot:
        t=app.query_one(Transcript)
        lines=[]
        for j in range(N): lines += [f"⏺ tool.call_{j}(args)", f"  ⎿  result {j} · 0.{j%9}s"]
        lines.append("")
        t0=time.perf_counter(); t.set_lines(lines); await pilot.pause(); print(f"[N={N} blocks={len(lines)} lines] set: {time.perf_counter()-t0:.3f}s")
        t.scroll_end(animate=False); await pilot.pause()
        lat=[]
        for i in range(0,3640,40):
            a=time.perf_counter(); t.append_to_last("x"*40); t.scroll_end(animate=False); await pilot.pause(); lat.append(time.perf_counter()-a)
        lat.sort(); print(f"stream flush p50 {lat[len(lat)//2]*1000:.0f}ms p95 {lat[int(len(lat)*.95)]*1000:.0f}ms")
        a=time.perf_counter()
        for _ in range(20): t.scroll_page_up(animate=False); await pilot.pause()
        print(f"20 page-ups: {time.perf_counter()-a:.2f}s")
asyncio.run(main())
