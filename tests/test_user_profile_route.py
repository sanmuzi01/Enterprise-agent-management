"""用户画像保存：新用户第一次保存曾因 UserProfile(created_at=...) 报 500（模型没有这个列）。
这是安全模糊测试发现的真实缺陷，这里固定下来。"""
import unittest

from tests import _route_client as rc  # 先导入：测试用的异步连接不复用（必须在导入 models 之前）

_AVAILABLE, _WHY = rc.route_tests_available()


@unittest.skipUnless(_AVAILABLE, _WHY)
class UserProfileRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = rc.make_client()
        cls.user = rc.create_user("prof-u")

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def test_first_save_creates_the_profile_and_second_save_updates_it(self):
        body = {"occupation": "会计", "skills": "Excel", "preferences": "简洁", "communication_style": "balanced", "persona": "professional", "extra_info": "x"}
        first = self.client.put("/user/profile", json=body, headers=self.user["headers"])
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.client.get("/user/profile", headers=self.user["headers"]).json()["occupation"], "会计")
        body["occupation"] = "财务经理"
        self.assertEqual(self.client.put("/user/profile", json=body, headers=self.user["headers"]).status_code, 200)
        self.assertEqual(self.client.get("/user/profile", headers=self.user["headers"]).json()["occupation"], "财务经理")

    def test_injection_strings_are_stored_verbatim_not_executed(self):
        payload = "'; DROP TABLE user_profile; --x"
        body = {"occupation": payload, "skills": payload, "preferences": payload, "communication_style": "balanced", "persona": "professional", "extra_info": payload}
        self.assertEqual(self.client.put("/user/profile", json=body, headers=self.user["headers"]).status_code, 200)
        self.assertEqual(self.client.get("/user/profile", headers=self.user["headers"]).json()["skills"], payload)


if __name__ == "__main__":
    unittest.main()
