import os
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))


class FileManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Same lazy-import dance as test_wp_cli: importing `app` pulls in `auth`
        # and mounts SPAWNWP_STATIC_DIR/assets at import time.
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        (Path(cls.temp.name) / "assets").mkdir()
        os.environ["SPAWNWP_STATIC_DIR"] = cls.temp.name
        try:
            from fastapi import HTTPException
            import app as cockpit_app
        except Exception as exc:  # cockpit deps not installed in this environment
            raise unittest.SkipTest(f"cockpit app dependencies not available: {exc}")
        cls.http_exception = HTTPException
        cls.app = cockpit_app

    # ── jail_path: accepted paths resolve under the docroot ──────────────────
    def test_jail_accepts_nested_path(self):
        self.assertEqual(
            self.app.jail_path("wp-content/themes/x.php"),
            "/var/www/html/wp-content/themes/x.php",
        )

    def test_jail_empty_is_docroot(self):
        self.assertEqual(self.app.jail_path(""), "/var/www/html")
        self.assertEqual(self.app.jail_path("."), "/var/www/html")

    def test_jail_normalises_inner_dotdot(self):
        # A .. that stays inside the jail is fine and collapses.
        self.assertEqual(
            self.app.jail_path("wp-content/../wp-config.php"),
            "/var/www/html/wp-config.php",
        )

    # ── jail_path: rejected paths raise 400 ──────────────────────────────────
    def assert_rejected(self, rel):
        with self.assertRaises(self.http_exception) as ctx:
            self.app.jail_path(rel)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_jail_rejects_absolute(self):
        self.assert_rejected("/etc/passwd")

    def test_jail_rejects_escape(self):
        self.assert_rejected("../../etc/passwd")
        self.assert_rejected("..")
        self.assert_rejected("wp-content/../../secret")

    def test_jail_rejects_nul_and_newline(self):
        self.assert_rejected("wp-content/x\x00.php")
        self.assert_rejected("wp-content/x\n.php")
        self.assert_rejected("wp-content/x\r.php")

    # ── requires_recent_auth: file writes need step-up, reads do not ─────────
    def test_step_up_matches_write_verbs(self):
        for verb in ("write", "upload", "delete", "rename", "mkdir"):
            self.assertTrue(self.app.requires_recent_auth(f"/api/files/site-1/{verb}"))

    def test_step_up_excludes_reads(self):
        self.assertFalse(self.app.requires_recent_auth("/api/files/site-1"))
        self.assertFalse(self.app.requires_recent_auth("/api/files/site-1/read"))
        self.assertFalse(self.app.requires_recent_auth("/api/files/site-1/download"))

    def test_step_up_keeps_static_destructive_set(self):
        self.assertTrue(self.app.requires_recent_auth("/api/destroy"))
        self.assertTrue(self.app.requires_recent_auth("/api/php-switch"))
        self.assertFalse(self.app.requires_recent_auth("/api/projects"))


if __name__ == "__main__":
    unittest.main()
