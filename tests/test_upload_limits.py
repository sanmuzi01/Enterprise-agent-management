"""上传：流式写临时文件 + 大小 / 个数 / 并发上限，超限立即拒绝，不把整个文件读进内存。

一部分是不依赖数据库的单元测试（假文件数读了多少字节、用 tracemalloc 量内存峰值）；
另一部分走真实路由（知识库空间、助手知识库），需要本机 MySQL，没有时自动跳过。"""
import asyncio
import glob
import io
import os
import tempfile
import tracemalloc
import unittest
from unittest import mock

from fastapi import HTTPException, UploadFile

from tests import _route_client as rc
from utils import upload_limits

_AVAILABLE, _WHY = rc.route_tests_available()
MB = 1024 * 1024


class CountingFile:
    """假的上传文件：只在被读时才“生成”数据（不占内存），并记录一共被读走了多少字节。"""

    def __init__(self, size: int, filename: str = "a.txt", fill: bytes = b"x"):
        self.filename = filename
        self.remaining = size
        self.served = 0
        self.fill = fill

    async def read(self, n: int = -1) -> bytes:
        n = self.remaining if n is None or n < 0 else min(n, self.remaining)
        self.remaining -= n
        self.served += n
        return self.fill * n


def run(coro):
    return asyncio.run(coro)


class TempDirCase(unittest.TestCase):
    """让临时文件落在测试自己的目录里，测完检查有没有残留。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(upload_limits, "incoming_dir", return_value=self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def leftovers(self):
        return glob.glob(os.path.join(self.tmp.name, "*"))


class SpoolUploadTests(TempDirCase):
    def test_content_is_written_to_a_temp_file_and_nothing_is_kept_in_memory(self):
        file = UploadFile(io.BytesIO(b"hello world"), filename="a.txt")
        stored = run(upload_limits.spool_upload(file, max_bytes=100))
        self.assertEqual(stored.size, 11)
        self.assertEqual(len(stored), 11)
        self.assertTrue(stored)
        with open(stored.path, "rb") as handle:
            self.assertEqual(handle.read(), b"hello world")
        stored.discard()
        self.assertEqual(self.leftovers(), [])

    def test_empty_upload_is_falsy(self):
        stored = run(upload_limits.spool_upload(UploadFile(io.BytesIO(b""), filename="a.txt"), max_bytes=10))
        self.assertFalse(stored)
        stored.discard()

    def test_exactly_at_limit_is_allowed_and_one_more_byte_is_not(self):
        stored = run(upload_limits.spool_upload(CountingFile(1000), max_bytes=1000))
        self.assertEqual(stored.size, 1000)
        stored.discard()
        with self.assertRaises(HTTPException) as caught:
            run(upload_limits.spool_upload(CountingFile(1001), max_bytes=1000))
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(self.leftovers(), [], "超限后写下的临时文件必须删掉")

    def test_stops_reading_as_soon_as_the_limit_is_crossed(self):
        file = CountingFile(500 * MB)                           # 一个 500MB 的“文件”
        with self.assertRaises(HTTPException):
            run(upload_limits.spool_upload(file, max_bytes=2 * MB))
        self.assertLessEqual(file.served, 2 * MB + 1, f"超限后还多读了 {file.served} 字节")
        self.assertEqual(self.leftovers(), [])

    def test_a_failing_stream_leaves_no_temp_file(self):
        class Broken(CountingFile):
            async def read(self, n=-1):
                if self.served:
                    raise ConnectionError("客户端断开")
                return await super().read(n)
        with self.assertRaises(ConnectionError):
            run(upload_limits.spool_upload(Broken(10 * MB), max_bytes=50 * MB))
        self.assertEqual(self.leftovers(), [])

    def test_move_to_renames_the_temp_file_into_place(self):
        stored = run(upload_limits.spool_upload(UploadFile(io.BytesIO(b"abc"), filename="a.txt"), max_bytes=10))
        destination = os.path.join(self.tmp.name, "final.bin")
        temp_path = stored.path
        stored.move_to(destination)
        self.assertFalse(os.path.exists(temp_path))
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), b"abc")
        stored.discard()              # 已经移走：什么都不做，不会误删最终文件
        self.assertTrue(os.path.exists(destination))

    def test_default_limits_match_nginx(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ("KNOWLEDGE_UPLOAD_MAX_BYTES", "KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES"):
                os.environ.pop(name, None)
            self.assertEqual(upload_limits.max_file_bytes(), 50 * MB)
            self.assertEqual(upload_limits.max_request_bytes(), 50 * MB, "累计上限默认和 Nginx client_max_body_size 50m 一致")

    def test_bad_environment_values_fall_back_to_defaults(self):
        for bad in ("abc", "-5", "0", ""):
            with mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_BYTES": bad}):
                self.assertEqual(upload_limits.max_file_bytes(), 50 * MB)

    def test_move_falls_back_to_copy_when_rename_crosses_filesystems(self):
        stored = run(upload_limits.spool_upload(UploadFile(io.BytesIO(b"abc"), filename="a.txt"), max_bytes=10))
        destination = os.path.join(self.tmp.name, "final.bin")
        temp_path = stored.path
        with mock.patch.object(upload_limits.os, "replace", side_effect=OSError(18, "Invalid cross-device link")):
            stored.move_to(destination)
        self.assertFalse(os.path.exists(temp_path), "拷贝之后临时文件要删掉")
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), b"abc")

    def test_orphaned_temp_files_are_swept(self):
        old = os.path.join(self.tmp.name, "up_old.part")
        fresh = os.path.join(self.tmp.name, "up_fresh.part")
        for path in (old, fresh):
            with open(path, "wb") as handle:
                handle.write(b"x")
        os.utime(old, (1, 1))
        with mock.patch.object(upload_limits, "_last_sweep", 0.0):
            upload_limits._sweep_orphans()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh), "刚写下的（可能正在上传）不能被清掉")


class SpoolUploadsTests(TempDirCase):
    def env(self, **values):
        return mock.patch.dict(os.environ, {k: str(v) for k, v in values.items()})

    def test_batch_within_all_limits(self):
        with self.env(KNOWLEDGE_UPLOAD_MAX_BYTES=100, KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES=250, KNOWLEDGE_UPLOAD_MAX_FILES=3):
            stored = run(upload_limits.spool_uploads([CountingFile(100), CountingFile(100), CountingFile(50)]))
        self.assertEqual([item.size for item in stored], [100, 100, 50])
        for item in stored:
            item.discard()

    def test_too_many_files(self):
        with self.env(KNOWLEDGE_UPLOAD_MAX_FILES=2):
            files = [CountingFile(1) for _ in range(3)]
            with self.assertRaises(HTTPException) as caught:
                run(upload_limits.spool_uploads(files))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(sum(f.served for f in files), 0, "个数超限时一个字节都不该读")

    def test_one_file_over_its_own_limit_cleans_up_the_earlier_ones(self):
        with self.env(KNOWLEDGE_UPLOAD_MAX_BYTES=100, KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES=10_000):
            with self.assertRaises(HTTPException) as caught:
                run(upload_limits.spool_uploads([CountingFile(10), CountingFile(101, filename="big.txt")]))
        self.assertEqual(caught.exception.status_code, 413)
        self.assertIn("big.txt", caught.exception.detail)
        self.assertEqual(self.leftovers(), [], "整批拒绝：前面已经写好的临时文件也要删掉")

    def test_total_over_the_request_limit_even_if_each_file_is_small(self):
        with self.env(KNOWLEDGE_UPLOAD_MAX_BYTES=100, KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES=250):
            files = [CountingFile(100), CountingFile(100), CountingFile(100)]
            with self.assertRaises(HTTPException) as caught:
                run(upload_limits.spool_uploads(files))
        self.assertEqual(caught.exception.status_code, 413)
        self.assertIn("总大小", caught.exception.detail)
        self.assertLessEqual(files[2].served, 51, "累计超限后不该继续读第三个文件的剩余内容")
        self.assertEqual(self.leftovers(), [])

    def test_empty_batch(self):
        self.assertEqual(run(upload_limits.spool_uploads([])), [])


class MemoryPressureTests(TempDirCase):
    """多个并发的 50MB 上传：进程里的内存峰值必须远小于文件总大小（旧实现会把每个文件整个装进内存）。"""

    def test_concurrent_50mb_uploads_stay_far_below_the_total_size(self):
        sizes = 4 * 50 * MB

        async def scenario():
            files = [CountingFile(50 * MB, fill=b"y" * 1024) for _ in range(4)]
            # 假文件每次读返回 n 个 1KB 重复块——为了不让“造数据”本身占内存，这里用 1 字节填充，按需生成一个分块
            files = [CountingFile(50 * MB) for _ in range(4)]
            with mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_BYTES": str(50 * MB), "KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES": str(60 * MB)}):
                results = await asyncio.gather(*[upload_limits.spool_upload(f, max_bytes=50 * MB) for f in files])
            for item in results:
                self.assertEqual(item.size, 50 * MB)
                item.discard()

        tracemalloc.start()
        try:
            run(scenario())
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        # 4 个并发上传各自只持有一个 1MB 分块（外加假文件“造出来”的那一份）：峰值应当是几个 MB，而不是 200MB
        self.assertLess(peak, sizes // 10, f"内存峰值 {peak / MB:.1f}MB，接近文件总大小 {sizes / MB:.0f}MB：文件被整个读进了内存")
        self.assertEqual(self.leftovers(), [])


class UserConcurrencyTests(TempDirCase):
    def test_a_user_cannot_run_more_than_the_allowed_uploads_at_once(self):
        async def scenario():
            with mock.patch.dict(os.environ, {"USER_MAX_CONCURRENT_UPLOADS": "2"}):
                first = upload_limits.spooled_uploads(7, [UploadFile(io.BytesIO(b"a"), filename="a.txt")])
                second = upload_limits.spooled_uploads(7, [UploadFile(io.BytesIO(b"b"), filename="b.txt")])
                third = upload_limits.spooled_uploads(7, [UploadFile(io.BytesIO(b"c"), filename="c.txt")])
                other_user = upload_limits.spooled_uploads(8, [UploadFile(io.BytesIO(b"d"), filename="d.txt")])
                async with first, second:
                    with self.assertRaises(HTTPException) as caught:
                        async with third:
                            pass
                    self.assertEqual(caught.exception.status_code, 429)
                    async with other_user:                  # 别的用户不受影响
                        pass
                async with upload_limits.spooled_uploads(7, [UploadFile(io.BytesIO(b"e"), filename="e.txt")]):
                    pass                                    # 名额已释放
        run(scenario())
        self.assertEqual(self.leftovers(), [])

    def test_slot_and_temp_files_are_released_when_the_body_fails(self):
        async def scenario():
            with self.assertRaises(RuntimeError):
                async with upload_limits.spooled_uploads(9, [UploadFile(io.BytesIO(b"abc"), filename="a.txt")]) as stored:
                    self.assertTrue(os.path.exists(stored[0].path))
                    raise RuntimeError("业务代码出错")
            with mock.patch.dict(os.environ, {"USER_MAX_CONCURRENT_UPLOADS": "1"}):
                async with upload_limits.spooled_uploads(9, [UploadFile(io.BytesIO(b"x"), filename="a.txt")]):
                    pass
        run(scenario())
        self.assertEqual(self.leftovers(), [])


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
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_BYTES": "1024", "KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES": "1500",
                                               "KNOWLEDGE_UPLOAD_RATE_LIMIT": "0", "SPACE_UPLOAD_RATE_LIMIT": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)
        incoming = mock.patch.object(upload_limits, "incoming_dir", return_value=self.tmp.name)
        incoming.start()
        self.addCleanup(incoming.stop)
        self.headers = self.user["headers"]

    def post_files(self, path, sizes, field="files", names=None):
        files = [(field, ((names[i] if names else f"f{i}.txt"), b"a" * size, "text/plain")) for i, size in enumerate(sizes)]
        return self.client.post(path, files=files, headers=self.headers)

    def assert_no_temp_files(self):
        self.assertEqual(glob.glob(os.path.join(self.tmp.name, "*")), [], "拒绝之后不能留下临时文件")

    def test_space_without_write_permission_receives_no_bytes(self):
        """没有写权限的请求在写临时文件之前就被拒绝（不是收完 50MB 再拒绝）。"""
        with mock.patch.object(upload_limits, "spool_upload", side_effect=AssertionError("没有权限也开始收文件了")):
            response = self.post_files("/knowledge-spaces/999999/documents", [10], field="file")
        self.assertIn(response.status_code, (403, 404), response.text)

    def test_space_unsupported_type_receives_no_bytes(self):
        from unittest.mock import patch

        from service.knowledge_space import document_service
        with patch.object(document_service, "_require_write", return_value=None), \
                mock.patch.object(upload_limits, "spool_upload", side_effect=AssertionError("类型不对也开始收文件了")):
            response = self.post_files("/knowledge-spaces/999999/documents", [10], field="file", names=["evil.exe"])
        self.assertEqual(response.status_code, 400, response.text)

    def test_agent_single_upload_over_limit_is_413_and_leaves_nothing_behind(self):
        response = self.post_files(f"/knowledge/{self.agent_id}/upload", [2048], field="file")
        self.assertEqual(response.status_code, 413, response.text)
        self.assert_no_temp_files()

    def test_agent_batch_total_over_limit_is_413_and_leaves_nothing_behind(self):
        response = self.post_files(f"/knowledge/{self.agent_id}/upload-batch", [1000, 1000])
        self.assertEqual(response.status_code, 413, response.text)
        self.assert_no_temp_files()

    def test_agent_batch_too_many_files(self):
        with mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_FILES": "2"}):
            response = self.post_files(f"/knowledge/{self.agent_id}/upload-batch", [1, 1, 1])
        self.assertEqual(response.status_code, 400, response.text)

    def test_agent_batch_rejects_bad_file_name_before_receiving_anything(self):
        with mock.patch.object(upload_limits, "spool_upload", side_effect=AssertionError("文件名不合法也开始收文件了")):
            response = self.post_files(f"/knowledge/{self.agent_id}/upload-batch", [10, 10], names=["ok.txt", "evil.exe"])
        self.assertEqual(response.status_code, 400, response.text)

    def test_agent_upload_goes_through_without_holding_the_file_in_memory(self):
        """正常上传：内容被流式写进临时文件，再被移到知识库目录（临时目录里不留东西）。"""
        from models.init_db import SessionLocal
        from sqlalchemy import text
        with mock.patch.dict(os.environ, {"KNOWLEDGE_UPLOAD_MAX_BYTES": "5000", "KNOWLEDGE_UPLOAD_REQUEST_MAX_BYTES": "5000"}):
            response = self.post_files(f"/knowledge/{self.agent_id}/upload", [300], field="file", names=["stream-test.txt"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_no_temp_files()
        db = SessionLocal()
        try:
            row = db.execute(text("SELECT file_path, file_size FROM knowledge WHERE id=:i"), {"i": response.json()["knowledge_id"]}).first()
        finally:
            db.close()
        self.addCleanup(lambda: os.path.exists(row[0]) and os.remove(row[0]))      # 知识库目录里的文件不属于测试库，自己清
        self.assertEqual(row[1], 300)
        self.assertTrue(os.path.exists(row[0]))
        self.assertEqual(os.path.getsize(row[0]), 300)


if __name__ == "__main__":
    unittest.main()
