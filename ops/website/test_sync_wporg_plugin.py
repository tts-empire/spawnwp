from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import sync_wporg_plugin as sync


class Response:
    def __init__(self, body: bytes):
        self.body = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def plugin_zip(version: str = "1.2.3", extra: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    files = {
        "spawnwp-deploy/spawnwp-deploy.php": (
            "<?php\n/**\n * Plugin Name: SpawnWP Deploy\n"
            f" * Version: {version}\n */\n"
            f"define( 'SPAWNWP_DEPLOY_VERSION', '{version}' );\n"
        ).encode(),
        "spawnwp-deploy/readme.txt": f"=== SpawnWP Deploy ===\nStable tag: {version}\n".encode(),
        "spawnwp-deploy/assets/admin.js": b"void 0;\n",
        "spawnwp-deploy/assets/admin.css": b".spawnwp { display:block; }\n",
        "spawnwp-deploy/recovery/loader.php": b"<?php\n",
        "spawnwp-deploy/src/class-example.php": b"<?php\n",
    }
    files.update(extra or {})
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.target = self.root / "mirror"
        self.archive = self.root / "archive"
        self.private = self.root / "private.pem"
        self.public = self.root / "public.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(self.private)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["openssl", "pkey", "-in", str(self.private), "-pubout", "-out", str(self.public)],
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def release(self, version: str = "1.2.3") -> sync.Release:
        return sync.Release(
            version=version,
            download_url=f"https://downloads.wordpress.org/plugin/spawnwp-deploy.{version}.zip",
        )

    def write_zip(self, version: str = "1.2.3") -> Path:
        path = self.root / f"spawnwp-deploy-{version}.zip"
        path.write_bytes(plugin_zip(version))
        return path

    def test_fetch_release_accepts_only_official_stable_download(self):
        payload = json.dumps(
            {
                "slug": "spawnwp-deploy",
                "version": "1.2.3",
                "download_link": "https://downloads.wordpress.org/plugin/spawnwp-deploy.1.2.3.zip",
            }
        ).encode()
        with mock.patch.object(sync.urllib.request, "urlopen", return_value=Response(payload)):
            self.assertEqual(sync.fetch_release().version, "1.2.3")

        for bad in (
            {"slug": "other", "version": "1.2.3", "download_link": "https://downloads.wordpress.org/plugin/spawnwp-deploy.1.2.3.zip"},
            {"slug": "spawnwp-deploy", "version": "1.2.4-dev", "download_link": "https://downloads.wordpress.org/plugin/spawnwp-deploy.1.2.4-dev.zip"},
            {"slug": "spawnwp-deploy", "version": "1.2.3", "download_link": "https://example.com/plugin/spawnwp-deploy.1.2.3.zip"},
        ):
            with self.subTest(bad=bad), mock.patch.object(
                sync.urllib.request, "urlopen", return_value=Response(json.dumps(bad).encode())
            ):
                with self.assertRaises(sync.SyncError):
                    sync.fetch_release()

        with mock.patch.object(sync.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(sync.SyncError, "request failed"):
                sync.fetch_release()

    def test_validate_zip_rejects_version_mismatch_and_traversal(self):
        good = self.write_zip()
        files = sync.validate_zip(good, self.release())
        self.assertIn("spawnwp-deploy/spawnwp-deploy.php", files)

        mismatch = self.root / "mismatch.zip"
        mismatch.write_bytes(plugin_zip("1.2.2"))
        with self.assertRaisesRegex(sync.SyncError, "version"):
            sync.validate_zip(mismatch, self.release())

        unsafe = self.root / "unsafe.zip"
        unsafe.write_bytes(plugin_zip(extra={"spawnwp-deploy/../escape.php": b"bad"}))
        with self.assertRaisesRegex(sync.SyncError, "unsafe ZIP member"):
            sync.validate_zip(unsafe, self.release())

        corrupt = self.root / "corrupt.zip"
        corrupt.write_bytes(b"not a zip")
        with self.assertRaisesRegex(sync.SyncError, "invalid plugin ZIP"):
            sync.validate_zip(corrupt, self.release())

    def test_publish_check_noop_and_archive_dev(self):
        release = self.release()
        payload = plugin_zip()
        self.target.mkdir()
        (self.target / "release-public.pem").write_bytes(self.public.read_bytes())
        for suffix in ("", ".sha256", ".sig"):
            (self.target / f"spawnwp-deploy-0.9.0-dev.zip{suffix}").write_text("old")

        def download(_release, destination):
            destination.write_bytes(payload)

        with mock.patch.object(sync, "download_release", side_effect=download):
            digest, changed, archived = sync.publish_mirror(
                release, self.target, self.private, self.public, self.archive
            )
            self.assertTrue(changed)
            self.assertEqual(archived, 3)
            self.assertEqual(sync.read_latest(self.target), sync.expected_metadata(release, digest))
            remote = self.root / "remote.zip"
            remote.write_bytes(payload)
            sync.check_mirror(release, remote, self.target, self.public)

            signature = self.target / f"{release.filename}.sig"
            valid_signature = signature.read_bytes()
            signature.write_text("invalid\n")
            with self.assertRaisesRegex(sync.SyncError, "signature"):
                sync.check_mirror(release, remote, self.target, self.public)
            signature.write_bytes(valid_signature)

            digest2, changed2, archived2 = sync.publish_mirror(
                release, self.target, self.private, self.public, self.archive
            )
            self.assertEqual(digest2, digest)
            self.assertFalse(changed2)
            self.assertEqual(archived2, 0)

    def test_downgrade_is_rejected_without_changing_latest(self):
        self.target.mkdir()
        current = {"version": "2.0.0", "zip": "spawnwp-deploy-2.0.0.zip", "sha256": "x"}
        (self.target / "latest.json").write_text(json.dumps(current))
        with self.assertRaisesRegex(sync.SyncError, "downgrade"):
            sync.publish_mirror(
                self.release("1.9.9"), self.target, self.private, self.public, self.archive
            )
        self.assertEqual(sync.read_latest(self.target), current)

    def test_signing_failure_leaves_current_metadata_untouched(self):
        self.target.mkdir()
        current = {"version": "1.0.0", "zip": "spawnwp-deploy-1.0.0.zip", "sha256": "old"}
        (self.target / "latest.json").write_text(json.dumps(current))

        def download(_release, destination):
            destination.write_bytes(plugin_zip())

        with mock.patch.object(sync, "download_release", side_effect=download), mock.patch.object(
            sync, "sign_checksum", side_effect=sync.SyncError("signing failed")
        ):
            with self.assertRaisesRegex(sync.SyncError, "signing failed"):
                sync.publish_mirror(
                    self.release(), self.target, self.private, self.public, self.archive
                )
        self.assertEqual(sync.read_latest(self.target), current)

    def test_source_comparison_detects_changes(self):
        archive = self.write_zip()
        files = sync.validate_zip(archive, self.release())
        source = self.root / "source"
        for name, body in files.items():
            relative = Path(name).relative_to("spawnwp-deploy")
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        sync.check_source(files, source)
        (source / "assets/admin.js").write_text("changed")
        with self.assertRaisesRegex(sync.SyncError, "changed"):
            sync.check_source(files, source)


if __name__ == "__main__":
    unittest.main()
