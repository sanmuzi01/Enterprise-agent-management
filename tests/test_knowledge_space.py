"""知识库空间：纯逻辑单测（清洗 / 角色 / 集合键 / 默认空间创建）。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from service.exceptions import InvalidInput
from service.knowledge_space import membership, space_async_service, space_service
from service.rag import vector_store_service as vs


class MembershipTest(unittest.TestCase):
    def test_owner_only_in_phase1(self):
        space = SimpleNamespace(user_id=7)
        self.assertEqual(membership.resolve_role(7, space), "owner")
        self.assertIsNone(membership.resolve_role(8, space))
        self.assertIsNone(membership.resolve_role(7, None))

    def test_role_capabilities(self):
        self.assertTrue(membership.can_write_doc("editor"))
        self.assertFalse(membership.can_write_doc("viewer"))
        self.assertTrue(membership.can_manage_space("admin"))
        self.assertFalse(membership.can_manage_space("editor"))
        self.assertTrue(membership.can_delete_space("owner"))
        self.assertFalse(membership.can_delete_space("admin"))


class CleanCreateTest(unittest.TestCase):
    def test_empty_name_rejected(self):
        with self.assertRaises(InvalidInput):
            space_async_service._clean_create({"name": "  "})

    def test_unknown_purpose_falls_back_to_other(self):
        out = space_async_service._clean_create({"name": "x", "purpose": "wat"})
        self.assertEqual(out["purpose"], "other")

    def test_known_purpose_kept_and_tags_normalized(self):
        out = space_async_service._clean_create(
            {"name": "客服库", "purpose": "customer_service", "tags": [" a ", "", "b", 3]}
        )
        self.assertEqual(out["purpose"], "customer_service")
        self.assertIn('"a"', out["tags_json"])
        self.assertIn('"b"', out["tags_json"])
        self.assertIn('"3"', out["tags_json"])


class ToDictTest(unittest.TestCase):
    def test_shape(self):
        space = SimpleNamespace(
            id=3, name="制度库", description="", purpose="policy", tags_json='["制度"]',
            is_enabled=1, status="active", doc_count=5, chunk_count=42, health_score=None,
            last_indexed_at=None, created_at=None, updated_at=None,
            scope_type="personal", team_id=None, sensitivity="internal",
        )
        d = space_async_service._to_dict(space, {"bound_agent_count": 2})
        self.assertEqual(d["id"], 3)
        self.assertEqual(d["purpose_label"], "企业制度知识库")
        self.assertEqual(d["tags"], ["制度"])
        self.assertEqual(d["bound_agent_count"], 2)
        self.assertEqual(d["scope"], "personal")
        self.assertEqual(d["my_role"], "owner")
        self.assertEqual((d["team_id"], d["sensitivity"], d["sensitivity_label"]), (None, "internal", "内部"))


class CollectionKeyTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(vs._collection_name(5), "agent_5_knowledge")
        self.assertEqual(vs._collection_name("5"), "agent_5_knowledge")
        self.assertEqual(vs._collection_name("space_12"), "space_12_knowledge")
        self.assertEqual(vs._collection_name("agent_9"), "agent_9_knowledge")
        self.assertEqual(vs.space_collection_key(12), "space_12")
        self.assertEqual(vs.legacy_agent_key(9), "agent_9")


class DocumentServiceTest(unittest.TestCase):
    def setUp(self):
        from service.knowledge_space import document_service as ds
        self.ds = ds

    def test_validate_file_type(self):
        self.assertEqual(self.ds._validate_file("a.pdf"), "pdf")
        with self.assertRaises(InvalidInput):
            self.ds._validate_file("a.exe")
        with self.assertRaises(InvalidInput):
            self.ds._validate_file("noext")

    def test_tags_json_normalizes(self):
        self.assertIsNone(self.ds._tags_json(None))
        self.assertIsNone(self.ds._tags_json([]))
        self.assertEqual(self.ds._tags_json([" x ", "", "y"]), '["x", "y"]')

    def test_doc_dict_shape(self):
        k = SimpleNamespace(
            id=1, file_name="a.pdf", file_type="pdf", file_size=10, chunk_count=3,
            status="done", is_enabled=1, error_msg=None, category="制度",
            tags_json='["a"]', version="v1", source_type="upload", source_url=None,
            created_at=None, updated_at=None,
        )
        d = self.ds._doc_dict(k)
        self.assertEqual(d["status_label"], "已入库")
        self.assertEqual(d["tags"], ["a"])
        self.assertEqual(d["category"], "制度")


class EnsureDefaultSpaceTest(unittest.TestCase):
    def test_reuses_legacy_space(self):
        with patch("models.knowledge_space_dao.find_legacy_space", return_value=SimpleNamespace(id=99)):
            sid = space_service.ensure_default_space_for_agent(object(), 1, 5, "客服助手")
        self.assertEqual(sid, 99)

    def test_creates_when_missing(self):
        captured = {}

        def fake_create(db, user_id, fields):
            captured.update(user_id=user_id, **fields)
            return SimpleNamespace(id=123)

        with patch("models.knowledge_space_dao.find_legacy_space", return_value=None), \
             patch("models.knowledge_space_dao.create_space", fake_create):
            sid = space_service.ensure_default_space_for_agent(object(), 1, 5, "客服助手")

        self.assertEqual(sid, 123)
        self.assertEqual(captured["legacy_agent_id"], 5)
        self.assertEqual(captured["vector_migrated"], 0)
        self.assertIn("客服助手", captured["name"])


if __name__ == "__main__":
    unittest.main()
