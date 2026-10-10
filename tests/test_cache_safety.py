"""缓存安全（不需要真实 Redis，用一个假的 Redis 客户端）：
1. 缓存值只用 JSON 编码，不再有 pickle；不支持的类型不写进 Redis（只留在进程内存里），也不会把 Redis 标成故障；
2. Redis 里的内容无法解析时当作没命中并丢弃（旧版本的 pickle 数据、被改过的数据）；
3. 模型 API Key：写进缓存（可能是 Redis）的只有数据库里那份密文，调用方拿到的才是解密后的明文。
真实 Redis 上的并发与载荷测试见 tests/test_redis_concurrency.py。"""
import datetime
import pickle
import unittest
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest import mock

from utils.cache import TTLCache, decode_value, encode_value


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def delete(self, *keys):
        return sum(1 for k in keys if self.store.pop(k, None) is not None)

    def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]


def _cache_with_fake_redis():
    cache = TTLCache(default_ttl=30, namespace="unit")
    fake = FakeRedis()
    cache._redis = SimpleNamespace(get_client=lambda: fake, mark_failed=mock.Mock(), is_available=lambda: True)
    return cache, fake


class JsonCodecTests(unittest.TestCase):
    def test_round_trip(self):
        value = {"名称": "测试", "n": 3, "f": 1.5, "ok": True, "none": None, "items": [1, "a", {"x": []}]}
        self.assertEqual(decode_value(encode_value(value)), value)

    def test_datetime_and_path_and_set(self):
        now = datetime.datetime(2026, 10, 7, 12, 30, 5)
        decoded = decode_value(encode_value({"at": now, "day": now.date(), "p": PurePosixPath("/a/b"), "s": {3, 1, 2}}))
        self.assertEqual(decoded["at"], now)
        self.assertEqual(decoded["day"], now.date())
        self.assertEqual(decoded["p"], "/a/b")
        self.assertEqual(decoded["s"], [1, 2, 3])

    def test_arbitrary_objects_are_rejected(self):
        with self.assertRaises(TypeError):
            encode_value({"x": object()})

    def test_output_is_plain_json_never_pickle(self):
        raw = encode_value({"a": 1})
        self.assertTrue(raw.startswith(b"{"))
        self.assertNotIn(b"\x80", raw[:1])


class CacheBehaviourTests(unittest.TestCase):
    def test_stored_in_redis_as_json(self):
        cache, fake = _cache_with_fake_redis()
        cache.set(("k", 1), {"a": [1, 2]})
        (key, raw), = fake.store.items()
        self.assertTrue(key.startswith("cache:v2:unit:"))
        self.assertEqual(decode_value(raw), {"a": [1, 2]})
        self.assertEqual(cache.get(("k", 1)), {"a": [1, 2]})

    def test_unserializable_value_stays_in_memory_and_is_not_a_redis_failure(self):
        cache, fake = _cache_with_fake_redis()
        marker = object()
        cache.set(("k",), {"obj": marker})
        self.assertEqual(fake.store, {})
        self.assertIs(cache.get(("k",))["obj"].__class__, object)
        cache._redis.mark_failed.assert_not_called()

    def test_pickle_payload_is_discarded_not_executed(self):
        cache, fake = _cache_with_fake_redis()
        executed = []

        class Evil:
            def __reduce__(self):
                return (executed.append, ("pwned",))
        fake.store[cache._redis_key(("k",))] = pickle.dumps(Evil())
        with self.assertLogs("cache", level="WARNING"):
            self.assertIsNone(cache.get(("k",)))
        self.assertEqual(executed, [])
        self.assertEqual(fake.store, {}, "无法解析的内容应被删除")
        cache._redis.mark_failed.assert_not_called()

    def test_legacy_unversioned_keys_are_never_read(self):
        cache, fake = _cache_with_fake_redis()
        fake.store["cache:unit:k"] = pickle.dumps({"x": 1})
        self.assertIsNone(cache.get(("k",)))
        self.assertIn("cache:unit:k", fake.store)

    def test_invalidate_prefix_uses_versioned_keys(self):
        cache, fake = _cache_with_fake_redis()
        cache.set(("llm_api_key", 1, "glm-4"), "x")
        cache.set(("llm_api_key", 2, "glm-4"), "y")
        self.assertEqual(cache.invalidate(prefix=("llm_api_key", 1)), 1)
        self.assertEqual(len(fake.store), 1)


class ModelKeyIsOnlyCachedAsCiphertextTests(unittest.TestCase):
    def setUp(self):
        from service.llm import llm_config_service as svc
        self.svc = svc
        self.cache, self.fake = _cache_with_fake_redis()
        self.cipher = svc.encrypt("sk-real-secret-key-1234567890")
        self.config = SimpleNamespace(api_key=self.cipher, is_active=1, model_name="glm-4")
        patches = [mock.patch.object(svc, "config_cache", self.cache),
                   mock.patch.object(svc, "get_config_by_user_and_model", return_value=self.config)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _redis_dump(self) -> bytes:
        return b"".join(self.fake.store.values())

    def test_api_key_returns_plaintext_but_redis_holds_only_ciphertext(self):
        self.assertEqual(self.svc.get_api_key(None, 1, "glm-4"), "sk-real-secret-key-1234567890")
        self.assertEqual(self.svc.get_api_key(None, 1, "glm-4"), "sk-real-secret-key-1234567890")      # 第二次走缓存
        self.assertNotIn(b"sk-real-secret-key", self._redis_dump())
        self.assertIn(self.cipher.encode() if isinstance(self.cipher, str) else self.cipher, self._redis_dump())

    def test_api_config_returns_plaintext_but_redis_holds_only_ciphertext(self):
        first = self.svc.get_api_config(None, 1, "glm-4")
        second = self.svc.get_api_config(None, 1, "glm-4")
        self.assertEqual(first["api_key"], "sk-real-secret-key-1234567890")
        self.assertEqual(second["api_key"], "sk-real-secret-key-1234567890")
        self.assertNotIn(b"sk-real-secret-key", self._redis_dump())

    def test_inactive_config_is_none(self):
        self.config.is_active = 0
        self.assertIsNone(self.svc.get_api_key(None, 1, "glm-4"))
        self.assertIsNone(self.svc.get_api_config(None, 1, "glm-4"))


if __name__ == "__main__":
    unittest.main()
