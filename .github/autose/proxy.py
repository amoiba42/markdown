"""Key-injecting proxy for the Anthropic API.

Runs in its own container (sandbox.start). The agent sandbox has no internet and no API key: Claude Code
inside it talks to this proxy with a placeholder key, and the proxy forwards the request to
api.anthropic.com with the real key added. The key never enters the sandbox (ADR-006, ADR-010).

Only /v1/ paths are forwarded. Responses, including streamed ones, are passed through chunk by chunk.
"""

import http.client
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "api.anthropic.com"
KEY = os.environ["ANTHROPIC_API_KEY"]
DROP_REQUEST = {"host", "x-api-key", "authorization", "content-length", "connection", "accept-encoding"}
DROP_RESPONSE = {"transfer-encoding", "content-length", "connection", "content-encoding"}
CONTEXT = ssl.create_default_context()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):            # one short line per request, never headers
        print(f"proxy {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}", flush=True)

    def _forward(self):
        if not self.path.startswith("/v1/"):
            self.send_error(403, "only /v1/ is forwarded")
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_REQUEST}
        headers["x-api-key"] = KEY
        conn = http.client.HTTPSConnection(UPSTREAM, 443, context=CONTEXT, timeout=600)
        try:
            conn.request(self.command, self.path, body=body or None, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in DROP_RESPONSE:
                    self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while chunk := resp.read1(65536):
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        finally:
            conn.close()

    do_GET = do_POST = _forward


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
