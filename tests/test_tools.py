import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recalls_server import limits, tools  # noqa: E402
from tests import fixture  # noqa: E402


class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "recalls.db")
        fixture.build(cls.path)
        os.environ["RECALLS_DB"] = cls.path

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("RECALLS_DB", None)
        cls.tmp.cleanup()

    def test_search_matches_title(self):
        out = tools.search_recalls("Helmets")
        self.assertEqual([r["recall_number"] for r in out["results"]], ["25202"])

    def test_search_matches_where_sold(self):
        out = tools.search_recalls("Amazon")
        self.assertEqual(out["total"], 3)

    def test_search_matches_hazard(self):
        out = tools.search_recalls("fire")
        self.assertEqual(out["total"], 4)

    def test_search_is_newest_first(self):
        out = tools.search_recalls("")
        dates = [r["recall_date"] for r in out["results"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_year_filter(self):
        self.assertEqual(tools.search_recalls(year=2026)["total"], 8)
        self.assertEqual(tools.search_recalls(year=2025)["total"], 2)

    def test_count_matches_search_total(self):
        for query, year in (("", None), ("fire", None), ("Amazon", 2026), ("", 2025)):
            self.assertEqual(tools.count_recalls(query, year)["count"], tools.search_recalls(query, year)["total"])

    def test_paging_walks_every_row(self):
        seen, cursor = [], 0
        while True:
            out = tools.search_recalls("", limit=3, cursor=cursor)
            seen += [r["recall_number"] for r in out["results"]]
            if "next_cursor" not in out:
                break
            cursor = out["next_cursor"]
        self.assertEqual(len(seen), 10)
        self.assertEqual(len(set(seen)), 10)

    def test_limit_is_clamped(self):
        self.assertEqual(limits.clamp_limit(500), limits.MAX_LIMIT)
        self.assertEqual(limits.clamp_limit(0), limits.DEFAULT_LIMIT)

    def test_get_recall_accepts_dash(self):
        self.assertEqual(tools.get_recall("26-101")["title"], tools.get_recall("26101")["title"])

    def test_get_recall_missing(self):
        self.assertFalse(tools.get_recall("99-999")["found"])

    def test_add_note_shows_on_get_recall(self):
        saved = tools.add_note("26102", "Checked the catalogue, nothing listed.", author="agent:test")
        self.assertTrue(saved["saved"])
        notes = tools.get_recall("26102")["notes"]
        self.assertEqual(notes[-1]["body"], "Checked the catalogue, nothing listed.")
        self.assertEqual(notes[-1]["author"], "agent:test")

    def test_add_note_rejects_empty(self):
        self.assertFalse(tools.add_note("26102", "   ")["saved"])

    def test_recent_recalls_window(self):
        out = tools.recent_recalls(30, today=dt.date(2026, 3, 25))
        self.assertEqual([r["recall_number"] for r in out["results"]], ["26108", "26107", "26106", "26105"])

    def test_status_reports_last_load(self):
        status = tools.server_status()
        self.assertEqual(status["recalls_loaded"], 10)
        self.assertEqual(status["last_load_status"], "ok")
        self.assertEqual(status["last_load_at"], "2026-03-25T02:00:07Z")

    def test_fit_keeps_payload_under_budget(self):
        payload = {"total": 400, "results": [{"title": "x" * 900} for _ in range(40)]}
        out = limits.fit(payload)
        import json
        self.assertLessEqual(len(json.dumps(out)), limits.MAX_RESPONSE_CHARS)
        self.assertGreater(len(out["results"]), 0)


class Phase1RegressionTests(unittest.TestCase):
    """Fixes for bugs found in review. Own DB, with rows that straddle CPSC's October fiscal year."""

    @classmethod
    def setUpClass(cls):
        import sqlite3
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "recalls.db")
        fixture.build(cls.path)
        con = sqlite3.connect(cls.path)
        # Fiscal-year numbers: '26-' prefix but announced in Dec 2025; '27-' prefix but Oct 2026.
        for number, date in (("26900", "2025-12-15"), ("27010", "2026-10-05")):
            fixture.insert_recall(con, number, date, f"Zed Recalls Thing {number}", sold_at="Amazon.com")
        con.commit()
        con.close()
        os.environ["RECALLS_DB"] = cls.path

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("RECALLS_DB", None)
        cls.tmp.cleanup()

    def test_year_filter_uses_recall_date_not_number_prefix(self):
        nums_2025 = {r["recall_number"] for r in tools.search_recalls(year=2025)["results"]}
        self.assertIn("26900", nums_2025)  # '26-' prefix, but announced in 2025
        nums_2026 = {r["recall_number"] for r in tools.search_recalls(year=2026, limit=50)["results"]}
        self.assertIn("27010", nums_2026)  # '27-' prefix, but announced in 2026
        self.assertNotIn("26900", nums_2026)
        self.assertEqual(tools.count_recalls(year=2025)["count"], 3)
        self.assertEqual(tools.count_recalls(year=2027)["count"], 0)

    def test_add_note_with_dash_shows_on_get_recall(self):
        saved = tools.add_note("27-010", "Legal is reviewing our listings.")
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["recall_number"], "27010")
        self.assertEqual(tools.get_recall("27-010")["notes"][-1]["body"], "Legal is reviewing our listings.")
        self.assertEqual(tools.get_recall("27010")["notes"][-1]["body"], "Legal is reviewing our listings.")

    def test_add_note_unknown_recall_is_rejected(self):
        out = tools.add_note("99-999", "orphan")
        self.assertFalse(out["saved"])
        self.assertIn("error", out)
        self.assertEqual(tools.get_recall("99999"), {"found": False, "recall_number": "99999"})

    def test_paging_does_not_skip_rows_when_page_is_trimmed(self):
        old = limits.MAX_RESPONSE_CHARS
        limits.MAX_RESPONSE_CHARS = 1500  # force fit() to trim each page
        try:
            seen, cursor, trimmed = [], 0, False
            for _ in range(50):
                out = tools.search_recalls("", limit=10, cursor=cursor)
                trimmed = trimmed or out.get("truncated", False)
                seen += [r["recall_number"] for r in out["results"]]
                if "next_cursor" not in out:
                    break
                cursor = out["next_cursor"]
        finally:
            limits.MAX_RESPONSE_CHARS = old
        self.assertTrue(trimmed)
        self.assertEqual(len(seen), 12)
        self.assertEqual(len(set(seen)), 12)

    def test_fit_flags_truncation(self):
        out = limits.fit({"results": [{"t": "x" * 900} for _ in range(40)]})
        self.assertTrue(out["truncated"])

    def test_recent_recalls_reports_cap(self):
        out = tools.recent_recalls(365, today=dt.date(2026, 10, 10))
        self.assertEqual(out["count"], len(out["results"]))


class RetailerAndUnitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sqlite3
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "recalls.db")
        fixture.build(cls.path)
        con = sqlite3.connect(cls.path)
        extra = [  # number, date, title, units_text, units, sold_at
            ("26201", "2026-04-01", "Kay Recalls Lamps Due to Fire Hazard", "About 75,400 Lamps and about 119,000 Bulbs", 75400, "Online at Wal-Mart.com from 2025"),
            ("26202", "2026-04-02", "Lee Recalls Fans Due to Fire Hazard", "About 1,000 (In addition, about 90 were sold in Canada)", 1000, "Targeted ads and Target stores"),
            ("26203", "2026-04-03", "Moe Recalls Kettles Due to Burn Hazard; Sold Exclusively on Amazon.com", "About 500 (About 200 were previously recalled in May 2025)", 500, "Kitchen stores"),
        ]
        for n, d, t, ut, u, s in extra:
            fixture.insert_recall(con, n, d, t, units_text=ut, units=u, sold_at=s)
        con.commit()
        con.close()
        os.environ["RECALLS_DB"] = cls.path

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("RECALLS_DB", None)
        cls.tmp.cleanup()

    def numbers(self, **kw):
        return sorted(r["recall_number"] for r in tools.search_recalls(limit=50, **kw)["results"])

    def test_retailer_spelling_variants(self):
        want = ["25201", "26106", "26201"]  # Walmart stores, Walmart.com, Wal-Mart.com
        for name in ("Walmart", "walmart", "Wal-Mart", "walmart.com", "www.walmart.com"):
            self.assertEqual(self.numbers(retailer=name), want, name)

    def test_retailer_does_not_match_inside_other_words(self):
        # 26202 says "Targeted ads and Target stores": matches once, via 'Target stores'; 26102 is Target too.
        self.assertEqual(self.numbers(retailer="Target"), ["26102", "26202"])
        self.assertEqual(self.numbers(retailer="Targ"), [])  # a prefix of a word is not a retailer

    def test_retailer_falls_back_to_title_phrase(self):
        self.assertIn("26203", self.numbers(retailer="Amazon"))  # sold_at is wrong, title says Sold on Amazon.com

    def test_retailer_combines_with_year_and_count(self):
        self.assertEqual(tools.count_recalls(retailer="Amazon", year=2025)["count"], 1)
        self.assertEqual(tools.count_recalls(retailer="Amazon", year=2026)["count"], 3)
        self.assertEqual(tools.count_recalls(retailer="Amazon")["count"], tools.search_recalls(retailer="Amazon")["total"])

    def test_recent_recalls_by_retailer(self):
        out = tools.recent_recalls(30, today=dt.date(2026, 4, 10), retailer="Amazon")
        self.assertEqual([r["recall_number"] for r in out["results"]], ["26203"])

    def test_invalid_retailer_is_an_error_not_a_crash(self):
        for tool in (tools.search_recalls, tools.count_recalls, tools.sum_units, tools.recent_recalls):
            self.assertIn("error", tool(retailer="x"))

    def test_retailer_regex_metacharacters_are_literal(self):
        self.assertIn("error", tools.count_recalls(retailer=".*"))  # no letters/digits: rejected, not a wildcard
        self.assertEqual(tools.count_recalls(retailer="(a|b)+")["count"], 0)

    def test_sum_units_year(self):
        out = tools.sum_units(year=2025)
        self.assertEqual((out["recall_count"], out["units_total"]), (2, 2100))
        self.assertEqual(out["review_recalls"], 0)

    def test_sum_units_retailer_ignores_canada_but_flags_overlap(self):
        out = tools.sum_units(retailer="Amazon", year=2026)
        self.assertEqual(out["units_total"], 4000 + 3300 + 500)
        self.assertEqual(out["review_recalls"], 1)  # 26203: "previously recalled"
        self.assertEqual(out["review_units_total"], 500)
        self.assertEqual(out["clean_units_total"], 7300)

    def test_multi_product_units_text_is_flagged(self):
        out = tools.sum_units(retailer="Walmart", year=2026)
        self.assertEqual(out["review_recalls"], 1)
        self.assertEqual(out["review_examples"][0]["recall_number"], "26201")

    def test_canada_only_parenthetical_is_clean(self):
        from recalls_server import units
        self.assertEqual(units.classify("About 8,740 (In addition, about 350 were sold in Canada)"), "clean")
        self.assertEqual(units.classify("About 52,000 (In addition, about 1,800 were sold in Canada and about 2,900 were sold in Mexico)"), "clean")
        self.assertEqual(units.classify("About 61,500 bottles"), "clean")
        self.assertEqual(units.classify("About 34,000 (About 32,000 were previously recalled in March 2022)"), "review")
        self.assertEqual(units.classify("About 1,980 Dressers and 35 Other Dressers"), "review")

    def test_top_recalls_and_empty_result(self):
        top = tools.sum_units(year=2026, top=1)["top_recalls"]
        self.assertEqual((top[0]["recall_number"], top[0]["units"]), ("26201", 75400))
        empty = tools.sum_units(year=1999)
        self.assertEqual((empty["recall_count"], empty["units_total"], empty["top_recalls"]), (0, 0, []))


class GuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "recalls.db")
        fixture.build(cls.path)
        os.environ["RECALLS_DB"] = cls.path

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("RECALLS_DB", None)
        cls.tmp.cleanup()

    def test_oversized_note_and_author_are_rejected_not_truncated(self):
        self.assertFalse(tools.add_note("26101", "x" * (tools.MAX_NOTE_CHARS + 1))["saved"])
        self.assertFalse(tools.add_note("26101", "ok", author="a" * (tools.MAX_AUTHOR_CHARS + 1))["saved"])
        self.assertTrue(tools.add_note("26101", "x" * tools.MAX_NOTE_CHARS)["saved"])

    def test_control_characters_are_stripped(self):
        tools.add_note("26103", "hello\x00\x1b[31m world", author="bob\x07")
        last = tools.get_recall("26103")["notes"][-1]
        self.assertEqual((last["body"], last["author"]), ("hello[31m world", "bob"))
        self.assertFalse(tools.add_note("26103", "\x00\x01")["saved"])  # nothing left after stripping

    def test_get_recall_shows_only_newest_notes(self):
        for i in range(tools.MAX_NOTES_SHOWN + 3):
            tools.add_note("26104", f"note {i}")
        rec = tools.get_recall("26104")
        self.assertEqual(len(rec["notes"]), tools.MAX_NOTES_SHOWN)
        self.assertEqual(rec["notes"][-1]["body"], f"note {tools.MAX_NOTES_SHOWN + 2}")
        self.assertTrue(rec["notes_truncated"])
        self.assertEqual(rec["notes_total"], tools.MAX_NOTES_SHOWN + 3)

    def test_get_recall_without_many_notes_has_no_truncation_flag(self):
        self.assertNotIn("notes_truncated", tools.get_recall("26105"))

    def test_sum_units_tolerates_null_text_and_units(self):
        from recalls_server import units
        row = {"recall_number": "1", "title": None, "recall_date": "d", "units": None, "units_text": None}
        self.assertEqual(units.summarize([row])["units_total"], 0)

    def test_read_connections_cannot_write(self):
        import sqlite3
        from recalls_server import db
        with db.connect() as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM recalls")
        self.assertEqual(tools.count_recalls()["count"], 10)

    def test_like_wildcards_in_query_are_literal(self):
        self.assertEqual(tools.count_recalls(query="%")["count"], 0)
        self.assertEqual(tools.count_recalls(query="_")["count"], 0)
        self.assertEqual(tools.count_recalls(query="Fire")["count"], 4)  # normal search unchanged

    def test_year_zero_is_not_ignored(self):
        self.assertEqual(tools.count_recalls(year=0)["count"], 0)

    def test_hostile_inputs_do_not_raise(self):
        for kw in ({"retailer": "x'; DROP TABLE recalls;--"}, {"query": "' OR 1=1 --"}, {"year": -5}, {"year": 99999}):
            self.assertIn("count", tools.count_recalls(**kw))
        self.assertEqual(tools.count_recalls()["count"], 10)  # table still there


class StatusFreshnessTests(unittest.TestCase):
    """Fixture: newest recall 2026-03-20, last load 2026-03-25T02:00:07Z (rows_upserted 1)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "recalls.db")
        fixture.build(cls.path)
        os.environ["RECALLS_DB"] = cls.path

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("RECALLS_DB", None)
        cls.tmp.cleanup()

    def at(self, text):
        return dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc)

    def test_fresh_data_has_no_warnings(self):
        out = tools.server_status(self.at("2026-03-25T12:00:00"))
        self.assertFalse(out["data_may_be_stale"])
        self.assertEqual(out["warnings"], [])
        self.assertEqual(out["newest_recall_date"], "2026-03-20")
        self.assertEqual(out["recalls_loaded"], 10)  # existing fields unchanged
        self.assertEqual(out["last_load_status"], "ok")

    def test_overdue_load_is_flagged(self):
        out = tools.server_status(self.at("2026-03-28T02:00:00"))
        self.assertTrue(out["data_may_be_stale"])
        self.assertTrue(any("overdue" in w for w in out["warnings"]))

    def test_old_newest_recall_with_ok_loads_flags_log_vs_data_mismatch(self):
        out = tools.server_status(self.at("2026-04-15T00:00:00"))
        self.assertTrue(any("newest recall is 26 days old" in w for w in out["warnings"]))
        self.assertTrue(any("disagree" in w for w in out["warnings"]))


if __name__ == "__main__":
    unittest.main()
