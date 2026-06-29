import os
import json
import urllib.request
import urllib.error
import urllib.parse
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

DATABRICKS_ENDPOINT = "https://dbc-da9b23db-7eb7.cloud.databricks.com/serving-endpoints/contact-center-agent/invocations"
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
BOT_APP_ID = os.environ.get("BOT_APP_ID", "")
BOT_APP_SECRET = os.environ.get("BOT_APP_SECRET", "")
PORT = int(os.environ.get("PORT", 8080))

TEAMS_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
TEAMS_TOKEN_URL_FALLBACK = "https://login.microsoftonline.com/5cb7efef-7ee3-4f9f-88c1-836c7c65fcc5/oauth2/v2.0/token"
TEAMS_REPLY_BASE = "https://smba.trafficmanager.net/apis"

_bot_token_cache = {"token": None, "expires": 0}


def get_bot_token():
    now = time.time()
    if _bot_token_cache["token"] and now < _bot_token_cache["expires"] - 60:
        return _bot_token_cache["token"]
    data = (
        f"grant_type=client_credentials"
        f"&client_id={BOT_APP_ID}"
        f"&client_secret={urllib.parse.quote(BOT_APP_SECRET)}"
        f"&scope=https%3A%2F%2Fapi.botframework.com%2F.default"
    ).encode()
    req = urllib.request.Request(TEAMS_TOKEN_URL, data=data,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
    _bot_token_cache["token"] = body["access_token"]
    _bot_token_cache["expires"] = now + body.get("expires_in", 3600)
    return _bot_token_cache["token"]


def ask_databricks(question):
    body = json.dumps({"dataframe_records": [{"question": question}]}).encode()
    req = urllib.request.Request(
        DATABRICKS_ENDPOINT, data=body,
        headers={"Authorization": f"Bearer {DATABRICKS_TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    predictions = data.get("predictions", data)
    pred = predictions[0] if isinstance(predictions, list) else predictions
    answer = pred.get("answer") or pred.get("output") or "I couldn't get an answer."
    sources = pred.get("sources", [])
    return answer, sources


def send_teams_reply(service_url, conversation_id, activity_id, text):
    token = get_bot_token()
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities/{activity_id}"
    payload = json.dumps({
        "type": "message",
        "text": text,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req):
            pass
    except urllib.error.HTTPError as e:
        print(f"Teams reply error {e.code}: {e.read().decode()}")


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
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/api/messages":
            self._handle_teams(body)
        elif self.path == "/chat":
            self._handle_chat(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_teams(self, body):
        try:
            activity = json.loads(body)
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

        if activity.get("type") != "message":
            return

        question = (activity.get("text") or "").strip()
        if not question:
            return

        service_url = activity.get("serviceUrl", TEAMS_REPLY_BASE)
        conversation_id = activity.get("conversation", {}).get("id", "")
        activity_id = activity.get("id", "")

        try:
            answer, sources = ask_databricks(question)
            if sources:
                answer += "\n\n**Sources:**\n" + "\n".join(f"- {s}" for s in sources)
        except Exception as e:
            answer = f"Sorry, I'm having trouble connecting to the knowledge base. ({e})"

        send_teams_reply(service_url, conversation_id, activity_id, answer)

    def _handle_chat(self, body):
        req = urllib.request.Request(
            DATABRICKS_ENDPOINT, data=body,
            headers={"Authorization": f"Bearer {DATABRICKS_TOKEN}",
                     "Content-Type": "application/json"})
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

    def log_message(self, format, *args):
        print(f"{args[0]} {args[1]}")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"SMCU proxy running on port {PORT}")
    server.serve_forever()
