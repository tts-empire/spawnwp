import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


path = Path(__file__).with_name("report.py")
spec = importlib.util.spec_from_file_location("telemetry_report", path)
report_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_module)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "telemetry.sqlite3"
        with sqlite3.connect(self.db_path) as db:
            db.execute("""CREATE TABLE installations (
                installation_hash TEXT PRIMARY KEY,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                spawnwp_version TEXT NOT NULL,
                os_family TEXT NOT NULL,
                os_version TEXT NOT NULL,
                architecture TEXT NOT NULL,
                features_json TEXT NOT NULL,
                environments_current INTEGER NOT NULL,
                heartbeat_count INTEGER NOT NULL,
                metrics_json TEXT,
                hardware_json TEXT
            )""")

    def tearDown(self):
        self.temp.cleanup()

    def insert(self, identifier, first_seen, last_seen=None, heartbeat_count=1,
               version="0.5.32", os_version="6.8.0", environments=1):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                identifier, first_seen, last_seen if last_seen is not None else first_seen,
                version, "Linux", os_version, "x86_64",
                json.dumps({"telemetry": True}), environments, heartbeat_count, None, None,
            ))

    def test_dense_one_shot_cohort_is_excluded_but_preserved_as_observed(self):
        start = 1_787_499_000
        for index in range(20):
            self.insert(f"burst-{index}", start + index * 60,
                        version="0.3.8", os_version="6.18-test", environments=index % 3)
        self.insert("confirmed", start - 100_000, start, heartbeat_count=2)
        self.insert("provisional", start + 30_000)

        result = report_module.collect(self.db_path)

        self.assertEqual(22, result["observed_installations"])
        self.assertEqual(2, result["installations"])
        self.assertEqual(1, result["confirmed_installations"])
        self.assertEqual(1, result["provisional_installations"])
        self.assertEqual(20, result["anomalous_installations"])
        self.assertEqual(1, len(result["anomalous_cohorts"]))
        self.assertEqual([["0.5.32", 2]], json.loads(json.dumps(result["versions"])))
        self.assertEqual(2, result["environments_current"])

    def test_small_or_slow_one_shot_groups_remain_provisional(self):
        start = 1_787_499_000
        for index in range(19):
            self.insert(f"small-{index}", start + index * 60,
                        version="0.3.8", os_version="6.18-test")
        for index in range(20):
            self.insert(f"slow-{index}", start + index * 86_400,
                        version="0.4.0", os_version="6.8-shared")

        result = report_module.collect(self.db_path)

        self.assertEqual(39, result["observed_installations"])
        self.assertEqual(39, result["installations"])
        self.assertEqual(0, result["confirmed_installations"])
        self.assertEqual(39, result["provisional_installations"])
        self.assertEqual(0, result["anomalous_installations"])


if __name__ == "__main__":
    unittest.main()
