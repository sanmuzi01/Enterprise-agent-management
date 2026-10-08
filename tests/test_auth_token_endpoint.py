"""浏览器登录与脚本令牌接口分离：
· POST /user/login（浏览器）：只下发 HttpOnly Cookie，响应体里没有 JWT；
· POST /auth/token（脚本 / CLI / 集成）：显式返回 Bearer 令牌，不下发 Cookie，有单独的限流和审计，可用 AUTH_TOKEN_ENDPOINT_ENABLED 关闭
  （生产默认关闭）。
走真实路由和真实 MySQL；没有本机 MySQL 时自动跳过。"""
import os
import unittest
from unittest import mock

from tests import _route_client as rc

from FasdtApi import auth_token

_AVAILABLE, _WHY = rc.route_tests_available()


class EndpointSwitchTests(unittest.TestCase):
    def test_default_is_on_outside_production_and_off_in_production(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "development"}):
            os.environ.pop("AUTH_TOKEN_ENDPOINT_ENABLED", None)
            self.assertTrue(auth_token.endpoint_enabled())
        with mock.patch.dict(os.environ, {"APP_ENV": "production"}):
            os.environ.pop("AUTH_TOKEN_ENDPOINT_ENABLED", None)
            self.assertFalse(auth_token.endpoint_enabled())

    def test_explicit_setting_wins(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "production", "AUTH_TOKEN_ENDPOINT_ENABLED": "1"}):
            self.assertTrue(auth_token.endpoint_enabled())
        with mock.patch.dict(os.environ, {"APP_ENV": "development", "AUTH_TOKEN_ENDPOINT_ENABLED": "0"}):
            self.assertFalse(auth_token.endpoint_enabled())


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class AuthTokenRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = mock.patch.dict(os.environ, {"LOGIN_IP_RATE_LIMIT": "1000", "LOGIN_USER_RATE_LIMIT": "1000",
                                                "TOKEN_IP_RATE_LIMIT": "1000", "TOKEN_USER_RATE_LIMIT": "1000"})
        cls._env.start()
        cls.user = rc.create_user("auth-token")
        cls.body = {"name": cls.user["name"], "password": cls.user["password"]}

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        rc.cleanup()

    def client(self):
        return rc.make_client()

    def test_browser_login_returns_no_jwt_in_the_body(self):
        response = self.client().post("/user/login", json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertNotIn("access_token", payload)
        self.assertNotIn("token_type", payload)
        self.assertEqual(payload["user_id"], self.user["id"])
        self.assertTrue(any(c.startswith("session_token=") for c in response.headers.get_list("set-cookie")))

    def test_token_endpoint_returns_a_working_bearer_token_and_no_cookie(self):
        client = self.client()
        response = client.post("/auth/token", json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["token_type"], "bearer")
        self.assertGreater(payload["expires_in"], 0)
        self.assertEqual(response.headers.get_list("set-cookie"), [], "令牌接口不下发 Cookie")
        me = client.get("/user/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
        self.assertEqual(me.status_code, 200, me.text)

    def test_wrong_password_is_rejected_like_login(self):
        response = self.client().post("/auth/token", json={**self.body, "password": "wrong-password-1"})
        self.assertEqual(response.status_code, 401, response.text)
        self.assertNotIn("access_token", response.text)

    def test_endpoint_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"AUTH_TOKEN_ENDPOINT_ENABLED": "0"}):
            response = self.client().post("/auth/token", json=self.body)
        self.assertEqual(response.status_code, 404, response.text)

    def test_token_endpoint_has_its_own_tighter_rate_limit(self):
        user = rc.create_user("auth-token-limit")
        body = {"name": user["name"], "password": user["password"]}
        client = self.client()
        with mock.patch.dict(os.environ, {"TOKEN_USER_RATE_LIMIT": "2", "TOKEN_IP_RATE_LIMIT": "1000"}):
            statuses = [client.post("/auth/token", json=body).status_code for _ in range(4)]
        self.assertEqual(statuses[:2], [200, 200])
        self.assertEqual(statuses[2:], [429, 429])
        # 浏览器登录的额度不受影响
        self.assertEqual(client.post("/user/login", json=body).status_code, 200)

    def test_token_issuance_is_audited(self):
        from sqlalchemy import text

        from models.init_db import SessionLocal
        user = rc.create_user("auth-token-audit")
        response = self.client().post("/auth/token", json={"name": user["name"], "password": user["password"]},
                                      headers={"User-Agent": "release-script/1.0"})
        self.assertEqual(response.status_code, 200, response.text)
        db = SessionLocal()
        try:
            rows = db.execute(text("SELECT detail FROM audit_event WHERE user_id=:u AND action='auth.token_issued'"), {"u": user["id"]}).fetchall()
        finally:
            db.close()
        self.assertEqual(len(rows), 1)
        self.assertIn("release-script/1.0", rows[0][0])
        self.assertNotIn(response.json()["access_token"], rows[0][0], "审计里不能记令牌本身")


if __name__ == "__main__":
    unittest.main()
