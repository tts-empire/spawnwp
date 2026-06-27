import importlib.machinery
import importlib.util
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path


def load_updater():
    path = Path(__file__).parents[1] / "spawnwp"
    loader = importlib.machinery.SourceFileLoader("spawnwp_updater", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


updater = load_updater()


class UpdaterTests(unittest.TestCase):
    def test_semver_accepts_stable_versions(self):
        self.assertEqual((1, 12, 3), updater.version_tuple("1.12.3"))

    def test_semver_rejects_prerelease(self):
        with self.assertRaises(updater.UpdateError):
            updater.version_tuple("1.2.3-beta.1")

    def test_safe_extract_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("../escape")
                data = b"bad"
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            with self.assertRaises(updater.UpdateError):
                updater.safe_extract(archive, root / "out")

    def test_signature_rejects_modified_manifest(self):
        key = os.environ.get("SPAWNWP_TEST_PRIVATE_KEY")
        if not key:
            self.skipTest("SPAWNWP_TEST_PRIVATE_KEY not set")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            signature = root / "manifest.sig"
            manifest.write_text('{"version":"0.1.0"}\n')
            import subprocess
            subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", key,
                            "-rawin", "-in", str(manifest), "-out", str(signature)], check=True)
            manifest.write_text('{"version":"0.1.1"}\n')
            old_key = updater.PUBLIC_KEY
            updater.PUBLIC_KEY = Path(__file__).parents[1] / "release-public.pem"
            try:
                with self.assertRaises(updater.UpdateError):
                    updater.verify_signature(manifest, signature)
            finally:
                updater.PUBLIC_KEY = old_key

    def test_restore_removes_files_absent_before_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_targets = updater.TARGETS
            updater.TARGETS = {"cockpit": root / "cockpit"}
            entry = {"target_root": "cockpit", "target": "new.txt"}
            backup = root / "backup"
            backup.mkdir()
            try:
                updater.snapshot_targets([entry], backup)
                target = updater.TARGETS["cockpit"] / "new.txt"
                target.parent.mkdir()
                target.write_text("introduced")
                updater.restore_targets([entry], backup)
                self.assertFalse(target.exists())
            finally:
                updater.TARGETS = old_targets


if __name__ == "__main__":
    unittest.main()
