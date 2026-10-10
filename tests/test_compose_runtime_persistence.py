"""Compose 必须持久化 API/Worker 在运行时生成的文件。"""
import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_MOUNTS = {
    "/app/data",
    "/app/prompt/prompts",
    "/app/skills/enterprise",
    "/app/skills/imported",
    "/app/skills/user_created",
    "/app/skills/user_templates",
    "/app/skills_packages/imported",
    "/app/static/charts",
    "/app/agent_templates",
    "/app/logs",
}


class ComposeRuntimePersistenceTest(unittest.TestCase):
    def test_api_and_worker_share_every_runtime_mount(self):
        for filename in ("docker-compose.yml", "docker-compose.prod.yml"):
            config = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
            for service_name in ("api", "worker"):
                volumes = config["services"][service_name]["volumes"]
                targets = {str(volume).split(":", 1)[1] for volume in volumes}
                self.assertTrue(
                    RUNTIME_MOUNTS <= targets,
                    f"{filename} {service_name} 缺少运行时数据卷: {sorted(RUNTIME_MOUNTS - targets)}",
                )


if __name__ == "__main__":
    unittest.main()
