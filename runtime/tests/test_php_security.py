import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


path = Path(__file__).parents[1] / "php_security.py"
spec = importlib.util.spec_from_file_location("php_security", path)
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


class PhpSecurityTests(unittest.TestCase):
    def test_fixed_release_floors(self):
        for version in ("8.2.35", "8.3.36", "8.4.27", "8.5.12"):
            with self.subTest(version=version):
                self.assertEqual("patched", security.status(version)["status"])

    def test_pre_fix_versions_are_vulnerable(self):
        for version in ("8.2.34", "8.3.35", "8.4.26", "8.5.11", "7.4.33"):
            with self.subTest(version=version):
                self.assertEqual("vulnerable", security.status(version)["status"])

    def test_unknown_and_malformed_versions_are_unverified(self):
        self.assertEqual("unverified", security.status(None)["status"])
        self.assertEqual("unverified", security.status("8.4.27-custom")["status"])

    def test_image_is_patched_only_when_policy_and_runtime_are_verified(self):
        inspect = SimpleNamespace(
            returncode=0,
            stdout=f"sha256:abc||8.4|{security.POLICY}\n",
        )
        runtime = SimpleNamespace(returncode=0, stdout="8.4.27\n")
        with mock.patch.object(security.subprocess, "run", side_effect=[inspect, runtime]):
            report = security.image_report("wp-dev-php:8.4")
        self.assertEqual("sha256:abc", report["image_id"])
        self.assertEqual("patched", report["status"])
        self.assertEqual("8.4.27", report["php_full_version"])

    def test_php_74_requires_exact_image_acceptance(self):
        inspect = SimpleNamespace(returncode=0, stdout="sha256:legacy|||\n")
        with mock.patch.object(security.subprocess, "run", return_value=inspect):
            self.assertFalse(security.accepts_image("wp-dev-php:7.4", True, "sha256:other"))
            self.assertTrue(security.accepts_image("wp-dev-php:7.4", True, "sha256:legacy"))

    def test_php_74_cannot_be_accepted_before_an_image_exists(self):
        inspect = SimpleNamespace(returncode=1, stdout="")
        with mock.patch.object(security.subprocess, "run", return_value=inspect):
            self.assertFalse(security.accepts_image("wp-dev-php:7.4", True, ""))


if __name__ == "__main__":
    unittest.main()
