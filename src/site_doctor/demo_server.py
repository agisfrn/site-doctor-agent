"""Serve the bundled demo site (or any folder) on a free localhost port."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEMO_DIR = Path(__file__).parent / "demo_site"


class _Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/error":
            self.send_error(500, "Planted server error")
            return
        super().do_GET()

    def log_message(self, format, *args):
        pass


@contextmanager
def serve(directory: Path = DEMO_DIR) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
