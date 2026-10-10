"""钉钉回调的验签与加解密。

1. 机器人消息回调（HTTP 模式）：请求头 timestamp（毫秒）+ sign，
   sign = Base64( HmacSHA256( key=AppSecret, msg=timestamp + "\n" + AppSecret ) )。
2. 事件订阅（HTTP 推送）：URL 参数 msg_signature / timestamp / nonce，请求体 {"encrypt": "..."}；
   签名 = SHA-1( 把 [token, timestamp, nonce, encrypt] 按字典序排序后直接拼接 )；
   AES 密钥 = Base64Decode(aes_key + "=")（32 字节），IV = 密钥前 16 字节，AES-256-CBC，PKCS#7 按 32 字节补齐；
   明文 = 16 字节随机串 + 4 字节网络序消息长度 + 消息 + AppKey（corpId / suiteKey）。
   应答也必须加密：{"msg_signature", "timeStamp", "nonce", "encrypt"}，内容是 "success"。
"""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from typing import Dict

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from service.integrations.base import VerificationError

BLOCK = 32


def robot_sign(timestamp: str, app_secret: str) -> str:
    digest = hmac.new(app_secret.encode("utf-8"), f"{timestamp}\n{app_secret}".encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def same(expected: str, given: str) -> bool:
    return bool(given) and hmac.compare_digest(expected, given.strip())


def event_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce, encrypt])).encode("utf-8")).hexdigest()


def _key(aes_key: str) -> bytes:
    try:
        key = base64.b64decode(aes_key + "=")
    except ValueError:
        raise VerificationError("aes_key 格式不对") from None
    if len(key) != 32:
        raise VerificationError("aes_key 长度不对（应为 43 位）")
    return key


def decrypt(aes_key: str, app_key: str, encrypted: str) -> str:
    key = _key(aes_key)
    try:
        data = base64.b64decode(encrypted)
        if not data or len(data) % 16:
            raise ValueError("bad length")
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        plain = decryptor.update(data) + decryptor.finalize()
        pad = plain[-1]
        if pad < 1 or pad > BLOCK:
            raise ValueError("bad padding")
        plain = plain[:-pad]
        length = struct.unpack(">I", plain[16:20])[0]
        message, tail = plain[20:20 + length], plain[20 + length:]
    except Exception:  # noqa: BLE001 —— 解不开就是密钥不对或内容被改过
        raise VerificationError("解密失败") from None
    if tail.decode("utf-8", "replace") != app_key:
        raise VerificationError("回调不是发给这个应用的")
    return message.decode("utf-8")


def encrypt(aes_key: str, app_key: str, plaintext: str) -> str:
    key = _key(aes_key)
    message = plaintext.encode("utf-8")
    raw = os.urandom(16) + struct.pack(">I", len(message)) + message + app_key.encode("utf-8")
    pad = BLOCK - len(raw) % BLOCK
    raw += bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(raw) + encryptor.finalize()).decode("ascii")


def encrypted_reply(token: str, aes_key: str, app_key: str, plaintext: str = "success") -> Dict[str, str]:
    timestamp, nonce = str(int(time.time() * 1000)), secrets.token_hex(8)
    body = encrypt(aes_key, app_key, plaintext)
    return {"msg_signature": event_signature(token, timestamp, nonce, body), "timeStamp": timestamp, "nonce": nonce,
            "encrypt": body}
