"""Application-level middleware: same-origin (CSRF) guard for POST routes."""
from urllib.parse import urlparse

from fastapi.responses import HTMLResponse


def _authority(netloc_with_scheme: str):
    """Return (host, port) for a URL authority, defaulting the port by scheme."""
    parsed = urlparse(netloc_with_scheme)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname, port


class SameOriginGuard:
    """CSRF defence for the browser-facing POST routes.

    Every state-changing request the UI makes is a same-origin form post or an
    htmx post from a page this app served, so requiring a same-origin
    ``Origin``/``Referer`` blocks cross-site form posts without threading a
    token through every template.

    The comparison target is the request's ``Host`` header, not the server's
    socket address: under uvicorn the socket is bound to 0.0.0.0, while a real
    browser sends ``Host: localhost:8000`` — comparing against the socket would
    reject every legitimate request. A cross-site attacker can spoof neither
    the victim's ``Host`` nor their own ``Origin``.

    Requests carrying neither header (curl, health probes, tests) are allowed,
    because a browser always sends one of them on a cross-site post — which is
    exactly the request this guard exists to reject.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") == "POST":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers", [])}
            origin = headers.get("origin") or headers.get("referer") or ""
            if origin:
                claimed = _authority(origin if "://" in origin else f"http://{origin}")
                host_header = headers.get("host")
                if host_header:
                    allowed = _authority(f"http://{host_header}")
                else:
                    server = scope.get("server") or ("localhost", 80)
                    allowed = (server[0] or "localhost", server[1] or 80)
                if claimed != allowed:
                    response = HTMLResponse("cross-origin request blocked",
                                            status_code=403)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)