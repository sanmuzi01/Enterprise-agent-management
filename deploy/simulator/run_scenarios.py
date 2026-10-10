"""仿真联调：在 docker compose 起来的整套平台上（nginx → api → MySQL / Redis / Chroma / Java 业务中心 / worker），
用开放平台仿真器扮演飞书和钉钉，按真实上线的顺序把每一步走一遍，最后打印一张结果表。

  docker compose -f docker-compose.yml -f docker-compose.simulate.yml exec platform-sim python /sim/run_scenarios.py

账号：管理员是脚本临时建的（随机密码，结束删除）；员工用演示数据里的账号（scripts/seed_enterprise_demo.py，
密码见 docs/demo-script.md）。脚本结束时删除它建的接入配置、绑定、事件记录、群记录和 CRM 活动、测试通知。
"""
import asyncio
import json
import os
import secrets
import string
import sys
import time
import traceback
import uuid

sys.path.insert(0, "/app")
os.chdir("/app")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import requests  # noqa: E402

from tests import _route_client as rc  # noqa: E402  —— 建 / 删临时管理员（和演示数据脚本同一套）

from sqlalchemy import text  # noqa: E402

from models.init_db import SessionLocal  # noqa: E402

API = os.getenv("SIM_PLATFORM_API", "http://web/api")
SIM = os.getenv("SIM_CONTROL", "http://localhost:9100")
DEMO_PASSWORD = "Demo@12345"                       # 演示数据的固定密码（docs/demo-script.md）

RESULTS = []                                       # (分组, 检查项, 结果, 说明)
GROUP = ["准备"]


def check(ok, item, detail=""):
    RESULTS.append((GROUP[0], item, "通过" if ok else "失败", str(detail)[:160]))
    print(f"  {'✓' if ok else '✗'} {item}" + (f"  —— {str(detail)[:160]}" if detail else ""), flush=True)
    return ok


def note(item, detail=""):
    RESULTS.append((GROUP[0], item, "说明", str(detail)[:160]))
    print(f"  · {item}" + (f"  —— {str(detail)[:160]}" if detail else ""), flush=True)


def section(name):
    GROUP[0] = name
    print(f"\n== {name} ==", flush=True)


# ------------------------------------------------------------------ 平台（浏览器里的人）

class Person:
    """像浏览器一样登录（Cookie 会话 + CSRF 头），所有请求都经 nginx。"""

    def __init__(self, name, password):
        self.name = name
        self.s = requests.Session()
        r = self.s.post(f"{API}/user/login", json={"name": name, "password": password}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"{name} 登录失败：HTTP {r.status_code} {r.text[:200]}")

    def _headers(self):
        return {"X-CSRF-Token": self.s.cookies.get("csrf_token", "")}

    def get(self, path, **params):
        return self.s.get(f"{API}{path}", params=params, timeout=60)

    def post(self, path, body=None):
        return self.s.post(f"{API}{path}", json=body, headers=self._headers(), timeout=120)

    def put(self, path, body=None):
        return self.s.put(f"{API}{path}", json=body, headers=self._headers(), timeout=60)


# ------------------------------------------------------------------ 仿真器

def sim(path, body=None, **params):
    if body is None and not path.startswith("/_sim/setup"):
        return requests.get(f"{SIM}{path}", params=params, timeout=60).json()
    return requests.post(f"{SIM}{path}", json=body or {}, timeout=60).json()


def last_seq():
    return sim("/_sim/inbox")["last"]


def wait_inbox(after, *, to=None, chat_id=None, contains=None, timeout=30.0, count=1):
    """等平台发到飞书 / 钉钉的消息（相当于员工手机上收到）。"""
    deadline = time.time() + timeout
    items = []
    while time.time() < deadline:
        params = {"after": after}
        if to:
            params["to"] = to
        if chat_id:
            params["chat_id"] = chat_id
        items = sim("/_sim/inbox", **params)["items"]
        if contains:
            items = [m for m in items if contains in m["text"]]
        if len(items) >= count:
            return items
        time.sleep(0.5)
    return items


def say(open_id, words, timeout=60, **extra):
    """员工在飞书里给机器人发消息（默认单聊；chat_type="group" + at_bot=True 是在群里 @ 机器人）。
    返回 (平台应答, 机器人回复的文字)。"""
    before = last_seq()
    r = sim("/_sim/feishu/message", {"open_id": open_id, "text": words, **extra})
    group = extra.get("chat_type") == "group"
    reply = wait_inbox(before, to=None if group else open_id, chat_id=extra.get("chat_id") if group else None, timeout=timeout)
    return r, (reply[0]["text"] if reply else "")


# ------------------------------------------------------------------ 数据库（只用于准备和清理）

def db_one(sql, **params):
    db = SessionLocal()
    try:
        return db.execute(text(sql), params).first()
    finally:
        db.close()


def db_exec(sql, **params):
    db = SessionLocal()
    try:
        db.execute(text(sql), params)
        db.commit()
    finally:
        db.close()


def run_async(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def notify(user_id, title):
    from models.async_db import AsyncSessionLocal
    from service import notification_center

    async def go():
        async with AsyncSessionLocal() as db:
            return await notification_center.notify(db, user_id, "task_due", title, body="仿真联调：业务模块产生的提醒",
                                                    link="/todos", dedupe_key=f"sim:{uuid.uuid4().hex}")
    return run_async(go())


def flush_groups():
    from models.async_db import AsyncSessionLocal
    from service.crm import group_capture

    async def go():
        async with AsyncSessionLocal() as db:
            return await group_capture.flush_due(db)
    return run_async(go())[0]


def random_text(n):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


# ------------------------------------------------------------------ 场景

def main():
    started = time.time()
    feishu = {"app_id": "cli_sim" + random_text(10).lower(), "app_secret": random_text(32), "verification_token": random_text(24),
              "encrypt_key": random_text(32)}
    dingtalk = {"app_key": "dingsim" + random_text(10).lower(), "app_secret": random_text(40), "robot_code": "dingsim" + random_text(8).lower(),
                "token": random_text(20), "aes_key": random_text(43)}
    marks = {}

    section("准备")
    if not check(requests.get(f"{SIM}/_sim/health", timeout=5).ok, "仿真器在运行"):
        return
    r = requests.get(f"{API}/ready", timeout=10)
    check(r.status_code == 200, "经 nginx 访问平台后端", f"HTTP {r.status_code}")
    if db_one("SELECT COUNT(*) FROM collaboration_app")[0]:
        check(False, "库里没有已有的飞书 / 钉钉配置", "已有配置：不在这个库上跑，避免覆盖")
        return
    users = {row[0]: row[1] for row in [db_one("SELECT name, id FROM `user` WHERE name=:n", n=n) or (n, None)
                                        for n in ("demo_emp", "demo_head", "demo_newbie", "demo_fin")]}
    if not check(all(users.values()), "演示企业数据已就绪",
                 "先执行：docker compose ... exec api python scripts/seed_enterprise_demo.py" if not all(users.values()) else ""):
        return
    sales = db_one("SELECT t.id FROM teams t JOIN team_members m ON m.team_id=t.id WHERE m.user_id=:u AND t.department_code='sales'",
                   u=users["demo_emp"])[0]
    marks["activity"] = db_one("SELECT COALESCE(MAX(id),0) FROM customer_activity")[0]
    marks["started"] = started
    admin_account = rc.create_user("sim-admin", password="Sim!" + random_text(16))
    db_exec("INSERT INTO user_role (user_id, role_id) SELECT :u, id FROM role WHERE role_name='admin'", u=admin_account["id"])
    sim("/_sim/setup", {"feishu": feishu, "dingtalk": dingtalk,
                        "names": {"ou_emp": "孙销售", "ou_head": "林经理", "ou_newbie": "新同事", "staff_head": "林经理"},
                        "org": [{"open_id": "ou_emp", "name": "孙销售", "department": "od_sales"},
                                {"open_id": "ou_head", "name": "林经理", "department": "od_sales"}],
                        "chats": {"oc_sim_qm": {"name": "启明教育项目群", "owner_id": "ou_head"},
                                  "oc_sim_other": {"name": "随便聊聊", "owner_id": "ou_emp"}}})
    try:
        admin = Person(admin_account["name"], admin_account["password"])
        emp, head = Person("demo_emp", DEMO_PASSWORD), Person("demo_head", DEMO_PASSWORD)
        check(True, "管理员（临时账号）和两名演示员工经 nginx 登录")
        scenarios(admin, emp, head, users, sales, feishu, dingtalk)
    except Exception as exc:  # noqa: BLE001
        check(False, "联调脚本异常中止", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        sim("/_sim/outage", {"on": False})
        cleanup(feishu, dingtalk, users, marks)
        report(time.time() - started)


def scenarios(admin, emp, head, users, sales, feishu, dingtalk):
    # ---------------------------------------------------------- 飞书：管理员接入
    section("飞书 · 管理员接入")
    r = admin.put("/admin/integrations/feishu", {"app_id": feishu["app_id"], "app_secret": feishu["app_secret"],
                                                  "verification_token": feishu["verification_token"],
                                                  "encrypt_key": feishu["encrypt_key"], "enabled": True})
    check(r.status_code == 200, "后台保存 App ID / Secret / Token / Encrypt Key 并启用", f"HTTP {r.status_code}")
    r = admin.post("/admin/integrations/feishu/test").json()
    check(r.get("ok"), "“测试连接”：用凭证向开放平台换到 tenant_access_token", r.get("error") or "")
    v = sim("/_sim/feishu/verify")
    check(v["status"] == 200 and isinstance(v["body"], dict) and v["body"].get("challenge") == v["expected"],
          "开放平台保存回调地址时的校验（url_verification，加密）", f"HTTP {v['status']}")
    ready = admin.get("/admin/integrations/feishu/readiness").json()
    for c in ready.get("checks", []):
        if c["key"] in ("public_url", "https", "trusted_host") or "HTTPS" in c["label"]:
            note(f"接入自检「{c['label']}」{'通过' if c['ok'] else '未通过'}", "仿真环境没有公网 HTTPS，属预期；正式上线前必须通过")
        else:
            check(c["ok"] or c["level"] in ("info", "warning"), f"接入自检「{c['label']}」", c.get("detail") or "")

    # ---------------------------------------------------------- 安全
    section("飞书 · 回调安全")
    r = sim("/_sim/feishu/message", {"open_id": "ou_emp", "text": "伪造", "bad_signature": True})
    check(r["status"] == 401, "签名不对的请求被拒绝", f"HTTP {r['status']}")
    r = sim("/_sim/feishu/message", {"open_id": "ou_emp", "text": "篡改", "tamper": True})
    check(r["status"] == 401, "内容被篡改的请求被拒绝", f"HTTP {r['status']}")
    r = sim("/_sim/feishu/message", {"open_id": "ou_emp", "text": "过期", "ts": int(time.time()) - 3600})
    check(r["status"] == 401, "1 小时前的请求（过期）被拒绝", f"HTTP {r['status']}")
    r = sim("/_sim/feishu/replay", {"open_id": "ou_newbie", "text": "重放"})
    check(r["first"]["status"] == 200 and r["second"]["status"] == 401, "抓包重放（同一个 nonce）被拒绝",
          f"第一次 {r['first']['status']}，重放 {r['second']['status']}")
    time.sleep(2)

    # ---------------------------------------------------------- 绑定
    section("飞书 · 员工绑定")
    _, reply = say("ou_newbie", "你好")
    check("绑定" in reply, "没绑定的人发消息：收到绑定步骤", reply.replace("\n", " ")[:80])
    for person, open_id in ((emp, "ou_emp"), (head, "ou_head")):
        code = person.post("/me/integrations/feishu/bind-code")
        command = code.json().get("command", "") if code.ok else ""
        check(command.startswith("绑定 "), f"{person.name} 在设置页领取绑定码", command[:3] + "******" if command else code.text[:80])
        _, group_reply = say(open_id, command, chat_type="group", chat_id="oc_sim_other", at_bot=True)
        if open_id == "ou_emp":
            check("私聊" in group_reply, "绑定码发在群里：被拒绝，提示私聊", group_reply[:60])
        _, reply = say(open_id, command)
        check("绑定成功" in reply, f"{person.name} 私聊机器人发绑定码：绑定成功", reply[:60])
        mine = {i["provider"]: i for i in person.get("/me/integrations").json()["items"]}
        check(mine["feishu"]["status"] == "bound", f"{person.name} 设置页显示“已绑定”", mine["feishu"].get("external_name"))

    # ---------------------------------------------------------- 对话
    section("飞书 · 和助手对话")
    started = time.time()
    _, reply = say("ou_emp", "我今年的年假还剩几天？")
    check(bool(reply) and "出错" not in reply, "员工私聊提问：助手回答（离线演示模型）",
          f"{time.time() - started:.1f} 秒：{reply.replace(chr(10), ' ')[:90]}")
    event_id = uuid.uuid4().hex
    before = last_seq()
    for _ in range(2):
        sim("/_sim/feishu/message", {"open_id": "ou_emp", "text": "帮我看看待办", "event_id": event_id})
    time.sleep(1)
    wait_inbox(before, to="ou_emp", timeout=40)
    time.sleep(3)                                  # 再等一会儿，确认没有第二条
    replies = sim("/_sim/inbox", after=before, to="ou_emp")["items"]
    check(len(replies) == 1, "飞书重推同一个事件（同 event_id 发两次）：只处理一次", f"收到 {len(replies)} 条回复")
    before = last_seq()
    r = sim("/_sim/feishu/message", {"open_id": "ou_emp", "text": "@_user_1 你看下", "chat_type": "group", "chat_id": "oc_sim_other"})
    time.sleep(3)
    check(r["status"] == 200 and not sim("/_sim/inbox", after=before)["items"], "群里没 @ 机器人的消息：不回复")

    # ---------------------------------------------------------- 聊天进 CRM
    section("飞书 · 聊天记录进 CRM")
    _, reply = say("ou_emp", "保存到CRM：云帆软件李总说下周三前确认续约，预算 20 万")
    check("云帆软件" in reply, "发“保存到CRM：……”：存进客户时间线并对上客户", reply[:70])
    before = last_seq()
    fwd = sim("/_sim/feishu/forward", {"open_id": "ou_emp", "lines": [
        {"open_id": "ou_emp", "text": "王总您好，远航物流的仓储方案和报价已经发您邮箱了", "minutes_ago": 50},
        {"open_id": "ou_cust_wang", "text": "收到，我们内部评估一下，下周给你答复", "minutes_ago": 45},
        {"open_id": "ou_cust_wang", "msg_type": "image", "content": {"image_key": "img_x"}, "minutes_ago": 44}]})
    reply = wait_inbox(before, to="ou_emp", timeout=60)
    reply = reply[0]["text"] if reply else ""
    check(fwd["status"] == 200 and "3 条" in reply and "远航物流" in reply, "合并转发聊天记录给机器人：整段存进 CRM", reply[:80])
    before = last_seq()
    sim("/_sim/feishu/message", {"open_id": "ou_emp", "message_id": fwd["message_id"], "message_type": "merge_forward",
                                 "content": {"content": "Merged and Forwarded Message"}})
    again = wait_inbox(before, to="ou_emp", timeout=40)
    check(again and "已经保存过" in again[0]["text"], "同一次转发再发一遍：不重复记录", again[0]["text"][:40] if again else "没有回复")

    _, reply = say("ou_emp", "开启CRM记录", chat_type="group", chat_id="oc_sim_qm", at_bot=True)
    check("负责人或群主" in reply, "普通销售（不是群主）在群里开启：被拒绝", reply[:50])
    _, reply = say("ou_head", "开启CRM记录 启明教育", chat_type="group", chat_id="oc_sim_qm", at_bot=True)
    check("已开启" in reply and "启明教育" in reply, "销售负责人在客户群里开启 CRM 记录：群里公告", reply.replace("\n", " ")[:90])
    sent = []
    for open_id, words, ago in (("ou_emp", "李老师，课程平台的试用账号已经开好了", 70), ("ou_cust_li", "好的，我们这周让老师们试一下", 68),
                                ("ou_cust_li", "这句发错了请忽略", 67), ("ou_emp", "有问题随时找我", 66)):
        sent.append(sim("/_sim/feishu/message", {"open_id": open_id, "text": words, "chat_type": "group", "chat_id": "oc_sim_qm",
                                                 "minutes_ago": ago})["message_id"])
    time.sleep(1.5)                                # 消息在应答之后才后台处理：等它存进暂存再撤回
    sim("/_sim/feishu/recall", {"message_id": sent[2], "chat_id": "oc_sim_qm"})
    sim("/_sim/feishu/message", {"open_id": "ou_emp", "text": "这个群没开启，不该被记录", "chat_type": "group", "chat_id": "oc_sim_other"})
    time.sleep(3)
    _, reply = say("ou_head", "CRM记录状态", chat_type="group", chat_id="oc_sim_qm", at_bot=True)
    check("已记录 3 条" in reply, "群里查看状态：撤回的那条不算", reply[:70])
    created = flush_groups()
    check(created == 1, "对话停下 30 分钟后由定时任务整理成一条群聊活动（这里直接触发一次）", f"新建 {created} 条")
    customers = emp.get("/enterprise/crm/customers", team_id=sales).json()
    by_name = {c["name"]: c["id"] for c in (customers if isinstance(customers, list) else customers.get("items", []))}
    for name, expect, forbid in (("启明教育", "群聊「启明教育项目群」", "这句发错了"), ("远航物流", "聊天记录", None),
                                 ("云帆软件", "李总说下周三前确认续约", None)):
        if name not in by_name:
            check(False, f"Java 业务中心里有客户「{name}」", "没找到")
            continue
        items = emp.get(f"/enterprise/crm-copilot/customers/{by_name[name]}/timeline", team_id=sales).json().get("items", [])
        blob = json.dumps(items, ensure_ascii=False)
        ok = expect in blob and (forbid is None or forbid not in blob)
        check(ok, f"销售在网页上打开「{name}」的客户时间线：看到这条沟通" + ("，撤回的内容不在" if forbid else ""), expect)
    leaked = db_one("SELECT COUNT(*) FROM customer_activity WHERE content LIKE '%不该被记录%'")[0]
    check(leaked == 0, "没开启记录的群：消息没有进 CRM")
    groups = head.get("/enterprise/crm-copilot/chat-groups", team_id=sales).json()
    mine = [g for g in groups.get("items", []) if g["chat_name"] == "启明教育项目群"]
    check(mine and mine[0]["status"] == "active" and groups.get("can_manage"), "销售负责人在网页上看到开启了记录的群")
    if mine:
        before = last_seq()
        r = head.post(f"/enterprise/crm-copilot/chat-groups/{mine[0]['id']}/close", {"team_id": sales})
        told = wait_inbox(before, chat_id="oc_sim_qm", contains="网页上关闭", timeout=15)
        check(r.status_code == 200 and told, "负责人在网页上关闭记录：机器人在群里告知", f"HTTP {r.status_code}")

    # ---------------------------------------------------------- 通知推送与故障恢复
    section("通知推送 · 可靠性")
    before = last_seq()
    t0 = time.time()
    notify(users["demo_emp"], "【仿真】待办即将到期：提交复盘报告")
    got = wait_inbox(before, to="ou_emp", contains="提交复盘报告", timeout=30)
    check(got, "业务产生站内通知：员工在飞书里收到", f"{time.time() - t0:.1f} 秒送达" if got else "30 秒内没收到")

    sim("/_sim/outage", {"on": True})
    before = last_seq()
    notify(users["demo_emp"], "【仿真】平台故障期间产生的通知")
    retrying = 0
    for _ in range(40):
        retrying = (admin.get("/admin/integrations/feishu/health").json().get("push") or {}).get("retrying", 0)
        if retrying:
            break
        time.sleep(0.5)
    check(retrying >= 1, "飞书不可用：发送失败进入重试（后台接入页显示“重试中”）", f"重试中 {retrying}")
    count = db_one("SELECT COUNT(*) FROM notification WHERE user_id=:u AND title LIKE '%平台故障期间%'", u=users["demo_emp"])[0]
    check(count == 1, "飞书不可用不影响业务：站内通知照常生成")
    _, reply = say("ou_emp", "帮我看看待办", timeout=8)
    note("故障期间员工发的消息", "机器人回复发不出去（平台不可用），回调本身已应答，事件记为失败可在后台看到" if not reply else "居然回复了")
    time.sleep(3)
    sim("/_sim/outage", {"on": False})
    t0 = time.time()
    got = wait_inbox(before, to="ou_emp", contains="平台故障期间", timeout=240)
    check(got, "平台恢复后自动补发，不用人工处理", f"恢复后 {time.time() - t0:.0f} 秒送达" if got else "4 分钟内没补发")

    # ---------------------------------------------------------- 钉钉
    section("钉钉 · 接入、绑定、对话、推送")
    r = admin.put("/admin/integrations/dingtalk", {"app_id": dingtalk["app_key"], "app_secret": dingtalk["app_secret"],
                                                    "robot_code": dingtalk["robot_code"], "verification_token": dingtalk["token"],
                                                    "encrypt_key": dingtalk["aes_key"], "enabled": True})
    check(r.status_code == 200, "后台保存 AppKey / AppSecret / robotCode / 事件订阅 Token、aes_key", f"HTTP {r.status_code} {r.text[:80] if not r.ok else ''}")
    r = admin.post("/admin/integrations/dingtalk/test").json()
    check(r.get("ok"), "钉钉“测试连接”：换到 accessToken", r.get("error") or "")
    v = sim("/_sim/dingtalk/verify")
    check(v["status"] == 200 and isinstance(v["body"], dict) and v["body"].get("encrypt"), "钉钉事件订阅的回调地址校验（check_url，加密应答）",
          f"HTTP {v['status']}")
    code = head.post("/me/integrations/dingtalk/bind-code").json().get("command", "")
    before = last_seq()
    r = sim("/_sim/dingtalk/message", {"staff_id": "staff_head", "text": code})
    got = wait_inbox(before, to="staff_head", timeout=30)
    check(r["status"] == 200 and got and "绑定成功" in got[0]["text"], "demo_head 在钉钉里私聊机器人发绑定码：绑定成功",
          got[0]["text"][:50] if got else f"HTTP {r['status']} {r['body']}")
    before = last_seq()
    sim("/_sim/dingtalk/message", {"staff_id": "staff_head", "text": "有哪些单据等我审批？"})
    got = wait_inbox(before, to="staff_head", timeout=60)
    check(got and "出错" not in got[0]["text"], "钉钉里提问：助手经 sessionWebhook 回复", got[0]["text"].replace("\n", " ")[:80] if got else "没有回复")
    before = last_seq()
    notify(users["demo_head"], "【仿真】两边都绑定了的员工收到的提醒")
    fs = wait_inbox(before, to="ou_head", contains="两边都绑定", timeout=30)
    dt = wait_inbox(before, to="staff_head", contains="两边都绑定", timeout=30)
    check(fs and dt, "同时绑定飞书和钉钉：两边各收到一条（钉钉经机器人单聊批量发送接口）", f"飞书 {len(fs)} 条，钉钉 {len(dt)} 条")

    # ---------------------------------------------------------- 离职
    section("员工离职")
    r = sim("/_sim/feishu/user_left", {"open_id": "ou_emp"})
    time.sleep(1)
    _, reply = say("ou_emp", "帮我查一下报销")
    check(r["status"] == 200 and "停用" in reply, "飞书推送离职事件：绑定立即停用，再发消息用不了助手", reply[:60])
    calls = sim("/_sim/calls")["calls"]
    note("平台调用过的开放接口", "、".join(sorted({c.split("?")[0].replace("POST ", "").replace("GET ", "") for c in calls
                                                   if not c.split()[1].startswith("/robot/")}))[:150])


def cleanup(feishu, dingtalk, users, marks):
    section("清理")
    tenants = {"f": feishu["app_id"], "d": dingtalk["app_key"]}
    try:
        db_exec("DELETE FROM crm_chat_message WHERE group_id IN (SELECT id FROM crm_chat_group WHERE tenant_id IN (:f, :d))", **tenants)
        db_exec("DELETE FROM crm_chat_group WHERE tenant_id IN (:f, :d)", **tenants)
        db_exec("DELETE FROM customer_activity WHERE id > :m AND source_provider IN ('feishu', 'dingtalk')", m=marks.get("activity", 1 << 62))
        db_exec("DELETE FROM external_event_inbox WHERE tenant_id IN (:f, :d)", **tenants)
        db_exec("DELETE FROM external_user_binding WHERE external_tenant_id IN (:f, :d)", **tenants)
        db_exec("DELETE FROM collaboration_app WHERE app_id IN (:f, :d)", **tenants)
        ids = [u for u in users.values() if u]
        if ids:
            db_exec(f"DELETE FROM external_bind_code WHERE user_id IN ({','.join(str(int(i)) for i in ids)})")
            db_exec(f"DELETE FROM notification WHERE dedupe_key LIKE 'sim:%' AND user_id IN ({','.join(str(int(i)) for i in ids)})")
        rc.cleanup()
        print("  已删除：接入配置、绑定、事件记录、群记录、本次产生的 CRM 活动、测试通知、临时管理员", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 清理失败：{exc}", flush=True)


def report(seconds):
    passed = sum(1 for r in RESULTS if r[2] == "通过")
    failed = [r for r in RESULTS if r[2] == "失败"]
    print(f"\n==== 仿真联调结果：通过 {passed}，失败 {len(failed)}，用时 {seconds:.0f} 秒 ====")
    for group, item, result, detail in failed:
        print(f"  ✗ [{group}] {item}：{detail}")
    print("RESULT_JSON=" + json.dumps(RESULTS, ensure_ascii=False))


if __name__ == "__main__":
    main()
