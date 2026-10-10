import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from service.config_validation import assert_runtime_config, validate_runtime_config


def _valid_env():
    return {
        "APP_ENV": "production",
        "DB_USER": "app_runtime",
        "DB_PASSWORD": "strong-password",
        "MYSQL_ROOT_PASSWORD": "strong-root-password-different-from-db-password",
        "DB_HOST": "mysql",
        "DB_PORT": "3306",
        "DB_NAME": "agent_sql",
        "JWT_SECRET_KEY": "strong-jwt-secret-value-for-production",
        "LLM_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "SMS_PROVIDER": "webhook",
        "SMS_WEBHOOK_URL": "https://sms.example.test/send",
        "SMS_WEBHOOK_TOKEN": "strong-sms-token",
        "SMS_EXPOSE_DEV_CODE": "0",
        "TRUSTED_HOSTS": "example.test,www.example.test,api",
        "CORS_ALLOW_ORIGINS": "https://example.test,https://www.example.test",
        "ADMIN_PASSWORD": "strong-admin-password",
        # 第五轮审计 P1-4 新增的必填项：独立审计账号 + 企业业务中心签名密钥/账号 +
        # 关掉进程内自动建表。
        "AUDIT_DB_USER": "audit_writer",
        "AUDIT_DB_PASSWORD": "strong-audit-password",
        "ENTERPRISE_HUB_HMAC_SECRET": "strong-hmac-secret-shared-with-enterprise-business-hub",
        "ENTERPRISE_DB_USER": "app_runtime_java",
        "ENTERPRISE_DB_PASSWORD": "strong-java-db-password",
        "DB_AUTO_BOOTSTRAP": "0",
    }


class ConfigValidationTest(unittest.TestCase):
    def test_production_config_accepts_valid_values(self):
        with patch.dict(os.environ, _valid_env(), clear=True):
            result = validate_runtime_config()
            self.assertTrue(result["ok"])
            self.assertEqual(result["environment"], "production")
            assert_runtime_config()

    def test_every_check_appears_exactly_once(self):
        """曾经把 Cookie 检查插进 Redis 的 if/else 中间：生产报告里 REDIS_URL 出现两次、Cookie 成功项缺失，而总结果仍然是成功，没人发现。"""
        for label, env in (("生产", _valid_env()), ("开发", {"APP_ENV": "development", "REDIS_URL": "redis://127.0.0.1:6379/0"}),
                           ("开发-无 Redis", {"APP_ENV": "development"})):
            with patch.dict(os.environ, env, clear=True):
                names = [c["name"] for c in validate_runtime_config()["checks"]]
            self.assertEqual(len(names), len(set(names)), f"{label}：检查项重复 {sorted({n for n in names if names.count(n) > 1})}")

    def test_production_reports_redis_and_secure_cookie_once_each(self):
        with patch.dict(os.environ, _valid_env(), clear=True):
            checks = {c["name"]: c for c in validate_runtime_config()["checks"]}
        self.assertEqual(checks["REDIS_URL"]["level"], "ok")
        self.assertEqual(checks["SESSION_COOKIE_SECURE"]["level"], "ok")
        self.assertIn("Secure", checks["SESSION_COOKIE_SECURE"]["message"])

    def test_development_does_not_report_the_cookie_check_and_still_reports_redis(self):
        with patch.dict(os.environ, {"APP_ENV": "development", "REDIS_URL": "redis://127.0.0.1:6379/0"}, clear=True):
            checks = {c["name"]: c for c in validate_runtime_config()["checks"]}
        self.assertNotIn("SESSION_COOKIE_SECURE", checks)
        self.assertEqual(checks["REDIS_URL"]["level"], "ok")
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
            self.assertEqual({c["name"]: c for c in validate_runtime_config()["checks"]}["REDIS_URL"]["level"], "warn")

    def test_production_rejects_insecure_session_cookie(self):
        for value in ("0", "false", "No"):
            env = _valid_env()
            env["SESSION_COOKIE_SECURE"] = value
            with patch.dict(os.environ, env, clear=True):
                result = validate_runtime_config()
                self.assertFalse(result["ok"], value)
                self.assertIn("SESSION_COOKIE_SECURE", [c["name"] for c in result["checks"] if c["level"] == "error"])
                with self.assertRaises(RuntimeError):
                    assert_runtime_config()
        for value in ("", "1", "true"):
            env = _valid_env()
            env["SESSION_COOKIE_SECURE"] = value
            with patch.dict(os.environ, env, clear=True):
                self.assertTrue(validate_runtime_config()["ok"], value)

    def test_production_warns_when_token_endpoint_is_enabled(self):
        env = _valid_env()
        env["AUTH_TOKEN_ENDPOINT_ENABLED"] = "1"
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertTrue(result["ok"], "只是提醒，不阻止启动")
            self.assertIn("AUTH_TOKEN_ENDPOINT_ENABLED", [c["name"] for c in result["checks"] if c["level"] == "warn"])
        with patch.dict(os.environ, _valid_env(), clear=True):
            self.assertNotIn("AUTH_TOKEN_ENDPOINT_ENABLED", [c["name"] for c in validate_runtime_config()["checks"]])

    def test_production_warns_when_redis_has_no_password(self):
        with patch.dict(os.environ, _valid_env(), clear=True):
            result = validate_runtime_config()
            self.assertTrue(result["ok"], "没有口令只是警告，不阻止启动（容器内部网络的 Redis 常见）")
            self.assertIn("REDIS_PASSWORD", [c["name"] for c in result["checks"] if c["level"] == "warn"])
        env = _valid_env()
        env["REDIS_URL"] = "rediss://:s3cret@redis.internal:6380/0"
        with patch.dict(os.environ, env, clear=True):
            self.assertNotIn("REDIS_PASSWORD", [c["name"] for c in validate_runtime_config()["checks"]])

    def test_production_config_rejects_placeholders(self):
        env = _valid_env()
        env["JWT_SECRET_KEY"] = "change-me-random-64-hex-or-long-secret"
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            with self.assertRaises(RuntimeError):
                assert_runtime_config()

    def test_production_config_requires_admin_password(self):
        env = _valid_env()
        env.pop("ADMIN_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            self.assertTrue(
                any(item["name"] == "ADMIN_PASSWORD" for item in result["checks"] if item["level"] == "error")
            )

    def test_production_config_rejects_weak_admin_password(self):
        env = _valid_env()
        env["ADMIN_PASSWORD"] = "admin123"
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])

    def test_production_config_rejects_known_demo_secrets(self):
        """docker-compose.yml 里为了让 `docker compose up` 零配置跑通写死的公开演示密钥，
        即使格式合法（尤其 LLM_ENCRYPTION_KEY 是一把能用的 Fernet key），生产环境也必须拒绝。"""
        env = _valid_env()
        env["JWT_SECRET_KEY"] = "dev-only-not-a-secret-change-me"
        env["LLM_ENCRYPTION_KEY"] = "LG5sThiGcVsg9jRbbN_fezONjfKdo3E72yQPYIUwZHQ="
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            error_names = {item["name"] for item in result["checks"] if item["level"] == "error"}
            self.assertIn("JWT_SECRET_KEY", error_names)
            self.assertIn("LLM_ENCRYPTION_KEY", error_names)
            with self.assertRaises(RuntimeError):
                assert_runtime_config()

    def test_production_config_requires_mysql_root_password(self):
        env = _valid_env()
        env.pop("MYSQL_ROOT_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            self.assertTrue(
                any(item["name"] == "MYSQL_ROOT_PASSWORD" for item in result["checks"] if item["level"] == "error")
            )

    def test_production_config_rejects_mysql_root_password_same_as_db_password(self):
        # 业务运行时账号密码和 root 密码相同，一个被攻破的应用进程泄露的凭据
        # 就直接等于泄露了 root——限权账号完全形同虚设，必须拦。
        env = _valid_env()
        env["MYSQL_ROOT_PASSWORD"] = env["DB_PASSWORD"]
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            with self.assertRaises(RuntimeError):
                assert_runtime_config()

    def test_production_config_rejects_mysql_root_password_same_as_enterprise_db_password(self):
        env = _valid_env()
        env["ENTERPRISE_DB_PASSWORD"] = "strong-java-db-password"
        env["MYSQL_ROOT_PASSWORD"] = env["ENTERPRISE_DB_PASSWORD"]
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])

    def test_production_config_requires_audit_db_credentials(self):
        env = _valid_env()
        env.pop("AUDIT_DB_USER", None)
        env.pop("AUDIT_DB_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            self.assertTrue(
                any(item["name"] == "AUDIT_DB_USER" for item in result["checks"] if item["level"] == "error")
            )
            with self.assertRaises(RuntimeError):
                assert_runtime_config()

    def test_production_config_rejects_audit_db_user_same_as_main_db_user(self):
        """真实撞见的 bug（models/audit_db.py）：AUDIT_DB_USER 跟 DB_USER 相同——
        不管是显式配成一样，还是 docker-compose 把 AUDIT_DB_PASSWORD 设成空字符串
        导致 os.getenv(key, default) 悄悄退回主账号——都必须在生产环境被拦下来，
        否则审计账号形同虚设。"""
        env = _valid_env()
        env["AUDIT_DB_USER"] = env["DB_USER"]
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            self.assertTrue(
                any(item["name"] == "AUDIT_DB_USER" for item in result["checks"] if item["level"] == "error")
            )

    def test_production_config_rejects_audit_db_password_placeholder(self):
        env = _valid_env()
        env["AUDIT_DB_PASSWORD"] = "change-me-strong-audit-password"
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])

    def test_production_config_requires_enterprise_hub_hmac_secret(self):
        env = _valid_env()
        env.pop("ENTERPRISE_HUB_HMAC_SECRET", None)
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            self.assertTrue(
                any(item["name"] == "ENTERPRISE_HUB_HMAC_SECRET" for item in result["checks"]
                    if item["level"] == "error")
            )

    def test_production_config_requires_enterprise_db_credentials(self):
        env = _valid_env()
        env.pop("ENTERPRISE_DB_USER", None)
        env.pop("ENTERPRISE_DB_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertFalse(result["ok"])
            error_names = {item["name"] for item in result["checks"] if item["level"] == "error"}
            self.assertIn("ENTERPRISE_DB_USER", error_names)
            self.assertIn("ENTERPRISE_DB_PASSWORD", error_names)

    def test_production_config_rejects_root_as_any_runtime_db_user(self):
        for var_name in ("DB_USER", "AUDIT_DB_USER", "ENTERPRISE_DB_USER"):
            env = _valid_env()
            env[var_name] = "root"
            with patch.dict(os.environ, env, clear=True):
                result = validate_runtime_config()
                self.assertFalse(result["ok"], f"{var_name}=root 应该被拒绝")
                error_names = {item["name"] for item in result["checks"] if item["level"] == "error"}
                self.assertIn(f"{var_name}_NOT_ROOT", error_names)

    def test_production_config_requires_db_auto_bootstrap_disabled(self):
        for bad_value in ("1", "true", ""):
            env = _valid_env()
            if bad_value:
                env["DB_AUTO_BOOTSTRAP"] = bad_value
            else:
                env.pop("DB_AUTO_BOOTSTRAP", None)
            with patch.dict(os.environ, env, clear=True):
                result = validate_runtime_config()
                self.assertFalse(result["ok"], f"DB_AUTO_BOOTSTRAP={bad_value!r} 应该被拒绝")
                self.assertTrue(
                    any(item["name"] == "DB_AUTO_BOOTSTRAP" for item in result["checks"] if item["level"] == "error")
                )

    def test_development_config_allows_missing_redis(self):
        env = _valid_env()
        env["APP_ENV"] = "development"
        env["REDIS_URL"] = ""
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["warning_count"], 1)

    def test_development_config_allows_missing_audit_and_enterprise_hub_vars(self):
        """这几条是第五轮审计 P1-4 新增的生产环境专属校验——本地开发/CI 没配这些账号
        时（AUDIT_DB_USER 留空退回主账号、DB_AUTO_BOOTSTRAP 用默认的自动建表）必须
        还能正常跑，不能变成新的硬依赖。"""
        env = _valid_env()
        env["APP_ENV"] = "development"
        for name in ("AUDIT_DB_USER", "AUDIT_DB_PASSWORD", "ENTERPRISE_HUB_HMAC_SECRET",
                     "ENTERPRISE_DB_USER", "ENTERPRISE_DB_PASSWORD", "DB_AUTO_BOOTSTRAP"):
            env.pop(name, None)
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertTrue(result["ok"])

    def test_aliyun_sms_requires_credentials_and_system_template(self):
        env = _valid_env()
        env["SMS_PROVIDER"] = "aliyun"
        env.pop("SMS_WEBHOOK_URL")
        env.pop("SMS_WEBHOOK_TOKEN")
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            missing = {item["name"] for item in result["checks"] if item["level"] == "error"}
            self.assertEqual(missing, {
                "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
                "SMS_ALIYUN_SIGN_NAME", "SMS_ALIYUN_TEMPLATE_CODE",
            })
        env.update({
            "ALIBABA_CLOUD_ACCESS_KEY_ID": "test-key-id",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test-key-secret",
            "SMS_ALIYUN_SIGN_NAME": "system-sign",
            "SMS_ALIYUN_TEMPLATE_CODE": "100001",
        })
        with patch.dict(os.environ, env, clear=True):
            assert_runtime_config()


if __name__ == "__main__":
    unittest.main()
