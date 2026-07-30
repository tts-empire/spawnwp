import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


path = Path(__file__).parents[1] / "migrations/add-provision-nginx-location.py"
spec = importlib.util.spec_from_file_location("add_provision_nginx_location", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class AddProvisionNginxLocationTests(unittest.TestCase):
    def test_adds_zone_and_location_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nginx = root / "spawnwp"
            snippet = root / "spawnwp-proxy.conf"
            snippet.write_text("proxy_set_header Host $host;\n")
            nginx.write_text(
                "limit_req_zone $binary_remote_addr zone=spawnwp_auth:10m rate=30r/m;\n"
                "limit_req_zone $binary_remote_addr zone=spawnwp_ingest:10m rate=120r/m;\n"
                "server {\n"
                "    location /api/ingest/ {\n"
                "        proxy_pass http://127.0.0.1:9393;\n"
                "    }\n"
                "    location / {\n"
                "        proxy_pass http://127.0.0.1:9393/;\n"
                "    }\n"
                "}\n"
            )
            previous = migration.NGINX_CONF, migration.PROXY_SNIPPET
            migration.NGINX_CONF, migration.PROXY_SNIPPET = nginx, snippet
            try:
                with mock.patch.object(migration, "run") as run:
                    migration.add_provision_location()
                    first = nginx.read_text()
                    migration.add_provision_location()
            finally:
                migration.NGINX_CONF, migration.PROXY_SNIPPET = previous
            self.assertIn("zone=spawnwp_provision", first)
            self.assertIn("location /api/provision", first)
            self.assertIn("proxy_read_timeout 600s", first)
            self.assertEqual(first, nginx.read_text())
            self.assertEqual(
                [mock.call(["nginx", "-t"]), mock.call(["systemctl", "reload", "nginx"])],
                run.call_args_list,
            )


if __name__ == "__main__":
    unittest.main()
