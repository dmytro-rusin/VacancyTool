import tempfile
import unittest
import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from vacancytool.config import DEFAULT_SETTINGS, TIMEZONE, next_due
from vacancytool.database import connect, counts, excluded_role, initialize, list_vacancies, pending_notifications, set_status, upsert_item
from vacancytool.notifications import build_digest, load_mail_settings, send_pending
from vacancytool.scoring import assess, detect_platforms, onsite_only
from vacancytool.sources import FeedItem, canonicalize, extract_detail, parse_feed, without_salary_title
from vacancytool.web import create_app


class CoreTests(unittest.TestCase):
    def test_mail_password_can_come_from_keychain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "mail.json"
            path.write_text(json.dumps({"host": "smtp.gmail.com", "username": "me@example.org",
                                        "keychain_service": "VacancyTool-Gmail",
                                        "from": "me@example.org", "to": "me@example.org"}))
            path.chmod(0o600)
            completed = type("Completed", (), {"stdout": "app-password\n"})()
            with patch("vacancytool.notifications.subprocess.run", return_value=completed) as lookup:
                self.assertEqual(load_mail_settings(root)["password"], "app-password")
            self.assertEqual(lookup.call_args.args[0],
                             ["/usr/bin/security", "find-generic-password", "-w", "-a", "me@example.org",
                              "-s", "VacancyTool-Gmail"])

    def test_mail_queue_delivery_and_no_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "vacancies.sqlite3"
            initialize(database)
            for key, title in (("1", "Senior iOS Developer"), ("2", "Junior Python Backend Engineer"),
                               ("3", "QA iOS Engineer")):
                item = FeedItem("djinni", key, f"https://djinni.co/jobs/{key}-test/",
                                f"https://djinni.co/jobs/{key}-test/", title, "Acme", "Swift Python", None)
                upsert_item(database, item, "ios", None, None, False)
            self.assertEqual([row["score"] for row in pending_notifications(database)], [100, 36])
            self.assertEqual(send_pending(root, database), 0)
            self.assertEqual(len(pending_notifications(database)), 2)
            path = root / "mail.json"
            path.write_text(json.dumps({"host": "smtp.example.org", "username": "me@example.org",
                                        "password": "secret", "from": "me@example.org", "to": "me@example.org"}))
            path.chmod(0o600)
            with patch("vacancytool.notifications.send_digest", side_effect=OSError("offline")):
                with self.assertRaises(OSError):
                    send_pending(root, database)
            self.assertEqual(len(pending_notifications(database)), 2)
            with patch("vacancytool.notifications.send_digest") as sender:
                self.assertEqual(send_pending(root, database), 2)
                rows = sender.call_args.args[1]
                self.assertEqual([row["score"] for row in rows], [100, 36])
                message = build_digest(sender.call_args.args[0], rows)
                self.assertEqual(message["Subject"], "2 new vacancies found")
                self.assertEqual(message.get_content().strip(),
                                 "100 | Senior iOS Developer\nhttps://djinni.co/jobs/1-test/\n\n"
                                 "36 | Junior Python Backend Engineer\nhttps://djinni.co/jobs/2-test/")
                self.assertEqual(send_pending(root, database), 0)
            self.assertEqual(sender.call_count, 1)
            self.assertFalse(pending_notifications(database))
            self.assertEqual(counts(database)["Viewed"], 2)
            self.assertEqual(counts(database)["New"], 0)

    def test_tailscale_host_and_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "templates").symlink_to(Path(__file__).resolve().parents[1] / "templates")
            (root / "static").symlink_to(Path(__file__).resolve().parents[1] / "static")
            (root / "access.json").write_text('{"tailscale_host":"my-mac.example.ts.net"}')
            client = create_app(root).test_client()
            self.assertEqual(client.get("/", headers={"Host": "my-mac.example.ts.net"}).status_code, 200)
            self.assertEqual(client.get("/", headers={"Host": "evil.example.ts.net"}).status_code, 403)
            self.assertEqual(client.post("/api/refresh", headers={"Host": "my-mac.example.ts.net"}).status_code, 403)
            self.assertEqual(client.post("/api/vacancies/1/status", json={"status": "Viewed"},
                                         headers={"Host": "my-mac.example.ts.net",
                                                  "Origin": "https://evil.example.ts.net"}).status_code, 403)
    def test_exclusions_and_workplace_extraction(self):
        for title in ("Senior AQA Engineer iOS", "Project Manager", "UI/UX Designer", "Customer Support Specialist"):
            self.assertTrue(excluded_role(title), title)
        self.assertFalse(excluded_role("Senior iOS Developer"))
        description = "<p>Build iOS applications with Swift. " + "Production code. " * 12 + "</p>"
        djinni = ('<script type="application/ld+json">'
                  '{"@type":"JobPosting","description":"' + description.replace('"', '\\"') + '",'
                  '"jobLocationType":"TELECOMMUTE","applicantLocationRequirements":'
                  '{"address":{"addressRegion":"Europe"}}}</script>')
        self.assertEqual(extract_detail("djinni", djinni)[2:], ("Remote", "Europe"))
        dou = '<div class="l-vacancy"><div class="sh-info"><span class="place">Київ, віддалено</span></div>'
        dou += '<div class="vacancy-section">' + description + '</div></div>'
        self.assertEqual(extract_detail("dou", dou)[2:], ("Remote", "Київ"))

    def test_schedule_boundaries_and_manual_independence(self):
        windows = DEFAULT_SETTINGS["schedule"]
        for before, expected in [
            ("2026-09-20T06:59:00", "2026-09-20T07:00:00"),
            ("2026-09-20T11:51:00", "2026-09-20T12:00:00"),
            ("2026-09-20T15:46:00", "2026-09-20T16:00:00"),
            ("2026-09-20T19:51:00", "2026-09-20T20:00:00"),
            ("2026-09-20T23:41:00", "2026-09-21T00:00:00"),
        ]:
            with self.subTest(before=before):
                actual = next_due(datetime.fromisoformat(before).replace(tzinfo=TIMEZONE), windows)
                self.assertEqual(actual.replace(tzinfo=None), datetime.fromisoformat(expected))

    def test_score_role_guard_and_priority(self):
        self.assertEqual(assess("Senior Product Designer (iOS/Android)", "Figma").score, 0)
        self.assertEqual(assess("Tech Support iOS", "App Store").score, 0)
        self.assertEqual(assess("Trainee AQA (iOS)", "Swift testing").score, 0)
        self.assertEqual(assess("User Acquisition Manager (Mobile)", "iOS and Android apps").score, 0)
        self.assertEqual(assess("Lead of Growth and Analytics (Mobile apps)", "Python APIs").score, 0)
        self.assertEqual(assess("Senior iOS Developer", "Swift UIKit").score, 100)
        self.assertEqual(assess("Lead iOS Engineer", "Swift").score, 96)
        self.assertEqual(assess("Middle Flutter Developer", "Dart").score, 60)
        self.assertEqual(assess("Junior Python Backend Engineer", "Django").score, 36)
        self.assertEqual(assess("Senior iOS Developer", "KMP knowledge is a plus").stack, "iOS")
        self.assertEqual(assess("Senior SDK Developer", "Android/Kotlin + iOS/Swift SDK").stack, "iOS + Android")
        self.assertEqual(assess("Software Engineer", "Build a frontend with TypeScript and React.js").stack, "JavaScript")

    def test_platform_detection_uses_primary_stack_and_title(self):
        self.assertEqual(detect_platforms("Senior iOS Developer", "KMP is a plus", "iOS"), ("iOS",))
        self.assertEqual(detect_platforms("Senior Mobile Engineer", "iOS and Android", "iOS + Android"),
                         ("iOS", "Android"))
        self.assertEqual(detect_platforms("Android Engineer", "Kotlin", "—"), ("Android",))
        self.assertEqual(detect_platforms("Backend Engineer", "Python and Django", "Python"), ("Python",))

    def test_onsite_only_penalty(self):
        self.assertEqual(assess("Senior iOS Developer", "Swift UIKit", "Hybrid").score, 95)
        self.assertEqual(assess("Senior iOS Developer", "Swift UIKit", "Office").score, 95)
        self.assertEqual(assess("Senior iOS Developer", "Swift UIKit", "On-site").score, 95)
        self.assertEqual(assess("Senior iOS Developer", "Swift UIKit", "Remote").score, 100)
        self.assertEqual(assess("Senior iOS Developer", "Swift UIKit", "Hybrid / Remote").score, 100)
        self.assertEqual(assess("Embedded C++ Developer", "C++", "Onsite").score, 1)
        self.assertTrue(onsite_only("Hybrid"))
        self.assertFalse(onsite_only("Remote"))

    def test_work_format_change_recalculates_score(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "vacancies.sqlite3"
            initialize(database)
            item = FeedItem("djinni", "format", "https://djinni.co/jobs/format/",
                            "https://djinni.co/jobs/format/", "Senior iOS Developer", "Acme",
                            "<p>Swift UIKit</p>", None)
            self.assertTrue(upsert_item(database, item, "ios", None, None, False, "Remote"))
            self.assertEqual(list_vacancies(database)[0]["score"], 100)
            self.assertFalse(upsert_item(database, item, "ios", None, None, False, "Hybrid"))
            self.assertEqual(list_vacancies(database)[0]["score"], 95)

    def test_feed_identity_and_salary_redaction(self):
        self.assertEqual(canonicalize("https://jobs.dou.ua/companies/acme/vacancies/123/?utm_source=jobsrss"),
                         "https://jobs.dou.ua/companies/acme/vacancies/123/")
        self.assertEqual(without_salary_title("Senior iOS в Acme, $4000–4500, віддалено"),
                         "Senior iOS в Acme, віддалено")
        xml = b"<rss><channel><title>Jobs</title><item><title>iOS Developer</title><link>https://djinni.co/jobs/123-ios-developer/</link><guid>https://djinni.co/jobs/123-ios-developer/</guid></item></channel></rss>"
        self.assertEqual(parse_feed("djinni", xml)[0].source_key, "123")
        dou_xml = b"<rss><channel><title>Jobs</title><item><title>Senior iOS Developer v Acme</title><link>https://jobs.dou.ua/companies/acme/vacancies/456/?utm_source=jobsrss</link></item></channel></rss>"
        self.assertEqual(parse_feed("dou", dou_xml)[0].source_key, "456")
        dou_salary = '<rss><channel><title>Jobs</title><item><title>Senior iOS в Acme, $4000–4500, віддалено</title><link>https://jobs.dou.ua/companies/acme/vacancies/456/</link></item></channel></rss>'
        self.assertEqual(parse_feed("dou", dou_salary.encode())[0].company, "Acme")
        dou_inc = '<rss><channel><title>Jobs</title><item><title>Senior iOS в Petcube, Inc., Ukraine, USA and worldwide</title><link>https://jobs.dou.ua/companies/petcube/vacancies/457/</link></item></channel></rss>'
        self.assertEqual(parse_feed("dou", dou_inc.encode())[0].company, "Petcube, Inc.")

    def test_dedup_status_and_web(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "templates").symlink_to(Path(__file__).resolve().parents[1] / "templates")
            (root / "static").symlink_to(Path(__file__).resolve().parents[1] / "static")
            database = root / "vacancies.sqlite3"
            initialize(database)
            item = FeedItem("djinni", "123", "https://djinni.co/jobs/123-ios/", "https://djinni.co/jobs/123-ios/",
                            "Senior iOS Developer, $4000–4500", "Acme", "<p>Swift UIKit</p><p>Salary: $5000</p>", None)
            self.assertTrue(upsert_item(database, item, "djinni:iOS", None, None, False))
            self.assertFalse(upsert_item(database, item, "djinni:Mobile", None, None, False))
            rows = list_vacancies(database)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["score"], 100)
            self.assertNotIn("$", rows[0]["title"] + rows[0]["description_text"])
            app = create_app(root)
            client = app.test_client()
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True)
            self.assertIn('lang="uk"', html)
            self.assertIn('data-platform="iOS"', html)
            self.assertIn('data-platforms="iOS"', html)
            self.assertIn('Платформа', html)
            self.assertIn('Оновити зараз', html)
            self.assertEqual(client.post("/api/refresh", headers={"Origin": "https://example.org"}).status_code, 403)
            response = client.post(f'/api/vacancies/{rows[0]["id"]}/status', json={"status": "Interested"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(counts(database)["Interested"], 1)
            self.assertEqual(list_vacancies(database)[0]["status"], "Interested")

    def test_exclusion_counts_status_order_and_publication_order(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "vacancies.sqlite3"
            initialize(database)
            for key, title, published in (
                ("1", "Senior iOS Developer", "2026-09-19T10:00:00+00:00"),
                ("2", "Senior iOS Engineer", "2026-09-20T10:00:00+00:00"),
                ("3", "Senior iOS Programmer", "2026-09-21T10:00:00+00:00"),
                ("4", "Senior iOS QA Engineer", "2026-09-22T10:00:00+00:00"),
            ):
                item = FeedItem("djinni", key, f"https://djinni.co/jobs/{key}-test/",
                                f"https://djinni.co/jobs/{key}-test/", title, "Acme", "Swift UIKit", published)
                upsert_item(database, item, "ios", None, None, False)
            with closing(connect(database)) as db:
                ids = {row["title"]: row["id"] for row in db.execute("SELECT id,title FROM vacancies")}
            set_status(database, ids["Senior iOS Engineer"], "Rejected")
            set_status(database, ids["Senior iOS Programmer"], "Applied")
            self.assertEqual([row["title"] for row in list_vacancies(database)],
                             ["Senior iOS Developer", "Senior iOS Programmer", "Senior iOS Engineer"])
            self.assertEqual(sum(counts(database).values()), 3)
            self.assertEqual(counts(database)["Rejected"], 1)
            set_status(database, ids["Senior iOS Developer"], "Applied")
            self.assertEqual([row["title"] for row in list_vacancies(database)][:2],
                             ["Senior iOS Programmer", "Senior iOS Developer"])
            initialize(database)
            self.assertEqual(len(list_vacancies(database)), 3)

    def test_status_precedes_score_and_score_precedes_publication_date(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "vacancies.sqlite3"
            initialize(database)
            items = (
                ("1", "Senior iOS Developer", "2026-09-19T10:00:00+00:00"),
                ("2", "Junior Python Backend Engineer", "2026-09-21T10:00:00+00:00"),
                ("3", "Senior iOS Engineer", "2026-09-20T10:00:00+00:00"),
                ("4", "Senior iOS Programmer", "2026-09-22T10:00:00+00:00"),
            )
            for key, title, published in items:
                item = FeedItem("djinni", key, f"https://djinni.co/jobs/{key}-test/",
                                f"https://djinni.co/jobs/{key}-test/", title, "Acme",
                                "Swift UIKit Python Django", published)
                upsert_item(database, item, "ios", None, None, False)
            with closing(connect(database)) as db:
                ids = {row["title"]: row["id"] for row in db.execute("SELECT id,title FROM vacancies")}
            set_status(database, ids["Junior Python Backend Engineer"], "Interested")
            for title in ("Senior iOS Developer", "Senior iOS Engineer", "Senior iOS Programmer"):
                set_status(database, ids[title], "Applied")
            self.assertEqual([row["title"] for row in list_vacancies(database)], [
                "Junior Python Backend Engineer",
                "Senior iOS Programmer",
                "Senior iOS Engineer",
                "Senior iOS Developer",
            ])


if __name__ == "__main__":
    unittest.main()
