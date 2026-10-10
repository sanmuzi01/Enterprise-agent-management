"""飞书回调的验签与解密（按飞书开放平台“事件订阅 → 接收事件”文档）。

- 签名：X-Lark-Signature = SHA-256( X-Lark-Request-Timestamp + X-Lark-Request-Nonce + Encrypt Key + 原始请求体 ) 的十六进制。
  必须用原始请求体（不能先解析再序列化），比对时忽略大小写、用常量时间比较。
- 解密：请求体 {"encrypt": "<base64>"}；密钥 = SHA-256(Encrypt Key)（32 字节）；base64 解码后前 16 字节是 IV，
  其余是 AES-256-CBC 密文；去掉 PKCS#7 填充后是 UTF-8 的 JSON。
- 旧版消息卡片回调（没走事件订阅的）签名：SHA-1( timestamp + nonce + Verification Token + 原始请求体 )。
"""
import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from service.integrations.base import VerificationError


def event_signature(timestamp: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    return hashlib.sha256((timestamp + nonce + encrypt_key).encode("utf-8") + body).hexdigest()


def legacy_card_signature(timestamp: str, nonce: str, verification_token: str, body: bytes) -> str:
    return hashlib.sha1((timestamp + nonce + verification_token).encode("utf-8") + body).hexdigest()


def same(expected: str, given: str) -> bool:
    return bool(given) and hmac.compare_digest(expected.lower(), given.strip().lower())


def _key(encrypt_key: str) -> bytes:
    return hashlib.sha256(encrypt_key.encode("utf-8")).digest()


def decrypt(encrypt_key: str, encrypted: str) -> str:
    try:
        raw = base64.b64decode(encrypted)
        iv, data = raw[:16], raw[16:]
        if len(iv) != 16 or not data or len(data) % 16:
            raise ValueError("bad length")
        decryptor = Cipher(algorithms.AES(_key(encrypt_key)), modes.CBC(iv)).decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    except Exception:  # noqa: BLE001 —— 解不开就是密钥不对或内容被改过
        raise VerificationError("解密失败") from None


def encrypt(encrypt_key: str, plaintext: str, iv: bytes = None) -> str:
    """和飞书一样的加密方式。平台自己用不到，测试用它构造“飞书发来的”加密回调。"""
    iv = iv or os.urandom(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_key(encrypt_key)), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode("ascii")
