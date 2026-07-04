import os
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parents[1]
sys.path.insert(0, str(RUNTIME))

class ParseWpCliCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Imported lazily: importing `app` pulls in `auth`, whose storage paths
        # are captured from the environment at first import — an import at
        # discovery time would freeze them before test_auth sets its temp dirs.
        # `app` also mounts SPAWNWP_STATIC_DIR/assets at import, and test_auth
        # may have left that variable pointing at a removed temp dir.
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

    def parse(self, command):
        return self.app.parse_wp_cli_command(command)

    def assert_rejected(self, command, fragment):
        with self.assertRaises(self.http_exception) as ctx:
            self.parse(command)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn(fragment, ctx.exception.detail)

    def test_plain_command(self):
        self.assertEqual(self.parse("plugin list"), ["plugin", "list"])

    def test_leading_wp_token_is_stripped(self):
        self.assertEqual(self.parse("wp plugin list"), ["plugin", "list"])

    def test_quoted_arguments_survive(self):
        self.assertEqual(
            self.parse('db query "SELECT ID FROM wp_posts LIMIT 1"'),
            ["db", "query", "SELECT ID FROM wp_posts LIMIT 1"],
        )

    def test_destructive_with_yes_is_allowed(self):
        self.assertEqual(self.parse("db reset --yes"), ["db", "reset", "--yes"])

    def test_empty_input_rejected(self):
        self.assert_rejected("", "WP-CLI command")
        self.assert_rejected("   ", "WP-CLI command")
        self.assert_rejected("wp", "WP-CLI command")

    def test_unbalanced_quotes_rejected(self):
        self.assert_rejected('option get "foo', "parse")

    def test_shell_rejected_even_with_flags(self):
        self.assert_rejected("shell", "interactive")
        self.assert_rejected("wp shell", "interactive")
        self.assert_rejected("wp --skip-plugins shell", "interactive")

    def test_db_cli_rejected(self):
        self.assert_rejected("db cli", "interactive")

    def test_db_query_without_sql_rejected(self):
        self.assert_rejected("db query", "SQL")
        self.assertEqual(self.parse('db query "SELECT 1"'), ["db", "query", "SELECT 1"])

    def test_prompt_flag_rejected(self):
        self.assert_rejected("user create --prompt", "interactive")
        self.assert_rejected("post create --prompt=post_title", "interactive")


if __name__ == "__main__":
    unittest.main()
