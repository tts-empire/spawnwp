import hashlib
import importlib.util
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).parents[1] / "module_manager.py"
spec = importlib.util.spec_from_file_location("module_manager", MODULE_PATH)
module_manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module_manager)


class ModuleManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old = (
            module_manager.MODULES_ROOT, module_manager.STATE_ROOT,
            module_manager.CORE_VERSION_FILE,
        )
        module_manager.MODULES_ROOT = root / "modules"
        module_manager.STATE_ROOT = root / "state"
        module_manager.CORE_VERSION_FILE = root / "VERSION"
        module_manager.CORE_VERSION_FILE.write_text("0.5.32\n")
        self.root = root

    def tearDown(self):
        (module_manager.MODULES_ROOT, module_manager.STATE_ROOT,
         module_manager.CORE_VERSION_FILE) = self.old
        self.temp.cleanup()

    def package(self, *, module_id="demo-launcher", version="1.0.0",
                min_core="0.5.29", max_core="0.9.99", core_api_scope=None,
                lifecycle=False):
        package = self.root / f"{module_id}-{version}"
        package.mkdir()
        (package / "app.py").write_text("VALUE = 1\n")
        files = ["app.py"]
        if lifecycle:
            (package / "activate.py").write_text("#!/usr/bin/env python3\n")
            (package / "deactivate.py").write_text("#!/usr/bin/env python3\n")
            (package / "activate.py").chmod(0o755)
            (package / "deactivate.py").chmod(0o755)
            files.extend(["activate.py", "deactivate.py"])
        archive = self.root / f"{module_id}-{version}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(package, arcname=package.name)
        digest = hashlib.sha256((package / "app.py").read_bytes()).hexdigest()
        archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = {
            "schema": 1, "id": module_id, "name": "Demo Launcher",
            "version": version, "description": "Temporary public demos",
            "min_core_version": min_core, "max_core_version": max_core,
            "archive_sha256": archive_digest,
            "admin_path": "/modules/demo-launcher/",
            "files": [{"path": path, "sha256": hashlib.sha256((package / path).read_bytes()).hexdigest()} for path in files],
        }
        if core_api_scope is not None:
            manifest["core_api_scope"] = core_api_scope
        manifest_path = self.root / f"{module_id}-{version}.manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        (self.root / f"{module_id}-{version}.manifest.sig").write_bytes(b"signature")
        return archive

    def test_install_and_discovery_are_atomic(self):
        archive = self.package()
        with mock.patch.object(module_manager, "verify_signature"):
            result = module_manager.install(str(archive))
        self.assertEqual(result["id"], "demo-launcher")
        current = module_manager.MODULES_ROOT / "demo-launcher" / "current"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve().name, "1.0.0")
        self.assertEqual(module_manager.installed()[0]["name"], "Demo Launcher")

    def test_incompatible_core_is_rejected(self):
        archive = self.package(min_core="1.0.0", max_core="2.0.0")
        with mock.patch.object(module_manager, "verify_signature"), self.assertRaises(
            module_manager.ModuleError,
        ):
            module_manager.install(str(archive))

    def test_unsupported_core_api_scope_is_rejected(self):
        archive = self.package(core_api_scope="root")
        with mock.patch.object(module_manager, "verify_signature"), self.assertRaises(
            module_manager.ModuleError,
        ):
            module_manager.install(str(archive))

    def test_signed_scope_gets_credential_on_install_and_revocation_on_remove(self):
        archive = self.package(core_api_scope="provision")
        with mock.patch.object(module_manager, "verify_signature"), \
                mock.patch.object(module_manager, "_manage_credential", return_value={}) as credentials:
            module_manager.install(str(archive))
            module_manager.remove("demo-launcher")
        self.assertEqual(credentials.call_args_list, [
            mock.call("ensure", "demo-launcher", "provision"),
            mock.call("revoke", "demo-launcher"),
        ])

    def test_failed_fresh_install_revokes_new_credential(self):
        archive = self.package(core_api_scope="provision")
        with mock.patch.object(module_manager, "verify_signature"), \
                mock.patch.object(module_manager, "_manage_credential", return_value={}) as credentials, \
                mock.patch.object(module_manager, "_run_hook", side_effect=RuntimeError("failed")), \
                self.assertRaises(RuntimeError):
            module_manager.install(str(archive))
        self.assertEqual(credentials.call_args_list, [
            mock.call("ensure", "demo-launcher", "provision"),
            mock.call("revoke", "demo-launcher"),
        ])

    def test_update_rejects_another_module_before_activation(self):
        original = self.package()
        with mock.patch.object(module_manager, "verify_signature"):
            module_manager.install(str(original))
        replacement = self.package(module_id="another-module", version="2.0.0")
        with mock.patch.object(module_manager, "verify_signature"), self.assertRaises(
            module_manager.ModuleError,
        ):
            module_manager.update("demo-launcher", str(replacement))
        self.assertFalse((module_manager.MODULES_ROOT / "another-module").exists())
        current = module_manager.MODULES_ROOT / "demo-launcher" / "current"
        self.assertEqual(current.resolve().name, "1.0.0")

    def test_failed_upgrade_reactivates_previous_release(self):
        first = self.package(version="1.0.0")
        with mock.patch.object(module_manager, "verify_signature"):
            module_manager.install(str(first))
        second = self.package(version="2.0.0")

        def hook(release, name, **kwargs):
            if release.name == "2.0.0":
                raise module_manager.ModuleError("new hook failed")

        with mock.patch.object(module_manager, "verify_signature"), \
                mock.patch.object(module_manager, "_run_hook", side_effect=hook) as run, \
                self.assertRaises(module_manager.ModuleError):
            module_manager.install(str(second))
        current = module_manager.MODULES_ROOT / "demo-launcher" / "current"
        self.assertEqual(current.resolve().name, "1.0.0")
        self.assertEqual([call.args[0].name for call in run.call_args_list], ["2.0.0", "1.0.0"])

    def test_archive_path_traversal_is_rejected(self):
        archive = self.root / "bad.tar.gz"
        payload = self.root / "payload"
        payload.write_text("bad")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(payload, arcname="../escape")
        destination = self.root / "out"
        destination.mkdir()
        with self.assertRaises(module_manager.ModuleError):
            module_manager.safe_extract(archive, destination)

    def test_archive_expansion_limit_is_enforced(self):
        archive = self.root / "large.tar.gz"
        payload = self.root / "large-payload"
        payload.write_bytes(b"1234567890")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(payload, arcname="large-payload")
        destination = self.root / "large-out"
        destination.mkdir()
        with mock.patch.object(module_manager, "MAX_EXTRACTED_BYTES", 5), \
                self.assertRaises(module_manager.ModuleError):
            module_manager.safe_extract(archive, destination)

    def test_remove_runs_hook_and_unlinks_current(self):
        archive = self.package()
        with mock.patch.object(module_manager, "verify_signature"):
            module_manager.install(str(archive))
        with mock.patch.object(module_manager, "_run_hook") as hook:
            module_manager.remove("demo-launcher", force=True)
        hook.assert_called_once()
        self.assertFalse((module_manager.MODULES_ROOT / "demo-launcher" / "current").exists())

    def test_lifecycle_state_and_controls(self):
        archive = self.package(lifecycle=True, core_api_scope="provision")
        with mock.patch.object(module_manager, "verify_signature"), \
                mock.patch.object(module_manager, "_manage_credential", return_value={}):
            module_manager.install(str(archive))
            self.assertEqual(module_manager.installed()[0]["status"], "active")
            module_manager.disable("demo-launcher")
            self.assertEqual(module_manager.installed()[0]["status"], "disabled")
            module_manager.enable("demo-launcher")
            item = module_manager.installed()[0]
            self.assertEqual(item["status"], "active")
            self.assertTrue(item["capabilities"]["activate"])

    def test_legacy_module_does_not_expose_lifecycle_controls(self):
        archive = self.package()
        with mock.patch.object(module_manager, "verify_signature"):
            module_manager.install(str(archive))
        self.assertFalse(module_manager.installed()[0]["capabilities"]["activate"])
        with self.assertRaises(module_manager.ModuleError):
            module_manager.disable("demo-launcher")


if __name__ == "__main__":
    unittest.main()
