"""E2E 冒烟测试的数据准备：直接建库里的用户（跳过短信注册，图快），
其余（模型配置、知识库空间、文档、Agent、Skill）都走真实 HTTP 接口——
这些接口本身就是要被验收的对象，不能绕过去。

只在 `scripts/e2e_smoke.py`（编排脚本，另开一个进程跑 tests_e2e/e2e_server.py）里用；
直接对着已经在跑的后端发请求，不导入 FasdtApi.main，避免和后端进程抢应用实例。
"""
import time
from typing import Dict, Optional

import requests

DEFAULT_PASSWORD = "E2eSmoke-Passw0rd!"


def create_db_user(name: str, *, admin: bool = False, password: str = DEFAULT_PASSWORD) -> Dict:
    """直接落库建用户（bcrypt 哈希），不走注册接口——不需要短信验证码这一步。"""
    from models.init_db import SessionLocal
    from models.user_dao import create_user as dao_create_user
    from service.auth_service import hash_password

    db = SessionLocal()
    try:
        user = dao_create_user(db, name=name, password=hash_password(password), age=30, phone=None)
        return {"id": user.id, "name": name, "password": password, "admin": admin}
    finally:
        db.close()


def grant_admin(name: str) -> None:
    """把用户名加进 ADMIN_USER_NAMES，让它在这次进程里被当管理员（不改数据库角色表）。"""
    import os
    current = os.environ.get("ADMIN_USER_NAMES", "admin")
    names = {n.strip() for n in current.split(",") if n.strip()}
    names.add(name)
    os.environ["ADMIN_USER_NAMES"] = ",".join(sorted(names))


class ApiClient:
    """薄薄一层 requests 封装：带 token、抛出可读的错误信息。"""

    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def login(self, name: str, password: str) -> str:
        r = self.session.post(f"{self.base_url}/auth/token", json={"name": name, "password": password})
        r.raise_for_status()
        self.token = r.json()["access_token"]
        return self.token

    def request(self, method: str, path: str, **kwargs):
        r = self.session.request(method, f"{self.base_url}{path}", headers=self._headers(), **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)


def wait_for_health(base_url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/health", timeout=3)
            if r.status_code == 200:
                return
        except requests.RequestException as e:  # noqa: BLE001
            last_error = e
        time.sleep(0.5)
    raise TimeoutError(f"后端 {base_url}/health 在 {timeout}s 内没起来: {last_error}")


def wait_for_http(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=3)
            if r.status_code < 500:
                return
        except requests.RequestException as e:  # noqa: BLE001
            last_error = e
        time.sleep(0.5)
    raise TimeoutError(f"{url} 在 {timeout}s 内没起来: {last_error}")


def ensure_llm_configs(client: ApiClient, *, chat_model: str = "glm-4",
                       embedding_model: str = "text-embedding-3-small") -> None:
    """配好聊天 + 资料检索两个模型——Key 是假的，反正 LLM/Embedding 客户端已经被换成假实现，
    不会真的发出网络请求。"""
    client.post("/llm_config", json={"model_name": chat_model, "api_key": "fake-key-for-e2e"})
    client.post("/llm_config", json={"model_name": embedding_model, "api_key": "fake-key-for-e2e"})


def create_space_with_document(client: ApiClient, *, name: str, doc_text: str,
                               filename: str = "e2e_doc.md", timeout: float = 30.0) -> Dict:
    """建一个知识库空间，塞一份文档，等它真正索引完（status=done）才返回——
    不等的话，紧接着的检索/绑定可能命中一份还没向量化的文档。"""
    space = client.post("/knowledge-spaces", json={"name": name, "purpose": "e2e-smoke"})
    space_id = space["id"]
    client.post(
        f"/knowledge-spaces/{space_id}/documents",
        files={"file": (filename, doc_text.encode("utf-8"), "text/markdown")},
    )
    deadline = time.time() + timeout
    doc = None
    while time.time() < deadline:
        docs = client.get(f"/knowledge-spaces/{space_id}/documents")
        items = docs.get("items", docs) if isinstance(docs, dict) else docs
        if items:
            doc = items[0]
            if doc.get("status") == "done":
                return {"space_id": space_id, "doc": doc}
            if doc.get("status") == "failed":
                raise RuntimeError(f"文档索引失败: {doc}")
        time.sleep(0.5)
    raise TimeoutError(f"文档在 {timeout}s 内没索引完，最后状态: {doc}")


def create_public_skill(client: ApiClient, *, name: str, system_prompt: str) -> Dict:
    """管理员建一个公开技能（供普通用户在创建助手时勾选绑定）。"""
    return client.post("/skill/", json={
        "name": name,
        "description": "E2E 冒烟测试用的技能",
        "system_prompt": system_prompt,
        "tool_names": [],
        "is_public": 1,
    })


def purge_e2e_data(name_prefix: str = "e2e_") -> int:
    """删掉本次（以及历史上任何一次）冒烟测试建的用户及其级联数据。

    跑一次 scripts/e2e_smoke.py 会建用户、Agent、Skill、知识库空间和文档；不清理的话每跑一次
    本地库就多一批。级联删除涉及十几张表、外键关系容易漏，不重新写一遍——直接复用
    tests/_route_client.py 里已经验证过、踩过坑改对的 `_purge_users`（按用户名前缀删 `user`
    表并级联清所有关联表，每条 DELETE 独立提交，某张表撞 FK 不连累其它表）。
    """
    from tests._route_client import _purge_users
    return _purge_users(f"name LIKE '{name_prefix}%'")
