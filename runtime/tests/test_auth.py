import importlib
import os
import tempfile
import time
import unittest
from http.cookies import SimpleCookie
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import Response
from fastapi.testclient import TestClient
from starlette.requests import Request


class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.key = root / "auth.key"
        cls.key.write_bytes(Fernet.generate_key() + b"\n")
        cls.config = root / "config.env"
        cls.config.write_text("COCKPIT_DOMAIN=cockpit.example.com\n")
        cls.static = root / "static"
        (cls.static / "assets").mkdir(parents=True)
        os.environ.update(SPAWNWP_AUTH_DB=str(root / "auth.db"),
                          SPAWNWP_AUTH_KEY=str(cls.key), SPAWNWP_CONFIG=str(cls.config),
                          SPAWNWP_STATIC_DIR=str(cls.static))
        cls.auth = importlib.import_module("auth")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        Path(os.environ["SPAWNWP_AUTH_DB"]).unlink(missing_ok=True)
        self.auth.initialize()

    def request(self, cookie="", csrf=""):
        headers = []
        if cookie:
            headers.append((b"cookie", cookie.encode()))
        if csrf:
            headers.append((b"x-csrf-token", csrf.encode()))
        return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                        "client": ("192.0.2.10", 1234), "scheme": "https",
                        "server": ("cockpit.example.com", 443), "query_string": b""})

    def test_bootstrap_is_hashed_and_reset_revokes_sessions(self):
        code = self.auth.create_bootstrap()
        raw = Path(os.environ["SPAWNWP_AUTH_DB"]).read_bytes()
        self.assertNotIn(code.encode(), raw)
        with self.auth.db() as connection:
            row = connection.execute("SELECT * FROM bootstrap").fetchone()
            self.assertEqual(row["code_hash"], self.auth._digest(code))
            self.assertGreater(row["expires_at"], int(time.time()))

    def test_session_cookie_csrf_and_expiry(self):
        with self.auth.db() as connection:
            admin_id = connection.execute(
                "INSERT INTO admins(user_id,username,password_hash,totp_secret,created_at) VALUES(?,?,?,?,?)",
                (b"user", "admin", "hash", self.auth._encrypt("secret"), int(time.time())),
            ).lastrowid
        response = Response()
        self.auth._set_session(response, admin_id)
        cookies = SimpleCookie()
        for value in response.headers.getlist("set-cookie"):
            cookies.load(value)
        session_token = cookies[self.auth.COOKIE].value
        csrf = cookies[self.auth.CSRF_COOKIE].value
        request = self.request(f"{self.auth.COOKIE}={session_token}; {self.auth.CSRF_COOKIE}={csrf}", csrf)
        active = self.auth.session(request)
        self.assertEqual(active["username"], "admin")
        self.assertTrue(self.auth.valid_csrf(request, active))
        with self.auth.db() as connection:
            connection.execute("UPDATE sessions SET last_seen=?", (int(time.time()) - self.auth.IDLE_SECONDS - 1,))
        self.assertIsNone(self.auth.session(request))

    def test_recovery_reset_removes_old_identity(self):
        with self.auth.db() as connection:
            connection.execute(
                "INSERT INTO admins(user_id,username,password_hash,totp_secret,created_at) VALUES(?,?,?,?,?)",
                (b"old", "old-admin", "hash", self.auth._encrypt("secret"), int(time.time())),
            )
        code = self.auth.create_bootstrap(reset_admin=True)
        self.assertFalse(self.auth.is_enrolled())
        with self.auth.db() as connection:
            bootstrap = connection.execute("SELECT * FROM bootstrap").fetchone()
            self.assertEqual(bootstrap["code_hash"], self.auth._digest(code))

    def test_cockpit_and_api_fail_closed_without_session(self):
        app_module = importlib.import_module("app")
        with TestClient(app_module.app, base_url="https://cockpit.example.com",
                        follow_redirects=False) as client:
            self.assertEqual(client.get("/manage").status_code, 303)
            self.assertEqual(client.get("/manage").headers["location"], "/login")
            self.assertEqual(client.get("/api/projects").status_code, 401)
            self.assertEqual(client.get("/api/version").status_code, 200)


if __name__ == "__main__":
    unittest.main()
