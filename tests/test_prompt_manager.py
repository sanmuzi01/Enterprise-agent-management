"""第五轮审计 P1-7：prompt/prompt_manager.py 的原子写入。

之前 create_prompt_file/update_prompt_file 是直接 `open(file_path, 'w')`——
这个调用本身就会先截断文件再写，写到一半失败（磁盘满、进程被杀）文件就留在
被截断/损坏的状态，没有任何办法恢复成写之前的内容。改成先写一份临时文件、
整个成功了再 `os.replace` 原子换上去。这里不依赖真实 DB，纯文件系统操作，
把 `PROMPT_DIR` 换成一个临时目录跑，不碰真实的 prompt/prompts/。
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from prompt import prompt_manager


class AtomicPromptWriteTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patcher = patch.object(prompt_manager, "PROMPT_DIR", Path(self._tmpdir.name))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_update_survives_a_failed_write_without_corrupting_existing_file(self):
        """真实复现旧写法的坑：写到一半失败时，`open(file_path, 'w')` 已经把
        文件截断成空文件——用 mock 让 yaml.dump 在写文件时抛异常，模拟"磁盘满/
        进程被杀在写文件中途"，验证原子写法下原文件完全没被动过。"""
        agent_id = 9001
        prompt_manager.create_prompt_file(agent_id, role="旧角色", task="旧任务",
                                           constraints="旧约束", output="旧输出")
        before = prompt_manager.read_prompt_file(agent_id)
        self.assertEqual(before["role"], "旧角色")

        with patch("prompt.prompt_manager.yaml.dump", side_effect=RuntimeError("模拟写入中途失败")):
            with self.assertRaises(RuntimeError):
                prompt_manager.update_prompt_file(agent_id, role="新角色", task="新任务",
                                                   constraints="新约束", output="新输出")

        after = prompt_manager.read_prompt_file(agent_id)
        self.assertEqual(after, before, "写入失败后原文件必须完全不变，不能是空文件/截断内容")

        # 目录里不该留下没清理掉的临时文件。
        leftovers = [p for p in Path(self._tmpdir.name).iterdir() if p.name.startswith(".prompt-")]
        self.assertEqual(leftovers, [])

    def test_update_replaces_content_atomically_on_success(self):
        agent_id = 9002
        prompt_manager.create_prompt_file(agent_id, role="旧角色", task="旧任务",
                                           constraints="旧约束", output="旧输出")
        prompt_manager.update_prompt_file(agent_id, role="新角色", task="新任务",
                                           constraints="新约束", output="新输出")
        data = prompt_manager.read_prompt_file(agent_id)
        self.assertEqual(data["role"], "新角色")
        self.assertEqual(data["task"], "新任务")

        leftovers = [p for p in Path(self._tmpdir.name).iterdir() if p.name.startswith(".prompt-")]
        self.assertEqual(leftovers, [])

    def test_written_file_is_valid_yaml(self):
        """避免"原子写入"绕开了正常的 yaml 序列化格式（比如手写字符串拼接）。"""
        agent_id = 9003
        path = prompt_manager.create_prompt_file(agent_id, role="A", task="B",
                                                   constraints="C", output="D")
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self.assertEqual(raw, {"role": "A", "task": "B", "constraints": "C", "output": "D"})


if __name__ == "__main__":
    unittest.main()
