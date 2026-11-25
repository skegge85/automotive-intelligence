from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from intelligence.adapters import IncompleteInventory, group_one, paginated, paylocity
from intelligence.report import prepare_daily
from intelligence.model import Job, resolve_observations
from intelligence.run import import_history, persist_check, registry_data, scan
from intelligence.storage import ROOT, Store, now


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        with patch.dict("os.environ", {"DATABASE_URL": ""}):
            self.store = Store(Path(self.directory.name) / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.registry = registry_data(ROOT / "registry.json")
        self.store.seed(self.registry)
        self.employer = next(row for row in self.registry["employers"] if row["id"] == "autonation")
        self.run_number = 0

    def check(self, jobs, status="success"):
        self.run_number += 1
        run_id = str(self.run_number)
        stamp = now()
        with self.store.transaction():
            self.store.execute("insert into runs (id,started,status) values (?,?,'running')", (run_id, stamp))
        return persist_check(self.store, run_id, self.employer, {
            "attempted": 1, "started": stamp, "finished": stamp, "status": status, "jobs": jobs
        })

    def test_known_requisitions_preserve_employer_and_closed_evidence(self):
        import_history(self.store)
        import_history(self.store)
        records = {row["requisition"]: row for row in self.store.execute("select * from vacancies")}
        self.assertEqual(2, len(records))
        a = records["JR2026152536"]
        p = records["3532264"]
        self.assertEqual(("autonation", "filled"), (a["employer_id"], a["status"]))
        self.assertEqual(("preston", "inactive"), (p["employer_id"], p["status"]))
        self.assertIsNone(p["first_seen"])
        self.assertIn("https://careers.autonation.com/", a["url"])
        self.assertIn("paylocity.com", p["url"])
        self.assertEqual(0, self.store.execute("select count(*) from deliveries").fetchone()[0])

    def test_direct_inventory_does_not_override_conflicting_direct_closure(self):
        import_history(self.store)
        job = Job("Market President", "https://example.com/autonation/market-president", "JR2026152536")
        self.check([job])
        row = self.store.execute("select * from vacancies where requisition=?", (job.requisition,)).fetchone()
        self.assertEqual("unverified", row["status"])
        self.assertIn("https://careers.autonation.com", row["evidence"])
        self.assertEqual("applied", json.loads(row["details"])["application_status"])
        self.assertEqual(0, self.store.execute("select count(*) from deliveries").fetchone()[0])

    def test_third_party_or_conflicting_direct_evidence_unverified(self):
        active = {"status": "active", "direct": False}
        closed = {"status": "inactive", "direct": True, "date": "2026-11-25"}
        self.assertEqual("unverified", resolve_observations([active]))
        self.assertEqual("inactive", resolve_observations([active, closed]))
        self.assertEqual("unverified", resolve_observations([closed, dict(closed, status="active")]))

    def test_failed_inventory_preserves_active_jobs_and_repost_is_tracked(self):
        url = "https://careers.example.com/job/abc"
        posting = Job("Market President", url, "ABC")
        self.assertEqual(1, self.check([posting])["new"])
        self.assertEqual(0, self.check([posting])["new"])
        self.check(None, "failed")
        self.check([], "partial_evaluation")
        self.assertEqual("active", self.store.execute("select status from vacancies").fetchone()[0])
        self.assertEqual(1, self.check([])["changed"])
        self.assertEqual("removed", self.store.execute("select status from vacancies").fetchone()[0])
        self.assertEqual(1, self.check([posting])["changed"])
        self.assertEqual(1, self.store.execute("select count(*) from vacancies").fetchone()[0])
        self.assertEqual(2, self.store.execute("select count(*) from deliveries").fetchone()[0])

    def test_candidate_criteria_never_exclude_for_location_or_pay(self):
        names = ["Market President", "Director of Variable Operations", "Vice President",
                 "Multi-rooftop General Sales Manager", "Automotive Market Area Director",
                 "Fixed Operations Director", "Operating Partner", "CEO"]
        for name in names:
            self.assertTrue(Job(name, "https://example.com", location="Alaska",
                                compensation="$120,000").eligible, name)
        self.assertFalse(Job("Service Technician", "https://example.com").eligible)

    def test_unconfigured_employers_force_partial_and_counts_stay_unknown(self):
        with patch("intelligence.run.ADAPTERS", {}):
            audit = scan(self.store, self.registry)
        self.assertEqual("PARTIAL", audit["status"])
        self.assertEqual(150, audit["required_top"])
        self.assertEqual(0, audit["unidentified_rank_slots"])
        self.assertEqual(0, audit["successful"])
        check = self.store.execute("select retrieved from checks limit 1").fetchone()
        self.assertIsNone(check[0])

    def test_pagination_does_not_accept_duplicates_or_missing_pages(self):
        item = Job("CEO", "https://example.com/job/1", "1")
        other = Job("COO", "https://example.com/job/2", "2")
        self.assertEqual([item, other], paginated(lambda offset: (2, [item if not offset else other]), 1))
        with self.assertRaises(ValueError):
            paginated(lambda offset: (2, [item]), 1)
        with self.assertRaises(ValueError):
            paginated(lambda offset: (2, [item] if not offset else []), 1)

    def test_partial_inventory_preserves_valid_rows_without_claiming_coverage(self):
        item = Job("CEO", "https://example.com/job/1", "1")
        with self.assertRaises(IncompleteInventory) as failure:
            paginated(lambda offset: (2, [item, None]), 2)
        self.assertEqual([item], failure.exception.jobs)
        self.assertEqual(1, failure.exception.missing)

    def test_zero_discovery_daily_report_queues_once(self):
        prepare_daily(self.store)
        prepare_daily(self.store)
        reports = list(self.store.execute("select * from deliveries"))
        self.assertEqual(1, len(reports))
        self.assertIn("New eligible active vacancies in the last 24 hours: 0", reports[0]["body"])
        self.assertIsNone(reports[0]["sent"])

    def test_registry_preserves_published_tie_and_distinct_priority_identities(self):
        rows = {row["id"]: row for row in self.registry["employers"]}
        self.assertEqual(150, sum(row["rank"] is not None for row in rows.values()))
        self.assertEqual([139, 139], [row["rank"] for row in rows.values() if row["rank"] == 139])
        self.assertEqual(147, rows["roster-147"]["rank"])
        self.assertIsNone(rows["kelly"]["rank"])
        self.assertEqual(59, rows["cavender"]["rank"])
        self.assertIsNone(rows["cavender-auto-group"]["rank"])

    def test_group_one_requires_complete_unique_inventory(self):
        employer = {"board": "https://www.group1careers.com/results"}
        row = ('<td class="hidden-xs af-job-id">2026-12</td>'
               '<td class="af-job-title"><a href="/job.html?id=2026-12&category=Sales">Sales Director</a></td>'
               '<td class="af-job-location">US-PA-Philadelphia</td>')
        with patch("intelligence.adapters.fetch", return_value="<h1>1 Jobs Found</h1>" + row):
            job, = group_one(employer)
            self.assertEqual("2026-12", job.requisition)
            self.assertEqual("https://www.group1careers.com/job.html?id=2026-12", job.url)
        for bad in (row, row * 2):
            with patch("intelligence.adapters.fetch", return_value="<h1>2 Jobs Found</h1>" + bad):
                with self.assertRaises(ValueError):
                    group_one(employer)

    def test_paylocity_requires_board_identity_even_for_empty_board(self):
        employer = {"name": "Preston Automotive Group", "board": "https://example.com/jobs"}
        data = {"ModuleTitle": employer["name"], "ShowInternal": False, "Jobs": []}
        with patch("intelligence.adapters.fetch", return_value="window.pageData = " + json.dumps(data)):
            self.assertEqual([], paylocity(employer))
        data["ModuleTitle"] = "Different employer"
        with patch("intelligence.adapters.fetch", return_value="window.pageData = " + json.dumps(data)):
            with self.assertRaises(ValueError):
                paylocity(employer)


if __name__ == "__main__":
    unittest.main()
