import base64
import importlib.machinery
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CLIENT_PATH = Path(__file__).parents[1] / "spawnwp-api"
LOADER = importlib.machinery.SourceFileLoader("spawnwp_api_client", str(CLIENT_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
client = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(client)


def bundle(**overrides):
    payload = {
        "version": 1,
        "server_url": "https://server.example",
        "pairing_id": "pairing-id",
        "token": "single-use-token",
        "scope": "provision",
        "expires": int(time.time()) + 900,
    }
    payload.update(overrides)
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return f"spawnbp1:{encoded}"


class ClientTests(unittest.TestCase):
    def test_parse_bundle_accepts_provision_https(self):
        result = client.parse_bundle(bundle())
        self.assertEqual(result["server_url"], "https://server.example")
        self.assertEqual(result["scope"], "provision")

    def test_parse_bundle_rejects_wrong_scope_insecure_or_expired(self):
        for value in (
            bundle(scope="ingest"),
            bundle(server_url="http://server.example"),
            bundle(expires=int(time.time()) - 1),
            bundle(expires="not-a-time"),
        ):
            with self.subTest(value=value), self.assertRaises(client.ClientError):
                client.parse_bundle(value)

    def test_duration_parser(self):
        self.assertEqual(client.parse_duration("30m"), 1800)
        self.assertEqual(client.parse_duration("2h"), 7200)
        self.assertEqual(client.parse_duration("604800"), 604800)
        with self.assertRaises(Exception):
            client.parse_duration("4m")

    def test_openssl_key_and_signature_are_valid_ed25519(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "client.key"
            client.generate_private_key(key)
            public = base64.b64decode(client.public_key_b64(key))
            signature = base64.b64decode(client.sign_bytes(key, b"signed message"))
            Ed25519PublicKey.from_public_bytes(public).verify(signature, b"signed message")
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_pair_writes_private_credentials_without_exposing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "api.json"
            args = SimpleNamespace(
                pairing_code=bundle(),
                config=config,
                force=False,
                source_host="automation.example",
                label="CI",
            )
            response = {
                "connection_id": "connection-id",
                "scope": "provision",
            }
            with mock.patch.object(client, "http_json", return_value=response) as request:
                result = client.command_pair(args)
            saved = json.loads(config.read_text())
            key = Path(saved["private_key"])
            self.assertTrue(key.is_file())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("private_key", result)
            sent = json.loads(request.call_args.kwargs["body"])
            self.assertEqual(sent["source_host"], "automation.example")
            self.assertEqual(len(base64.b64decode(sent["proof"])), 64)

    def test_provision_builds_defaults_and_custom_idempotency_key(self):
        args = SimpleNamespace(
            config=Path("/tmp/config"),
            blueprint="development",
            expires=3600,
            role="administrator",
            group="API",
            name=None,
            access_profile="restricted-admin",
            credentials_mode="managed",
            idempotency_key="deployment-0001",
            timeout=610,
        )
        config = {
            "server_url": "https://server.example",
            "connection_id": "id",
            "private_key": "/tmp/key",
        }
        expected = {"project": "demo"}
        with mock.patch.object(client, "load_config", return_value=config), mock.patch.object(
            client, "signed_request", return_value=expected,
        ) as signed:
            self.assertEqual(client.command_provision(args), expected)
        _, method, path = signed.call_args.args
        self.assertEqual((method, path), ("POST", "/api/provision"))
        self.assertEqual(signed.call_args.kwargs["payload"]["role"], "administrator")
        self.assertEqual(signed.call_args.kwargs["payload"]["access_profile"], "restricted-admin")
        self.assertEqual(signed.call_args.kwargs["payload"]["credentials_mode"], "managed")
        self.assertEqual(
            signed.call_args.kwargs["headers"]["Idempotency-Key"],
            "deployment-0001",
        )

    def test_magic_link_is_scoped_to_project_path(self):
        args = SimpleNamespace(config=Path("/tmp/config"), project="demo-site")
        config = {
            "server_url": "https://server.example", "connection_id": "id",
            "private_key": "/tmp/key",
        }
        with mock.patch.object(client, "load_config", return_value=config), mock.patch.object(
            client, "signed_request", return_value={"url": "https://example.test/magic"},
        ) as signed:
            client.command_magic_link(args)
        self.assertEqual(
            signed.call_args.args[1:],
            ("POST", "/api/provision/sites/demo-site/magic-link"),
        )
        with self.assertRaises(client.ClientError):
            client.command_magic_link(SimpleNamespace(config=args.config, project="../bad"))

    def test_revoke_removes_local_credentials_after_server_success(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "api.json"
            key_path = Path(directory) / "api.key"
            key_path.write_text("private")
            config_path.write_text(json.dumps({
                "server_url": "https://server.example",
                "connection_id": "id",
                "private_key": str(key_path),
            }))
            args = SimpleNamespace(config=config_path, yes=True)
            with mock.patch.object(
                client,
                "signed_request",
                return_value={"status": "revoked", "active_sites": 1},
            ):
                result = client.command_revoke(args)
            self.assertEqual(result["status"], "revoked")
            self.assertFalse(config_path.exists())
            self.assertFalse(key_path.exists())

    def test_revoke_requires_explicit_confirmation(self):
        with self.assertRaises(client.ClientError):
            client.command_revoke(SimpleNamespace(config=Path("/tmp/no"), yes=False))


if __name__ == "__main__":
    unittest.main()
