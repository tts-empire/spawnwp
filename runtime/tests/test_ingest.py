import base64
import importlib
import io
import json
import os
import secrets
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAS_TESTCLIENT = True
except Exception:  # httpx is not part of the cockpit venv (raises RuntimeError)
    HAS_TESTCLIENT = False

try:
    import machine_auth
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def build_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def blueprint_fields(**overrides) -> dict:
    fields = {
        "schema_version": 2,
        "id": "agency-base",
        "name": "Agency base",
        "version": "1.0.0",
        "description": "Captured from a template site.",
        "php": {"default": "8.3", "allowed": ["8.3"]},
        "wordpress": "latest",
        "created_at": "2026-07-03T10:00:00Z",
        "capture": {"plugins": True, "themes": False, "uploads": False, "database": True},
        "wporg_plugins": ["woocommerce"],
        "premium_plugins": [{"name": "ACF Pro", "slug": "acf-pro", "version": "6.3.1"}],
        "theme": None,
    }
    fields.update(overrides)
    return fields


@unittest.skipUnless(HAS_TESTCLIENT and HAS_CRYPTO, "requires httpx and cryptography")
class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.custom = root / "custom"
        self.payloads = root / "payloads"
        self.custom.mkdir()
        self.payloads.mkdir()
        (root / "VERSION").write_text("0.4.0\n")
        (root / "config.env").write_text("COCKPIT_DOMAIN=cockpit.example.com\n")
        os.environ.update(
            SPAWNWP_INGEST_DB=str(root / "ingest.db"),
            SPAWNWP_BLUEPRINT_PAYLOADS=str(self.payloads),
            SPAWNWP_CUSTOM_BLUEPRINTS=str(self.custom),
            SPAWNWP_BLUEPRINT_TOOL=str(RUNTIME / "scripts/blueprint.py"),
            SPAWNWP_BUILTIN_BLUEPRINTS=str(RUNTIME / "blueprints"),
            SPAWNWP_VERSION_FILE=str(root / "VERSION"),
            SPAWNWP_CONFIG=str(root / "config.env"),
            SPAWNWP_METRICS_FILE=str(root / "metrics.json"),
        )
        import ingest
        importlib.reload(ingest)
        self.ingest = ingest
        app = FastAPI()
        app.include_router(ingest.router)
        self.client = TestClient(app)
        self.keys = machine_auth.generate_keypair()
        self.connection_id = ""

    def tearDown(self):
        self.temp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def make_pairing(self) -> dict:
        response = self.client.post("/api/blueprint-pairings")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        encoded = data["bundle"].removeprefix("spawnbp1:")
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        return json.loads(decoded)

    def pair(self, bundle=None, token=None, host="template.example.com") -> dict:
        bundle = bundle or self.make_pairing()
        # the pairing proof signs the raw proof string, not a canonical request
        proof_data = f"pair|{bundle['pairing_id']}|{self.keys['public']}|{host}"
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(self.keys["private"]))
        proof = base64.b64encode(private.sign(proof_data.encode())).decode()
        response = self.client.post("/api/ingest/pair", json={
            "pairing_id": bundle["pairing_id"],
            "token": token or bundle["token"],
            "source_public_key": self.keys["public"],
            "source_host": host,
            "proof": proof,
            "label": "Template site",
        })
        if response.status_code == 200:
            self.connection_id = response.json()["connection_id"]
        return response

    def signed(self, method: str, path: str, body: bytes = b"", extra_headers=None,
               timestamp=None, nonce=None, signature=None):
        timestamp = timestamp or int(time.time())
        nonce = nonce or secrets.token_hex(16)
        signature = signature or machine_auth.sign(
            self.keys["private"], method, path, timestamp, nonce, body)
        headers = {
            "X-SpawnWP-Connection": self.connection_id,
            "X-SpawnWP-Timestamp": str(timestamp),
            "X-SpawnWP-Nonce": nonce,
            "X-SpawnWP-Signature": signature,
        }
        headers.update(extra_headers or {})
        return self.client.request(method, path, content=body, headers=headers)

    def upload(self, payload: bytes, blueprint=None, replace=False, chunk_size=1024):
        import hashlib
        blueprint = blueprint or blueprint_fields()
        chunk_count = -(-len(payload) // chunk_size)
        body = json.dumps({
            "blueprint": blueprint,
            "archive": {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                        "chunk_size": chunk_size, "chunk_count": chunk_count},
            "replace": replace,
        }).encode()
        response = self.signed("POST", "/api/ingest/jobs", body)
        if response.status_code != 200:
            return response, None
        job_id = response.json()["job_id"]
        for index in range(chunk_count):
            chunk = payload[index * chunk_size:(index + 1) * chunk_size]
            result = self.signed(
                "PUT", f"/api/ingest/jobs/{job_id}/chunks/{index}", chunk,
                extra_headers={"X-SpawnWP-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()})
            self.assertEqual(result.status_code, 200, result.text)
        response = self.signed("POST", f"/api/ingest/jobs/{job_id}/finalize")
        return response, job_id

    def wait_for(self, job_id: str, timeout=15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = self.signed("GET", f"/api/ingest/jobs/{job_id}")
            state = response.json()
            if state["state"] in {"complete", "failed"}:
                return state
            time.sleep(0.05)
        self.fail("finalize did not settle in time")

    def good_payload(self) -> bytes:
        return build_zip({
            "database.jsonl": b'{"type":"table"}\n',
            "content/plugins/woocommerce/woocommerce.php": b"<?php // plugin",
        })

    # ── tests ────────────────────────────────────────────────────────────────

    def test_happy_path_installs_blueprint(self):
        self.assertEqual(self.pair().status_code, 200)
        preflight = self.signed("GET", "/api/ingest/preflight")
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertEqual(preflight.json()["ingest_format"], 1)
        self.assertIn("development", preflight.json()["existing_blueprint_ids"])
        response, job_id = self.upload(self.good_payload())
        self.assertEqual(response.status_code, 202, response.text)
        state = self.wait_for(job_id)
        self.assertEqual(state["state"], "complete", state["error"])
        manifest = json.loads((self.custom / "agency-base.json").read_text())
        self.assertEqual(manifest["schema_version"], 2)
        payload_file = self.payloads / "agency-base" / manifest["payload"]["file"]
        self.assertTrue(payload_file.is_file())
        metrics = json.loads(Path(os.environ["SPAWNWP_METRICS_FILE"]).read_text())
        self.assertEqual(metrics["blueprint_captures"], 1)
        self.assertFalse((self.payloads / ".staging" / job_id).exists())

    def test_pair_rejects_bad_token(self):
        response = self.pair(token="wrong-token")
        self.assertEqual(response.status_code, 403)

    def test_pair_single_use(self):
        bundle = self.make_pairing()
        self.assertEqual(self.pair(bundle=bundle).status_code, 200)
        self.assertEqual(self.pair(bundle=bundle).status_code, 403)

    def test_tampered_signature_rejected(self):
        self.pair()
        response = self.signed("GET", "/api/ingest/preflight",
                               signature=base64.b64encode(b"x" * 64).decode())
        self.assertEqual(response.status_code, 401)

    def test_stale_timestamp_rejected(self):
        self.pair()
        response = self.signed("GET", "/api/ingest/preflight",
                               timestamp=int(time.time()) - 600)
        self.assertEqual(response.status_code, 401)

    def test_nonce_replay_rejected(self):
        self.pair()
        nonce = secrets.token_hex(16)
        timestamp = int(time.time())
        first = self.signed("GET", "/api/ingest/preflight", timestamp=timestamp, nonce=nonce)
        self.assertEqual(first.status_code, 200)
        second = self.signed("GET", "/api/ingest/preflight", timestamp=timestamp, nonce=nonce)
        self.assertEqual(second.status_code, 409)

    def test_unpaired_connection_rejected(self):
        self.connection_id = "nope"
        response = self.signed("GET", "/api/ingest/preflight")
        self.assertEqual(response.status_code, 401)

    def test_duplicate_id_needs_replace(self):
        self.pair()
        response, job_id = self.upload(self.good_payload())
        self.assertEqual(self.wait_for(job_id)["state"], "complete")
        response, _ = self.upload(self.good_payload())
        self.assertEqual(response.status_code, 409)
        old_payload = json.loads((self.custom / "agency-base.json").read_text())["payload"]["file"]
        response, job_id = self.upload(self.good_payload(), replace=True)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.wait_for(job_id)["state"], "complete")
        manifest = json.loads((self.custom / "agency-base.json").read_text())
        self.assertNotEqual(manifest["payload"]["file"], old_payload)
        remaining = list((self.payloads / "agency-base").glob("payload*.zip"))
        self.assertEqual([p.name for p in remaining], [manifest["payload"]["file"]])

    def test_builtin_id_never_replaceable(self):
        self.pair()
        blueprint = blueprint_fields(id="development")
        response, _ = self.upload(self.good_payload(), blueprint=blueprint, replace=True)
        self.assertEqual(response.status_code, 409)

    def test_invalid_manifest_rejected_at_job_creation(self):
        self.pair()
        blueprint = blueprint_fields(wporg_plugins=["Bad_Slug"])
        response, _ = self.upload(self.good_payload(), blueprint=blueprint)
        self.assertEqual(response.status_code, 422)

    def test_chunk_checksum_mismatch_rejected(self):
        self.pair()
        payload = self.good_payload()
        import hashlib
        body = json.dumps({
            "blueprint": blueprint_fields(),
            "archive": {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                        "chunk_size": len(payload), "chunk_count": 1},
            "replace": False,
        }).encode()
        job_id = self.signed("POST", "/api/ingest/jobs", body).json()["job_id"]
        response = self.signed("PUT", f"/api/ingest/jobs/{job_id}/chunks/0", payload,
                               extra_headers={"X-SpawnWP-Chunk-SHA256": "0" * 64})
        self.assertEqual(response.status_code, 422)

    def test_traversal_zip_fails_without_manifest(self):
        self.pair()
        payload = build_zip({
            "database.jsonl": b"{}\n",
            "content/../../etc/evil": b"nope",
        })
        response, job_id = self.upload(payload)
        self.assertEqual(response.status_code, 202)
        state = self.wait_for(job_id)
        self.assertEqual(state["state"], "failed")
        self.assertIn("unsafe path", state["error"])
        self.assertFalse((self.custom / "agency-base.json").exists())
        self.assertFalse((self.payloads / "agency-base").exists())

    def test_unexpected_top_level_entry_fails(self):
        self.pair()
        payload = build_zip({"database.jsonl": b"{}\n", "wp-config.php": b"<?php"})
        _, job_id = self.upload(payload)
        self.assertEqual(self.wait_for(job_id)["state"], "failed")

    def test_capture_flags_must_match_archive(self):
        self.pair()
        payload = build_zip({"content/plugins/x/x.php": b"<?php"})
        _, job_id = self.upload(payload)  # capture.database=True but no database.jsonl
        state = self.wait_for(job_id)
        self.assertEqual(state["state"], "failed")
        self.assertIn("capture flags", state["error"])

    def test_checksum_mismatch_at_finalize_fails(self):
        self.pair()
        payload = self.good_payload()
        import hashlib
        body = json.dumps({
            "blueprint": blueprint_fields(),
            "archive": {"bytes": len(payload), "sha256": "0" * 64,
                        "chunk_size": len(payload), "chunk_count": 1},
            "replace": False,
        }).encode()
        job_id = self.signed("POST", "/api/ingest/jobs", body).json()["job_id"]
        self.signed("PUT", f"/api/ingest/jobs/{job_id}/chunks/0", payload,
                    extra_headers={"X-SpawnWP-Chunk-SHA256": hashlib.sha256(payload).hexdigest()})
        self.signed("POST", f"/api/ingest/jobs/{job_id}/finalize")
        state = self.wait_for(job_id)
        self.assertEqual(state["state"], "failed")
        self.assertFalse((self.custom / "agency-base.json").exists())

    def test_finalize_requires_all_chunks(self):
        self.pair()
        payload = self.good_payload()
        import hashlib
        body = json.dumps({
            "blueprint": blueprint_fields(),
            "archive": {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                        "chunk_size": 64, "chunk_count": -(-len(payload) // 64)},
            "replace": False,
        }).encode()
        job_id = self.signed("POST", "/api/ingest/jobs", body).json()["job_id"]
        response = self.signed("POST", f"/api/ingest/jobs/{job_id}/finalize")
        self.assertEqual(response.status_code, 409)

    def test_delete_blueprint_endpoint(self):
        self.pair()
        _, job_id = self.upload(self.good_payload())
        self.assertEqual(self.wait_for(job_id)["state"], "complete")
        response = self.client.delete("/api/blueprints/agency-base")
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.custom / "agency-base.json").exists())
        self.assertFalse((self.payloads / "agency-base").exists())
        self.assertEqual(self.client.delete("/api/blueprints/agency-base").status_code, 404)

    def test_delete_refuses_v1_manifests(self):
        source = (RUNTIME / "blueprints/development.json").read_text()
        (self.custom / "development.json").write_text(source)
        response = self.client.delete("/api/blueprints/development")
        self.assertEqual(response.status_code, 400)

    def test_revoked_connection_stops_working(self):
        self.pair()
        response = self.client.delete(f"/api/blueprint-connections/{self.connection_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.signed("GET", "/api/ingest/preflight").status_code, 401)


if __name__ == "__main__":
    unittest.main()
