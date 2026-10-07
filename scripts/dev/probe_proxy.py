"""只读探针：验证"系统代理会不会劫持对 127.0.0.1 测试端口的请求"。

背景：tests/hub/helpers.py 的 do_request() 用裸 urllib.request.urlopen，
而本机 getproxies() = {'http': 'http://127.0.0.1:7897', ...}（来自 Windows 注册表）。
若 urllib 把 http://127.0.0.1:<测试端口>/ 也发给 7897 代理，就会得到
"不是被测 hub 返回的 403"——正是全量跑里 SmokeTest 全 403 且无 [hub ...] 日志的原因。
"""
import http.server
import threading
import urllib.error
import urllib.request


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


PORT = 18997
srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

print("getproxies =", urllib.request.getproxies(), flush=True)
print("no_proxy =", __import__("os").environ.get("no_proxy"), flush=True)

url = f"http://127.0.0.1:{PORT}/x"
try:
    r = urllib.request.urlopen(url, timeout=10)
    print("默认 opener ->", r.status, r.read()[:200])
except urllib.error.HTTPError as e:
    print("默认 opener -> HTTPError", e.code, e.headers.get("Server"), e.read()[:300])
except Exception as e:
    print("默认 opener -> 异常", type(e).__name__, e)

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    r = op.open(url, timeout=10)
    print("禁用代理 opener ->", r.status, r.read()[:200])
except urllib.error.HTTPError as e:
    print("禁用代理 opener -> HTTPError", e.code, e.read()[:300])
except Exception as e:
    print("禁用代理 opener -> 异常", type(e).__name__, e)

srv.shutdown()
