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
    def test_deployed_schema_one_catalog_remains_the_default(self):
        self.assertEqual(module_catalog.DEFAULT_URL,
                         "https://spawnwp.com/modules/catalog.json")

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

    def test_validate_accepts_a_complete_premium_schema_two_entry(self):
        premium = entry(
            commercial_model="premium", code_license="Proprietary",
            product_id="spawnwp-mcp", published_at=1_789_000_000,
            purchase_url="https://polar.example/checkout/spawnwp-mcp",
        )
        premium.pop("license")
        result = module_catalog.validate(
            {"schema": 2, "catalog_version": 2, "publisher": "SpawnWP", "modules": [premium]},
            "0.5.34",
        )
        self.assertEqual(result["modules"][0]["commercial_model"], "premium")

    def test_validate_rejects_incomplete_premium_schema_two_entry(self):
        premium = entry(commercial_model="premium", code_license="Proprietary")
        premium.pop("license")
        with self.assertRaises(module_catalog.CatalogError):
            module_catalog.validate(
                {"schema": 2, "catalog_version": 2, "publisher": "SpawnWP", "modules": [premium]},
                "0.5.34",
            )

    def test_validate_rejects_premium_without_purchase_url(self):
        premium = entry(
            commercial_model="premium", code_license="Proprietary",
            product_id="spawnwp-mcp", published_at=1_789_000_000,
        )
        premium.pop("license")
        with self.assertRaises(module_catalog.CatalogError):
            module_catalog.validate(
                {"schema": 2, "catalog_version": 2, "publisher": "SpawnWP", "modules": [premium]},
                "0.5.34",
            )

    def test_validate_rejects_premium_purchase_url_with_credentials(self):
        premium = entry(
            commercial_model="premium", code_license="Proprietary",
            product_id="spawnwp-mcp", published_at=1_789_000_000,
            purchase_url="https://user:secret@polar.example/checkout",
        )
        premium.pop("license")
        with self.assertRaises(module_catalog.CatalogError):
            module_catalog.validate(
                {"schema": 2, "catalog_version": 2, "publisher": "SpawnWP", "modules": [premium]},
                "0.5.34",
            )

    def test_validate_rejects_non_string_premium_purchase_url(self):
        premium = entry(
            commercial_model="premium", code_license="Proprietary",
            product_id="spawnwp-mcp", published_at=1_789_000_000,
            purchase_url=123,
        )
        premium.pop("license")
        with self.assertRaises(module_catalog.CatalogError):
            module_catalog.validate(
                {"schema": 2, "catalog_version": 2, "publisher": "SpawnWP", "modules": [premium]},
                "0.5.34",
            )


if __name__ == "__main__":
    unittest.main()
