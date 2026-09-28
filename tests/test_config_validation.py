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
    }


class ConfigValidationTest(unittest.TestCase):
    def test_production_config_accepts_valid_values(self):
        with patch.dict(os.environ, _valid_env(), clear=True):
            result = validate_runtime_config()
            self.assertTrue(result["ok"])
            self.assertEqual(result["environment"], "production")
            assert_runtime_config()

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

    def test_development_config_allows_missing_redis(self):
        env = _valid_env()
        env["APP_ENV"] = "development"
        env["REDIS_URL"] = ""
        with patch.dict(os.environ, env, clear=True):
            result = validate_runtime_config()
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["warning_count"], 1)

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
