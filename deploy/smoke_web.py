"""HTTP acceptance checks for the disposable CI Compose stack, never live accounts."""
import argparse
import http.client
from http.cookies import SimpleCookie
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode

HOST = "learn.example.test"
STATE = Path(".tmp/online-smoke.json")


class Browser:
    def __init__(self, cookie=""):
        self.cookie = cookie

    def request(self, path, method="GET", payload=None, form=None):
        headers = {"Host": HOST, "X-Forwarded-Proto": "https", "Cookie": self.cookie}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload)
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(form)
        if method == "POST":
            headers["Origin"] = "https://" + HOST
        # Only the CI overlay publishes this HTTP port, on loopback.
        connection = http.client.HTTPConnection("127.0.0.1", 18080, timeout=90)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            cookie = response.getheader("Set-Cookie")
            if cookie:
                parsed = SimpleCookie(cookie)
                self.cookie = "; ".join(f"{key}={value.value}" for key, value in parsed.items())
            return response.status, data, cookie
        finally:
            connection.close()

    def document(self, path, **kwargs):
        status, body, _ = self.request(path, **kwargs)
        assert status == 200, (path, status, body[:500])
        return json.loads(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["create", "verify"])
    args = parser.parse_args()
    browser = Browser()
    for attempt in range(45):
        try:
            if browser.document("/readyz")["ok"]:
                break
        except (AssertionError, OSError, http.client.HTTPException):
            pass
        time.sleep(1)
    else:
        raise AssertionError("Web/broker did not become ready")
    if args.phase == "create":
        assert browser.request("/api/run", "POST", payload={"code": "int main(){}"})[0] == 401
        state = {"username": "deployment-smoke", "password": secrets.token_urlsafe(24)}
        status, _, cookie = browser.request("/login", "POST", form=dict(action="register", **state))
        assert status == 302 and cookie, status
        assert all(part in cookie for part in ("Secure", "HttpOnly", "SameSite=Lax", "__Host-cmaster")), cookie
        state["cookie"] = browser.cookie
        for page in ("/", "/training", "/memory", "/notebook", "/playground", "/problem/c001", "/chapter/1/1"):
            assert browser.request(page)[0] == 200, page
        code = '#include <stdio.h>\nint main(void){puts("Hello, C");return 0;}'
        result = browser.document("/api/submit", method="POST", payload={"qid": "c001", "code": code})
        assert result["result"]["status"] == "AC" and result["progress_recorded"], result
        assert browser.document("/api/progress/export")["progress"]["read"]
        assert browser.request("/admin")[0] == 403
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(state), encoding="utf-8")
        STATE.chmod(0o600)
        print("HTTP smoke passed: registration, secure session, course pages, real AC, saved progress.")
    else:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        browser.cookie = state["cookie"]
        document = browser.document("/api/progress/export")
        assert document["username"] == state["username"]
        assert document["progress"]["solved"]["c001"]["status"] == "AC"
        assert document["progress"]["read"]
        fresh = Browser()
        status, _, _ = fresh.request("/login", "POST", form={"action": "login", "username": state["username"], "password": state["password"]})
        assert status == 302
        assert fresh.document("/api/progress/export")["progress"]["solved"]["c001"]["status"] == "AC"
        STATE.unlink()
        print("Restart smoke passed: account, session key, learning history and real AC survive recreation.")


if __name__ == "__main__":
    main()
