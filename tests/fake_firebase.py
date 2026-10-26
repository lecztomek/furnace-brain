"""
Minimal in-memory Firebase Realtime Database REST server for tests:
GET (with shallow=true), PUT, PATCH (multi-path), DELETE, CORS.
Writes need ?auth=<secret>; reads under /furnace/<key>/ are public, like the
rules in the README. Run alone: python tests/fake_firebase.py <port> <secret>
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List


class FakeFirebase:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.root: Dict[str, Any] = {}
        self.lock = threading.Lock()
        self.requests: List[str] = []

    @staticmethod
    def _parts(path: str) -> List[str]:
        path = urllib.parse.unquote(path)
        if path.endswith(".json"):
            path = path[:-5]
        return [p for p in path.split("/") if p]

    def get(self, parts: List[str]) -> Any:
        node: Any = self.root
        for p in parts:
            if not isinstance(node, dict) or p not in node:
                return None
            node = node[p]
        return node

    def set(self, parts: List[str], value: Any) -> None:
        if not parts:
            self.root = value if isinstance(value, dict) else {}
            return
        node = self.root
        for p in parts[:-1]:
            if not isinstance(node.get(p), dict):
                node[p] = {}
            node = node[p]
        if value is None:
            node.pop(parts[-1], None)
        else:
            node[parts[-1]] = value

    def serve(self, port: int) -> ThreadingHTTPServer:
        db = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _req(self, method: str) -> None:
                url = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(url.query)
                parts = db._parts(url.path)
                authed = q.get("auth", [""])[0] == db.secret
                db.requests.append(f"{method} {url.path}")
                with db.lock:
                    if method == "GET":
                        if not authed and (len(parts) < 2 or parts[0] != "furnace"):
                            return self._send(401, {"error": "Permission denied"})
                        value = db.get(parts)
                        if q.get("shallow") == ["true"] and isinstance(value, dict):
                            value = {k: True for k in value}
                        return self._send(200, value)
                    if not authed:
                        return self._send(401, {"error": "Permission denied"})
                    body = None
                    if method in ("PUT", "PATCH"):
                        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
                    if method == "PUT":
                        db.set(parts, body)
                    elif method == "PATCH":
                        for k, v in body.items():
                            db.set(parts + db._parts(k), v)
                    elif method == "DELETE":
                        db.set(parts, None)
                    return self._send(200, body)

            def do_GET(self) -> None:
                self._req("GET")

            def do_PUT(self) -> None:
                self._req("PUT")

            def do_PATCH(self) -> None:
                self._req("PATCH")

            def do_DELETE(self) -> None:
                self._req("DELETE")

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


if __name__ == "__main__":
    fb = FakeFirebase(sys.argv[2])
    srv = fb.serve(int(sys.argv[1]))
    print(f"fake firebase on :{srv.server_address[1]}", flush=True)
    threading.Event().wait()
