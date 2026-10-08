"""登录令牌放进 HttpOnly Cookie + 签名双重提交 CSRF（service/session_cookie.py）。

走真实路由和真实 MySQL：登录下发的 Cookie 属性、只靠 Cookie 能不能访问、会改数据的请求缺 / 错 CSRF 是否被拒、
Bearer 头不受影响、退出登录和“退出所有设备”之后 Cookie 是否真的失效。没有本机 MySQL 时自动跳过。"""
import os
import unittest
from unittest import mock

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class SessionCookieTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = mock.patch.dict(os.environ, {"LOGIN_IP_RATE_LIMIT": "1000", "LOGIN_USER_RATE_LIMIT": "1000"})
        cls._env.start()
        cls.user = rc.create_user("cookie-user")

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        rc.cleanup()

    def browser(self):
        """一个像浏览器的客户端：登录后只带 Cookie，不带 Authorization。"""
        client = rc.make_client()
        response = client.post("/user/login", json={"name": self.user["name"], "password": self.user["password"]})
        self.assertEqual(response.status_code, 200, response.text)
        return client, response

    def csrf(self, client):
        return client.cookies.get("csrf_token")

    def test_login_sets_httponly_session_cookie_and_readable_csrf_cookie(self):
        _, response = self.browser()
        cookies = response.headers.get_list("set-cookie")
        session = next(c for c in cookies if c.startswith("session_token="))
        csrf = next(c for c in cookies if c.startswith("csrf_token="))
        self.assertIn("HttpOnly", session)
        self.assertNotIn("HttpOnly", csrf, "前端要读它放进请求头，不能是 HttpOnly")
        for cookie in (session, csrf):
            self.assertIn("samesite=lax", cookie.lower())
            self.assertIn("Path=/", cookie)
            self.assertIn("Max-Age=", cookie)
        self.assertIn("access_token", response.json(), "响应体里的令牌仍然保留，给脚本 / 集成使用")

    def test_secure_flag_follows_configuration(self):
        with mock.patch.dict(os.environ, {"SESSION_COOKIE_SECURE": "1"}):
            _, response = self.browser()
        self.assertTrue(all("Secure" in c for c in response.headers.get_list("set-cookie")))
        with mock.patch.dict(os.environ, {"SESSION_COOKIE_SECURE": "0"}):
            _, response = self.browser()
        self.assertFalse(any("Secure" in c for c in response.headers.get_list("set-cookie")))

    def test_cookie_alone_authenticates_safe_requests(self):
        client, _ = self.browser()
        me = client.get("/user/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["username"], self.user["name"])

    def test_state_changing_request_without_csrf_header_is_rejected(self):
        client, _ = self.browser()
        response = client.put("/user/profile", json={"occupation": "x"})
        self.assertEqual(response.status_code, 403, response.text)

    def test_wrong_csrf_header_is_rejected(self):
        client, _ = self.browser()
        response = client.put("/user/profile", json={"occupation": "x"}, headers={"X-CSRF-Token": "0" * 48})
        self.assertEqual(response.status_code, 403, response.text)

    def test_correct_csrf_header_is_accepted(self):
        client, _ = self.browser()
        response = client.put("/user/profile", json={"occupation": "测试"}, headers={"X-CSRF-Token": self.csrf(client)})
        self.assertEqual(response.status_code, 200, response.text)

    def test_csrf_cookie_must_match_even_if_the_header_is_right(self):
        """攻击者能往浏览器里塞 Cookie（比如控制了某个子域名）也不行：值是服务端密钥算出来的。"""
        client, _ = self.browser()
        good = self.csrf(client)
        client.cookies.set("csrf_token", "f" * 48)
        response = client.put("/user/profile", json={"occupation": "x"}, headers={"X-CSRF-Token": good})
        self.assertEqual(response.status_code, 403, response.text)

    def test_csrf_value_from_another_session_does_not_work(self):
        client_a, _ = self.browser()
        client_b, _ = self.browser()
        # 两次登录各自有各自的令牌（时间戳不同），A 的 CSRF 值不能用在 B 的会话上
        if client_a.cookies.get("session_token") == client_b.cookies.get("session_token"):
            self.skipTest("两次登录签发了相同的令牌（同一秒内），无法区分会话")
        response = client_b.put("/user/profile", json={"occupation": "x"}, headers={"X-CSRF-Token": self.csrf(client_a)})
        self.assertEqual(response.status_code, 403, response.text)

    def test_bearer_header_needs_no_csrf(self):
        client = rc.make_client()
        response = client.put("/user/profile", json={"occupation": "脚本"}, headers=self.user["headers"])
        self.assertEqual(response.status_code, 200, response.text)

    def test_bearer_wins_over_a_stale_cookie_and_cookie_does_not_widen_access(self):
        client, _ = self.browser()
        client.cookies.set("session_token", "not-a-valid-token")
        ok = client.get("/user/me", headers=self.user["headers"])
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_missing_credentials_is_401(self):
        response = rc.make_client().get("/user/me")
        self.assertEqual(response.status_code, 401, response.text)

    def test_garbage_cookie_is_401(self):
        client = rc.make_client()
        client.cookies.set("session_token", "garbage")
        self.assertEqual(client.get("/user/me").status_code, 401)

    def test_logout_clears_both_cookies(self):
        client, _ = self.browser()
        response = client.post("/user/logout")
        self.assertEqual(response.status_code, 200)
        cleared = " ".join(response.headers.get_list("set-cookie")).lower()
        self.assertIn("session_token=", cleared)
        self.assertIn("csrf_token=", cleared)
        self.assertIn("max-age=0", cleared)
        self.assertEqual(client.get("/user/me").status_code, 401)

    def test_logout_all_invalidates_the_session_everywhere(self):
        user = rc.create_user("cookie-logout-all")
        client = rc.make_client()
        client.post("/user/login", json={"name": user["name"], "password": user["password"]})
        other = rc.make_client()
        other.post("/user/login", json={"name": user["name"], "password": user["password"]})
        response = client.post("/user/logout-all", headers={"X-CSRF-Token": client.cookies.get("csrf_token")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(other.get("/user/me").status_code, 401, "另一台设备上的 Cookie 会话也要立即失效")
        self.assertEqual(client.get("/user/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
