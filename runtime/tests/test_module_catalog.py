import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))

import module_catalog


def entry(**overrides):
    value = {
        "id": "demo-launcher", "name": "Demo Launcher", "version": "0.2.10",
        "publisher": "SpawnWP", "description": "Demo sites", "license": "free",
        "min_core_version": "0.5.33", "max_core_version": "0.9.99",
        "archive_url": "https://github.com/tts-empire/spawnwp/releases/download/v0.5.33/demo-launcher-0.2.10.tar.gz",
    }
    value.update(overrides)
    return value


class ModuleCatalogTests(unittest.TestCase):
    def test_validate_filters_incompatible_entries(self):
        payload = {"schema": 1, "catalog_version": 1, "publisher": "SpawnWP",
                   "modules": [entry(), entry(id="future", min_core_version="9.0.0", max_core_version="9.9.9")]}
        result = module_catalog.validate(payload, "0.5.34")
        self.assertEqual([item["id"] for item in result["modules"]], ["demo-launcher"])

    def test_validate_rejects_paid_or_non_https_entry(self):
        payload = {"schema": 1, "catalog_version": 1, "publisher": "SpawnWP",
                   "modules": [entry(license="paid")]}
        with self.assertRaises(module_catalog.CatalogError):
            module_catalog.validate(payload, "0.5.34")
        payload["modules"] = [entry(archive_url="http://example.test/module.tar.gz")]
        with self.assertRaises(module_catalog.CatalogError):
            module_catalog.validate(payload, "0.5.34")

    def test_canonical_json_is_deterministic(self):
        payload = {"b": 1, "a": ["x"]}
        self.assertEqual(module_catalog.canonical_json(payload), b'{"a":["x"],"b":1}\n')


if __name__ == "__main__":
    unittest.main()
