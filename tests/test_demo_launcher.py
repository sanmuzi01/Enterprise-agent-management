"""一键演示脚本的纯逻辑：子进程环境（离线模型、非生产、放宽演示登录限流）和端口/进程辅助。"""
import importlib.util
import os
import pathlib
import socket
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("demo_launcher", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


class ChildEnvTest(unittest.TestCase):
    def test_demo_environment_is_offline_nonproduction_and_relaxes_login_limit(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            env = demo.child_env()
        self.assertEqual(env["OFFLINE_DEMO_MODEL"], "1")
        self.assertEqual(env["APP_ENV"], "development")        # 即使外面设成生产，演示脚本也不会把子进程带成生产
        self.assertEqual(env["LOGIN_IP_RATE_LIMIT"], "300")

    def test_explicit_login_limit_is_respected(self):
        with patch.dict(os.environ, {"LOGIN_IP_RATE_LIMIT": "5"}):
            self.assertEqual(demo.child_env()["LOGIN_IP_RATE_LIMIT"], "5")

    def test_hub_password_follows_db_password_in_dotenv(self):
        with patch.object(demo, "dotenv_values", return_value={"DB_PASSWORD": "pw", "DB_USER": "u"}), \
                patch.dict(os.environ, {}, clear=True):
            env = demo.child_env()
        self.assertEqual((env["ENTERPRISE_DB_PASSWORD"], env["ENTERPRISE_DB_USER"]), ("pw", "u"))


class HelperTest(unittest.TestCase):
    def test_port_open_detects_a_listening_socket(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            self.assertTrue(demo.port_open(server.getsockname()[1]))
        self.assertFalse(demo.port_open(1))

    def test_pid_alive(self):
        self.assertTrue(demo.pid_alive(os.getpid()))
        self.assertFalse(demo.pid_alive(999999))

    def test_healthy_is_false_for_closed_port(self):
        self.assertFalse(demo.healthy("http://127.0.0.1:1/"))

    def test_read_pids_tolerates_missing_or_corrupt_file(self):
        with patch.object(demo, "PID_FILE", pathlib.Path("does-not-exist.json")):
            self.assertEqual(demo.read_pids(), {})


if __name__ == "__main__":
    unittest.main()
