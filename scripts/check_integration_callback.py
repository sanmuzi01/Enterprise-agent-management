"""真机联调前的自测：模拟飞书 / 钉钉给公网回调地址发一次“校验回调地址”请求，确认整条链路通了。

它做的事和飞书 / 钉钉开放平台“保存回调地址”时完全一样：用后台保存的凭证按两家的算法签名加密，
发到 {公网地址}/integrations/{provider}/events，检查返回的是不是平台要的应答。通过了，说明：
  内网穿透 / 反向代理能到后端、Host 白名单放行了这个域名、接入已启用、Token / Encrypt Key（aes_key）对得上。

用法：
  .venv\\Scripts\\python.exe scripts\\check_integration_callback.py feishu
  .venv\\Scripts\\python.exe scripts\\check_integration_callback.py dingtalk --url https://xxxx.trycloudflare.com
不传 --url 时用 .env 里的 INTEGRATION_PUBLIC_BASE_URL。不会产生任何业务数据。
"""
import argparse
import json
import os
import pathlib
import secrets
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CONSOLE_LOG_LEVEL", "CRITICAL")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="模拟飞书 / 钉钉校验回调地址")
    parser.add_argument("provider", choices=["feishu", "dingtalk"])
    parser.add_argument("--url", default=os.getenv("INTEGRATION_PUBLIC_BASE_URL", ""), help="公网访问后端的地址（经 nginx 时带 /api）")
    parser.add_argument("--local", action="store_true",
                        help="先在本机确认配置（凭证、启用、Token / Encrypt Key）：直接测本机后端，不经过公网")
    parser.add_argument("--local-url", default="http://127.0.0.1:8011", help="--local 时本机后端的地址")
    args = parser.parse_args()
    base = args.local_url.rstrip("/") if args.local else args.url.rstrip("/")
    if not args.local and not base.startswith("https://"):
        print("✗ 需要公网 HTTPS 地址：用 --url 指定，或在 .env 设置 INTEGRATION_PUBLIC_BASE_URL")
        return 2

    from models.init_db import SessionLocal
    from service.integrations import apps
    db = SessionLocal()
    try:
        app = apps.load_enabled_sync(db, args.provider)
    finally:
        db.close()
    if app is None:
        print(f"✗ {args.provider} 接入还没有在后台配置并启用")
        return 2

    url = f"{base}/integrations/{args.provider}/events"
    if args.provider == "feishu":
        from service.integrations.feishu import signature
        challenge = secrets.token_hex(8)
        plain = json.dumps({"type": "url_verification", "challenge": challenge, "token": app.verification_token})
        body = json.dumps({"encrypt": signature.encrypt(app.encrypt_key, plain)})
        params, expect = None, lambda data: data.get("challenge") == challenge
    else:
        from service.integrations.dingtalk import signature
        if not app.verification_token or not app.encrypt_key:
            print("✗ 钉钉事件订阅的签名 Token / aes_key 没有配置（只用机器人消息的话可以不配，这个自测也就不适用）")
            return 2
        encrypted = signature.encrypt(app.encrypt_key, app.app_id, json.dumps({"EventType": "check_url"}))
        ts, nonce = str(int(time.time() * 1000)), secrets.token_hex(4)
        params = {"msg_signature": signature.event_signature(app.verification_token, ts, nonce, encrypted),
                  "timestamp": ts, "nonce": nonce}
        body = json.dumps({"encrypt": encrypted})

        def expect(data):
            try:
                return signature.decrypt(app.encrypt_key, app.app_id, data.get("encrypt", "")) == "success"
            except Exception:  # noqa: BLE001
                return False

    print(f"→ POST {url}")
    started = time.time()
    try:
        response = requests.post(url, params=params, data=body.encode("utf-8"), timeout=10,
                                 headers={"Content-Type": "application/json"})
    except requests.RequestException as exc:
        print(f"✗ 连不上：{type(exc).__name__}。检查内网穿透 / 反向代理是否在运行、地址是否写对")
        return 1
    elapsed = time.time() - started
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code == 200 and expect(data):
        if args.local:
            print(f"✓ 本机配置正确（{elapsed:.2f} 秒）。下一步：配好公网 HTTPS 地址后，不带 --local 再跑一次")
            return 0
        print(f"✓ 通过（{elapsed:.2f} 秒）。可以把这个地址填到{('飞书' if args.provider == 'feishu' else '钉钉')}开放平台了：{url}")
        if elapsed > 2:
            print("  注意：响应超过 2 秒，飞书要求 3 秒内应答，网络慢时可能偶发失败，建议换更近的穿透节点或正式服务器")
        return 0
    hints = {400: "多半是 Host 白名单：把这个域名加进 .env 的 TRUSTED_HOSTS 后重启后端",
             401: "验签 / 解密没通过：核对后台填的 Verification Token、Encrypt Key（aes_key）和开放平台上的是否一致",
             404: "地址路径不对或接入没启用：经 nginx 时公网地址要带 /api；在后台勾选“启用接入”",
             502: "穿透 / 代理到不了后端：确认后端在运行、穿透指向的端口正确"}
    print(f"✗ 失败：HTTP {response.status_code} {response.text[:200]}")
    print("  " + hints.get(response.status_code, "看后端日志里同一时间的报错"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
