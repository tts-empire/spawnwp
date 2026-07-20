import hashlib
import os
import re
import secrets
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
        for verb in ("write", "upload", "delete", "rename", "mkdir", "unzip"):
            self.assertTrue(self.app.requires_recent_auth(f"/api/files/site-1/{verb}"))

    def test_step_up_excludes_reads(self):
        self.assertFalse(self.app.requires_recent_auth("/api/files/site-1"))
        self.assertFalse(self.app.requires_recent_auth("/api/files/site-1/read"))
        self.assertFalse(self.app.requires_recent_auth("/api/files/site-1/download"))

    def test_step_up_keeps_static_destructive_set(self):
        self.assertTrue(self.app.requires_recent_auth("/api/destroy"))
        self.assertTrue(self.app.requires_recent_auth("/api/php-switch"))
        self.assertFalse(self.app.requires_recent_auth("/api/projects"))

    def test_step_up_covers_snapshot_delete_not_label(self):
        # Deleting a snapshot destroys the only restore point, so it needs the
        # same step-up as /api/restore. Naming one is cosmetic and does not.
        self.assertTrue(self.app.requires_recent_auth("/api/snapshots/delete"))
        self.assertFalse(self.app.requires_recent_auth("/api/snapshots/label"))


class SnapshotLabelTests(unittest.TestCase):
    """Labels live in a sidecar so snapshot files keep their timestamp name,
    which is both the id and the path-traversal defence."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        (Path(cls.temp.name) / "assets").mkdir()
        os.environ["SPAWNWP_STATIC_DIR"] = cls.temp.name
        try:
            import app as cockpit_app
        except Exception as exc:  # cockpit deps not installed in this environment
            raise unittest.SkipTest(f"cockpit app dependencies not available: {exc}")
        cls.app = cockpit_app

    def setUp(self):
        self.proj = Path(tempfile.mkdtemp(dir=self.temp.name))
        (self.proj / "backups").mkdir()

    def test_clean_label_strips_control_chars_and_caps_length(self):
        self.assertEqual(self.app.clean_snapshot_label("  before the new theme \n"),
                         "before the new theme")
        self.assertEqual(self.app.clean_snapshot_label("a\x00b\tc"), "abc")
        self.assertEqual(len(self.app.clean_snapshot_label("x" * 200)),
                         self.app.SNAP_LABEL_MAX)

    def test_clean_label_handles_empty(self):
        self.assertEqual(self.app.clean_snapshot_label(""), "")
        self.assertEqual(self.app.clean_snapshot_label(None), "")

    def test_missing_sidecar_reads_as_no_labels(self):
        self.assertEqual(self.app.read_snapshot_labels(self.proj), {})

    def test_corrupt_sidecar_reads_as_no_labels(self):
        (self.proj / "backups" / "labels.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.app.read_snapshot_labels(self.proj), {})

    def test_sidecar_of_wrong_shape_reads_as_no_labels(self):
        (self.proj / "backups" / "labels.json").write_text('["a", "b"]', encoding="utf-8")
        self.assertEqual(self.app.read_snapshot_labels(self.proj), {})

    def test_sidecar_drops_keys_that_are_not_timestamps(self):
        (self.proj / "backups" / "labels.json").write_text(
            '{"20260714-093712": "good", "../evil": "bad", "20260714-093712x": "bad"}',
            encoding="utf-8")
        self.assertEqual(self.app.read_snapshot_labels(self.proj),
                         {"20260714-093712": "good"})

    def test_sidecar_drops_non_string_values(self):
        (self.proj / "backups" / "labels.json").write_text(
            '{"20260714-093712": 42}', encoding="utf-8")
        self.assertEqual(self.app.read_snapshot_labels(self.proj), {})

    def test_write_then_read_roundtrip_preserves_unicode(self):
        self.app.write_snapshot_labels(self.proj, {"20260714-093712": "prima del tema è ok"})
        self.assertEqual(self.app.read_snapshot_labels(self.proj),
                         {"20260714-093712": "prima del tema è ok"})

    def test_write_leaves_no_temp_file_behind(self):
        self.app.write_snapshot_labels(self.proj, {"20260714-093712": "x"})
        leftovers = list((self.proj / "backups").glob("*.tmp"))
        self.assertEqual(leftovers, [])


class UnzipSafetyTests(unittest.TestCase):
    """jail_path() validates the path of the archive; these guard its contents.

    The listings below are literal `unzip -l` output, captured from real archives
    built with python's zipfile, so the parser is pinned to the actual format
    (note the entry whose name contains spaces).
    """

    SAFE = """Archive:  ok.zip
  Length      Date    Time    Name
---------  ---------- -----   ----
        0  2026-07-20 18:44   a/
        0  2026-07-20 18:44   a/b/
        6  2026-07-20 18:44   a/b/c.txt
        2  2026-07-20 18:44   file with spaces.txt
---------                     -------
        8                     4 files
"""

    SLIP = """Archive:  evil.zip
  Length      Date    Time    Name
---------  ---------- -----   ----
        5  2026-07-20 18:44   ../../etc/evil.txt
        5  2026-07-20 18:44   /abs/evil.txt
        4  2026-07-20 18:44   ok/inner.txt
---------                     -------
       14                     3 files
"""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        (Path(cls.temp.name) / "assets").mkdir()
        os.environ["SPAWNWP_STATIC_DIR"] = cls.temp.name
        try:
            import app as cockpit_app
        except Exception as exc:
            raise unittest.SkipTest(f"cockpit app dependencies not available: {exc}")
        cls.app = cockpit_app

    def test_safe_archive_has_no_unsafe_entries(self):
        self.assertEqual(self.app.unsafe_zip_entries(self.SAFE), [])

    def test_zip_slip_entries_are_flagged(self):
        self.assertEqual(
            self.app.unsafe_zip_entries(self.SLIP),
            ["../../etc/evil.txt", "/abs/evil.txt"],
        )

    def test_size_and_count_ignore_header_and_summary(self):
        # 4 entries totalling 8 bytes — the "8  4 files" summary line must not
        # be counted as a fifth entry.
        self.assertEqual(self.app.zip_uncompressed_size(self.SAFE), (8, 4))

    def test_name_with_spaces_is_read_whole(self):
        listing = self.SAFE.replace("file with spaces.txt", "../sneaky name.txt")
        self.assertEqual(self.app.unsafe_zip_entries(listing), ["../sneaky name.txt"])

    def test_backslash_absolute_is_flagged(self):
        listing = self.SAFE.replace("a/b/c.txt", "\\\\windows\\\\evil.txt")
        self.assertTrue(self.app.unsafe_zip_entries(listing))

    def test_inner_dotdot_is_flagged(self):
        listing = self.SAFE.replace("a/b/c.txt", "a/../../escape.txt")
        self.assertEqual(self.app.unsafe_zip_entries(listing), ["a/../../escape.txt"])

    def test_empty_listing_is_harmless(self):
        self.assertEqual(self.app.unsafe_zip_entries(""), [])
        self.assertEqual(self.app.zip_uncompressed_size(""), (0, 0))


class MagicLoginTests(unittest.TestCase):
    """The cockpit mints the token; spawnwp-autologin.php validates and consumes
    it. These tests pin the contract between the two halves."""

    # Must match the regex in runtime/mu-plugins/spawnwp-autologin.php.
    PHP_TOKEN_RE = re.compile(r"\A[A-Za-z0-9_-]{16,128}\Z")

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        (Path(cls.temp.name) / "assets").mkdir()
        os.environ["SPAWNWP_STATIC_DIR"] = cls.temp.name
        try:
            import app as cockpit_app
        except Exception as exc:
            raise unittest.SkipTest(f"cockpit app dependencies not available: {exc}")
        cls.app = cockpit_app
        cls.plugin = (Path(__file__).parents[1] / "mu-plugins" / "spawnwp-autologin.php")

    def test_minted_tokens_match_the_php_validator(self):
        for _ in range(50):
            token = secrets.token_urlsafe(32)
            self.assertRegex(token, self.PHP_TOKEN_RE)

    def test_key_is_prefix_plus_sha256_of_token(self):
        key = self.app.autologin_key("abc")
        self.assertEqual(
            key,
            "spawnwp_autologin_" + hashlib.sha256(b"abc").hexdigest(),
        )

    def test_key_never_contains_the_token(self):
        token = secrets.token_urlsafe(32)
        self.assertNotIn(token, self.app.autologin_key(token))

    def test_key_fits_wordpress_transient_name_limit(self):
        # WordPress option names are capped at 191 chars; "_transient_timeout_"
        # (19) is the longest prefix WP prepends.
        self.assertLessEqual(len(self.app.autologin_key(secrets.token_urlsafe(32))) + 19, 191)

    def test_ttl_is_short(self):
        self.assertLessEqual(self.app.AUTOLOGIN_TTL_SECONDS, 300)

    def test_plugin_source_is_shipped_outside_the_public_assets_dir(self):
        # install.sh copies runtime/assets/* into the cockpit's public /assets
        # mount; the mu-plugin must not ride along.
        self.assertTrue(self.plugin.is_file(), f"missing {self.plugin}")
        self.assertNotIn("assets", self.plugin.parts[-2:-1])

    def test_plugin_deletes_the_transient_before_authenticating(self):
        # Single-use only holds if the token is burned first: two concurrent
        # requests must not both reach wp_set_auth_cookie().
        src = self.plugin.read_text(encoding="utf-8")
        self.assertLess(
            src.index("delete_transient"),
            src.index("wp_set_auth_cookie"),
            "the token must be consumed before the session is created",
        )


if __name__ == "__main__":
    unittest.main()
