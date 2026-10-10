"""外部智能体服务的最小示例（只用标准库，复制就能跑）。

把你自己写好的 Agent（LangGraph、AutoGen、自研循环……）接进平台，只需要提供一个 HTTP 接口：
  1. 校验平台的签名（X-Agent-Signature），拒绝不是平台发来的请求；
  2. 读取 message / history / user，调用你自己的 Agent；
  3. 返回 JSON {"answer": "..."}，或者返回事件流（text/event-stream）逐段输出。

运行：
    set AGENT_SIGNING_SECRET=<平台上生成的签名密钥>
    python examples/external_agent_example.py
然后在平台“组织管理 → 智能体管理”里新建智能体，运行方式选“接入我们自己部署的智能体服务”，
地址填 http://<这台机器>:9100/chat（内网地址需要先加入 EXTERNAL_AGENT_ALLOWED_HOSTS）。
协议全文见 docs/external-agent-protocol.md。
"""
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOLERANCE_SECONDS = 300          # 时间戳偏差超过 5 分钟的请求一律拒绝（防重放）


def verify(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """和平台的签名算法一致：HMAC-SHA256(密钥, 时间戳 + "." + 请求体)。"""
    try:
        if abs(time.time() - float(timestamp)) > TOLERANCE_SECONDS:
            return False
    except (TypeError, ValueError):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def my_agent(message: str, history: list, user: dict, knowledge: dict | None) -> str:
    """在这里调用你自己的 Agent。示例只是回显。"""
    who = user.get("name") or "同事"
    depts = "、".join(d["name"] for d in user.get("departments", [])) or "未分配部门"
    return f"{who}（{depts}）你好，我收到了：{message}"


def make_handler(secret: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not verify(secret, self.headers.get("X-Agent-Timestamp", ""), body, self.headers.get("X-Agent-Signature", "")):
                return self._json(401, {"error": "invalid signature"})
            request = json.loads(body)
            if request.get("type") == "ping":                     # 平台“测试连接”
                return self._json(200, {"answer": "pong"})
            answer = my_agent(request["message"], request.get("history", []), request.get("user", {}), request.get("knowledge"))
            if "text/event-stream" in self.headers.get("Accept", "") and request.get("stream_demo"):
                return self._stream(answer)
            self._json(200, {"answer": answer, "usage": {"total_tokens": len(answer)}})

        def _json(self, status, data):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _stream(self, answer):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [{"type": "delta", "text": answer[i:i + 8]} for i in range(0, len(answer), 8)] + [{"type": "done"}]
            for event in events:
                self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()

    return Handler


def make_server(secret: str, port: int = 9100, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(secret))


if __name__ == "__main__":
    signing_secret = os.environ.get("AGENT_SIGNING_SECRET", "")
    if not signing_secret:
        raise SystemExit("请先设置环境变量 AGENT_SIGNING_SECRET（平台上生成的签名密钥）")
    print("外部智能体示例监听 http://127.0.0.1:9100/chat")
    make_server(signing_secret).serve_forever()
