import asyncio

from backend.worker.bootstrap import bootstrap
from backend.worker.consumer import run_forever


def main() -> None:
    bootstrap()
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
