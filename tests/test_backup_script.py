import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import backup


class DockerBackupTest(unittest.TestCase):
    def test_compose_command_uses_explicit_file(self):
        command = backup._compose_command(pathlib.Path("prod.yml"), "exec", "-T", "api", "true")
        self.assertEqual(command[:4], ["docker", "compose", "-f", "prod.yml"])
        self.assertEqual(command[-4:], ["exec", "-T", "api", "true"])

    def test_stream_failure_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = pathlib.Path(directory) / "partial.tar.gz"

            def failed(cmd, stdout, stderr):
                stdout.write(b"partial")
                return SimpleNamespace(returncode=2, stderr=b"failed")

            with patch("scripts.backup.subprocess.run", side_effect=failed):
                self.assertFalse(backup._stream_command(dest, ["docker"], "test"))
            self.assertFalse(dest.exists())

    def test_docker_backup_streams_database_app_data_and_chroma(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            compose_file = root / "compose.yml"
            compose_file.write_text("services: {}\n", encoding="utf-8")
            calls = []

            def succeeded(dest, cmd, label):
                calls.append((dest.name, cmd, label))
                return True

            with (
                patch.object(backup, "BACKUP_DIR", root),
                patch("scripts.backup.shutil.which", return_value="docker"),
                patch("scripts.backup._stream_command", side_effect=succeeded),
            ):
                self.assertTrue(backup.backup_docker(compose_file, "20261010_120000"))

            self.assertEqual(
                [item[0] for item in calls],
                [
                    "docker_db_20261010_120000.sql",
                    "docker_app_20261010_120000.tar.gz",
                    "docker_chroma_20261010_120000.tar.gz",
                ],
            )
            self.assertIn("enterprise_business", calls[0][1][-1])
            self.assertIn("skills/enterprise", calls[1][1][-1])
            self.assertIn("-C /data", calls[2][1][-1])
            self.assertIn("ls -A /data", calls[2][1][-1])      # 空目录直接失败，不出空备份

    def test_docker_backup_rejects_unsafe_database_name(self):
        with tempfile.TemporaryDirectory() as directory:
            compose_file = pathlib.Path(directory) / "compose.yml"
            compose_file.write_text("services: {}\n", encoding="utf-8")
            with (
                patch("scripts.backup.shutil.which", return_value="docker"),
                patch.dict("scripts.backup.os.environ", {"ENTERPRISE_DB_NAME": "db; touch /tmp/pwn"}),
                patch("scripts.backup._stream_command") as stream,
            ):
                self.assertFalse(backup.backup_docker(compose_file, "now"))
            stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
