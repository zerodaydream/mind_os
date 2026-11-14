import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    # shortcut: in-memory per-IP, single-process only; upgrade when multi-replica

    def __init__(self, app, *, max_per_minute: int = 60):
        super().__init__(app)
        self._max = max_per_minute
        self._hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host if request.client else "local"
        now = time.monotonic()
        window = [t for t in self._hits[ip] if now - t < 60.0]
        if len(window) >= self._max:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        window.append(now)
        self._hits[ip] = window
        return await call_next(request)
