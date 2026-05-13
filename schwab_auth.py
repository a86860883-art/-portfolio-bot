"""
嘉信初次認證小工具
首次執行 + 每 7 天需重新執行一次（Schwab refresh token 效期限制）
用法：python schwab_auth.py
"""
import os
import json
import base64
import urllib.parse
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import httpx

APP_KEY    = os.environ["SCHWAB_APP_KEY"]
APP_SECRET = os.environ["SCHWAB_APP_SECRET"]
REDIRECT   = "https://127.0.0.1:8182"
TOKEN_FILE = Path(os.environ.get("SCHWAB_TOKEN_FILE", "schwab_token.json"))

auth_code = None

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h2>認證完成，請回到 terminal</h2>")
    def log_message(self, *args): pass


def main():
    # 1. 開啟 Schwab 授權頁
    auth_url = (
        "https://api.schwabapi.com/v1/oauth/authorize"
        f"?client_id={APP_KEY}&redirect_uri={urllib.parse.quote(REDIRECT)}"
    )
    print(f"\n請在瀏覽器登入嘉信帳號並授權：\n{auth_url}\n")
    webbrowser.open(auth_url)

    # 2. 本地 server 接收 callback
    print("等待授權回調（port 8182）...")
    server = HTTPServer(("127.0.0.1", 8182), Handler)
    server.handle_request()

    if not auth_code:
        print("❌ 未取得授權碼，請重試")
        return

    # 3. 用授權碼換 token
    credentials = base64.b64encode(f"{APP_KEY}:{APP_SECRET}".encode()).decode()
    resp = httpx.post(
        "https://api.schwabapi.com/v1/oauth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": REDIRECT,
        },
    )
    resp.raise_for_status()
    token = resp.json()
    TOKEN_FILE.write_text(json.dumps(token, indent=2))
    print(f"\n✅ 認證成功！Token 已儲存至 {TOKEN_FILE}")
    print("⚠️  請每 7 天重新執行此程式更新 Refresh Token")


if __name__ == "__main__":
    main()
