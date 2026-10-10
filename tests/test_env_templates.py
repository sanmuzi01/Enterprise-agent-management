"""两份生产环境模板（直接部署 / Docker 部署）必须保持同步：变量一一对应，只有主机地址不同；
Docker 模板里不能再出现指向容器自己的 127.0.0.1（容器里连不上），直接部署模板里不能出现容器服务名。"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOST_FILE = ROOT / ".env.production.example"
DOCKER_FILE = ROOT / ".env.production.docker.example"
# 两份模板里允许不同的变量（都是“服务在哪”）
HOST_ADDRESS_KEYS = {"DB_HOST", "REDIS_URL", "ENTERPRISE_HUB_BASE_URL"}


def _assignments(path: pathlib.Path) -> dict:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


class EnvTemplateTests(unittest.TestCase):
    def test_same_variables_in_both_templates(self):
        self.assertEqual(set(_assignments(HOST_FILE)), set(_assignments(DOCKER_FILE)))

    def test_only_service_addresses_differ(self):
        host, docker = _assignments(HOST_FILE), _assignments(DOCKER_FILE)
        differing = {key for key in host if host[key] != docker[key]}
        self.assertEqual(differing, HOST_ADDRESS_KEYS)

    def test_docker_template_uses_service_names_not_loopback(self):
        docker = _assignments(DOCKER_FILE)
        self.assertEqual(docker["DB_HOST"], "db")
        self.assertEqual(docker["REDIS_URL"], "redis://redis:6379/0")
        self.assertEqual(docker["ENTERPRISE_HUB_BASE_URL"], "http://enterprise-hub:8090")
        for key in HOST_ADDRESS_KEYS:
            self.assertNotIn("127.0.0.1", docker[key])
            self.assertNotIn("localhost", docker[key])

    def test_host_template_uses_loopback_not_container_names(self):
        host = _assignments(HOST_FILE)
        self.assertEqual(host["DB_HOST"], "127.0.0.1")
        self.assertIn("127.0.0.1", host["REDIS_URL"])
        self.assertIn("127.0.0.1", host["ENTERPRISE_HUB_BASE_URL"])

    def test_both_files_explain_which_one_to_use(self):
        self.assertIn(".env.production.docker.example", HOST_FILE.read_text(encoding="utf-8"))
        self.assertIn(".env.production.example", DOCKER_FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
