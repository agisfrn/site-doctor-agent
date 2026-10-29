"""A tiny configurable HTTP server for browser tests: per-path status, headers, body or delay."""

import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def serve_routes(routes: dict):
    """routes: path -> dict(status=200, headers={}, body=b"" | str, delay=0)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = routes.get(self.path.split("?")[0])
            if route is None:
                self.send_error(404)
                return
            time.sleep(route.get("delay", 0))
            body = route.get("body", b"")
            body = body.encode() if isinstance(body, str) else body
            self.send_response(route.get("status", 200))
            for k, v in route.get("headers", {"Content-Type": "text/html"}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
