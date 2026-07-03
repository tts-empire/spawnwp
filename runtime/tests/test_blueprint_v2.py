import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/blueprint.py"
spec = importlib.util.spec_from_file_location("blueprint", SCRIPT)
blueprint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blueprint)

REPO_BUILTINS = Path(__file__).parents[1] / "blueprints"


def v2_manifest(**overrides) -> dict:
    manifest = {
        "schema_version": 2,
        "id": "agency-base",
        "name": "Agency base",
        "version": "1.0.0",
        "description": "Captured from a template site.",
        "php": {"default": "8.3", "allowed": ["8.3"]},
        "wordpress": "latest",
        "created_at": "2026-07-03T10:00:00Z",
        "capture": {"plugins": True, "themes": True, "uploads": True, "database": True},
        "payload": {"file": "payload.zip", "bytes": 4, "sha256": "a" * 64},
        "wporg_plugins": ["woocommerce", "wordpress-seo"],
        "premium_plugins": [{"name": "ACF Pro", "slug": "advanced-custom-fields-pro", "version": "6.3.1"}],
        "theme": "astra",
    }
    manifest.update(overrides)
    return manifest


class BlueprintV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.builtin = root / "builtin"
        self.custom = root / "custom"
        self.payloads = root / "payloads"
        for directory in (self.builtin, self.custom, self.payloads):
            directory.mkdir()
        self.saved = (blueprint.BUILTIN_DIR, blueprint.CUSTOM_DIR, blueprint.PAYLOAD_DIR)
        blueprint.BUILTIN_DIR = self.builtin
        blueprint.CUSTOM_DIR = self.custom
        blueprint.PAYLOAD_DIR = self.payloads

    def tearDown(self):
        blueprint.BUILTIN_DIR, blueprint.CUSTOM_DIR, blueprint.PAYLOAD_DIR = self.saved
        self.temp.cleanup()

    def install(self, manifest: dict, directory=None, payload=b"zip!"):
        directory = directory or self.custom
        (directory / f"{manifest['id']}.json").write_text(json.dumps(manifest), encoding="utf-8")
        if payload is not None and manifest.get("schema_version") == 2:
            payload_dir = self.payloads / manifest["id"]
            payload_dir.mkdir(parents=True, exist_ok=True)
            (payload_dir / manifest["payload"]["file"]).write_bytes(payload)

    def copy_builtin(self, name="development"):
        source = REPO_BUILTINS / f"{name}.json"
        (self.builtin / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def test_repo_builtins_still_validate(self):
        for source in REPO_BUILTINS.glob("*.json"):
            self.copy_builtin(source.stem)
        found, errors = blueprint.discover()
        self.assertEqual(errors, [])
        self.assertEqual({item["schema_version"] for item in found.values()}, {1})

    def test_valid_v2_discovered(self):
        self.install(v2_manifest())
        found, errors = blueprint.discover()
        self.assertEqual(errors, [])
        item = found["agency-base"]
        self.assertEqual(item["source"], "custom")
        self.assertEqual(item["schema_version"], 2)

    def test_v2_rejected_in_builtin_dir(self):
        self.install(v2_manifest(), directory=self.builtin)
        found, errors = blueprint.discover()
        self.assertEqual(found, {})
        self.assertIn("custom directory", errors[0]["error"])

    def test_missing_payload_file_rejected(self):
        self.install(v2_manifest(), payload=None)
        found, errors = blueprint.discover()
        self.assertEqual(found, {})
        self.assertIn("payload archive is missing", errors[0]["error"])

    def test_payload_size_mismatch_rejected(self):
        self.install(v2_manifest(), payload=b"wrong size")
        found, errors = blueprint.discover()
        self.assertEqual(found, {})
        self.assertIn("size does not match", errors[0]["error"])

    def test_duplicate_id_across_schemas_rejected(self):
        self.copy_builtin("development")
        manifest = v2_manifest(id="development")
        self.install(manifest)
        found, errors = blueprint.discover()
        self.assertEqual(found["development"]["schema_version"], 1)
        self.assertIn("duplicate id", errors[0]["error"])

    def test_field_validation(self):
        cases = [
            (v2_manifest(extra=1), "unknown fields"),
            ({k: v for k, v in v2_manifest().items() if k != "capture"}, "missing fields"),
            (v2_manifest(capture={"plugins": True}), "exactly plugins, themes"),
            (v2_manifest(capture=dict(plugins=False, themes=False, uploads=False, database=False)),
             "at least one component"),
            (v2_manifest(payload={"file": "../evil.zip", "bytes": 4, "sha256": "a" * 64}),
             "bare archive filename"),
            (v2_manifest(payload={"file": "payload.zip", "bytes": 0, "sha256": "a" * 64}),
             "between 1 and 2 GiB"),
            (v2_manifest(payload={"file": "payload.zip", "bytes": 4, "sha256": "zz"}),
             "64 lowercase hex"),
            (v2_manifest(wporg_plugins=["Bad_Slug"]), "WordPress.org-style"),
            (v2_manifest(premium_plugins=[{"name": "X"}]), "exactly name, slug and version"),
            (v2_manifest(created_at="yesterday"), "created_at"),
            (v2_manifest(wordpress="6.5"), "wordpress must be latest"),
        ]
        for manifest, fragment in cases:
            with self.subTest(fragment=fragment):
                with self.assertRaises(blueprint.BlueprintError) as ctx:
                    blueprint.validate(manifest, self.custom / "agency-base.json")
                self.assertIn(fragment, str(ctx.exception))

    def test_schema_version_gate(self):
        with self.assertRaises(blueprint.BlueprintError) as ctx:
            blueprint.validate(v2_manifest(schema_version=3), self.custom / "agency-base.json")
        self.assertIn("schema_version must be 1 or 2", str(ctx.exception))

    def test_resolve_v2_sets_payload_path(self):
        self.install(v2_manifest())
        item = blueprint.resolve("agency-base", None)
        self.assertEqual(item["selected_php"], "8.3")
        self.assertEqual(item["payload_path"], str(self.payloads / "agency-base/payload.zip"))
        with self.assertRaises(blueprint.BlueprintError):
            blueprint.resolve("agency-base", "8.2")

    def test_shell_values_v2(self):
        self.install(v2_manifest())
        values = blueprint.shell_values(blueprint.resolve("agency-base", None))
        self.assertEqual(values["BLUEPRINT_SCHEMA"], "2")
        self.assertEqual(values["BLUEPRINT_CONTENT"], "payload")
        self.assertEqual(values["BLUEPRINT_PLUGINS"], "woocommerce wordpress-seo")
        self.assertEqual(values["BLUEPRINT_PAYLOAD"], str(self.payloads / "agency-base/payload.zip"))
        self.assertEqual(values["BLUEPRINT_PAYLOAD_SHA256"], "a" * 64)
        self.assertEqual(values["BLUEPRINT_CAPTURE_DATABASE"], "1")
        self.assertEqual(values["WP_DEBUG_VALUE"], "")
        self.assertEqual(values["BLUEPRINT_DEVKIT"], "0")

    def test_shell_values_v1_unchanged(self):
        self.copy_builtin("development")
        values = blueprint.shell_values(blueprint.resolve("development", None))
        self.assertEqual(values["BLUEPRINT_SCHEMA"], "1")
        self.assertEqual(values["BLUEPRINT_PAYLOAD"], "")
        self.assertEqual(values["BLUEPRINT_CAPTURE_DATABASE"], "0")

    def run_cli(self, *args, stdin=""):
        env = {"PATH": "/usr/bin:/bin",
               "SPAWNWP_BUILTIN_BLUEPRINTS": str(self.builtin),
               "SPAWNWP_CUSTOM_BLUEPRINTS": str(self.custom),
               "SPAWNWP_BLUEPRINT_PAYLOADS": str(self.payloads)}
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              input=stdin, capture_output=True, text=True, env=env)

    def test_validate_stdin_skip_payload(self):
        manifest = v2_manifest()
        proc = self.run_cli("validate-stdin", "--filename", "agency-base.json", "--skip-payload",
                            stdin=json.dumps(manifest))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["id"], "agency-base")

    def test_validate_stdin_checks_payload_without_flag(self):
        proc = self.run_cli("validate-stdin", "--filename", "agency-base.json",
                            stdin=json.dumps(v2_manifest()))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("payload archive is missing", proc.stderr)

    def test_validate_stdin_rejects_bad_filename(self):
        proc = self.run_cli("validate-stdin", "--filename", "../x.json", "--skip-payload",
                            stdin=json.dumps(v2_manifest()))
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
