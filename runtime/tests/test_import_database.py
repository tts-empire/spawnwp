import json
import subprocess
import tempfile
import unittest
from pathlib import Path


IMPORTER = Path(__file__).parents[1] / "scripts" / "import-database.php"


def database_export(path: Path, *prefixes: str) -> None:
    records = []
    for prefix in prefixes:
        for suffix in ("options", "posts"):
            name = prefix + suffix
            records.append({
                "type": "table",
                "name": name,
                "create": f"CREATE TABLE `{name}` (`id` bigint)",
            })
    path.write_text("".join(
        json.dumps(record, separators=(",", ":")) + "\n" for record in records
    ))


class ImportDatabaseTests(unittest.TestCase):
    def run_import(self, prefixes, declared=""):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "database.jsonl"
            harness = root / "harness.php"
            database_export(export, *prefixes)
            harness.write_text(
                "<?php\n"
                "define( 'WP_CLI', true );\n"
                "class WP_CLI {\n"
                "  public static function error( $message ) { throw new RuntimeException( $message ); }\n"
                "  public static function success( $message ) { echo $message, \"\\n\"; }\n"
                "}\n"
                "function get_user_by( $field, $login ) { return (object) array( 'ID' => 1 ); }\n"
                "function esc_sql( $value ) { return $value; }\n"
                "class TestWpdb {\n"
                "  public $prefix = 'target_';\n"
                "  public $options = 'target_options';\n"
                "  public function query( $sql ) { return 1; }\n"
                "  public function prepare( $sql, ...$args ) { return $sql; }\n"
                "  public function get_var( $sql ) { return 'value'; }\n"
                "  public function insert( $table, $data ) { return true; }\n"
                "}\n"
                "$wpdb = new TestWpdb();\n"
                "$args = array( $argv[1], 'admin', $argv[2] );\n"
                "require $argv[3];\n"
            )
            return subprocess.run(
                ["php", str(harness), str(export), declared, str(IMPORTER)],
                capture_output=True, text=True,
            )

    def test_declared_prefix_disambiguates_export(self):
        result = self.run_import(("pdtnqy_wp_", "wp_"), "pdtnqy_wp_")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Imported 4 database tables", result.stdout)

    def test_legacy_unique_prefix_still_imports(self):
        result = self.run_import(("wp_",))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_ambiguous_prefix_names_candidates(self):
        result = self.run_import(("pdtnqy_wp_", "wp_"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("candidates: pdtnqy_wp_, wp_", result.stderr)


if __name__ == "__main__":
    unittest.main()
