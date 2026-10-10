"""测试不能碰真实的提示词目录 prompt/prompts/（见 tests/_route_client.py 开头）。

以前在空测试库上跑测试，测试建的 1 号、5 号助手会覆盖、再在清理时删掉开发库 1 号、5 号助手的提示词文件。
"""
import pathlib
import unittest

from tests import _route_client as rc  # noqa: F401  —— 导入即把提示词目录切到临时目录

import prompt.prompt_manager as prompt_manager

REAL_DIR = pathlib.Path(prompt_manager.__file__).resolve().parent / "prompts"


class PromptDirIsolationTest(unittest.TestCase):
    def test_tests_write_prompts_into_a_temp_directory(self):
        self.assertNotEqual(prompt_manager.PROMPT_DIR.resolve(), REAL_DIR.resolve())
        self.assertEqual(prompt_manager.PROMPT_DIR, rc.TEST_PROMPT_DIR)

    def test_low_agent_ids_do_not_touch_real_prompt_files(self):
        real = REAL_DIR / "5.yaml"   # 仓库里跟踪的演示助手提示词，一定存在
        before = real.read_bytes() if real.exists() else None
        prompt_manager.create_prompt_file(5, role="测试", task="测试", constraints="", output="")
        self.assertIsNotNone(prompt_manager.read_prompt_file(5))
        prompt_manager.delete_prompt_file(5)
        after = real.read_bytes() if real.exists() else None
        self.assertEqual(after, before, "真实目录里的 5.yaml 不能被测试改写或删除")


if __name__ == "__main__":
    unittest.main()
