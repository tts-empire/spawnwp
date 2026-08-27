import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))

try:
    import module_api
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


@unittest.skipUnless(HAS_CRYPTO, "requires cryptography")
class LocalModuleCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old = module_api.MODULES_ROOT, module_api.CREDENTIALS_ROOT
        module_api.MODULES_ROOT = root / "modules"
        module_api.CREDENTIALS_ROOT = root / "credentials"
        self.old_env = {key: os.environ.get(key) for key in (
            "SPAWNWP_INGEST_DB", "SPAWNWP_CONFIG",
        )}
        os.environ["SPAWNWP_INGEST_DB"] = str(root / "ingest.db")
        os.environ["SPAWNWP_CONFIG"] = str(root / "config.env")
        (root / "config.env").write_text("COCKPIT_DOMAIN=cockpit.example.test\n")
        current = module_api.MODULES_ROOT / "demo-launcher" / "current"
        current.mkdir(parents=True)
        (current / "module.json").write_text(json.dumps({
            "id": "demo-launcher", "name": "Demo Launcher",
            "core_api_scope": "provision",
        }))

    def tearDown(self):
        module_api.MODULES_ROOT, module_api.CREDENTIALS_ROOT = self.old
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def test_ensure_is_atomic_private_and_idempotent(self):
        first = module_api.ensure("demo-launcher", "provision")
        path = module_api.credential_path("demo-launcher")
        credential = json.loads(path.read_text())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(credential["connection_id"], first["connection_id"])
        self.assertNotIn(credential["private_key"], first.values())

        second = module_api.ensure("demo-launcher", "provision")
        self.assertFalse(second["created"])
        self.assertEqual(second["connection_id"], first["connection_id"])
        self.assertEqual(json.loads(path.read_text())["private_key"], credential["private_key"])

        db = module_api.ingest._connect()
        try:
            row = db.execute("SELECT * FROM connections WHERE id=?", (first["connection_id"],)).fetchone()
        finally:
            db.close()
        self.assertEqual((row["connection_kind"], row["module_id"], row["scope"]),
                         ("local_module", "demo-launcher", "provision"))

    def test_scope_must_match_signed_manifest(self):
        with self.assertRaises(module_api.ModuleAPIError):
            module_api.ensure("demo-launcher", "ingest")

    def test_revoke_removes_key_but_preserves_managed_site(self):
        issued = module_api.ensure("demo-launcher", "provision")
        db = module_api.ingest._connect()
        try:
            db.execute("CREATE TABLE provision_sites(project TEXT,connection_id TEXT)")
            db.execute("INSERT INTO provision_sites VALUES (?,?)", ("demo-site", issued["connection_id"]))
            db.commit()
        finally:
            db.close()
        result = module_api.revoke("demo-launcher")
        self.assertEqual(result["revoked"], 1)
        self.assertFalse(module_api.credential_path("demo-launcher").exists())
        db = module_api.ingest._connect()
        try:
            status = db.execute("SELECT status FROM connections WHERE id=?", (issued["connection_id"],)).fetchone()[0]
            sites = db.execute("SELECT COUNT(*) FROM provision_sites").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(status, "revoked")
        self.assertEqual(sites, 1)


if __name__ == "__main__":
    unittest.main()
