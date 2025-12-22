#!/usr/bin/env python3
# PY_GATEWAY_FURNACE_BRAIN
import sys, os, urllib.parse, http.client, mimetypes
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

FRONT_DIR = sys.argv[1]
BACKEND_HOST = sys.argv[2]
BACKEND_PORT = int(sys.argv[3])
GATEWAY_PORT = int(sys.argv[4])
BIND_HOST = sys.argv[5] if len(sys.argv) > 5 else "0.0.0.0"

os.chdir(FRONT_DIR)

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade"
}

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))

    def _proxy(self):
        conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=30)

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None

        headers = {k: v for (k, v) in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["Host"] = f"{BACKEND_HOST}:{BACKEND_PORT}"

        conn.request(self.command, self.path, body=body, headers=headers)
        resp = conn.getresponse()

        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() in HOP_BY_HOP:
                continue
            self.send_header(k, v)
        self.end_headers()

        data = resp.read()
        if data:
            self.wfile.write(data)
        conn.close()

    def do_OPTIONS(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        return super().do_OPTIONS()

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._proxy()

        parsed = urllib.parse.urlparse(self.path)
        req_path = parsed.path.lstrip("/")
        if req_path == "" or os.path.exists(req_path):
            return super().do_GET()

        self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(405, "POST only supported on /api/*")

    def do_PUT(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(405, "PUT only supported on /api/*")

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(405, "PATCH only supported on /api/*")

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(405, "DELETE only supported on /api/*")

mimetypes.init()

httpd = ThreadingHTTPServer((BIND_HOST, GATEWAY_PORT), Handler)
print(f"Gateway on {BIND_HOST}:{GATEWAY_PORT} (static: {FRONT_DIR}, api -> {BACKEND_HOST}:{BACKEND_PORT})")
httpd.serve_forever()
