import importlib.machinery
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class KnockSessionTests(unittest.TestCase):
    def test_rebuild_keeps_active_and_removes_expired_sessions(self):
        loader = importlib.machinery.SourceFileLoader("knock_session", str(Path(__file__).parents[1] / "knock-session"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module.SESSIONS = root / "sessions"
            module.SESSIONS.mkdir()
            module.ALLOW = root / "allow.conf"
            active = module.SESSIONS / "192.0.2.10"
            expired = module.SESSIONS / "198.51.100.20"
            active.touch(); expired.touch()
            old = time.time() - 1900
            __import__("os").utime(expired, (old, old))
            with patch.object(module.subprocess, "run"):
                module.rebuild()
            self.assertIn("allow 192.0.2.10;", module.ALLOW.read_text())
            self.assertIn("deny all;", module.ALLOW.read_text())
            self.assertFalse(expired.exists())


if __name__ == "__main__":
    unittest.main()
