"""Cross-implementation vectors: signatures produced by PHP sodium
(plugins/spawnwp-deploy/src/class-spawnwp-deploy-crypto.php canonical format, fixture generated
with sodium_crypto_sign_detached) must verify with the Python machine_auth
module, so the two sides cannot drift apart silently."""

import base64
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))

import machine_auth

VECTORS = json.loads((Path(__file__).parent / "fixtures/machine-auth-vectors.json").read_text())


class MachineAuthVectorTests(unittest.TestCase):
    def test_php_signatures_verify(self):
        for case in VECTORS["cases"]:
            with self.subTest(path=case["path"]):
                body = base64.b64decode(case["body_b64"])
                with mock.patch.object(machine_auth.time, "time", return_value=case["timestamp"]):
                    self.assertTrue(machine_auth.verify(
                        VECTORS["public_key"], case["signature"], case["method"],
                        case["path"], case["timestamp"], case["nonce"], body))

    def test_php_signature_fails_on_tampered_body(self):
        case = VECTORS["cases"][1]
        with mock.patch.object(machine_auth.time, "time", return_value=case["timestamp"]):
            self.assertFalse(machine_auth.verify(
                VECTORS["public_key"], case["signature"], case["method"],
                case["path"], case["timestamp"], case["nonce"], b"{}"))

    def test_php_pair_proof_verifies(self):
        proof = VECTORS["pair_proof"]
        self.assertTrue(machine_auth.verify_detached(
            VECTORS["public_key"], proof["signature"], proof["data"].encode()))

    def test_clock_skew_enforced(self):
        case = VECTORS["cases"][0]
        body = base64.b64decode(case["body_b64"])
        with mock.patch.object(machine_auth.time, "time",
                               return_value=case["timestamp"] + machine_auth.CLOCK_SKEW + 1):
            self.assertFalse(machine_auth.verify(
                VECTORS["public_key"], case["signature"], case["method"],
                case["path"], case["timestamp"], case["nonce"], body))

    def test_short_nonce_rejected(self):
        keys = machine_auth.generate_keypair()
        now = int(time.time())
        signature = machine_auth.sign(keys["private"], "GET", "/x", now, "short", b"")
        self.assertFalse(machine_auth.verify(keys["public"], signature, "GET", "/x",
                                             now, "short", b""))

    def test_roundtrip_sign_verify(self):
        keys = machine_auth.generate_keypair()
        now = int(time.time())
        nonce = "0123456789abcdef"
        signature = machine_auth.sign(keys["private"], "post", "/api/ingest/jobs", now, nonce, b"{}")
        self.assertTrue(machine_auth.verify(keys["public"], signature, "POST",
                                            "/api/ingest/jobs", now, nonce, b"{}"))


if __name__ == "__main__":
    unittest.main()
