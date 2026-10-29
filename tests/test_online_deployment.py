"""Deployment contract tests; run without Docker or real account data."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_DATA = tempfile.TemporaryDirectory(prefix="cmaster-deploy-import-")
with patch.dict(os.environ, {"CMASTER_DATA": IMPORT_DATA.name}):
    import app as cm
from deploy import broker
from deploy.client import JudgeClient
from deploy.web import configure


class BrokerTests(unittest.TestCase):
    def test_container_has_no_network_mounts_or_host_identity(self):
        command = broker.command("cmaster-job-fixed")
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index("--user") + 1], "10001:10001")
        for option in ("--mount", "-v", "--volume", "--privileged", "--env", "-e", "--pid"):
            self.assertNotIn(option, command)
        self.assertEqual(command[-1], "cmaster-judge:online")

    def test_untrusted_input_cannot_inject_a_docker_command(self):
        for payload in ([], {"action": "exec"}, {"action": "info", "image": "malicious"},
                        {"action": "run", "code": []},
                        {"action": "run", "code": "x", "stdin": "x" * 100001},
                        {"action": "judge", "code": "x", "question": {"cases": [{}] * 17}}):
            self.assertFalse(broker.validate(payload))
        self.assertTrue(broker.validate({"action": "judge", "code": "x",
                                        "question": {"cases": [{"input": "", "output": "ok"}]}}))

    def test_job_failure_is_system_error_and_container_is_removed(self):
        completed = type("Result", (), {"returncode": 137, "stdout": b""})()
        with patch.object(broker.subprocess, "run", return_value=completed) as run:
            result = broker.execute({"action": "info"})
        self.assertEqual(result["status"], "SE")
        self.assertEqual(result["owner"], "system")
        self.assertEqual(run.call_args_list[-1].args[0][:3], ["docker", "rm", "-f"])

    def test_success_returns_real_judge_result(self):
        completed = type("Result", (), {"returncode": 0, "stdout": b'{"status":"AC","passed":2}'})()
        with patch.object(broker.subprocess, "run", return_value=completed):
            result = broker.execute({"action": "run", "code": "int main(){}"})
        self.assertEqual(result["status"], "AC")

    def test_lost_broker_never_falls_back_to_running_on_web_host(self):
        result = JudgeClient("missing-socket").run_freeform("int main(){}")
        self.assertEqual(result["status"], "SE")
        self.assertEqual(result["owner"], "system")


class OnlineWebTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.directory = tempfile.TemporaryDirectory(prefix="cmaster-online-test-")
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(setattr, cm, "judge", cm.judge)
        for attr, value in (("DATA_DIR", self.directory.name),
                            ("PROGRESS_DIR", str(Path(self.directory.name) / "progress")),
                            ("USERS_FILE", str(Path(self.directory.name) / "users.json"))):
            mocked = patch.object(cm, attr, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.app = Flask(__name__, template_folder=str(ROOT / "templates"),
                         static_folder=str(ROOT / "static"))
        self.app.secret_key = "isolated-test-secret"
        self.app.config["TESTING"] = True
        self.fake = type("FakeJudge", (), {"compiler_info": lambda _: {"ok": True, "version": "test"}})()
        self.app.add_url_rule("/login", "login_page", cm.login_page, methods=["GET", "POST"])
        self.app.add_url_rule("/", "index", lambda: "ok")
        self.app.add_url_rule("/api/run", "run", lambda: "run", methods=["POST"])
        self.app.add_url_rule("/healthz", "healthz", lambda: "ok")
        self.app.add_url_rule("/readyz", "readyz", lambda: "pending")
        for endpoint in ("problems_page", "playground_page", "training_page", "wrongbook_page",
                         "notebook_page", "analytics_page", "map_page", "memory_page",
                         "ops_page", "admin_page", "profile_page", "logout_page"):
            self.app.add_url_rule("/test-" + endpoint, endpoint, lambda: "ok")
        self.app.context_processor(cm.inject_globals)
        configure(self.app, self.fake, "https://learn.example.com")
        self.client = self.app.test_client()

    def request(self, path, **kwargs):
        return self.client.open(path, base_url="https://learn.example.com", **kwargs)

    def test_secure_login_cookie_and_persistent_account(self):
        response = self.request("/login", method="POST", data={"action": "register",
            "username": "online-test", "password": "temporary-test-password"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("Secure", response.headers["Set-Cookie"])
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("__Host-cmaster", response.headers["Set-Cookie"])
        self.assertTrue(Path(cm.USERS_FILE).is_file())
        with self.client.session_transaction(base_url="https://learn.example.com") as session:
            self.assertEqual(session["user"], "online-test")
        self.assertEqual(self.request("/api/run", method="POST").status_code, 200)

    def test_anonymous_and_cross_site_run_are_rejected(self):
        self.assertEqual(self.request("/api/run", method="POST").status_code, 401)
        response = self.request("/login", method="POST", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_host_and_login_redirect_validation(self):
        self.assertEqual(self.client.get("/", base_url="https://other.example").status_code, 400)
        for target in ("//evil.example", "/\\evil.example", "/\r\nLocation: evil"):
            response = self.request("/login", method="POST", data={"next": target})
            self.assertEqual(response.status_code, 400)

    def test_online_management_endpoints_are_not_public(self):
        for path in ("/admin", "/ops", "/api/admin/panel", "/api/admin/selftest", "/api/ops"):
            self.assertEqual(self.request(path).status_code, 403)

    def test_login_rate_limit(self):
        for _ in range(10):
            response = self.request("/login", method="POST", data={"next": "//invalid"})
            self.assertEqual(response.status_code, 400)
        # Invalid redirects do not consume a hash computation or the login quota.
        for _ in range(10):
            self.assertEqual(self.request("/login", method="POST", data={
                "username": "limit-user", "password": "temporary-pass", "next": "/"}).status_code, 200)
        response = self.request("/login", method="POST")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")

    def test_readiness_requires_persistence_and_isolated_compiler(self):
        response = self.request("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["checks"]["persistent_data"])


if __name__ == "__main__":
    unittest.main()
