"""第五轮审计 P1-6：数据库连接串在密码包含特殊字符时可能失效。

同步/异步/审计/Alembic 四处连接串（models/init_db.py、models/async_db.py、
models/audit_db.py、migrations/env.py）之前都是裸 f-string 拼接
`f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}"`。密码如果含
`@`/`:`/`/`/`%` 等字符，会被误解析成主机名/路径的一部分，甚至直接解析失败
（比如密码里有第二个 `@`，SQLAlchemy 会把 `@` 之后的部分错当成"新的"
host:port，端口段解析成非数字直接报错）。改成
`URL.create(...).render_as_string(hide_password=False)`，由 SQLAlchemy
负责正确的百分号编码/解码。

分两块：
  1. `UrlCreateEncodingTest`：不依赖真实 DB——直接验证 URL.create() +
     render_as_string() + 回读解析这个模式本身是对的，并且真实复现旧写法
     （裸 f-string）在同一个密码下解析失败/解析出错误结果（不是纸面推测，
     是真的跑一遍旧写法的字符串去解析）。
  2. `RealModuleUrlEncodingTest`：真实子进程验证——DB_USER/DB_PASSWORD 在
     models/init_db.py 里是模块级常量，同一个进程内没法通过 patch 环境变量
     后 importlib.reload 安全地重新触发（models/init_db.py 定义了全部 ORM
     模型类，reload 会撞 "Table already defined"；models/async_db.py 和
     models/audit_db.py 的 DB_USER/DB_PASSWORD 又是从 models.init_db 的
     模块属性直接 import 过来的缓存值，不是自己重新读环境变量，reload 它们
     也测不出东西）。所以这里另起一个真实子进程，从环境变量层面注入一个含
     特殊字符的 DB_PASSWORD，一次性验证 models/init_db.py、
     models/async_db.py、models/audit_db.py 三处产出的连接串都能被正确
     解析回原密码——这是不用碰任何模块缓存/reload 顺序问题的最干净验证方式。
     migrations/env.py 的 _database_url() 因为模块顶层会在 import 时就跑
     真实的 alembic 迁移逻辑，不适合同样起子进程测，覆盖靠上面第1块的共享
     模式测试（migrations/env.py 用的是完全相同的 URL.create() 写法）加上
     `alembic current`/`upgrade head` 的真实手动验证（本次改动记录见
     docs/enterprise-rbac-plan.md）。
"""
import json
import subprocess
import sys
import unittest

from sqlalchemy.engine import URL, make_url

_NASTY_PASSWORD = "p@ss:w/o%rd#1?2&3"


class UrlCreateEncodingTest(unittest.TestCase):
    def test_url_create_roundtrips_special_characters(self):
        url = URL.create(
            "mysql+pymysql", username="app_runtime", password=_NASTY_PASSWORD,
            host="127.0.0.1", port=3306, database="agent_sql",
        )
        rendered = url.render_as_string(hide_password=False)
        parsed = make_url(rendered)
        self.assertEqual(parsed.password, _NASTY_PASSWORD)
        self.assertEqual(parsed.host, "127.0.0.1")
        self.assertEqual(parsed.port, 3306)
        self.assertEqual(parsed.database, "agent_sql")
        self.assertEqual(parsed.username, "app_runtime")

    def test_naive_fstring_concatenation_breaks_on_same_password(self):
        """真实复现旧写法的 bug，不是纸面推测：同一个密码，裸 f-string 拼接
        出来的连接串直接解析失败（密码里的第二个 @ 把解析器带偏，端口段
        解析成非数字）。"""
        naive = f"mysql+pymysql://app_runtime:{_NASTY_PASSWORD}@127.0.0.1:3306/agent_sql"
        with self.assertRaises(ValueError):
            make_url(naive)

    def test_naive_fstring_silently_wrong_with_at_sign_only_password(self):
        """即使密码只含一个 @（不到"解析直接报错"的程度），裸拼接解析出来的
        host/password 也是错的——@ 被当成"用户信息和主机的分隔符"，密码的
        后半段被错当成了主机名。"""
        password = "pass@word"
        naive = f"mysql+pymysql://app_runtime:{password}@127.0.0.1:3306/agent_sql"
        parsed = make_url(naive)
        self.assertNotEqual(parsed.password, password)
        self.assertNotEqual(parsed.host, "127.0.0.1")

        fixed = URL.create(
            "mysql+pymysql", username="app_runtime", password=password,
            host="127.0.0.1", port=3306, database="agent_sql",
        ).render_as_string(hide_password=False)
        fixed_parsed = make_url(fixed)
        self.assertEqual(fixed_parsed.password, password)
        self.assertEqual(fixed_parsed.host, "127.0.0.1")


_PROBE_SCRIPT = """
import json
import models.init_db as init_db
import models.async_db as async_db
import models.audit_db as audit_db

print(json.dumps({
    "init_db": init_db.DATABASE_URL,
    "async_db": async_db.ASYNC_DATABASE_URL,
    "audit_db_sync": audit_db.AUDIT_DATABASE_URL,
    "audit_db_async": audit_db.AUDIT_ASYNC_DATABASE_URL,
}))
"""


class RealModuleUrlEncodingTest(unittest.TestCase):
    """真实子进程：注入含特殊字符的 DB_PASSWORD，验证三个模块产出的连接串
    都能正确解析回原密码。不需要真的连上 MySQL——create_engine/
    create_async_engine 都是懒连接，只是构造 URL 阶段就已经能证明对错。"""

    def test_special_char_db_password_across_init_async_audit(self):
        import os

        env = dict(os.environ)
        env["DB_PASSWORD"] = _NASTY_PASSWORD
        # AUDIT_DB_USER/PASSWORD 不设，让它退回 DB_USER/DB_PASSWORD——
        # 顺带验证第30节修的那个 `or` 兜底跟这里的编码修复叠加使用也没问题。
        env.pop("AUDIT_DB_USER", None)
        env.pop("AUDIT_DB_PASSWORD", None)

        result = subprocess.run(
            [sys.executable, "-c", _PROBE_SCRIPT],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr[-4000:])
        urls = json.loads(result.stdout.strip().splitlines()[-1])

        for label, url_str in urls.items():
            parsed = make_url(url_str)
            self.assertEqual(parsed.password, _NASTY_PASSWORD, msg=f"{label}: {url_str}")

        self.assertIn("mysql+pymysql://", urls["init_db"])
        self.assertIn("mysql+asyncmy://", urls["async_db"])
        self.assertIn("mysql+pymysql://", urls["audit_db_sync"])
        self.assertIn("mysql+asyncmy://", urls["audit_db_async"])


if __name__ == "__main__":
    unittest.main()
