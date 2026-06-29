import os
import json
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

DATABRICKS_ENDPOINT = "https://dbc-da9b23db-7eb7.cloud.databricks.com/serving-endpoints/contact-center-agent/invocations"
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))


class ProxyHandler(BaseHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html", "/smcu-chat-widget.html"):
            try:
                html_path = os.path.join(os.path.dirname(__file__), "smcu-chat-widget.html")
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("X-Frame-Options", "ALLOWALL")
                self.send_header("Content-Security-Policy", "frame-ancestors *")
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Widget HTML not found.")
        else:
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"SMCU proxy is running.")

    def do_POST(self):
        if self.path == "/chat":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            req = urllib.request.Request(
                DATABRICKS_ENDPOINT,
                data=body,
                headers={
                    "Authorization": f"Bearer {DATABRICKS_TOKEN}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    result = resp.read()
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(result)
            except urllib.error.HTTPError as e:
                error_body = e.read()
                print(f"Databricks error {e.code}: {error_body.decode()}")
                self.send_response(e.code)
                self.send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(error_body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"{args[0]} {args[1]}")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"SMCU proxy running on port {PORT}")
    server.serve_forever()
