"""上传大小上限：每个文件、一次请求累计、批量个数三个上限，超限立即拒绝（413），不先把整个文件读进内存。

一部分是不依赖数据库的单元测试（假的 UploadFile，能数出到底读了多少字节）；
另一部分走真实路由（知识库空间、助手知识库），需要本机 MySQL，没有时自动跳过。"""
import asyncio
import io
import os
import unittest
from unittest import mock

from fastapi import HTTPException, UploadFile

from tests import _route_client as rc
from utils import upload_limits

_AVAILABLE, _WHY = rc.route_tests_available()


class CountingFile:
    """假的上传文件：记录一共被读走了多少字节，用来证明“超限后立即停止”。"""

    def __init__(self, size: int, filename: str = "a.txt"):
        self.filename = filename
        self.remaining = size
        self.served = 0

    async def read(self, n: int = -1) -> bytes:
        n = self.remaining if n is None or n < 0 else min(n, self.remaining)
        self.remaining -= n
        self.served += n
        return b"x" * n


def run(coro):
    return asyncio.run(coro)


class ReadUploadTests(unittest.TestCase):
    def test_within_limit_returns_everything(self):
        file = UploadFile(io.BytesIO(b"hello"), filename="a.txt")
        self.assertEqual(run(upload_limits.read_upload(file, max_bytes=10)), b"hello")

    def test_exactly_at_limit_is_allowed_and_one_more_byte_is_not(self):
        self.assertEqual(len(run(upload_limits.read_upload(CountingFile(1000), max_bytes=1000))), 1000)
        with self.assertRaises(HTTPException) as caught:
            run(upload_limits.read_upload(CountingFile(1001), max_bytes=1000))
        self.assertEqual(caught.exception.status_code, 413)

    def test_stops_reading_as_soon_as_the_limit_is_crossed(self):
        file = CountingFile(500 * 1024 * 1024)                 # 一个 500MB 的“文件”
        with self.assertRaises(HTTPException):
            run(upload_limits.read_upload(file, max_bytes=2 * 1024 * 1024))
        self.assertLessEqual(file.served, 2 * 1024 * 1024 + 1, f"超限后还多读了 {file.served} 字节")

    def test_default_limit_comes_from_environment(self):
        with mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_BYTES": "100"}):
            with self.assertRaises(HTTPException):
                run(upload_limits.read_upload(CountingFile(101)))
            self.assertEqual(len(run(upload_limits.read_upload(CountingFile(100)))), 100)

    def test_bad_environment_values_fall_back_to_defaults(self):
        for bad in ("abc", "-5", "0", ""):
            with mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_BYTES": bad}):
                self.assertEqual(upload_limits.max_file_bytes(), 50 * 1024 * 1024)


class ReadUploadsTests(unittest.TestCase):
    def env(self, **values):
        return mock.patch.dict(os.environ, {k: str(v) for k, v in values.items()})

    def test_batch_within_all_limits(self):
        with self.env(KNOWLEDGE_UPLOAD_MAX_BYTES=100, KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES=250, KNOWLEDGE_UPLOAD_MAX_FILES=3):
            contents = run(upload_limits.read_uploads([CountingFile(100), CountingFile(100), CountingFile(50)]))
        self.assertEqual([len(c) for c in contents], [100, 100, 50])

    def test_too_many_files(self):
        with self.env(KNOWLEDGE_UPLOAD_MAX_FILES=2):
            files = [CountingFile(1) for _ in range(3)]
            with self.assertRaises(HTTPException) as caught:
                run(upload_limits.read_uploads(files))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(sum(f.served for f in files), 0, "个数超限时一个字节都不该读")

    def test_one_file_over_its_own_limit(self):
        with self.env(KNOWLEDGE_UPLOAD_MAX_BYTES=100, KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES=10_000):
            with self.assertRaises(HTTPException) as caught:
                run(upload_limits.read_uploads([CountingFile(10), CountingFile(101)]))
        self.assertEqual(caught.exception.status_code, 413)
        self.assertIn("a.txt", caught.exception.detail)

    def test_total_over_the_request_limit_even_if_each_file_is_small(self):
        with self.env(KNOWLEDGE_UPLOAD_MAX_BYTES=100, KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES=250):
            files = [CountingFile(100), CountingFile(100), CountingFile(100)]
            with self.assertRaises(HTTPException) as caught:
                run(upload_limits.read_uploads(files))
        self.assertEqual(caught.exception.status_code, 413)
        self.assertIn("总大小", caught.exception.detail)
        self.assertLessEqual(files[2].served, 51, "累计超限后不该继续读第三个文件的剩余内容")

    def test_empty_batch(self):
        self.assertEqual(run(upload_limits.read_uploads([])), [])


@unittest.skipUnless(_AVAILABLE, f"需要本地 MySQL：{_WHY}")
class UploadRouteLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from models.agent_dao import create_agent
        from models.init_db import SessionLocal
        cls.client = rc.make_client()
        cls.user = rc.create_user("upload-limit")
        db = SessionLocal()
        try:
            agent = create_agent(db, name="upload-limit-agent", user_id=cls.user["id"])
            db.commit()
            cls.agent_id = agent.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        rc.cleanup()

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_BYTES": "1024", "KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES": "1500",
                                               "KNOWLEDGE_UPLOAD_RATE_LIMIT": "0", "SPACE_UPLOAD_RATE_LIMIT": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.headers = self.user["headers"]

    def post_files(self, path, sizes, field="files", names=None):
        files = [(field, ((names[i] if names else f"f{i}.txt"), b"a" * size, "text/plain")) for i, size in enumerate(sizes)]
        return self.client.post(path, files=files, headers=self.headers)

    def test_space_single_upload_over_limit_is_413(self):
        response = self.post_files("/knowledge-spaces/999999/documents", [2048], field="file")
        self.assertEqual(response.status_code, 413, response.text)

    def test_space_batch_total_over_limit_is_413(self):
        response = self.post_files("/knowledge-spaces/999999/documents/batch", [1000, 1000])
        self.assertEqual(response.status_code, 413, response.text)

    def test_space_batch_too_many_files(self):
        with mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_FILES": "2"}):
            response = self.post_files("/knowledge-spaces/999999/documents/batch", [1, 1, 1])
        self.assertEqual(response.status_code, 400, response.text)

    def test_agent_single_upload_over_limit_is_413(self):
        response = self.post_files(f"/knowledge/{self.agent_id}/upload", [2048], field="file")
        self.assertEqual(response.status_code, 413, response.text)

    def test_agent_batch_rejects_bad_file_name_before_reading_anything(self):
        response = self.post_files(f"/knowledge/{self.agent_id}/upload-batch", [10, 10], names=["ok.txt", "evil.exe"])
        self.assertEqual(response.status_code, 400, response.text)

    def test_agent_batch_total_over_limit_is_413(self):
        response = self.post_files(f"/knowledge/{self.agent_id}/upload-batch", [1000, 1000])
        self.assertEqual(response.status_code, 413, response.text)


if __name__ == "__main__":
    unittest.main()
