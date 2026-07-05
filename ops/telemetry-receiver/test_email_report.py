import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

path = Path(__file__).with_name("email_report.py")
spec = importlib.util.spec_from_file_location("telemetry_email_report", path)
email_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(email_report)


class EmailReportTests(unittest.TestCase):
    def test_load_config_and_send_with_starttls(self):
        config = {
            "host": "smtp.example.test", "port": 587, "security": "starttls",
            "username": "sender@example.test", "password": "secret",
            "from": "sender@example.test", "to": "owner@example.test",
        }
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "mail.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            loaded = email_report.load_config(config_path)

        now = datetime(2026, 6, 29, 8, tzinfo=ZoneInfo("Europe/Rome"))
        report = {
            "installations": 2,
            "versions": [("0.5.6", 2)],
            "operating_systems": [("Linux", 2)],
            "architectures": [("x86_64", 2)],
            "features": [("telemetry", 2)],
            "environments_current": 1,
            "metrics_installations": 1,
            "performance": {},
            "feature_usage": [{"key": "blueprint_captures", "label": "blueprints captured", "value": 3}],
            "hardware": {},
        }
        message = email_report.build_message(loaded, report, now)
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        with patch.object(email_report.smtplib, "SMTP", return_value=smtp):
            email_report.send_message(loaded, message)

        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("sender@example.test", "secret")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "owner@example.test")
        self.assertIn("2026-06-29", message["Subject"])
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Active installations (seen within 90 days): 2", plain)
        self.assertIn("blueprints captured: 3", plain)
        self.assertIn("SpawnWP versions", html)
        self.assertIn("blueprints captured", html)

    def test_rejects_insecure_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "mail.json"
            config_path.write_text(json.dumps({
                "host": "localhost", "port": 25, "security": "plain",
                "username": "u", "password": "p", "from": "a@b", "to": "c@d",
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "starttls"):
                email_report.load_config(config_path)


if __name__ == "__main__":
    unittest.main()
