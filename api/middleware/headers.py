"""Baseline security response headers on every response."""

from starlette.middleware.base import BaseHTTPMiddleware

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # the chat UI is self-contained except for the Google Fonts stylesheet;
    # everything else is same-origin
    "Content-Security-Policy": (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    ),
}


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for key, value in _HEADERS.items():
            response.headers.setdefault(key, value)
        return response
