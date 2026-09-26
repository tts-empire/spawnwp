import base64
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))

import module_licensing


class ModuleLicensingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = {key: os.environ.get(key) for key in (
            "SPAWNWP_LICENSE_STATE", "SPAWNWP_LICENSE_PUBLIC_KEYRING",
        )}
        os.environ["SPAWNWP_LICENSE_STATE"] = str(self.root / "licenses")
        self.signing_key = Ed25519PrivateKey.generate()
        public = self.signing_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        keyring_path = self.root / "license-public-keys.json"
        keyring_path.write_text(__import__("json").dumps({"schema": 1, "keys": [{
            "kid": "test-2026", "public_key_pem": public.decode(),
        }]}))
        os.environ["SPAWNWP_LICENSE_PUBLIC_KEYRING"] = str(keyring_path)
        self.identity = module_licensing.identity(create=True)
        self.manifest = {
            "schema": 2, "id": "spawnwp-mcp", "commercial_model": "premium",
            "product_id": "spawnwp-mcp", "published_at": 1_789_000_000,
        }

    def tearDown(self):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def entitlement(self, *, updates_until):
        claims = {
            "schema": 1, "module_id": self.manifest["id"],
            "product_id": self.manifest["product_id"],
            "installation_id": self.identity["installation_id"],
            "public_key": self.identity["public_key"],
            "updates_until": updates_until,
            "issued_at": int(time.time()),
        }
        return {
            "kid": "test-2026",
            "claims": claims,
            "signature": base64.b64encode(
                self.signing_key.sign(module_licensing.canonical(claims))
            ).decode(),
        }

    def test_signed_entitlement_keeps_an_old_release_usable_after_updates_expire(self):
        envelope = self.entitlement(updates_until=int(time.time()) - 1)
        module_licensing.atomic_json(module_licensing.root() / "spawnwp-mcp.json", envelope)
        result = module_licensing.check(self.manifest)
        self.assertEqual(result["state"], "updates_expired")
        self.assertTrue(result["usable"])

    def test_release_after_updates_window_is_rejected(self):
        envelope = self.entitlement(updates_until=self.manifest["published_at"] - 1)
        module_licensing.atomic_json(module_licensing.root() / "spawnwp-mcp.json", envelope)
        with self.assertRaises(module_licensing.LicenseError):
            module_licensing.check(self.manifest)

    def test_entitlement_bound_to_another_installation_is_rejected(self):
        envelope = self.entitlement(updates_until=self.manifest["published_at"] + 1)
        envelope["claims"]["installation_id"] = "0" * 32
        envelope["signature"] = base64.b64encode(
            self.signing_key.sign(module_licensing.canonical(envelope["claims"]))
        ).decode()
        with self.assertRaises(module_licensing.LicenseError):
            module_licensing.verify(envelope, "spawnwp-mcp", "spawnwp-mcp")

    def test_unknown_signing_key_is_rejected(self):
        envelope = self.entitlement(updates_until=self.manifest["published_at"] + 1)
        envelope["kid"] = "retired-key"
        with self.assertRaises(module_licensing.LicenseError):
            module_licensing.verify(envelope, "spawnwp-mcp", "spawnwp-mcp")


if __name__ == "__main__":
    unittest.main()
