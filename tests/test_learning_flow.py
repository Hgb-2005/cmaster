"""学习流程回归：临时数据目录，不触碰真实账号；判题故障采用可控返回值。"""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_DATA = tempfile.TemporaryDirectory(prefix="cmaster-import-")
os.environ["CMASTER_DATA"] = IMPORT_DATA.name
import app as cm


class LearningFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cmaster-test-")
        self.addCleanup(self.directory.cleanup)
        self.patches = [patch.object(cm, "DATA_DIR", self.directory.name),
                        patch.object(cm, "PROGRESS_DIR", os.path.join(self.directory.name, "progress")),
                        patch.object(cm, "USERS_FILE", os.path.join(self.directory.name, "users.json"))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        cm.app.config.update(TESTING=True)
        self.client = self.register("learner")

    def register(self, username):
        client = cm.app.test_client()
        response = client.post("/login", data={"action": "register", "username": username,
                               "password": "isolated-test-pass"})
        self.assertEqual(response.status_code, 302)
        return client

    def submit(self, status, code="int main(void) { return 0; }", qid="c001"):
        result = {"status": status, "message": "Controlled test result", "cases": [],
                  "total": 1, "passed": int(status == "AC"), "owner": cm.verdicts.owner(status)}
        with patch.object(cm.judge, "judge", return_value=result):
            response = self.client.post("/api/submit", json={"qid": qid, "code": code})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_training_is_packaged_and_available_on_fresh_start(self):
        self.assertTrue((ROOT / "data/training.json").is_file())
        self.assertEqual(len(cm.TRAINING_MODES), 3)
        self.assertEqual(sum(cm.TRAINING_COUNT.values()), 166)
        self.assertEqual(self.client.get("/training").status_code, 200)
        for mode in ("trace", "cloze", "flash"):
            response = self.client.get("/api/training/cards?mode=" + mode)
            self.assertTrue(response.get_json()["cards"])

    def test_missing_training_content_is_rebuilt(self):
        with patch.object(cm, "_load_content", return_value=None):
            result = cm._load_training()
        self.assertEqual(len(result["modes"]), 3)
        self.assertTrue(Path(self.directory.name, "training.json").is_file())

    def test_training_survives_read_only_storage(self):
        with patch.object(cm, "_load_content", return_value=None), \
             patch.object(cm, "_atomic_json", side_effect=PermissionError("read only")):
            result = cm._load_training()
        self.assertTrue(result["cards"]["trace"])

    def test_unavailable_training_has_a_useful_empty_state(self):
        with patch.object(cm, "TRAINING_MODES", []):
            page = self.client.get("/training")
            self.assertEqual(page.status_code, 200)
            self.assertIn("训练内容正在准备", page.get_data(as_text=True))
            self.assertNotIn('id="tr-boot"', page.get_data(as_text=True))
            self.assertEqual(self.client.get("/api/training/cards?mode=trace").status_code, 503)

    def test_special_characters_long_names_and_case_are_isolated(self):
        names = ["review.a", "review?a", "A" * 40 + "one", "A" * 40 + "two", "Case", "case"]
        paths = []
        for username in names:
            client = self.register(username)
            self.assertEqual(cm.load_progress(username)["read"], [])
            client.get("/chapter/1/1")
            paths.append(cm._progress_path(username))
        self.assertEqual(len(set(paths)), len(names))
        self.assertEqual(len(set(path.casefold() for path in paths)), len(names))
        other = self.register("review*a")
        self.assertEqual(cm.load_progress("review*a")["read"], [])
        self.assertEqual(other.get("/api/submissions/c001").get_json()["solution"], {})

    def test_unique_legacy_progress_is_migrated_without_deleting_source(self):
        self.register("legacy.user")
        old = Path(cm.PROGRESS_DIR, "legacy_user.json")
        cm._atomic_json(str(old), {"read": ["legacy-lesson"], "training": {"totals": {"xp": 20}}})
        before = old.read_bytes()
        result = cm.load_progress("legacy.user")
        self.assertEqual(result["read"], ["legacy-lesson"])
        self.assertEqual(result["training"]["totals"]["xp"], 20)
        self.assertTrue(Path(cm._progress_path("legacy.user")).is_file())
        self.assertEqual(old.read_bytes(), before)

    def test_ambiguous_legacy_file_is_preserved_and_not_shared(self):
        first = self.register("old.a")
        self.register("old?a")
        old = Path(cm.PROGRESS_DIR, "old_a.json")
        cm._atomic_json(str(old), {"read": ["private-old-record"]})
        for username in ("old.a", "old?a"):
            data = cm.load_progress(username)
            self.assertEqual(data["read"], [])
            self.assertIn("migration_notice", data)
        self.assertIn("原始文件已保留", first.get("/").get_data(as_text=True))
        self.assertEqual(json.loads(old.read_text(encoding="utf-8"))["read"], ["private-old-record"])

    def test_parallel_lesson_reads_do_not_overwrite_each_other(self):
        def visit(kid):
            client = cm.app.test_client()
            with client.session_transaction() as session:
                session["user"] = "learner"
            return client.get("/chapter/1/%d" % kid).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(visit, range(1, 5)))
        self.assertEqual(statuses, [200] * 4)
        self.assertEqual(len(cm.load_progress("learner")["read"]), 4)

    def test_system_error_preserves_code_without_penalizing_student(self):
        response = self.submit("SE", "/* preserved during outage */")
        data = cm.load_progress("learner")
        self.assertFalse(response["progress_recorded"])
        self.assertEqual(data["wrong"], {})
        self.assertEqual(data["attempts"], {})
        self.assertEqual(data["solved"], {})
        self.assertNotIn("days", data)
        self.assertEqual(data["solutions"]["c001"]["latest"]["code"], "/* preserved during outage */")

    def test_missing_compiler_is_a_system_error(self):
        with patch.object(cm.judge, "find_compiler", return_value=None):
            response = self.client.post("/api/submit", json={"qid": "c001", "code": "int main(void) {}"})
        self.assertEqual(response.get_json()["result"]["status"], "SE")
        self.assertEqual(cm.load_progress("learner")["attempts"], {})

    def test_old_system_wrongbook_entries_are_removed_but_code_is_preserved(self):
        cm.save_progress({"wrong": {"c001": {"status": "SE", "code": "/* old */"}},
                          "attempts": {"c001": 2}}, "learner")
        data = cm.load_progress("learner")
        self.assertEqual(data["wrong"], {})
        self.assertEqual(data["solutions"]["c001"]["latest"]["code"], "/* old */")
        self.assertEqual(data["attempts"]["c001"], 2)  # 旧数据不能猜测有多少次系统故障。

    def test_accepted_code_survives_later_errors_and_history_is_bounded(self):
        self.submit("WA", "/* wrong */")
        self.submit("AC", "/* accepted */")
        for i in range(12):
            self.submit("WA", "/* later %d */" % i)
        data = cm.load_progress("learner")
        self.assertEqual(data["solutions"]["c001"]["accepted"]["code"], "/* accepted */")
        self.assertEqual(len(data["solutions"]["c001"]["history"]), 10)
        self.assertNotIn("c001", data["wrong"])
        self.assertEqual(data["solved"]["c001"]["attempts"], 2)
        self.submit("AC", "/* accepted again */")
        self.assertEqual(cm.load_progress("learner")["solved"]["c001"]["attempts"], 2)

    def test_submission_history_is_private_and_restores_server_code(self):
        self.submit("AC", "/* own solution </script> */")
        self.assertEqual(cm.app.test_client().get("/api/submissions/c001").status_code, 401)
        other = self.register("another")
        self.assertEqual(other.get("/api/submissions/c001").get_json()["solution"], {})
        page = self.client.get("/problem/c001").get_data(as_text=True)
        boot = json.loads(re.search(r'id="problem-data">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(boot["lastCode"], "/* own solution </script> */")
        self.assertEqual(boot["storageId"], cm.user_storage_id("learner"))

    def test_home_resumes_last_lesson_and_recommends_related_question(self):
        self.client.get("/chapter/7/2")
        with cm.app.test_request_context("/"):
            plan = cm.study_plan(cm.load_progress("learner"))
        self.assertEqual(plan[0]["url"], "/chapter/7/2")
        qid = plan[1]["url"].rsplit("/", 1)[1]
        self.assertEqual(cm.QMAP[qid]["chapter"], 7)
        self.assertIn('id="continue-learning" href="/chapter/7/2"', self.client.get("/").get_data(as_text=True))

    def test_home_has_useful_new_and_completed_states(self):
        with cm.app.test_request_context("/"):
            new_plan = cm.study_plan({})
            completed = {"read": [cm._kp_key(cid, kp) for cid, ch in cm.CHAPTERS.items()
                                   for kp in ch["knowledge_points"]],
                         "solved": {qid: {"status": "AC"} for qid in cm.QMAP}}
            done_plan = cm.study_plan(completed)
        self.assertEqual(new_plan[0]["url"], "/chapter/1/1")
        self.assertEqual(done_plan[0]["url"], "/training")
        self.assertEqual(done_plan[1]["url"], "/playground")

    def test_wrong_recommendation_disappears_after_acceptance(self):
        self.submit("WA")
        with cm.app.test_request_context("/"):
            self.assertEqual(cm.study_plan(cm.load_progress("learner"))[2]["url"], "/problem/c001")
        self.submit("AC")
        with cm.app.test_request_context("/"):
            self.assertEqual(cm.study_plan(cm.load_progress("learner"))[2]["url"], "/training")

    def test_submit_rejects_malformed_payloads(self):
        for payload in ([], "text", {"qid": [], "code": "x"}, {"qid": "c001", "code": 12}):
            self.assertEqual(self.client.post("/api/submit", json=payload).status_code, 400)

    def test_stale_tab_cannot_submit_into_a_different_account(self):
        response = self.client.post("/api/submit", json={"qid": "c001", "code": "int main(void) {}",
                                                       "accountId": cm.user_storage_id("another")})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(cm.load_progress("learner")["attempts"], {})
        response = self.client.get("/api/submissions/c001", headers={"X-CMaster-Account": "another"})
        self.assertEqual(response.status_code, 409)

    def note(self, ref="lesson:1:1", **payload):
        return self.client.post("/api/notebook/" + ref, json=payload,
                                headers={"X-CMaster-Account": cm.user_storage_id("learner")})

    def test_notebook_requires_login_and_real_resource(self):
        guest = cm.app.test_client()
        self.assertEqual(guest.get("/notebook").status_code, 302)
        for path in ("/api/notebook/lesson:1:1", "/api/notebook/export", "/api/progress/export"):
            self.assertEqual(guest.get(path).status_code, 401)
        for ref in ("lesson:99:1", "lesson:1:99", "lesson:01:1", "problem:missing", "../users"):
            self.assertEqual(self.note(ref, note="text", version=0).status_code, 404)
        self.assertEqual(self.client.get("/api/notebook/lesson:1:1").get_json()["record"]["note"], "")

    def test_bookmark_and_note_update_independently(self):
        first = self.note(bookmarked=True, version=0).get_json()["record"]
        self.assertEqual(first["version"], 1)
        second = self.note(note="先编译，再链接。", version=1).get_json()["record"]
        self.assertTrue(second["bookmarked"])
        third = self.note(bookmarked=False, version=2).get_json()["record"]
        self.assertEqual(third["note"], "先编译，再链接。")
        self.assertFalse(third["bookmarked"])
        self.assertEqual(cm.load_progress("learner")["attempts"], {})

    def test_stale_note_save_preserves_newer_server_version(self):
        self.note(note="新版思路", version=0)
        response = self.note(note="过期标签页", version=0)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.get_json()["conflict"])
        self.assertEqual(response.get_json()["record"]["note"], "新版思路")
        self.assertEqual(cm.load_progress("learner")["notebook"]["lesson:1:1"]["note"], "新版思路")

    def test_empty_note_keeps_revision_against_stale_tabs(self):
        self.note(note="需要删除的笔记", bookmarked=True, version=0)
        response = self.note(note="", bookmarked=False, version=1)
        self.assertEqual(response.get_json()["record"]["version"], 2)
        self.assertNotIn('data-notebook-ref=', self.client.get("/notebook").get_data(as_text=True))
        self.assertEqual(self.note(note="旧版本复活", version=0).status_code, 409)

    def test_notebook_is_private_and_stale_account_is_rejected(self):
        self.note(note="learner private notebook", bookmarked=True, version=0)
        other = self.register("another-note-user")
        other_record = other.get("/api/notebook/lesson:1:1").get_json()["record"]
        self.assertEqual(other_record["note"], "")
        self.assertNotIn("learner private notebook", other.get("/notebook").get_data(as_text=True))
        response = other.post("/api/notebook/lesson:1:1", json={"note": "wrong account", "version": 0},
                              headers={"X-CMaster-Account": cm.user_storage_id("learner")})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("notebook", cm.load_progress("another-note-user"))
        self.assertEqual(self.client.post("/api/notebook/lesson:1:1", json={"version": 1, "note": "x"}).status_code, 409)

    def test_notebook_validates_payload_and_length(self):
        bad = [{"version": 0}, {"version": True, "note": "x"}, {"version": -1, "note": "x"},
               {"version": 0, "note": 123}, {"version": 0, "note": "长" * 8001},
               {"version": 0, "bookmarked": "true"}]
        for payload in bad:
            with self.subTest(payload_type=list(payload)):
                self.assertEqual(self.note(**payload).status_code, 400)
        self.assertEqual(self.client.post("/api/notebook/lesson:1:1", json=[],
                         headers={"X-CMaster-Account": cm.user_storage_id("learner")}).status_code, 400)
        self.assertEqual(self.note(note="边界" * 4000, version=0).status_code, 200)

    def test_notebook_plain_text_is_escaped_in_pages(self):
        text = '</textarea><script id="unsafe-note">alert(1)</script>\n指针 & 数组'
        self.note(note=text, version=0)
        for path in ("/notebook", "/chapter/1/1"):
            page = self.client.get(path).get_data(as_text=True)
            self.assertNotIn('<script id="unsafe-note">', page)
            self.assertIn("&lt;/textarea&gt;", page)
        self.assertEqual(self.client.get("/api/notebook/lesson:1:1").get_json()["record"]["note"], text)

    def test_notebook_filters_saved_content(self):
        self.note(note="编译管线 memory", version=0)
        self.note("lesson:7:1", bookmarked=True, version=0)
        self.note("problem:c001", note="Hello 的解题思路", bookmarked=True, version=0)
        self.assertIn('data-notebook-ref="lesson:1:1"', self.client.get("/notebook?q=memory&view=notes&chapter=1").get_data(as_text=True))
        filtered = self.client.get("/notebook?view=bookmarks&kind=problem").get_data(as_text=True)
        self.assertIn('data-notebook-ref="problem:c001"', filtered)
        self.assertNotIn('data-notebook-ref="lesson:', filtered)
        self.assertIn("没有找到符合条件", self.client.get("/notebook?q=missing-query").get_data(as_text=True))
        self.assertEqual(self.client.get("/notebook?view=invalid&kind=invalid&chapter=999").status_code, 200)

    def test_notebook_exports_only_current_accounts_learning_data(self):
        self.note(note="只属于 learner 的笔记", version=0)
        self.submit("AC", "/* own accepted code */")
        other = self.register("export-other")
        other.post("/api/notebook/problem:c001", json={"note": "OTHER_PRIVATE", "version": 0},
                   headers={"X-CMaster-Account": cm.user_storage_id("export-other")})
        markdown = self.client.get("/api/notebook/export")
        self.assertEqual(markdown.mimetype, "text/markdown")
        self.assertIn("只属于 learner 的笔记", markdown.get_data(as_text=True))
        exported = self.client.get("/api/progress/export")
        self.assertIn("attachment", exported.headers["Content-Disposition"])
        self.assertEqual(exported.headers["Cache-Control"], "no-store")
        data = exported.get_json()
        self.assertEqual(data["schema"], "cmaster.progress")
        self.assertEqual(data["username"], "learner")
        self.assertEqual(data["progress"]["solutions"]["c001"]["accepted"]["code"], "/* own accepted code */")
        self.assertNotIn("OTHER_PRIVATE", exported.get_data(as_text=True))
        for private_field in ("users", "salt", "hash", "secret"):
            self.assertNotIn(private_field, data)

    def test_noop_note_save_keeps_revision(self):
        self.assertEqual(self.note(note="", version=0).get_json()["record"]["version"], 0)
        self.note(note="保持原样", version=0)
        self.assertEqual(self.note(note="保持原样", version=1).get_json()["record"]["version"], 1)

    def test_parallel_notebook_updates_keep_each_resource(self):
        def save(kid):
            client = cm.app.test_client()
            with client.session_transaction() as session:
                session["user"] = "learner"
            return client.post("/api/notebook/lesson:1:%d" % kid,
                               json={"version": 0, "note": "小节 %d" % kid},
                               headers={"X-CMaster-Account": cm.user_storage_id("learner")}).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(save, range(1, 5))), [200] * 4)
        self.assertEqual(len(cm.load_progress("learner")["notebook"]), 4)

    def test_all_course_problem_and_learning_pages_render(self):
        paths = ["/", "/notebook", "/training", "/wrongbook", "/profile", "/analytics", "/map", "/memory", "/playground"]
        paths += ["/chapter/%d/%d" % (cid, kid) for cid, ch in cm.CHAPTERS.items()
                  for kid in range(1, len(ch["knowledge_points"]) + 1)]
        paths += ["/problem/" + qid for qid in cm.QMAP]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)


def tearDownModule():
    cm.metrics.flush(True)
    cm.metrics.init(None)
    IMPORT_DATA.cleanup()


if __name__ == "__main__":
    unittest.main()
