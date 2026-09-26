from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "docs/changelog.md"


class ChangelogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = CHANGELOG.read_text(encoding="utf-8")

    def test_latest_release_and_module_boundary_are_documented(self) -> None:
        headings = re.findall(r"^## (\d+\.\d+\.\d+)$", self.text, flags=re.MULTILINE)
        self.assertEqual(headings[0], "0.5.51")
        self.assertIn("existing core functionality", self.text)
        self.assertIn("without payment or a subscription", self.text)
        self.assertIn("Future separately signed", self.text)
        self.assertIn("## 0.5.33", self.text)
        self.assertIn("Modules are optional extensions", self.text)

    def test_intermediate_premium_releases_are_present_in_order(self) -> None:
        headings = re.findall(r"^## (0\.5\.(?:4[5-9]|50))$", self.text, flags=re.MULTILINE)
        self.assertEqual(headings, ["0.5.50", "0.5.49", "0.5.48", "0.5.47", "0.5.46", "0.5.45"])


if __name__ == "__main__":
    unittest.main()
