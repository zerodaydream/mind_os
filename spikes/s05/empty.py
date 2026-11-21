import asyncio,time
from textual.app import App
from textual.widgets import Static
class A(App):
    def compose(self): yield Static("x")
async def main():
    async with A().run_test(size=(120,40)) as p:
        l=[]
        for _ in range(50):
            a=time.perf_counter(); await p.pause(); l.append(time.perf_counter()-a)
        l.sort(); print(f"bare pilot.pause() p50 {l[25]*1000:.0f}ms p95 {l[47]*1000:.0f}ms")
asyncio.run(main())
