"""拉飞书通讯录：全部部门 + 每个部门的直属成员（按 open_id 去重）。

需要应用开通“获取通讯录基本信息”“获取用户手机号”权限，并把通讯录权限范围设为全员；
没开手机号权限时 mobile 为空，人就对不上账号，会全部列为“待手动绑定”。
"""
from typing import Any, Dict, List

from service.integrations.base import IntegrationError
from service.integrations.feishu import client

MAX_PAGES = 500          # 防止对方接口异常时无限翻页


def _pages(app, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    items, token = [], None
    for _ in range(MAX_PAGES):
        data = client.call(app, "GET", path, params={**params, **({"page_token": token} if token else {})})
        items.extend(data.get("items") or [])
        if not data.get("has_more"):
            return items
        token = data.get("page_token")
    raise IntegrationError("通讯录太大或翻页异常，已停止同步")


def fetch_organization(app) -> Dict[str, Any]:
    departments = _pages(app, "/open-apis/contact/v3/departments/0/children",
                         {"fetch_child": "true", "page_size": 50, "department_id_type": "open_department_id"})
    dept_ids = ["0"] + [d.get("open_department_id") for d in departments if d.get("open_department_id")]
    users: Dict[str, Dict[str, Any]] = {}
    for dept_id in dept_ids:
        for person in _pages(app, "/open-apis/contact/v3/users/find_by_department",
                             {"department_id": dept_id, "page_size": 50, "user_id_type": "open_id",
                              "department_id_type": "open_department_id"}):
            open_id = person.get("open_id")
            if not open_id or open_id in users:
                continue
            status = person.get("status") or {}
            users[open_id] = {"user_id": open_id, "union_id": person.get("union_id"), "name": person.get("name"),
                              "mobile": person.get("mobile"), "email": person.get("email"),
                              "department_ids": person.get("department_ids") or [],
                              "active": not status.get("is_resigned")}
    return {"tenant_id": app.app_id,
            "departments": [{"id": d["open_department_id"], "name": d.get("name"),
                             "parent_id": d.get("parent_department_id")} for d in departments if d.get("open_department_id")],
            "users": list(users.values())}
