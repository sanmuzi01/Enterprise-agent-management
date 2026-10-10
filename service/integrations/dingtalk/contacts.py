"""拉钉钉通讯录：从根部门（dept_id=1）逐层取子部门，再分页取每个部门的成员（按 userid 去重）。

需要应用开通“通讯录部门信息读权限”“成员信息读权限”“手机号码信息”权限；没有手机号权限时人对不上账号，
会全部列为“待手动绑定”。
"""
from typing import Any, Dict, List

from service.integrations.base import IntegrationError
from service.integrations.dingtalk import client

MAX_CALLS = 2000          # 部门 + 分页调用的总上限，防止对方接口异常时停不下来
ROOT = 1


def fetch_organization(app) -> Dict[str, Any]:
    calls = 0
    departments: List[Dict[str, Any]] = []
    queue = [ROOT]
    while queue:
        parent = queue.pop(0)
        calls += 1
        if calls > MAX_CALLS:
            raise IntegrationError("通讯录太大或接口异常，已停止同步")
        children = client.oapi(app, "/topapi/v2/department/listsub", {"dept_id": parent}) or []
        for dept in children if isinstance(children, list) else []:
            departments.append({"id": str(dept["dept_id"]), "name": dept.get("name"), "parent_id": str(dept.get("parent_id") or "")})
            queue.append(dept["dept_id"])

    users: Dict[str, Dict[str, Any]] = {}
    for dept_id in [ROOT] + [int(d["id"]) for d in departments]:
        cursor = 0
        while True:
            calls += 1
            if calls > MAX_CALLS:
                raise IntegrationError("通讯录太大或接口异常，已停止同步")
            page = client.oapi(app, "/topapi/v2/user/list", {"dept_id": dept_id, "cursor": cursor, "size": 100})
            for person in page.get("list") or []:
                uid = str(person.get("userid") or "")
                if uid and uid not in users:
                    users[uid] = {"user_id": uid, "union_id": person.get("unionid"), "name": person.get("name"),
                                  "mobile": person.get("mobile"), "email": person.get("email"),
                                  "department_ids": [str(d) for d in person.get("dept_id_list") or []],
                                  "active": person.get("active", True) is not False}
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor") or 0
    return {"tenant_id": app.app_id, "departments": departments, "users": list(users.values())}
