"""SQL 注入专项。

1. 静态守卫：应用代码里允许拼接进 SQL 的只有“写死的标识符”（表名 / 列名 / 生成的 :占位符），新增的拼接一律失败，
   必须登记到白名单并说明为什么安全；`name_lookup.names` 对传入的表名、列名做白名单格式校验。
2. 模糊测试：从 OpenAPI 自动枚举所有 GET 路由和 JSON 请求体路由，把注入载荷填进每一个字符串参数，
   用真实用户（普通 + 管理员）、真实 MySQL 发请求；基线（正常字符串）不是 5xx 而载荷触发 5xx、响应里泄漏数据库报错、
   或者时间盲注让请求明显变慢，都算失败。
"""
import json
import pathlib
import re
import time
import unittest

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

ROOT = pathlib.Path(__file__).resolve().parent.parent
_AVAILABLE, _WHY = rc.route_tests_available()

# 允许把变量拼进 SQL 文本的位置：文件 → 必须是写死的标识符 / 生成的占位符
SQL_CONCAT_ALLOWLIST = {
    "service/admin_service.py": "遍历写死的表名元组",
    "service/enterprise_access.py": "由 range(len(codes)) 生成的 :c0,:c1 占位符，值走绑定参数",
    "service/name_lookup.py": "table/column 由调用方代码写死，且函数内再做标识符格式校验；ids 走生成的占位符",
    "service/responsibility_service.py": "由 enumerate 生成的 :i0,:i1 占位符，值走绑定参数",
}
_SQL_BUILT = re.compile(r"""(?:text|execute)\(\s*f["']|f["']\s*(?:SELECT|INSERT|UPDATE|DELETE)\b|["']\s*(?:SELECT|INSERT|UPDATE|DELETE)\b[^"']*["']\s*(?:%|\+)\s*\w|\.format\([^)]*\)[^\n]*(?:SELECT|INSERT|UPDATE|DELETE)\b""", re.I)


def scan_string_built_sql():
    offenders = {}
    for folder in ("service", "FasdtApi", "models", "utils"):
        for path in (ROOT / folder).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                if line.strip().startswith("#") or not _SQL_BUILT.search(line):
                    continue
                if "last_error" in line or "redact_text" in line:      # 日志/错误消息里的 f-string，不是 SQL
                    continue
                offenders.setdefault(relative, []).append(number)
    return offenders


class StaticGuardTest(unittest.TestCase):
    def test_no_new_string_built_sql(self):
        unexpected = {f: lines for f, lines in scan_string_built_sql().items() if f not in SQL_CONCAT_ALLOWLIST}
        self.assertEqual(unexpected, {}, "出现了新的字符串拼接 SQL：请改成绑定参数；确实只拼写死的标识符时，登记到 SQL_CONCAT_ALLOWLIST 并写明原因")

    def test_allowlist_has_no_stale_entries_and_the_scanner_really_detects(self):
        found = scan_string_built_sql()
        self.assertEqual(set(SQL_CONCAT_ALLOWLIST) - set(found), set(),
                         "白名单里有已经不再拼接 SQL 的文件（或者扫描正则失效了）：删掉对应条目")

    def test_java_sql_uses_placeholders_only(self):
        """Java 业务服务：JdbcTemplate / @Query / createQuery 的 SQL 文本里不能把变量拼进去（只能用 ? 或 :name 占位符）。
        把一条长 SQL 拆成几个字符串字面量用 + 连起来是允许的。"""
        java = ROOT / "enterprise-business-hub" / "src" / "main" / "java"
        self.assertTrue(java.exists())
        start = re.compile(r"jdbc\w*\.(?:query\w*|update|execute|batchUpdate)\(|createNativeQuery\(|createQuery\(|@Query\(")
        variable_concat = re.compile(r'"\s*\+\s*[A-Za-z_(]')            # 字符串字面量后面接了变量 / 方法调用
        offenders, files = [], 0
        for path in java.rglob("*.java"):
            files += 1
            source = path.read_text(encoding="utf-8", errors="ignore")
            for match in start.finditer(source):
                depth, i = 0, match.end() - 1
                while i < len(source):
                    depth += source[i] == "("
                    depth -= source[i] == ")"
                    if depth == 0:
                        break
                    i += 1
                if variable_concat.search(source[match.start():i + 1]):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:{source[:match.start()].count(chr(10)) + 1}")
        self.assertGreater(files, 50)
        self.assertEqual(offenders, [], "Java 里出现了把变量拼进 SQL 的写法：改成占位符")

    def test_name_lookup_rejects_non_identifiers(self):
        import asyncio
        from service import name_lookup

        class FakeDb:
            async def execute(self, *a, **k):
                raise AssertionError("不应该执行到数据库")
        for table, column in (("user; DROP TABLE user", "name"), ("`user` WHERE 1=1 --", "name"), ("user", "name FROM user --"), ("a b", "c"), ("", "name"), ("user", "")):
            with self.subTest(table=table, column=column), self.assertRaises(ValueError):
                asyncio.run(name_lookup.names(FakeDb(), table, [1], column))
        # 合法写法不受影响（反引号包裹的 `user` 是现有调用方在用的）
        class OkDb:
            async def execute(self, statement, params):
                class R:
                    def all(self):
                        return [(1, "x")]
                return R()
        self.assertEqual(asyncio.run(name_lookup.names(OkDb(), "`user`", [1], "name")), {1: "x"})


# ---------------------------------------------------------------- 模糊测试
PAYLOADS = ["'", "\"", "' OR '1'='1' -- ", "1; DROP TABLE user; --", "' UNION SELECT 1,2,3,4,5 -- ", "\\", "`", "' OR SLEEP(3) -- ", "\u0000", "；DROP TABLE x"]
BASELINE = "baseline-value"
SQL_ERROR = re.compile(r"You have an error in your SQL|SQL syntax|pymysql\.err|asyncmy|sqlalchemy\.|OperationalError|ProgrammingError|IntegrityError|mysql_|ORA-\d{5}|"
                       r"SQLSTATE|Unknown column|Traceback \(most recent call last\)", re.I)
# 不适合自动化请求的路由：会真的调用模型/外网、产生不可逆副作用、改动当前测试账号的登录态、需要文件上传或长连接
SKIP = re.compile(r"/chat|/stream|/logout|/revoke|/password|/login|/register|/sms|/upload|/import|/export|/crawl|/embedding|/rerank|/debug|/dry-run|/run$|/run/|"
                  r"/sandbox|/restart|/shutdown|/purge|/delete-all|/web-monitor|/widgets/.*/refresh|/pipelines/.*/run|/automation$|/automation/batch|/health|/metrics|"
                  r"/admin/users/\d+/(disable|delete)|/user/delete|/connectors/.*/test|/channels/.*/test|/notification-channels/.*/test|/docs|/openapi|/redoc", re.I)


def _resolve(schema, spec):
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
    return schema


REAL_IDS = {}      # setUpClass 里填：{"team_id": 真实部门, "organization_id": 真实企业}


def _fill(schema, spec, value, depth=0, field=""):
    """按 JSON Schema 生成请求体：字符串字段填 value，数字填 1，其余给最小合法值。"""
    schema = _resolve(schema, spec)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if _resolve(o, spec).get("type") != "null"]
            return _fill(options[0], spec, value, depth + 1) if options else None
    kind = schema.get("type")
    if kind == "string":
        if schema.get("format") == "date-time":
            return "2026-01-01T00:00:00"
        if schema.get("enum"):
            return schema["enum"][0]
        return value
    if kind in ("integer", "number"):
        return REAL_IDS.get(field, max(schema.get("minimum", 1), 1))
    if kind == "boolean":
        return False
    if kind == "array":
        return [] if depth > 2 else [_fill(schema.get("items", {}), spec, value, depth + 1, field)]
    if kind == "object" or "properties" in schema:
        if depth > 3:
            return {}
        return {name: _fill(sub, spec, value, depth + 1, name) for name, sub in schema.get("properties", {}).items()}
    return value


@unittest.skipUnless(_AVAILABLE, _WHY)
class FuzzTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import text
        from models.init_db import SessionLocal
        from tests.test_enterprise_access import _add_org_member, _add_team_member, _create_org, _create_team
        cls.client = rc.make_client()
        cls.user = rc.create_user("sqli-u")
        cls.admin = rc.create_user("sqli-a", admin=True)
        cls.db = SessionLocal()
        cls.org = _create_org(cls.db, "sqli-org", cls.admin["id"])
        cls.team = _create_team(cls.db, cls.org, "sqli-team", cls.admin["id"])
        for member in (cls.user, cls.admin):
            _add_org_member(cls.db, cls.org, member["id"], "member")
        _add_team_member(cls.db, cls.team, cls.user["id"], "admin")             # 部门负责人：能走到最多的业务路由
        _add_team_member(cls.db, cls.team, cls.admin["id"], "member")
        cls.db.execute(text("UPDATE teams SET department_code='hr' WHERE id=:t"), {"t": cls.team})
        cls.db.commit()
        REAL_IDS.update({"team_id": cls.team, "organization_id": cls.org, "teamId": cls.team})
        cls.spec = cls.client.get("/openapi.json").json()

    @classmethod
    def tearDownClass(cls):
        from sqlalchemy import text
        rc.cleanup()
        cls.db.commit()
        for table in ("work_item", "notification", "automation_work", "attendance_anomaly", "attendance_punch", "attendance_import", "attendance_alias", "attendance_rule", "attendance_calendar"):
            for column in ("organization_id", "team_id"):
                try:
                    cls.db.execute(text(f"DELETE FROM {table} WHERE {column}=:v"), {"v": cls.org if column == "organization_id" else cls.team})
                    cls.db.commit()
                except Exception:  # noqa: BLE001 —— 这张表没有这一列：跳过
                    cls.db.rollback()
        cls.db.execute(text("DELETE FROM team_members WHERE team_id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM teams WHERE id=:t"), {"t": cls.team})
        cls.db.execute(text("DELETE FROM organization_members WHERE organization_id=:o"), {"o": cls.org})
        cls.db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": cls.org})
        cls.db.commit()
        cls.db.close()
        REAL_IDS.clear()

    def targets(self):
        for path, methods in self.spec["paths"].items():
            if SKIP.search(path):
                continue
            for method, operation in methods.items():
                if method.upper() not in ("GET", "POST", "PUT", "PATCH"):
                    continue
                body = operation.get("requestBody", {}).get("content", {})
                if body and "application/json" not in body:
                    continue                               # multipart / 表单上传不在这里测
                if method.upper() != "GET" and not path.startswith(("/enterprise/", "/knowledge", "/agent", "/user", "/skill", "/work", "/todos", "/notification", "/widgets", "/conversation", "/memory", "/admin/issues", "/orchestration")):
                    continue                               # 写接口只测业务路由，避免对管理类写接口产生真实副作用
                yield path, method.upper(), operation

    def build(self, path, method, operation, value):
        url = path
        query = {}
        for parameter in operation.get("parameters", []):
            schema = _resolve(parameter.get("schema", {}), self.spec)
            if parameter["in"] == "path":
                filled = "1" if schema.get("type") in ("integer", "number") else value
                url = url.replace("{" + parameter["name"] + "}", str(filled) if schema.get("type") in ("integer", "number") else __import__("urllib.parse", fromlist=["quote"]).quote(str(value), safe=""))
            elif parameter["in"] == "query":
                type_ = schema.get("type") or next((_resolve(o, self.spec).get("type") for o in schema.get("anyOf", []) if _resolve(o, self.spec).get("type") != "null"), "string")
                query[parameter["name"]] = REAL_IDS.get(parameter["name"], 1) if type_ in ("integer", "number") else (False if type_ == "boolean" else value)
        body = None
        content = operation.get("requestBody", {}).get("content", {}).get("application/json")
        if content:
            body = _fill(content.get("schema", {}), self.spec, value)
        return url, query, body

    def send(self, headers, path, method, operation, value):
        url, query, body = self.build(path, method, operation, value)
        started = time.time()
        response = self.client.request(method, url, params=query, json=body, headers=headers)
        return response, time.time() - started

    def run_fuzz(self, who, label):
        problems, count = [], 0
        for path, method, operation in self.targets():
            try:
                base, _ = self.send(who["headers"], path, method, operation, BASELINE)
            except Exception:  # noqa: BLE001 —— 客户端抛异常的路由（流式 / 未处理）不属于本测试的判断范围
                continue
            if base.status_code in (401, 403, 404, 405, 422) and base.status_code != 422:
                continue                                    # 没权限 / 找不到：根本进不到查询逻辑
            for payload in PAYLOADS:
                count += 1
                try:
                    response, elapsed = self.send(who["headers"], path, method, operation, payload)
                except Exception as exc:  # noqa: BLE001
                    if base.status_code < 500:
                        problems.append(f"{label} {method} {path} payload={payload!r}: 请求抛出 {type(exc).__name__}")
                    continue
                text = response.text[:4000]
                if response.status_code >= 500 and base.status_code < 500:
                    problems.append(f"{label} {method} {path} payload={payload!r}: 基线 {base.status_code} → 载荷 {response.status_code}")
                elif SQL_ERROR.search(text) and not SQL_ERROR.search(base.text[:4000]):
                    problems.append(f"{label} {method} {path} payload={payload!r}: 响应泄漏数据库/堆栈信息：{SQL_ERROR.search(text).group(0)}")
                elif "SLEEP" in payload and elapsed > 2.5:
                    problems.append(f"{label} {method} {path} payload={payload!r}: 疑似时间盲注，耗时 {elapsed:.1f}s")
        return problems, count

    def test_normal_user_cannot_inject_anywhere(self):
        problems, count = self.run_fuzz(self.user, "user")
        self.assertGreater(count, 200, "枚举到的请求太少，OpenAPI 解析可能出了问题")
        self.assertEqual(problems, [], "\n" + "\n".join(problems[:40]))

    def test_admin_cannot_inject_anywhere(self):
        with rc.admin_env(self.admin["name"]):
            problems, count = self.run_fuzz(self.admin, "admin")
        self.assertGreater(count, 200)
        self.assertEqual(problems, [], "\n" + "\n".join(problems[:40]))


if __name__ == "__main__":
    unittest.main()
