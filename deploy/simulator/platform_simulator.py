"""飞书 / 钉钉开放平台仿真器：没有真实测试应用和公网 HTTPS 时，用它把整条链路在容器里跑一遍。

它同时扮演两个角色：
1. 开放平台的接口（平台通过 FEISHU_BASE_URL / DINGTALK_BASE_URL / DINGTALK_OAPI_URL 指过来）：换令牌、发消息、回复、
   查姓名、查群、取合并转发的子消息、通讯录、钉钉机器人单聊发送和 sessionWebhook。发出来的消息都记在“收件箱”里，
   相当于员工在飞书 / 钉钉客户端里看到的内容。
2. 推送事件的一方：像飞书 / 钉钉服务器一样，把员工发的消息签名、加密后 POST 到平台的回调地址。

签名和加密**按两家的文档独立实现**，不引用平台的代码——两边各写一遍还能对上，才说明平台的实现和文档一致。
只依赖标准库、cryptography、requests。控制接口都在 /_sim/ 下（给联调脚本用），不是开放平台的一部分。

仿真不了的：公网 HTTPS 和证书、飞书 / 钉钉服务器的真实网络、开放平台后台的权限审核与发布、
一键授权（授权页在 accounts.feishu.cn / login.dingtalk.com 上，需要真实浏览器登录）。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PORT = int(os.getenv("SIM_PORT", "9100"))
SELF_URL = os.getenv("SIM_SELF_URL", f"http://platform-sim:{PORT}")            # 平台访问仿真器的地址（容器网络里）
CALLBACK_BASE = os.getenv("SIM_CALLBACK_BASE", "http://web/api").rstrip("/")     # 平台的回调地址前缀（经 nginx，和公网部署一样）
BOT_OPEN_ID = "ou_sim_bot"

LOCK = threading.Lock()
STATE = {
    "feishu": {},           # app_id, app_secret, verification_token, encrypt_key
    "dingtalk": {},         # app_key, app_secret, robot_code, token, aes_key
    "outage": False,        # True：所有开放接口返回 503（模拟平台故障）
    "tokens": set(),
    "names": {},            # open_id / staff_id → 姓名（本企业员工）
    "org": [],              # 通讯录：[{open_id, name, mobile, department}]
    "chats": {},            # chat_id → {name, owner_id}
    "forwards": {},         # 合并转发 message_id → 子消息
    "sent": {},             # 平台推给员工的事件里的 message_id → {open_id, chat_id}（回复时知道发给谁）
    "history": {},          # 群 chat_id → 群里的消息（“获取会话历史消息”用）
    "inbox": [],            # 平台发出的消息：{seq, provider, to, chat_id, kind, text, at}
    "calls": [],            # 平台调过的开放接口（路径），排查用
}


# ------------------------------------------------------------------ 按文档实现的加密 / 签名

def feishu_encrypt(encrypt_key: str, plaintext: str) -> str:
    """飞书：key = SHA256(Encrypt Key)，AES-256-CBC，PKCS7，base64(IV + 密文)。"""
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + enc.update(data) + enc.finalize()).decode()


def feishu_signature(ts: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    """飞书：X-Lark-Signature = sha256(timestamp + nonce + encrypt_key + body) 的十六进制。"""
    return hashlib.sha256((ts + nonce + encrypt_key).encode() + body).hexdigest()


def dingtalk_robot_sign(ts: str, app_secret: str) -> str:
    """钉钉机器人回调：sign = base64(HmacSHA256(key=appSecret, timestamp + "\\n" + appSecret))。"""
    return base64.b64encode(hmac.new(app_secret.encode(), f"{ts}\n{app_secret}".encode(), hashlib.sha256).digest()).decode()


def dingtalk_encrypt(aes_key: str, app_key: str, plaintext: str) -> str:
    """钉钉事件订阅：key = base64(aes_key + "=")，IV = key 前 16 字节；明文 = 16 随机字节 + 4 字节长度 + 内容 + appKey，32 字节块 PKCS7。"""
    key = base64.b64decode(aes_key + "=")
    raw = os.urandom(16) + struct.pack(">I", len(plaintext.encode())) + plaintext.encode() + app_key.encode()
    pad = 32 - len(raw) % 32
    raw += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(enc.update(raw) + enc.finalize()).decode()


def dingtalk_event_signature(token: str, ts: str, nonce: str, encrypt: str) -> str:
    return hashlib.sha1("".join(sorted([token, ts, nonce, encrypt])).encode()).hexdigest()


# ------------------------------------------------------------------ 推送事件给平台（扮演飞书 / 钉钉服务器）

def _post(url, body: bytes, headers=None, params=None):
    try:
        r = requests.post(url, data=body, headers={"Content-Type": "application/json", **(headers or {})}, params=params, timeout=15)
    except requests.RequestException as exc:
        return {"status": 0, "body": f"连不上平台：{type(exc).__name__}"}
    try:
        data = r.json()
    except ValueError:
        data = r.text[:300]
    return {"status": r.status_code, "body": data}


def feishu_deliver(payload: dict, *, path="events", tamper=False, bad_signature=False, nonce=None, ts=None):
    app = STATE["feishu"]
    body = json.dumps({"encrypt": feishu_encrypt(app["encrypt_key"], json.dumps(payload, ensure_ascii=False))}).encode()
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or uuid.uuid4().hex
    sig = "0" * 64 if bad_signature else feishu_signature(ts, nonce, app["encrypt_key"], body)
    if tamper:
        body = body[:-3] + b'x"}'
    return {**_post(f"{CALLBACK_BASE}/integrations/feishu/{path}", body,
                    {"X-Lark-Request-Timestamp": ts, "X-Lark-Request-Nonce": nonce, "X-Lark-Signature": sig}), "nonce": nonce, "ts": ts}


def feishu_message_event(spec: dict) -> dict:
    app = STATE["feishu"]
    message_id = spec.get("message_id") or "om_" + uuid.uuid4().hex[:16]
    chat_type = spec.get("chat_type", "p2p")
    chat_id = spec.get("chat_id") or f"oc_p2p_{spec['open_id']}"
    msg_type = spec.get("message_type", "text")
    content = spec.get("content") or {"text": spec.get("text", "")}
    mentions = []
    if spec.get("at_bot"):
        content = {"text": "@_user_1 " + content.get("text", "")}
        mentions = [{"key": "@_user_1", "id": {"open_id": BOT_OPEN_ID}, "name": "企业助手"}]
    create_ms = int((time.time() - 60 * float(spec.get("minutes_ago", 0))) * 1000)
    with LOCK:
        STATE["sent"][message_id] = {"open_id": spec["open_id"], "chat_id": chat_id, "chat_type": chat_type}
        if chat_type == "group":
            STATE["history"].setdefault(chat_id, []).append({
                "message_id": message_id, "msg_type": msg_type, "create_time": str(create_ms), "chat_id": chat_id, "deleted": False,
                "sender": {"id": spec["open_id"], "id_type": "open_id", "sender_type": "user", "tenant_key": "sim_tenant"},
                "body": {"content": json.dumps(content, ensure_ascii=False)}, "mentions": mentions})
    return {"schema": "2.0",
            "header": {"event_id": spec.get("event_id") or uuid.uuid4().hex, "token": app["verification_token"],
                       "create_time": str(int(time.time() * 1000)), "event_type": "im.message.receive_v1",
                       "tenant_key": "sim_tenant", "app_id": app["app_id"]},
            "event": {"sender": {"sender_id": {"open_id": spec["open_id"], "union_id": "on_" + spec["open_id"]},
                                 "sender_type": "user", "tenant_key": "sim_tenant"},
                      "message": {"message_id": message_id, "chat_id": chat_id, "chat_type": chat_type, "message_type": msg_type,
                                  "content": json.dumps(content, ensure_ascii=False), "mentions": mentions,
                                  "create_time": str(create_ms)}}}


def feishu_plain_event(event_type: str, event: dict) -> dict:
    app = STATE["feishu"]
    return {"schema": "2.0", "header": {"event_id": uuid.uuid4().hex, "token": app["verification_token"],
                                        "event_type": event_type, "app_id": app["app_id"], "tenant_key": "sim_tenant"},
            "event": event}


def dingtalk_robot_deliver(spec: dict):
    app = STATE["dingtalk"]
    group = spec.get("group", False)
    session = uuid.uuid4().hex
    payload = {"msgtype": "text", "text": {"content": spec["text"]}, "msgId": spec.get("msg_id") or "msg" + uuid.uuid4().hex,
               "createAt": int(time.time() * 1000), "conversationType": "2" if group else "1",
               "conversationId": spec.get("conversation_id") or f"cid_{spec['staff_id']}", "senderId": "$:LWCP:" + spec["staff_id"],
               "senderNick": STATE["names"].get(spec["staff_id"], ""), "senderStaffId": spec["staff_id"], "chatbotUserId": "bot_sim",
               "robotCode": app["robot_code"], "isInAtList": True if group else None,
               "sessionWebhook": f"{SELF_URL}/robot/sendBySession?session={session}&to={spec['staff_id']}",
               "sessionWebhookExpiredTime": int((time.time() + 5400) * 1000)}
    ts = str(int(time.time() * 1000))
    body = json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False).encode()
    return _post(f"{CALLBACK_BASE}/integrations/dingtalk/events", body, {"timestamp": ts, "sign": dingtalk_robot_sign(ts, app["app_secret"])})


def dingtalk_event_deliver(event: dict):
    app = STATE["dingtalk"]
    encrypted = dingtalk_encrypt(app["aes_key"], app["app_key"], json.dumps(event))
    ts, nonce = str(int(time.time() * 1000)), secrets.token_hex(4)
    params = {"msg_signature": dingtalk_event_signature(app["token"], ts, nonce, encrypted), "timestamp": ts, "nonce": nonce}
    return _post(f"{CALLBACK_BASE}/integrations/dingtalk/events", json.dumps({"encrypt": encrypted}).encode(), params=params)


# ------------------------------------------------------------------ 收件箱

def record(provider: str, to: str, kind: str, text: str, chat_id: str = "") -> None:
    with LOCK:
        STATE["inbox"].append({"seq": len(STATE["inbox"]) + 1, "provider": provider, "to": to, "chat_id": chat_id,
                               "kind": kind, "text": text, "at": time.time()})


def _content_text(msg_type: str, content: str) -> str:
    try:
        data = json.loads(content or "{}")
    except ValueError:
        return content
    if msg_type == "text":
        return data.get("text", "")
    if msg_type == "interactive":
        return json.dumps(data, ensure_ascii=False)[:2000]
    return json.dumps(data, ensure_ascii=False)[:500]


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "PlatformSim/1.0"

    def log_message(self, fmt, *args):          # 安静一点
        pass

    def _send(self, status: int, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        path, query = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        body = self._json() if method in ("POST", "PUT") else {}
        if path.startswith("/_sim/"):
            return self._control(method, path, query, body)
        with LOCK:
            STATE["calls"].append(f"{method} {path}")
            outage = STATE["outage"]
        if outage:
            return self._send(503, {"code": 503, "msg": "service unavailable (simulated outage)", "message": "simulated outage"})
        if path.startswith("/open-apis/"):
            return self._feishu(method, path, query, body)
        return self._dingtalk(method, path, query, body)

    # -------- 飞书开放接口

    def _feishu_auth(self) -> bool:
        token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        return token in STATE["tokens"]

    def _feishu(self, method, path, query, body):
        app = STATE["feishu"]
        if path == "/open-apis/auth/v3/tenant_access_token/internal":
            if body.get("app_id") != app.get("app_id") or body.get("app_secret") != app.get("app_secret"):
                return self._send(200, {"code": 10014, "msg": "app secret invalid"})
            token = "t-" + secrets.token_hex(12)
            STATE["tokens"].add(token)
            return self._send(200, {"code": 0, "msg": "ok", "tenant_access_token": token, "expire": 7200})
        if not self._feishu_auth():
            return self._send(200, {"code": 99991663, "msg": "Invalid access token"})
        if path == "/open-apis/bot/v3/info":
            return self._send(200, {"code": 0, "bot": {"open_id": BOT_OPEN_ID, "app_name": "企业助手（仿真）", "activate_status": 2}})
        if path.startswith("/open-apis/contact/v3/users/find_by_department"):
            dept = query.get("department_id")
            items = [{"open_id": u["open_id"], "union_id": "on_" + u["open_id"], "name": u["name"], "mobile": u.get("mobile"),
                      "department_ids": [u.get("department", "0")], "status": {"is_resigned": False}}
                     for u in STATE["org"] if u.get("department", "0") == dept]
            return self._send(200, {"code": 0, "data": {"items": items, "has_more": False}})
        if path.startswith("/open-apis/contact/v3/departments/0/children"):
            depts = sorted({u.get("department", "0") for u in STATE["org"]} - {"0"})
            return self._send(200, {"code": 0, "data": {"items": [{"open_department_id": d, "name": d, "parent_department_id": "0"}
                                                                  for d in depts], "has_more": False}})
        if path.startswith("/open-apis/contact/v3/users/"):
            open_id = path.rsplit("/", 1)[-1]
            name = STATE["names"].get(open_id)
            if not name:
                return self._send(200, {"code": 41050, "msg": "no user authority"})     # 外部联系人：查不到
            return self._send(200, {"code": 0, "data": {"user": {"open_id": open_id, "name": name}}})
        if path == "/open-apis/im/v1/chats":
            return self._send(200, {"code": 0, "data": {"items": [{"chat_id": cid, "name": c["name"]} for cid, c in STATE["chats"].items()],
                                                        "has_more": False}})
        if path.startswith("/open-apis/im/v1/chats/") and path.endswith("/members"):
            chat = STATE["chats"].get(path.split("/")[-2])
            if not chat:
                return self._send(200, {"code": 232011, "msg": "chat not found"})
            return self._send(200, {"code": 0, "data": {"items": [{"member_id": m, "member_id_type": "open_id"} for m in chat.get("members", [])],
                                                        "has_more": False}})
        if path == "/open-apis/im/v1/messages" and method == "GET":
            start, end = int(query.get("start_time", 0)) * 1000, int(query.get("end_time", 2 ** 40)) * 1000
            items = [m for m in STATE["history"].get(query.get("container_id"), []) if start <= int(m["create_time"]) <= end]
            items.sort(key=lambda m: int(m["create_time"]), reverse=query.get("sort_type") == "ByCreateTimeDesc")
            return self._send(200, {"code": 0, "data": {"items": items[:int(query.get("page_size", 20))], "has_more": False}})
        if path.startswith("/open-apis/im/v1/chats/"):
            chat = STATE["chats"].get(path.rsplit("/", 1)[-1])
            if not chat:
                return self._send(200, {"code": 232011, "msg": "chat not found"})
            return self._send(200, {"code": 0, "data": {"name": chat["name"], "owner_id": chat["owner_id"], "owner_id_type": "open_id"}})
        if path == "/open-apis/im/v1/messages" and method == "POST":
            kind = query.get("receive_id_type")
            to = body.get("receive_id", "")
            text = _content_text(body.get("msg_type"), body.get("content"))
            record("feishu", to if kind == "open_id" else "", body.get("msg_type"), text, chat_id=to if kind == "chat_id" else "")
            return self._send(200, {"code": 0, "data": {"message_id": "om_bot_" + uuid.uuid4().hex[:10]}})
        if path.startswith("/open-apis/im/v1/messages/") and path.endswith("/reply"):
            origin = STATE["sent"].get(path.split("/")[-2], {})
            text = _content_text(body.get("msg_type"), body.get("content"))
            to = origin.get("open_id", "") if origin.get("chat_type") == "p2p" else ""
            record("feishu", to, body.get("msg_type"), text, chat_id=origin.get("chat_id", ""))
            return self._send(200, {"code": 0, "data": {"message_id": "om_bot_" + uuid.uuid4().hex[:10]}})
        if path.startswith("/open-apis/im/v1/messages/") and method == "GET":
            message_id = path.rsplit("/", 1)[-1]
            items = STATE["forwards"].get(message_id)
            if items is None:
                return self._send(200, {"code": 230002, "msg": "message not found"})
            return self._send(200, {"code": 0, "data": {"items": items}})
        return self._send(404, {"code": 404, "msg": f"仿真器没有实现 {method} {path}"})

    # -------- 钉钉开放接口

    def _dingtalk(self, method, path, query, body):
        app = STATE["dingtalk"]
        if path == "/v1.0/oauth2/accessToken":
            if body.get("appKey") != app.get("app_key") or body.get("appSecret") != app.get("app_secret"):
                return self._send(400, {"code": "invalidClientId", "message": "appKey 或 appSecret 不正确"})
            token = "dt-" + secrets.token_hex(12)
            STATE["tokens"].add(token)
            return self._send(200, {"accessToken": token, "expireIn": 7200})
        if path == "/robot/sendBySession":
            record("dingtalk", query.get("to", ""), body.get("msgtype"), (body.get("text") or {}).get("content", "") or
                   json.dumps(body.get("markdown") or body, ensure_ascii=False))
            return self._send(200, {"errcode": 0, "errmsg": "ok"})
        if path.startswith("/topapi/"):
            if query.get("access_token") not in STATE["tokens"]:
                return self._send(200, {"errcode": 40014, "errmsg": "不合法的access_token"})
            if path == "/topapi/v2/user/get":
                name = STATE["names"].get(body.get("userid"))
                return self._send(200, {"errcode": 0, "result": {"userid": body.get("userid"), "name": name or ""}})
            return self._send(200, {"errcode": 0, "result": {}})
        if self.headers.get("x-acs-dingtalk-access-token") not in STATE["tokens"]:
            return self._send(401, {"code": "InvalidAuthentication", "message": "不合法的access_token"})
        if path == "/v1.0/robot/oToMessages/batchSend":
            param = json.loads(body.get("msgParam") or "{}")
            for staff in body.get("userIds") or []:
                record("dingtalk", staff, body.get("msgKey"), param.get("content") or param.get("text") or json.dumps(param, ensure_ascii=False))
            return self._send(200, {"processQueryKey": uuid.uuid4().hex})
        return self._send(404, {"code": "NotFound", "message": f"仿真器没有实现 {method} {path}"})

    # -------- 控制接口（联调脚本用）

    def _control(self, method, path, query, body):
        if path == "/_sim/health":
            return self._send(200, {"ok": True})
        if path == "/_sim/setup":
            with LOCK:
                if "feishu" in body:
                    STATE["feishu"] = body["feishu"]
                if "dingtalk" in body:
                    STATE["dingtalk"] = body["dingtalk"]
                STATE["names"].update(body.get("names") or {})
                STATE["org"] = body.get("org", STATE["org"])
                STATE["chats"].update(body.get("chats") or {})
            return self._send(200, {"ok": True})
        if path == "/_sim/outage":
            with LOCK:
                STATE["outage"] = bool(body.get("on"))
            return self._send(200, {"outage": STATE["outage"]})
        if path == "/_sim/inbox":
            after = int(query.get("after", 0))
            with LOCK:
                items = [m for m in STATE["inbox"] if m["seq"] > after and
                         (not query.get("to") or m["to"] == query["to"]) and (not query.get("chat_id") or m["chat_id"] == query["chat_id"])]
                last = len(STATE["inbox"])
            return self._send(200, {"items": items, "last": last})
        if path == "/_sim/calls":
            with LOCK:
                return self._send(200, {"calls": STATE["calls"][-200:]})
        if path == "/_sim/feishu/verify":
            app = STATE["feishu"]
            challenge = secrets.token_hex(8)
            plain = {"type": "url_verification", "challenge": challenge, "token": app["verification_token"]}
            body_ = json.dumps({"encrypt": feishu_encrypt(app["encrypt_key"], json.dumps(plain))}).encode()
            result = _post(f"{CALLBACK_BASE}/integrations/feishu/events", body_)
            return self._send(200, {**result, "expected": challenge})
        if path == "/_sim/feishu/message":
            event = feishu_message_event(body)
            result = feishu_deliver(event, tamper=body.get("tamper", False), bad_signature=body.get("bad_signature", False),
                                    nonce=body.get("nonce"), ts=body.get("ts"))
            return self._send(200, {**result, "message_id": event["event"]["message"]["message_id"],
                                    "event_id": event["header"]["event_id"]})
        if path == "/_sim/feishu/forward":
            message_id = "om_fwd_" + uuid.uuid4().hex[:10]
            now = time.time()
            items = [{"message_id": message_id, "msg_type": "merge_forward", "create_time": str(int(now * 1000)),
                      "body": {"content": json.dumps({"content": "Merged and Forwarded Message"})}}]
            for i, line in enumerate(body["lines"]):
                items.append({"message_id": f"{message_id}_{i}", "upper_message_id": message_id, "msg_type": line.get("msg_type", "text"),
                              "create_time": str(int((now - 60 * float(line.get("minutes_ago", 0))) * 1000)),
                              "sender": {"id": line["open_id"], "id_type": "open_id", "sender_type": "user", "tenant_key": "sim_tenant"},
                              "body": {"content": json.dumps(line.get("content") or {"text": line.get("text", "")}, ensure_ascii=False)}})
            with LOCK:
                STATE["forwards"][message_id] = items
            event = feishu_message_event({"open_id": body["open_id"], "message_id": message_id, "message_type": "merge_forward",
                                          "content": {"content": "Merged and Forwarded Message"}})
            return self._send(200, {**feishu_deliver(event), "message_id": message_id})
        if path == "/_sim/feishu/recall":
            with LOCK:
                for items in STATE["history"].values():
                    for item in items:
                        if item["message_id"] == body["message_id"]:
                            item["deleted"] = True
            event = feishu_plain_event("im.message.recalled_v1", {"message_id": body["message_id"], "chat_id": body.get("chat_id"),
                                                                  "recall_time": str(int(time.time() * 1000)), "recall_type": "message_owner"})
            return self._send(200, feishu_deliver(event))
        if path == "/_sim/feishu/user_left":
            event = feishu_plain_event("contact.user.deleted_v3", {"object": {"open_id": body["open_id"]}})
            return self._send(200, feishu_deliver(event))
        if path == "/_sim/feishu/replay":
            # 把同一个请求原样再发一次（同一个 nonce、同一个时间戳）：平台必须拒绝
            event = feishu_message_event(body)
            first = feishu_deliver(event, nonce=body.get("nonce") or uuid.uuid4().hex)
            second = feishu_deliver(event, nonce=first["nonce"], ts=first["ts"])
            return self._send(200, {"first": first, "second": second})
        if path == "/_sim/dingtalk/message":
            return self._send(200, dingtalk_robot_deliver(body))
        if path == "/_sim/dingtalk/verify":
            return self._send(200, dingtalk_event_deliver({"EventType": "check_url"}))
        return self._send(404, {"error": f"没有这个控制接口 {path}"})


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"飞书 / 钉钉开放平台仿真器已启动：端口 {PORT}，平台回调地址 {CALLBACK_BASE}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
