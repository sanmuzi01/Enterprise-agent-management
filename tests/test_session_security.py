"""认证安全第一阶段：token 版本号（auth_version）+ 统一密码策略的端到端验证。

走真实 FastAPI 应用、真实数据库、真实 JWT（同 test_routes_isolation.py 的路数）：
改密码 / 重置密码 / 强制下线之后，旧 token 必须立刻拿不到任何受保护接口，新签发的 token
必须正常可用；弱密码要在注册、改密码、找回密码这三个入口被统一拒绝。
"""
import unittest

from tests import _route_client as rc

_AVAILABLE, _WHY = rc.route_tests_available()

WEAK = "123456"                 # 太短 + 常见弱密码，双重违反策略
STRONG_A = "Str0ng-PassA!"      # 11 位以上，够格
STRONG_B = "Str0ng-PassB!"


@unittest.skipUnless(_AVAILABLE, f"路由级测试环境不可用：{_WHY}")
class ChangePasswordInvalidatesOldTokenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        rc.cleanup()

    def test_change_password_revokes_the_old_token_and_a_freshly_minted_one_still_works(self):
        user = rc.create_user("chpwsec", password=STRONG_A)
        old_headers = user["headers"]

        # 改密码前，旧 token 能正常访问受保护接口
        self.assertEqual(self.client.get("/user/me", headers=old_headers).status_code, 200)

        r = self.client.post(
            "/user/change-password",
            json={"old_password": STRONG_A, "new_password": STRONG_B},
            headers=old_headers,
        )
        self.assertEqual(r.status_code, 200, r.text)

        # 旧 token：401，且提示是"登录状态已失效"，不是"token 无效或已过期"那种笼统提示
        stale = self.client.get("/user/me", headers=old_headers)
        self.assertEqual(stale.status_code, 401, stale.text)
        self.assertIn("失效", stale.json()["detail"])

        # 用新密码重新登录拿到的 token（带最新 ver）可以正常用
        login = self.client.post("/auth/token", json={"name": user["name"], "password": STRONG_B})
        self.assertEqual(login.status_code, 200, login.text)
        new_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(self.client.get("/user/me", headers=new_headers).status_code, 200)

        # 用 _route_client 按当前 auth_version 现签的 token 同样可用（不一定非要走登录接口）
        minted_headers = rc.auth_headers(user["id"])
        self.assertEqual(self.client.get("/user/me", headers=minted_headers).status_code, 200)

    def test_weak_password_rejected_at_change_password(self):
        user = rc.create_user("chpwweak", password=STRONG_A)
        r = self.client.post(
            "/user/change-password",
            json={"old_password": STRONG_A, "new_password": WEAK},
            headers=user["headers"],
        )
        self.assertEqual(r.status_code, 422, r.text)  # Pydantic min_length 先挡一道

        # 绕过 Pydantic 长度下限、但仍是"常见弱密码"的情形（比如凑够 10 位的弱密码变体不在本测试范围内，
        # 这里直接验证 10 位以内一定被 422 拦住，业务层规则在 test_password_policy.py 单独覆盖）
        old_token_still_valid = self.client.get("/user/me", headers=user["headers"])
        self.assertEqual(old_token_still_valid.status_code, 200)


@unittest.skipUnless(_AVAILABLE, f"路由级测试环境不可用：{_WHY}")
class ResetPasswordInvalidatesOldTokenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        rc.cleanup()

    def test_admin_reset_password_revokes_all_old_tokens(self):
        from tests._route_client import admin_env

        user = rc.create_user("resetpwsec", password=STRONG_A)
        admin = rc.create_user("resetpwadmin")
        with admin_env(admin["name"]):
            old_headers = user["headers"]
            self.assertEqual(self.client.get("/user/me", headers=old_headers).status_code, 200)

            r = self.client.put(
                f"/admin/users/{user['id']}/password",
                json={"new_password": STRONG_B},
                headers=admin["headers"],
            )
            self.assertEqual(r.status_code, 200, r.text)

            stale = self.client.get("/user/me", headers=old_headers)
            self.assertEqual(stale.status_code, 401, stale.text)

            fresh = rc.auth_headers(user["id"])
            self.assertEqual(self.client.get("/user/me", headers=fresh).status_code, 200)

    def test_admin_reset_password_rejects_weak_password(self):
        from tests._route_client import admin_env

        user = rc.create_user("resetpwweak", password=STRONG_A)
        admin = rc.create_user("resetpwweakadmin")
        with admin_env(admin["name"]):
            r = self.client.put(
                f"/admin/users/{user['id']}/password",
                json={"new_password": WEAK},
                headers=admin["headers"],
            )
            self.assertEqual(r.status_code, 422, r.text)

    def test_admin_reset_password_rejects_password_equal_to_current_one(self):
        from tests._route_client import admin_env

        user = rc.create_user("resetpwsame", password=STRONG_A)
        admin = rc.create_user("resetpwsameadmin")
        with admin_env(admin["name"]):
            r = self.client.put(
                f"/admin/users/{user['id']}/password",
                json={"new_password": STRONG_A},
                headers=admin["headers"],
            )
            self.assertEqual(r.status_code, 400, r.text)
            self.assertIn("相同", r.json()["detail"])


@unittest.skipUnless(_AVAILABLE, f"路由级测试环境不可用：{_WHY}")
class ForceLogoutAndLogoutAllTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        rc.cleanup()

    def test_admin_force_logout_takes_effect_immediately_without_changing_the_password(self):
        from tests._route_client import admin_env

        user = rc.create_user("forcelogout", password=STRONG_A)
        admin = rc.create_user("forcelogoutadmin")
        with admin_env(admin["name"]):
            old_headers = user["headers"]
            self.assertEqual(self.client.get("/user/me", headers=old_headers).status_code, 200)

            r = self.client.post(f"/admin/users/{user['id']}/revoke-sessions", headers=admin["headers"])
            self.assertEqual(r.status_code, 200, r.text)

            self.assertEqual(self.client.get("/user/me", headers=old_headers).status_code, 401)

            # 密码没变，还能用原密码正常登录（强制下线不等于封号）
            login = self.client.post("/user/login", json={"name": user["name"], "password": STRONG_A})
            self.assertEqual(login.status_code, 200, login.text)

    def test_admin_force_logout_unknown_user_is_404(self):
        from tests._route_client import admin_env

        admin = rc.create_user("forcelogout404")
        with admin_env(admin["name"]):
            r = self.client.post("/admin/users/999999999/revoke-sessions", headers=admin["headers"])
            self.assertEqual(r.status_code, 404)

    def test_regular_user_cannot_force_logout_others(self):
        alice = rc.create_user("forcelogoutalice")
        bob = rc.create_user("forcelogoutbob")
        r = self.client.post(f"/admin/users/{bob['id']}/revoke-sessions", headers=alice["headers"])
        self.assertEqual(r.status_code, 403)

    def test_logout_all_invalidates_the_current_token_too(self):
        user = rc.create_user("logoutall", password=STRONG_A)
        old_headers = user["headers"]
        self.assertEqual(self.client.get("/user/me", headers=old_headers).status_code, 200)

        r = self.client.post("/user/logout-all", headers=old_headers)
        self.assertEqual(r.status_code, 200, r.text)

        # 连发起这次请求本身用的 token 也一起失效了
        self.assertEqual(self.client.get("/user/me", headers=old_headers).status_code, 401)

        fresh = rc.auth_headers(user["id"])
        self.assertEqual(self.client.get("/user/me", headers=fresh).status_code, 200)


@unittest.skipUnless(_AVAILABLE, f"路由级测试环境不可用：{_WHY}")
class DisabledUserStillForbiddenTest(unittest.TestCase):
    """确认这次给 get_current_user(_async) 加的版本号校验，没有把原有的禁用检查顶掉。"""

    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        rc.cleanup()

    def test_disabling_a_user_does_not_change_auth_version_but_still_returns_403(self):
        from tests._route_client import admin_env

        user = rc.create_user("disabledsec", password=STRONG_A)
        admin = rc.create_user("disabledsecadmin")
        with admin_env(admin["name"]):
            headers = user["headers"]
            self.assertEqual(self.client.get("/user/me", headers=headers).status_code, 200)

            r = self.client.patch(f"/admin/users/{user['id']}/status", json={"disabled": True}, headers=admin["headers"])
            self.assertEqual(r.status_code, 200, r.text)

            # 版本号没变 —— 同一个 token（版本号仍然匹配）现在被 403 挡下，而不是 401
            forbidden = self.client.get("/user/me", headers=headers)
            self.assertEqual(forbidden.status_code, 403, forbidden.text)

            # 重新现签一个 token（版本号同样匹配）也一样是 403，不是因为 token 旧
            fresh = rc.auth_headers(user["id"])
            self.assertEqual(self.client.get("/user/me", headers=fresh).status_code, 403)


@unittest.skipUnless(_AVAILABLE, f"路由级测试环境不可用：{_WHY}")
class WeakPasswordRejectedAtRegisterAndResetTest(unittest.TestCase):
    """三个入口（注册 / 改密码 / 找回密码）统一拒绝弱密码；改密码那一条在上面的类里已经覆盖。"""

    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        rc.cleanup()

    def test_register_rejects_weak_password(self):
        r = self.client.post("/user/register", json={
            "name": "rt_weakreg_probe", "password": WEAK, "age": 20,
            "phone": "13900001234", "sms_code": "000000", "accepted_terms": True,
        })
        self.assertEqual(r.status_code, 422, r.text)  # Pydantic min_length 先挡一道

    def test_reset_password_rejects_weak_password_shape(self):
        r = self.client.post("/user/reset-password", json={
            "phone": "13900001235", "sms_code": "000000", "new_password": WEAK,
        })
        self.assertEqual(r.status_code, 422, r.text)


if __name__ == "__main__":
    unittest.main()
