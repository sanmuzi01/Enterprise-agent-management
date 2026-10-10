"""真实路由级测试的共享脚手架。

不是测试文件（不匹配 test_*.py），提供：
- TestClient(app)（触发 lifespan / bootstrap_database）
- 直接建测试用户（bcrypt 落库）+ 签发真实 JWT
- 结束后清理这批用户及其组件 / Agent / 知识

依赖本地可连的 MySQL 与 .env 里的 JWT_SECRET_KEY —— 与 CI 的单测环境一致。
无法连库时，调用方应 skip（见 route_tests_available）。
"""

import atexit
import os
import sys
import time
import uuid

# 这里不再过滤 Starlette 的“TestClient 将改用 httpx2”弃用提示：requirements-dev.txt 已经装了 httpx2，正常环境没有这条警告；
# 如果你的环境没装（pip install -r requirements-dev.txt），会直接看到它——这是想要的：不要靠过滤器把警告藏起来。

# FasdtApi.main 在模块导入时就会 assert_runtime_config() —— 用非生产环境跑，避免生产校验拦截。
# 测试默认只在控制台显示 CRITICAL（预期内的故障日志太多会淹没真正的失败）；要看细节：TEST_LOG_LEVEL=INFO。文件日志不受影响。
os.environ.setdefault("CONSOLE_LOG_LEVEL", os.environ.get("TEST_LOG_LEVEL", "CRITICAL"))
os.environ.setdefault("ASYNC_DB_POOL", "null")   # 见 models/async_db.py：测试不复用跨事件循环的连接
_ORIG_APP_ENV = os.environ.get("APP_ENV")
os.environ["APP_ENV"] = "test"
# TestClient 默认 Host 是 testserver —— 必须在 import FasdtApi.main（构造 TrustedHostMiddleware）之前放行
os.environ.setdefault("TRUSTED_HOSTS", "127.0.0.1,localhost,api")
if "testserver" not in os.environ["TRUSTED_HOSTS"]:
    os.environ["TRUSTED_HOSTS"] = os.environ["TRUSTED_HOSTS"] + ",testserver"
os.environ.setdefault("CORS_ALLOW_ORIGINS", "http://testserver")

# 测试一律用临时的提示词目录，不碰真实的 prompt/prompts/：
# 测试库是空库时（CI、deploy/test-services）助手编号从 1 开始，测试建的 1 号助手会覆盖、清理时再删掉
# 开发库 1 号助手的提示词（踩过：prompt/prompts/1.yaml、5.yaml 被删）。按编号命名的文件和开发库共用目录，编号再怎么避让都不保险。
# prompt_manager 的函数每次调用都读模块里的 PROMPT_DIR，所以这里改了之后，无论谁先导入都生效。
import shutil as _shutil  # noqa: E402
import tempfile as _tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

import prompt.prompt_manager as _prompt_manager  # noqa: E402

TEST_PROMPT_DIR = _Path(_tempfile.mkdtemp(prefix="agent_test_prompts_"))
_prompt_manager.PROMPT_DIR = TEST_PROMPT_DIR
atexit.register(_shutil.rmtree, TEST_PROMPT_DIR, True)

_SUFFIX = f"{int(time.time()) % 100000}{uuid.uuid4().hex[:4]}"
_created_user_ids: list[int] = []
_created_issue_ids: list[int] = []
_created_notification_ids: list[int] = []
_created_event_ids: list[str] = []
_issue_tracking_installed = False


def _track_issues() -> None:
    """记下本进程里创建的问题中心记录，cleanup 时一并删除。

    测试会故意制造故障（Agent 运行失败、业务系统不可用、页面脚本错误），问题中心会如实登记；
    如果不清掉，开发库的问题中心会堆满测试产生的“严重问题”，真实试点前的预检也会被它们污染。
    只删本进程创建的，不碰别的进程（比如同时在跑的真实服务）产生的问题。
    """
    global _issue_tracking_installed
    if _issue_tracking_installed:
        return
    from sqlalchemy import event
    from models.init_db import SystemIssue

    @event.listens_for(SystemIssue, "after_insert")
    def _remember(_mapper, _connection, target):  # noqa: ANN001
        _created_issue_ids.append(target.id)

    # 问题登记会经事件触发“新问题”站内通知（发给真实的管理员账号）：测试制造的故障不能把通知留在管理员的收件箱里
    from models.init_db import Notification, OutboxEvent

    @event.listens_for(Notification, "after_insert")
    def _remember_notification(_mapper, _connection, target):  # noqa: ANN001
        _created_notification_ids.append(target.id)

    @event.listens_for(OutboxEvent, "after_insert")
    def _remember_event(_mapper, _connection, target):  # noqa: ANN001
        _created_event_ids.append(target.event_id)
    _issue_tracking_installed = True


def _purge_issues() -> None:
    if not (_created_issue_ids or _created_notification_ids or _created_event_ids):
        return
    from sqlalchemy import text
    from models.init_db import SessionLocal
    db = SessionLocal()
    try:
        if _created_issue_ids:
            ids = ",".join(str(i) for i in set(_created_issue_ids))
            for table in ("issue_event", "issue_occurrence"):
                db.execute(text(f"DELETE FROM {table} WHERE issue_id IN ({ids})"))
            db.execute(text(f"DELETE FROM system_issue WHERE id IN ({ids})"))
        if _created_notification_ids:
            db.execute(text(f"DELETE FROM notification WHERE id IN ({','.join(str(i) for i in set(_created_notification_ids))})"))
        for chunk_start in range(0, len(_created_event_ids), 500):
            marks = ",".join(f"'{e}'" for e in _created_event_ids[chunk_start:chunk_start + 500])      # event_id 是进程内生成的 uuid
            for table in ("consumer_inbox", "consumer_retry", "dead_letter"):
                db.execute(text(f"DELETE FROM {table} WHERE event_id IN ({marks})"))
            db.execute(text(f"DELETE FROM outbox_event WHERE event_id IN ({marks})"))
        db.commit()
    except Exception:  # noqa: BLE001 —— 清理失败不能让测试失败
        db.rollback()
    finally:
        db.close()
        _created_issue_ids.clear()
        _created_notification_ids.clear()
        _created_event_ids.clear()


_availability: "tuple[bool, str] | None" = None


def route_tests_available() -> tuple[bool, str]:
    """路由级测试的前置条件（JWT 密钥 + 能连上数据库）。每个路由测试模块在导入时都会问一遍：
    整个进程只真正探测一次（缓存结果），并且先用 1.5 秒的端口探测——数据库没起来时不能每个模块都等一次操作系统级的
    连接超时（Windows 上约 20 秒 × 60 多个模块，看起来就像测试卡死了）。"""
    global _availability
    if _availability is None:
        _availability = _probe_route_environment()
    return _availability


def _probe_route_environment() -> tuple[bool, str]:
    if not os.getenv("JWT_SECRET_KEY"):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:  # noqa: BLE001
            pass
    if not os.getenv("JWT_SECRET_KEY"):
        return False, "缺少 JWT_SECRET_KEY"
    import socket
    host, port = os.getenv("DB_HOST", "127.0.0.1"), int(os.getenv("DB_PORT", "3306") or 3306)
    try:
        socket.create_connection((host, port), timeout=1.5).close()
    except OSError as exc:
        return False, f"数据库不可用: 连不上 {host}:{port}（{exc}）。一条命令起测试用的 MySQL：docker compose -f deploy/test-services/docker-compose.yml up -d"
    from utils.db_probe import ping_database
    if not ping_database(timeout=2.0):       # 独立短连接 + 连接 / 读 / 写超时：数据库“半通不通”（握手不回应）时也不会卡住
        return False, f"数据库不可用: {host}:{port} 连得上但没有正常响应（超时 2 秒）。一条命令起测试用的 MySQL：docker compose -f deploy/test-services/docker-compose.yml up -d"
    return True, ""


def make_client():
    from fastapi.testclient import TestClient
    _track_issues()
    # 别的测试可能刚把 APP_ENV 改成 production 且没还原 —— import main 会跑生产校验，这里强制非生产
    os.environ["APP_ENV"] = "test"
    # 若 main 已被别的测试以旧 TRUSTED_HOSTS / APP_ENV 导入过，重新导入
    sys.modules.pop("FasdtApi.main", None)
    from FasdtApi.main import app
    return TestClient(app)


def mint_token(uid: int) -> str:
    """按数据库里这个用户当前的 auth_version 签发一个新 token。

    改密码 / 强制下线之后重新登录，或者测试"新 token 能正常用"，都用这个而不是自己拼 payload——
    否则很容易忘记带上最新的 ver，测出来的其实不是真实场景。
    """
    from models.init_db import SessionLocal
    from models.user_dao import get_user_by_id
    from service.auth import create_access_token

    db = SessionLocal()
    try:
        user = get_user_by_id(db, uid)
        ver = getattr(user, "auth_version", 0) or 0
    finally:
        db.close()
    return create_access_token({"user_id": uid, "ver": ver})


def auth_headers(uid: int) -> dict:
    return {"Authorization": f"Bearer {mint_token(uid)}"}


def create_user(name_prefix: str, password: str = "Passw0rd!Secure", *, admin: bool = False) -> dict:
    """建一个测试用户，返回 {id, name, password, headers}。"""
    from models.init_db import SessionLocal
    from models.user_dao import create_user as dao_create_user
    from service.auth_service import hash_password
    _track_issues()

    name = f"rt_{name_prefix}_{_SUFFIX}"[:20]
    db = SessionLocal()
    try:
        user = dao_create_user(db, name=name, password=hash_password(password), age=30, phone=None)
        uid = user.id
    finally:
        db.close()
    _created_user_ids.append(uid)
    return {
        "id": uid,
        "name": name,
        "password": password,
        "headers": auth_headers(uid),
        "admin": admin,
    }


def admin_env(*names: str):
    """上下文：把给定用户名并入 ADMIN_USER_NAMES。"""
    from unittest.mock import patch
    current = os.getenv("ADMIN_USER_NAMES", "admin")
    merged = ",".join([current, *names])
    return patch.dict(os.environ, {"ADMIN_USER_NAMES": merged})


def _purge_users(where_users: str) -> int:
    """按 `where_users`（`user` 表的 WHERE 片段）删用户及其全部级联数据。

    每条 DELETE 独立提交——某张表撞 FK / 不存在，不会把整轮清理一起回滚
    （这是历史上测试用户越积越多的根因）。返回删掉的用户数。
    """
    import pathlib
    from sqlalchemy import text
    from models.init_db import SessionLocal

    db = SessionLocal()
    try:
        ids = [r[0] for r in db.execute(text(f"SELECT id FROM `user` WHERE {where_users}")).all()]
        if not ids:
            return 0
        inc = "(" + ",".join(str(i) for i in ids) + ")"

        # 先删磁盘文件（提示词 yaml、企业助手的专业技能配置、上传的原始文件）。
        # 专业技能配置以前漏删了：数据体检（service/data_health.py）在开发库里查出几十个没有对应智能体的配置文件，就是这里来的。
        root = pathlib.Path(__file__).resolve().parents[1]
        for aid in [r[0] for r in db.execute(text(f"SELECT id FROM agent WHERE user_id IN {inc}")).all()]:
            (_prompt_manager.PROMPT_DIR / f"{aid}.yaml").unlink(missing_ok=True)   # 测试用的临时目录，见文件开头
            (root / "skills" / "enterprise" / f"agent_{aid}.yml").unlink(missing_ok=True)
        krows = db.execute(text(f"SELECT id, file_path FROM knowledge WHERE user_id IN {inc}")).all()
        for _kid, fpath in krows:
            try:
                if fpath:
                    pathlib.Path(fpath).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        kids = [r[0] for r in krows]
        kin = "(" + ",".join(str(i) for i in kids) + ")" if kids else None

        stmts = [
            f"DELETE dp FROM widget_data_points dp JOIN user_widgets w ON dp.widget_id=w.id WHERE w.user_id IN {inc}",
            f"DELETE FROM user_widgets WHERE user_id IN {inc}",
            # Phase 3B（docs/enterprise-rbac-plan.md）新增，organization_members/team_members
            # 都有 user.id 的外键——不先删这两张表，下面删 `user` 会直接撞 FK 报错。
            f"DELETE FROM organization_members WHERE user_id IN {inc}",
            f"DELETE FROM team_members WHERE user_id IN {inc}",
            # Phase 3D 阶段4新增，applicant_id/approver_id 都有 user.id 的外键，同理先删。
            f"DELETE FROM approval_request WHERE applicant_id IN {inc} OR approver_id IN {inc}",
        ]
        if kin:
            stmts += [
                f"DELETE FROM knowledge_chunk WHERE knowledge_id IN {kin}",
                f"DELETE FROM background_task WHERE target_type='knowledge' AND target_id IN {kin}",
            ]
        stmts += [
            f"DELETE FROM background_task WHERE user_id IN {inc}",
            f"DELETE FROM knowledge WHERE user_id IN {inc}",
            f"DELETE FROM rag_debug_samples WHERE user_id IN {inc}",
            f"DELETE FROM kb_audit_log WHERE user_id IN {inc}",
            f"DELETE FROM agent_api_connector WHERE user_id IN {inc}",
            # eval_run 没有 user_id，靠 eval_set 反查；必须先删它，eval_set 才能删（FK 子表）
            f"DELETE er FROM eval_run er JOIN eval_set es ON er.eval_set_id=es.id WHERE es.user_id IN {inc}",
            f"DELETE FROM eval_set WHERE user_id IN {inc}",
            f"DELETE FROM space_members WHERE user_id IN {inc}",
            f"DELETE sm FROM space_members sm JOIN knowledge_spaces s ON sm.space_id=s.id WHERE s.user_id IN {inc}",
            f"DELETE aks FROM agent_knowledge_space aks JOIN knowledge_spaces s ON aks.space_id=s.id WHERE s.user_id IN {inc}",
            f"DELETE FROM knowledge_spaces WHERE user_id IN {inc}",
            f"DELETE msg FROM message msg JOIN conversation c ON msg.conversation_id=c.id WHERE c.user_id IN {inc}",
            f"DELETE FROM conversation WHERE user_id IN {inc}",
            f"DELETE st FROM agent_step st JOIN agent_run r ON st.run_id=r.id WHERE r.user_id IN {inc}",
            f"DELETE FROM agent_run WHERE user_id IN {inc}",
            f"DELETE FROM chat WHERE user_id IN {inc}",
            # agent_skill 同时被 agent.id / skill.id 外键引用 —— 删 agent / skill 之前先清掉
            f"DELETE ask FROM agent_skill ask JOIN agent a ON ask.agent_id=a.id WHERE a.user_id IN {inc}",
            f"DELETE ask FROM agent_skill ask JOIN skill s ON ask.skill_id=s.id WHERE s.user_id IN {inc}",
            f"DELETE sv FROM skill_version sv JOIN skill s ON sv.skill_id=s.id WHERE s.user_id IN {inc}",
            f"DELETE FROM skill WHERE user_id IN {inc}",
            f"DELETE FROM web_monitor WHERE user_id IN {inc}",
            f"DELETE FROM user_workspace WHERE user_id IN {inc}",
            f"DELETE FROM operation_log WHERE user_id IN {inc}",
            # 第五轮审计 P0-2 新增，tool_confirmation.user_id 有 user.id 的外键，同理先删。
            f"DELETE os FROM orchestration_step os JOIN orchestration_plan op ON os.plan_id=op.id WHERE op.user_id IN {inc}",
            f"DELETE FROM orchestration_plan WHERE user_id IN {inc}",
            f"DELETE FROM automation_work WHERE user_id IN {inc}",
            f"DELETE FROM work_item WHERE user_id IN {inc}",
            f"DELETE FROM agent_handoff WHERE user_id IN {inc}",
            f"DELETE FROM notification WHERE user_id IN {inc}",
            f"DELETE FROM notification_preference WHERE user_id IN {inc}",
            f"DELETE FROM tool_confirmation WHERE user_id IN {inc}",
            f"UPDATE `user` SET selected_agent_id=NULL WHERE id IN {inc}",
            f"DELETE FROM agent WHERE user_id IN {inc}",
            f"DELETE FROM llm_config WHERE user_id IN {inc}",
            f"DELETE FROM user_profile WHERE user_id IN {inc}",
            f"DELETE FROM memory WHERE user_id IN {inc}",
            f"DELETE FROM user_subscription WHERE user_id IN {inc}",
            f"DELETE FROM user_role WHERE user_id IN {inc}",
            f"DELETE FROM `user` WHERE id IN {inc}",
        ]
        for s in stmts:
            try:
                db.execute(text(s))
                db.commit()
            except Exception:  # noqa: BLE001 - 单条失败不影响其它
                db.rollback()
        left = db.execute(text(f"SELECT COUNT(*) FROM `user` WHERE {where_users}")).scalar() or 0
        return len(ids) - int(left)
    finally:
        db.close()


def sweep_test_users() -> int:
    """兜底：删掉库里所有 `rt_%` 测试用户（不限本进程）。给清理脚本 / 手动用。"""
    return _purge_users(r"name LIKE 'rt\_%'")


def cleanup():
    """删除本模块建的用户及其级联数据。测试类 tearDownClass 调用。"""
    try:
        _purge_issues()
        if _created_user_ids:
            _purge_users("id IN (" + ",".join(str(i) for i in set(_created_user_ids)) + ")")
    finally:
        _created_user_ids.clear()
        if _ORIG_APP_ENV is None:
            os.environ.pop("APP_ENV", None)
        else:
            os.environ["APP_ENV"] = _ORIG_APP_ENV


# 进程退出兜底：某个测试类 setUpClass 崩了、或 Ctrl+C 中断，tearDownClass 没跑到，
# 本进程建的用户也不会漏在库里。
@atexit.register
def _cleanup_on_exit():
    if _created_user_ids:
        try:
            cleanup()
        except Exception:  # noqa: BLE001
            pass
