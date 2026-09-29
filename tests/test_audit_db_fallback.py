"""第五轮审计 P1-4：`models/audit_db.py` 的 AUDIT_DB_USER/PASSWORD 兜底逻辑。

真实撞见的 bug：docker-compose.prod.yml 给 AUDIT_DB_PASSWORD 写的是
`${AUDIT_DB_PASSWORD:-}`——`.env` 没设时，容器里这个环境变量会被设成空字符串，
不是"完全不存在"。原来的 `os.getenv(key, default)` 只有变量真的不存在时才会
用 default，变量存在但是空字符串会原样返回空字符串——导致审计连接尝试用一个
空密码去连一个不存在的账号，`service/audit_service.py` 的 broad except 把这个
连接失败吞掉，业务照常成功，但没有任何审计记录，而且不会有任何报错提示。

修法很简单（`os.getenv(key) or default`），但这个模块的兜底值是在模块导入时
就算好的顶层代码，不是函数内部逻辑，直接单测得用 importlib.reload 在补丁过的
环境变量下重新执行一遍模块顶层代码，测完再 reload 一次还原，不影响其它测试。
"""
import importlib
import os
import unittest
from unittest.mock import patch


class AuditDbFallbackTest(unittest.TestCase):
    def _reload_with_env(self, env: dict):
        import models.audit_db as audit_db_module
        with patch.dict(os.environ, env, clear=False):
            importlib.reload(audit_db_module)
            return audit_db_module.AUDIT_DB_USER, audit_db_module.AUDIT_DB_PASSWORD

    def test_empty_string_env_var_falls_back_like_unset(self):
        """核心回归：AUDIT_DB_USER/PASSWORD 显式设成空字符串（docker-compose 的
        `${VAR:-}` 效果）时，必须跟"完全没设这个变量"一样退回主账号，不能原样
        用空字符串去连一个不存在的账号。"""
        import models.audit_db as audit_db_module
        original_user, original_password = audit_db_module.AUDIT_DB_USER, audit_db_module.AUDIT_DB_PASSWORD
        try:
            user, password = self._reload_with_env({"AUDIT_DB_USER": "", "AUDIT_DB_PASSWORD": ""})
            from models.init_db import DB_PASSWORD, DB_USER
            self.assertEqual(user, DB_USER)
            self.assertEqual(password, DB_PASSWORD)
            self.assertNotEqual(user, "")
            self.assertNotEqual(password, "")
        finally:
            importlib.reload(audit_db_module)
            self.assertEqual(audit_db_module.AUDIT_DB_USER, original_user)
            self.assertEqual(audit_db_module.AUDIT_DB_PASSWORD, original_password)

    def test_explicit_value_is_used_when_set(self):
        """回归保护：真的配了独立账号时，必须用配的值，不能被兜底逻辑覆盖掉。"""
        import models.audit_db as audit_db_module
        try:
            user, password = self._reload_with_env({
                "AUDIT_DB_USER": "audit_writer_test", "AUDIT_DB_PASSWORD": "test-pw-123",
            })
            self.assertEqual(user, "audit_writer_test")
            self.assertEqual(password, "test-pw-123")
        finally:
            importlib.reload(audit_db_module)


if __name__ == "__main__":
    unittest.main()
