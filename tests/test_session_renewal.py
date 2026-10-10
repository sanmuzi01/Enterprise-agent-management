"""登录会话按使用续期（service/session_renewal.py）。

回归：之前登录令牌固定 60 分钟到期，一直在用也会突然被踢回登录页；后台每分钟的待办数轮询又会让“闲置”永远不成立。
"""
import calendar
import unittest
from datetime import timedelta
from unittest import mock

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()


def _ts(dt):
    return calendar.timegm(dt.utctimetuple())


class RenewalRuleTest(unittest.TestCase):
    def setUp(self):
        from service import session_renewal
        self.svc = session_renewal
        self.lifetime = session_renewal.lifetime_seconds()
        self.now = 2_000_000_000

    def payload(self, exp_in, auth_age=600, **extra):
        return {"user_id": 7, "username": "u", "ver": 3, "exp": self.now + exp_in, "auth_time": self.now - auth_age, **extra}

    def decode(self, token):
        from service.auth import decode_access_token
        return decode_access_token(token)

    def test_fresh_token_is_not_renewed(self):
        self.assertIsNone(self.svc.renewal_token(self.payload(self.lifetime - 60), now=self.now))

    def test_past_half_life_is_renewed_keeping_identity_version_and_login_time(self):
        token = self.svc.renewal_token(self.payload(self.lifetime // 2 - 60), now=self.now)
        self.assertIsNotNone(token)
        with mock.patch("service.session_renewal._now_ts", return_value=self.now):
            data = self.decode(token)
        self.assertEqual((data["user_id"], data["ver"], data["auth_time"]), (7, 3, self.now - 600))

    def test_no_renewal_past_the_absolute_session_limit(self):
        with mock.patch.dict("os.environ", {"SESSION_MAX_HOURS": "12"}):
            stale = self.payload(60, auth_age=12 * 3600)
            self.assertIsNone(self.svc.renewal_token(stale, now=self.now))

    def test_renewed_token_never_outlives_the_absolute_limit(self):
        with mock.patch.dict("os.environ", {"SESSION_MAX_HOURS": "1"}):
            token = self.svc.renewal_token(self.payload(60, auth_age=3600 - 300), now=self.now)
        from jose import jwt
        exp = jwt.get_unverified_claims(token)["exp"]
        self.assertLessEqual(exp, self.now - (3600 - 300) + 3600 + 5)

    def test_old_tokens_without_login_time_are_handled(self):
        payload = {"user_id": 7, "ver": 0, "exp": self.now + 60}
        self.assertIsNotNone(self.svc.renewal_token(payload, now=self.now))


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class RenewalOverHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = rc.create_user("renew")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def browser_with_token(self, minutes_left: float, ver_offset: int = 0):
        """一个只带 Cookie 的“浏览器”，令牌还剩 minutes_left 分钟。"""
        from models.init_db import SessionLocal
        from models.user_dao import get_user_by_id
        from service.auth import create_access_token
        from service import session_cookie
        with SessionLocal() as db:
            ver = (get_user_by_id(db, self.user["id"]).auth_version or 0) + ver_offset
        token = create_access_token({"user_id": self.user["id"], "username": self.user["name"], "ver": ver},
                                    expires_delta=timedelta(minutes=minutes_left))
        client = rc.make_client()
        client.cookies.set("session_token", token)
        client.cookies.set("csrf_token", session_cookie.csrf_for(token))
        return client, token

    @staticmethod
    def renewed(response):
        return [c for c in response.headers.get_list("set-cookie") if c.startswith("session_token=")]

    def test_active_request_near_expiry_gets_a_new_session_that_works(self):
        client, old = self.browser_with_token(5)
        response = client.get("/user/me")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.renewed(response)), 1, "快到期又在用：应该换发新令牌")
        issued = {c.split("=", 1)[0]: c.split("=", 1)[1].split(";", 1)[0] for c in response.headers.get_list("set-cookie")}
        self.assertNotEqual(issued["session_token"], old)
        # 像浏览器一样换上新的两个 Cookie：新令牌和新的 CSRF 一起生效，改数据的请求照常通过
        client.cookies.clear()
        client.cookies.set("session_token", issued["session_token"])
        client.cookies.set("csrf_token", issued["csrf_token"])
        follow = client.put("/user/profile", json={"occupation": "测试"}, headers={"X-CSRF-Token": issued["csrf_token"]})
        self.assertEqual(follow.status_code, 200, follow.text)

    def test_fresh_session_is_not_reissued_on_every_request(self):
        client, _ = self.browser_with_token(55)
        self.assertEqual(self.renewed(client.get("/user/me")), [])

    def test_background_polling_does_not_keep_the_session_alive(self):
        client, _ = self.browser_with_token(5)
        response = client.get("/work-items/counts", headers={"X-Background-Poll": "1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.renewed(response), [])

    def test_bearer_tokens_are_not_renewed(self):
        from service.auth import create_access_token
        from models.init_db import SessionLocal
        from models.user_dao import get_user_by_id
        with SessionLocal() as db:
            ver = get_user_by_id(db, self.user["id"]).auth_version or 0
        token = create_access_token({"user_id": self.user["id"], "ver": ver}, expires_delta=timedelta(minutes=5))
        response = rc.make_client().get("/user/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.renewed(response), [])

    def test_revoked_session_is_rejected_and_not_renewed(self):
        client, _ = self.browser_with_token(5, ver_offset=-1)   # 改过密码 / 强制下线后的旧令牌
        response = client.get("/user/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.renewed(response), [])


if __name__ == "__main__":
    unittest.main()
