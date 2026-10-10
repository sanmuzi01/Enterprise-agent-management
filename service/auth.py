from utils.timeutil import utcnow
from datetime import timedelta
from typing import Optional
import calendar

from jose import jwt, JWTError
from dotenv import load_dotenv
import os

# 加载环境变量
load_dotenv()

# 从 .env 读取 JWT 配置
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 60))


def _get_secret_key() -> str:
    if not SECRET_KEY:
        raise RuntimeError("缺少环境变量 JWT_SECRET_KEY，请在 .env 中配置 JWT 签名密钥")
    return SECRET_KEY

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    生成 JWT token
    :param data: 要存入 token 的数据，比如 {"user_id": 1, "username": "test"}
    :param expires_delta: 过期时间增量，不传则用默认值
    :return: JWT token 字符串
    """
    # 复制一份数据，避免修改原 dict
    to_encode = data.copy()
    # 计算过期时间
    if expires_delta:
        expire = utcnow() + expires_delta
    else:
        expire = utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # 把过期时间加到 payload；auth_time 是最初登录的时刻，续期时原样带过去（见 service/session_renewal.py）
    to_encode.update({"exp": expire})
    to_encode.setdefault("auth_time", calendar.timegm(utcnow().utctimetuple()))
    # 生成并返回 token
    return jwt.encode(to_encode, _get_secret_key(), algorithm=ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    """
    解析 JWT token
    :param token: JWT token 字符串
    :return: 解析出的 payload 字典；解析失败返回 None
    """
    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=[ALGORITHM])
        return payload
    except (JWTError, RuntimeError):
        return None
