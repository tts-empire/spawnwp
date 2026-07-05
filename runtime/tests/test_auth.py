import importlib
import os
import tempfile
import time
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cryptography.fernet import Fernet
from fastapi import HTTPException, Response
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
        # Reload unconditionally: if any other test module imported `auth`
        # (even indirectly, e.g. via `app`) before this env was set, its
        # module-level paths point at the LIVE database. Reload rebinds them,
        # and the assertion makes any regression fail loudly instead of
        # letting the suite run against /var/lib/spawnwp/auth.db.
        cls.auth = importlib.reload(importlib.import_module("auth"))
        assert cls.auth.DB_PATH == root / "auth.db", cls.auth.DB_PATH

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

    def reauth_fixture(self):
        now = int(time.time())
        with self.auth.db() as connection:
            admin_id = connection.execute(
                "INSERT INTO admins(user_id,username,password_hash,totp_secret,created_at) VALUES(?,?,?,?,?)",
                (b"reauth-user", "reauth-admin", "hash", self.auth._encrypt("secret"), now),
            ).lastrowid
            connection.execute(
                "INSERT INTO passkeys(admin_id,credential_id,public_key,sign_count,transports,name,created_at) VALUES(?,?,?,?,?,?,?)",
                (admin_id, b"credential", b"public-key", 1, "[]", "Primary", now),
            )
        response = Response()
        self.auth._set_session(response, admin_id)
        cookies = SimpleCookie()
        for value in response.headers.getlist("set-cookie"):
            cookies.load(value)
        token = cookies[self.auth.COOKIE].value
        csrf = cookies[self.auth.CSRF_COOKIE].value
        with self.auth.db() as connection:
            connection.execute(
                "UPDATE sessions SET recent_auth=? WHERE id_hash=?",
                (now - 900, self.auth._digest(token)),
            )
        request = self.request(
            f"{self.auth.COOKIE}={token}; {self.auth.CSRF_COOKIE}={csrf}", csrf
        )
        return admin_id, token, request

    def test_passkey_reauthentication_refreshes_only_current_session(self):
        admin_id, token, request = self.reauth_fixture()
        other = Response()
        self.auth._set_session(other, admin_id)
        ceremony = "reauth-ceremony"
        with self.auth.db() as connection:
            other_recent = connection.execute(
                "SELECT recent_auth FROM sessions WHERE id_hash<>?", (self.auth._digest(token),)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO challenges VALUES(?,?,?,?,?)",
                (self.auth._digest(ceremony), "reauth", b"challenge",
                 __import__("json").dumps({"admin_id": admin_id, "session_hash": self.auth._digest(token)}),
                 int(time.time()) + 300),
            )
        body = self.auth.PasskeyFinish(ceremony=ceremony, credential={"id": "credential"})
        with mock.patch.object(self.auth, "base64url_to_bytes", return_value=b"credential"), \
             mock.patch.object(self.auth, "verify_authentication_response",
                               return_value=SimpleNamespace(new_sign_count=2)):
            result = self.auth.reauth_finish(body, request)
        self.assertTrue(result["ok"])
        with self.auth.db() as connection:
            current = connection.execute(
                "SELECT recent_auth FROM sessions WHERE id_hash=?", (self.auth._digest(token),)
            ).fetchone()[0]
            unchanged = connection.execute(
                "SELECT recent_auth FROM sessions WHERE id_hash<>?", (self.auth._digest(token),)
            ).fetchone()[0]
        self.assertGreater(current, int(time.time()) - 5)
        self.assertEqual(other_recent, unchanged)
        with mock.patch.object(self.auth, "base64url_to_bytes", return_value=b"credential"):
            with self.assertRaises(HTTPException) as reused:
                self.auth.reauth_finish(body, request)
        self.assertEqual(400, reused.exception.status_code)

    def test_reauthentication_rejects_challenge_from_another_session(self):
        admin_id, _token, request = self.reauth_fixture()
        ceremony = "wrong-session"
        with self.auth.db() as connection:
            connection.execute(
                "INSERT INTO challenges VALUES(?,?,?,?,?)",
                (self.auth._digest(ceremony), "reauth", b"challenge",
                 __import__("json").dumps({"admin_id": admin_id, "session_hash": "wrong"}),
                 int(time.time()) + 300),
            )
        body = self.auth.PasskeyFinish(ceremony=ceremony, credential={"id": "credential"})
        with mock.patch.object(self.auth, "base64url_to_bytes", return_value=b"credential"):
            with self.assertRaises(HTTPException) as raised:
                self.auth.reauth_finish(body, request)
        self.assertEqual(400, raised.exception.status_code)

    def test_reauthentication_rejects_expired_challenge(self):
        admin_id, token, request = self.reauth_fixture()
        ceremony = "expired-reauth"
        with self.auth.db() as connection:
            connection.execute(
                "INSERT INTO challenges VALUES(?,?,?,?,?)",
                (self.auth._digest(ceremony), "reauth", b"challenge",
                 __import__("json").dumps({"admin_id": admin_id, "session_hash": self.auth._digest(token)}),
                 int(time.time()) - 1),
            )
        body = self.auth.PasskeyFinish(ceremony=ceremony, credential={"id": "credential"})
        with mock.patch.object(self.auth, "base64url_to_bytes", return_value=b"credential"):
            with self.assertRaises(HTTPException) as raised:
                self.auth.reauth_finish(body, request)
        self.assertEqual(400, raised.exception.status_code)

    def test_cockpit_and_api_fail_closed_without_session(self):
        try:
            from fastapi.testclient import TestClient
        except (ImportError, RuntimeError) as exc:
            self.skipTest(f"FastAPI test client unavailable: {exc}")
        app_module = importlib.import_module("app")
        with TestClient(app_module.app, base_url="https://cockpit.example.com",
                        follow_redirects=False) as client:
            self.assertEqual(client.get("/manage").status_code, 303)
            self.assertEqual(client.get("/manage").headers["location"], "/login")
            self.assertEqual(client.get("/api/projects").status_code, 401)
            self.assertEqual(client.get("/api/version").status_code, 200)

    def test_security_headers_present_on_every_response(self):
        try:
            from fastapi.testclient import TestClient
        except (ImportError, RuntimeError) as exc:
            self.skipTest(f"FastAPI test client unavailable: {exc}")
        app_module = importlib.import_module("app")
        with TestClient(app_module.app, base_url="https://cockpit.example.com",
                        follow_redirects=False) as client:
            # The login page (200) and the fail-closed redirect off /manage (303)
            # must both carry the hardened headers, including a CSP — which used
            # to be set only on /login.
            for response in (client.get("/login"), client.get("/manage")):
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                csp = response.headers["Content-Security-Policy"]
                self.assertIn("default-src 'self'", csp)
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn("object-src 'none'", csp)

    def test_frontend_escaper_covers_the_single_quote(self):
        # esc() feeds values into single-quoted inline handlers; guard against a
        # regression that drops the single-quote (or backtick) from its char set.
        cockpit_js = Path(__file__).resolve().parents[1] / "assets" / "cockpit.js"
        source = cockpit_js.read_text(encoding="utf-8")
        line = next(l for l in source.splitlines() if l.startswith("function esc("))
        self.assertIn("&#39;", line)
        self.assertIn("&#96;", line)

    def test_enrollment_page_explains_authenticator_and_recovery(self):
        page = self.auth.LOGIN_HTML
        self.assertIn("Step 1 of 3", page)
        self.assertIn("One-time activation code", page)
        self.assertNotIn("fallback password", page.lower())
        self.assertIn("any TOTP authenticator", page)
        self.assertIn("Google Authenticator", page)
        self.assertIn("Verify code and create passkey", page)
        self.assertIn("Copy all recovery codes", page)


if __name__ == "__main__":
    unittest.main()
