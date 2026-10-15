"""The checks that have to pass before this app touches a real account.
Run:  <bundled python> -m unittest discover -s tests   (from the install folder)
No browser, no network: pages, locators and HTTP answers are fakes."""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import coinbase_site as site  # noqa: E402
import coinbase_docs as docs  # noqa: E402


class Guard(unittest.TestCase):
    def test_allows_document_controls(self):
        for label in ["PDF", "Download PDF", "Download statement", "Monthly statement",
                      "View document", "Download", "Load more", "Download\n\U000f0382"]:
            self.assertTrue(site.is_safe_control(label), label)

    def test_denies_everything_else(self):
        for label in ["Download and continue", "Generate custom statement", "Generate",
                      "Request report", "Settings", "Advanced trade", "Buy", "Sell", "Send",
                      "Convert", "Export CSV", "CSV", "HTML", "Create API key", "Sign in",
                      "Statement settings", "Mark as read", "", None, "   ", "Deposit",
                      "Manage funds", "Allow all", "Confirm my choices"]:
            self.assertFalse(site.is_safe_control(label), repr(label))


class Hosts(unittest.TestCase):
    def test_exact_hosts(self):
        for u in ["https://accounts.coinbase.com/statements", "https://accounts.coinbase.com/a/b.pdf",
                  "https://accounts.coinbase.com/", "https://www.coinbase.com/", "https://www.coinbase.com/home"]:
            self.assertTrue(site.is_safe_url(u), u)
        for u in ["http://accounts.coinbase.com/statements", "https://accounts.coinbase.com:8443/statements",
                  "https://coinbase.com/statements", "https://login.coinbase.com/signin",
                  "https://api.coinbase.com/v2/accounts", "https://accounts.coinbase.com.evil.test/statements",
                  "https://accounts.coinbase.com@evil.test/statements", "https://notcoinbase.com/", "", None]:
            self.assertFalse(site.is_safe_url(u), repr(u))

    def test_list_calls_keep_to_their_paths(self):
        for u in ["https://accounts.coinbase.com/statements", "https://accounts.coinbase.com/taxes/documents",
                  "https://accounts.coinbase.com/v2/tax/forms?types=1099-DA&year=2025",
                  "https://accounts.coinbase.com/v2/tax/owner-info", "https://www.coinbase.com/"]:
            self.assertTrue(site.is_list_url(u), u)
        for u in ["https://accounts.coinbase.com/settings", "https://accounts.coinbase.com/v2/tax/forms/abc/mark-read",
                  "https://accounts.coinbase.com/v2/tax/transactions", "https://www.coinbase.com/advanced-markets",
                  "https://www.coinbase.com/settings", "https://login.coinbase.com/signin", "",
                  # A path left open below v1/statements took ".." as one of its parts.
                  "https://accounts.coinbase.com/v1/statements/../settings",
                  "https://accounts.coinbase.com/v1/statements/../../v2/accounts",
                  "https://accounts.coinbase.com/v1/statements/abc",
                  "https://accounts.coinbase.com/v2/tax/forms/../../settings"]:
            self.assertFalse(site.is_list_url(u), repr(u))

    def test_download_hosts_exact(self):
        self.assertTrue(site.is_download_url("https://statements-report-persistent-production.s3.amazonaws.com/a_b__pdf.pdf"))
        self.assertTrue(site.is_download_url("https://tax-center-forms-production.s3.amazonaws.com/2025/x/y.pdf?X-Amz-Signature=abc"))
        for u in ["https://s3.amazonaws.com/statements-report-persistent-production/a.pdf",
                  "https://evil.s3.amazonaws.com/a.pdf", "http://tax-center-forms-production.s3.amazonaws.com/a.pdf",
                  "https://accounts.coinbase.com/statements", "https://tax-center-forms-production.s3.amazonaws.com.evil.test/a.pdf", ""]:
            self.assertFalse(site.is_download_url(u), u)


class Rows(unittest.TestCase):
    def test_month_to_iso(self):
        self.assertEqual(site.month_to_iso("September 2026"), "2026-09-30")
        self.assertEqual(site.month_to_iso("February 2024"), "2024-02-29")
        self.assertEqual(site.month_to_iso("Last 30 days"), "")
        self.assertEqual(site.month_to_iso(""), "")
        self.assertEqual(site.month_to_iso("Sept 2026"), "")

    def test_row_binding_is_exact(self):
        rows = [{"label": "Last 30 days", "button": "PDF", "i": 0},
                {"label": "September 2026", "button": "PDF", "i": 1},
                {"label": "August 2026", "button": "PDF", "i": 2}]
        self.assertEqual(site.row_index_for(rows, "August 2026"), (2, "PDF"))
        self.assertEqual(site.row_index_for(rows, "July 2026"), (-1, ""))
        twice = rows + [{"label": "August 2026", "button": "PDF", "i": 3}]
        self.assertEqual(site.row_index_for(twice, "August 2026"), (-1, ""),
                         "two rows with one label: nothing is chosen")

    def test_statement_title_roundtrip(self):
        d = site.RawDoc(title="Monthly Statement September 2026", date_text="2026-09-30")
        m = site._STATEMENT_TITLE_RE.match(d.title)
        self.assertEqual(site.month_to_iso(m.group(1)), d.date_text)


def row(label, i):
    return {"label": label, "button": "PDF", "i": i}


class ListPage:
    """A statements page showing these rows, with no Load more unless a test
    gives it one. Every wait is a moment, and is counted."""
    url = site.STATEMENTS_URL

    def __init__(self, rows, load_more=None):
        self.rows, self.load_more, self.waits = list(rows), load_more, []

    def locator(self, sel):
        if sel == site._LOAD_MORE and self.load_more is not None:
            return self.load_more
        page = self

        class L:
            def count(s): return 0 if sel == site._LOAD_MORE else len(page.rows)
            def is_visible(s): return False
            def is_enabled(s, **kw): return False
            @property
            def first(s): return s
        return L()

    def evaluate(self, js): return list(self.rows)

    def wait_for_timeout(self, ms): self.waits.append(ms)


class LoadMore:
    """A Load more that shows, or not, at each look, as `shows` says, and is
    turned on as `on` says."""

    def __init__(self, shows, on=True):
        self.shows, self.on, self.looks = list(shows), on, 0

    @property
    def first(self): return self

    def count(self):
        self.looks += 1
        return 1 if (self.shows.pop(0) if self.shows else False) else 0

    def is_visible(self): return True
    def is_enabled(self, **kw): return self.on
    def inner_text(self, **kw): return "Load more"
    def get_attribute(self, name): return None


class StatementList(unittest.TestCase):
    """The statements list is called whole only when it is."""

    TODAY = date(2026, 10, 11)
    MONTHS = [row("Last 30 days", 0), row("September 2026", 1), row("August 2026", 2)]

    def test_two_last_30_days_rows_stop_the_list(self):
        rows = self.MONTHS + [row("Last 30 days", 3)]
        with self.assertRaises(site.ListStopped):
            site._collect_statements(ListPage(rows), "2026-08-03", self.TODAY)

    def test_a_list_reaching_the_first_month_is_whole(self):
        got = site._collect_statements(ListPage(self.MONTHS), "2026-08-03", self.TODAY)
        self.assertEqual([d.date_text for d in got], ["2026-09-30", "2026-08-31"])
        got = site._collect_statements(ListPage(self.MONTHS), "2026-09-20", self.TODAY)
        self.assertEqual(len(got), 2, "months before the first transaction are kept too")

    def test_a_list_short_of_the_first_month_stops_holding_what_it_read(self):
        with self.assertRaises(site.ListStopped) as stopped:
            site._collect_statements(ListPage(self.MONTHS), "2021-04-03", self.TODAY)
        self.assertEqual([d.date_text for d in stopped.exception.docs], ["2026-09-30", "2026-08-31"])
        with self.assertRaises(site.ListStopped):
            site._collect_statements(ListPage(self.MONTHS), "2026-07-31", self.TODAY)

    def test_a_list_of_no_month_is_not_read_as_an_empty_one(self):
        """Every month row read as something else used to leave nothing to
        list, and the listing was called whole."""
        page = ListPage([row("Last 30 days", 0)])
        with self.assertRaises(site.ListStopped):
            site._collect_statements(page, "2024-01-05", self.TODAY)
        self.assertEqual(sum(page.waits[:-1]), site.MONTH_ROWS_WAIT_MS, "the month rows were waited for")

    def test_an_account_with_no_complete_month_lists_none(self):
        page = ListPage([row("Last 30 days", 0)])
        self.assertEqual(site._collect_statements(page, "2026-10-02", self.TODAY), [])

    def test_an_account_whose_first_transaction_is_not_said_stops_the_list(self):
        with self.assertRaises(site.ListStopped):
            site._collect_statements(ListPage(self.MONTHS), "", self.TODAY)

    def test_the_first_transaction_is_read_from_owner_info(self):
        for answer, first in (({"first_transaction_date": "2021-04-03T10:00:00Z"}, "2021-04-03"),
                              ({"first_transaction_date": ""}, ""), ({}, ""), (None, ""),
                              ([{"first_transaction_date": "2021-04-03"}], ""),
                              ({"first_transaction_date": "2021-13-03"}, "")):
            with mock.patch.object(site, "_get_json", lambda page, url, a=answer: a):
                self.assertEqual(site._first_transaction(object()), first, answer)
        self.assertEqual(site._first_tax_year("2021-04-03"), 2021)
        self.assertEqual(site._first_tax_year(""), 2012)

    def test_load_more_is_looked_for_once_more_before_the_end(self):
        page = ListPage(self.MONTHS, load_more=LoadMore([False, True]))
        pressed = []

        def click(pg, loc, **kw):
            pressed.append(kw["step"])
            pg.rows.append(row("July 2026", 3))
        with mock.patch.object(site.pressing, "click", click):
            self.assertTrue(site._press_load_more(page))
        self.assertEqual(pressed, ["load more of the list"])
        self.assertIn(site.LOAD_MORE_LOOK_AGAIN_MS, page.waits)

    def test_no_load_more_at_either_look_is_the_end(self):
        lm = LoadMore([False, False])
        page = ListPage(self.MONTHS, load_more=lm)
        with mock.patch.object(site.pressing, "click") as click:
            self.assertFalse(site._press_load_more(page))
        self.assertEqual(click.call_count, 0)
        self.assertEqual(lm.looks, 2)
        self.assertEqual(page.waits, [site.LOAD_MORE_LOOK_AGAIN_MS])

    def test_a_turned_off_load_more_is_not_pressed(self):
        page = ListPage(self.MONTHS, load_more=LoadMore([True, True], on=False))
        with mock.patch.object(site.pressing, "click") as click:
            self.assertFalse(site._press_load_more(page))
        self.assertEqual(click.call_count, 0)


class TaxList(unittest.TestCase):
    def test_duplicate_forms_keep_both(self):
        forms = {2025: [{"id": "11111111-2222-3333-4444-555555555555", "form_type": "1099-DA"},
                        {"id": "aaaa1111-bbbb-2222-cccc-333344445555", "form_type": "1099-DA"},
                        {"id": "x", "form_type": "1099-MISC"}]}
        reports = [{"id": "rpt00001", "name": "PregeneratedGainLossPDF", "year": 2021,
                    "file": {"file_type": "PDF", "url": "https://tax-center-forms-production.s3.amazonaws.com/x?sig=1"}}]
        out = site.tax_rawdocs(forms, reports)
        titles = [d.title for d in out]
        self.assertEqual(titles, ["1099-DA Tax Year 2025 (#11111111222233334444555555555555)",
                                  "1099-DA Tax Year 2025 (#aaaa1111bbbb2222cccc333344445555)",
                                  "1099-MISC Tax Year 2025", "Gain Loss Report Tax Year 2021"])
        two = reports + [dict(reports[0], id="ffff0000")]
        rt = [d.title for d in site.tax_rawdocs({}, two)]
        self.assertEqual(rt, ["Gain Loss Report Tax Year 2021 (#rpt00001)", "Gain Loss Report Tax Year 2021 (#ffff0000)"])
        same_prefix = {2025: [{"id": "form0001-1111", "form_type": "1099-DA"}, {"id": "form0001-2222", "form_type": "1099-DA"}]}
        self.assertEqual(len({d.title for d in site.tax_rawdocs(same_prefix, [])}), 2)
        for d in out:
            self.assertTrue(site._TAX_TITLE_RE.match(d.title), d.title)
            self.assertNotIn("?", d.href); self.assertNotIn("http", d.href)
            self.assertRegex(d.date_text, r"^\d{4}-12-31$")

    def test_unreadable_rows_stop_the_statement_list(self):
        first, today = "2026-09-12", date(2026, 10, 11)
        ok = [{"label": "Last 30 days", "button": "PDF", "i": 0}, {"label": "September 2026", "button": "PDF", "i": 1}]
        self.assertEqual([d.date_text for d in site._collect_statements(ListPage(ok), first, today)],
                         ["2026-09-30"])
        for bad in ([{"label": "", "button": "PDF", "i": 0}],
                    [{"label": "September 2026", "button": "PDF", "i": 0}, {"label": "September 2026", "button": "PDF", "i": 1}],
                    [{"label": "Credit Card", "button": "PDF", "i": 0}], []):
            with self.assertRaises(site.ListStopped):
                site._collect_statements(ListPage(bad), first, today)

    def test_tax_pages_that_cannot_be_exhausted_stop_the_list(self):
        answers = []
        class Resp:
            ok, status, url, headers = True, 200, "", {}
            def json(self): return answers.pop(0)
            def dispose(self): pass
        class Req:
            def get(self, url, **kw): return Resp()
        class Ctx: request = Req()
        class Page: context = Ctx()
        answers[:] = [{"data": [{"id": "a"}], "next_cursor": "c1"}, {"data": [{"id": "b"}], "next_cursor": "c1"}]
        with self.assertRaises(site.ListStopped):
            site._tax_forms_for(Page(), 2025)
        answers[:] = [{"data": [{"id": "x"}], "next_cursor": "c%d" % i} for i in range(1, 40)]
        with self.assertRaises(site.ListStopped):
            site._tax_forms_for(Page(), 2025)
        answers[:] = [{"data": [{"id": "a"}], "next_cursor": "c1"}, {"data": [{"id": "b"}], "next_cursor": ""}]
        self.assertEqual([i["id"] for i in site._tax_forms_for(Page(), 2025)], ["a", "b"])

    @staticmethod
    def _answering(*answers):
        """A page whose list calls answer these, one per call, in order."""
        left = list(answers)

        class Resp:
            ok, status, url, headers = True, 200, "", {}
            def json(self): return left.pop(0)
            def dispose(self): pass

        class Req:
            def get(self, url, **kw): return Resp()

        class Ctx: request = Req()

        class Page: context = Ctx()
        return Page()

    def test_a_form_that_names_another_year_stops_the_list(self):
        for said in ({"tax_year": 2024}, {"year": "2024"}):
            page = self._answering({"data": [dict({"id": "form0001"}, **said)], "next_cursor": ""})
            with self.assertRaises(site.ListStopped):
                site._tax_forms_for(page, 2025)
        for said in ({"tax_year": 2025}, {"year": "2025"}, {}):
            page = self._answering({"data": [dict({"id": "form0001"}, **said)], "next_cursor": ""})
            self.assertEqual([i["id"] for i in site._tax_forms_for(page, 2025)], ["form0001"])

    def test_a_forms_answer_that_is_not_a_list_of_forms_stops_the_list(self):
        for answer in ([{"id": "form0001"}], {"data": ["form0001"]}, {"data": {"id": "form0001"}}):
            with self.assertRaises(site.ListStopped):
                site._tax_forms_for(self._answering(answer), 2025)

    def test_a_reports_list_with_more_than_one_page_stops_the_list(self):
        pdf = {"id": "rpt0001", "name": "PregeneratedGainLossPDF", "year": 2024, "file": {"file_type": "PDF"}}
        csv = {"id": "rpt0002", "name": "PregeneratedGainLossCSV", "year": 2024, "file": {"file_type": "CSV"}}
        for answer in ({"data": [pdf], "next_cursor": "c1"},
                       {"data": [pdf], "pagination": {"next_uri": "/v2/tax/tax-reports?starting_after=x"}},
                       {"data": [dict(pdf, id="rpt%04d" % i) for i in range(200)]}):
            with self.assertRaises(site.ListStopped):
                site._tax_reports(self._answering(answer))
        got = site._tax_reports(self._answering({"data": [pdf, csv], "next_cursor": "",
                                                 "pagination": {"next_uri": None}}))
        self.assertEqual([r["id"] for r in got], ["rpt0001"])

    def test_documents_without_an_id_or_a_year_stop_the_list(self):
        with self.assertRaises(site.ListStopped):
            site.tax_rawdocs({2025: [{"id": "", "form_type": "1099-DA"}]}, [])
        with self.assertRaises(site.ListStopped):
            site.tax_rawdocs({2025: [{"id": "same", "form_type": "1099-DA"}, {"id": "same", "form_type": "1099-MISC"}]}, [])
        with self.assertRaises(site.ListStopped):
            site.tax_rawdocs({}, [{"id": "r1", "name": "PregeneratedGainLossPDF", "file": {"file_type": "PDF", "url": "x"}}])
        with self.assertRaises(site.ListStopped):
            site.tax_rawdocs({}, [{"id": "", "name": "PregeneratedGainLossPDF", "year": 2021}])

    def test_unreadable_year_stops_the_list(self):
        class Resp:
            ok, status, url, headers = False, 500, "", {}
            def json(self): return {}
            def dispose(self): pass
        class Req:
            def get(self, url, **kw): return Resp()
        class Ctx: request = Req()
        class Page: context = Ctx()
        with self.assertRaises(site.ListStopped):
            site._tax_forms_for(Page(), 2025)
        with self.assertRaises(site.ListStopped):
            site._tax_reports(Page())

    def _fetch(self, title, forms, document_id=""):
        """_fetch_tax_pdf over a list of these forms, with Coinbase's tax host
        answering a PDF. Returns whether it saved and what it asked for."""
        asked = []

        class Resp:
            ok, status = True, 200
            def body(self): return b"%PDF-1.4 invented"

        class Req:
            def get(self, url, **kw): asked.append(url); return Resp()

        class Ctx: request = Req()

        class Page: context = Ctx()
        with mock.patch.object(site, "_tax_forms_for", lambda p, y: forms):
            with tempfile.TemporaryDirectory() as td:
                ok = site._fetch_tax_pdf(Page(), title, Path(td) / "out.pdf", [], document_id)
        return ok, asked

    @staticmethod
    def _form(did, form_type=None):
        out = {"id": did, "files": [{"file_type": "pdf",
                                     "url": "https://tax-center-forms-production.s3.amazonaws.com/%s.pdf" % did}]}
        if form_type is not None:
            out["form_type"] = form_type
        return out

    def test_a_form_the_list_names_no_type_for_is_found_by_its_title(self):
        forms = [self._form("form0001")]
        title = site.tax_rawdocs({2025: forms}, [])[0].title
        self.assertEqual(title, "Tax Form Tax Year 2025")
        ok, asked = self._fetch(title, forms)
        self.assertTrue(ok)
        self.assertEqual(len(asked), 1)

    def test_each_tax_document_keeps_its_own_id(self):
        forms = [self._form("form0001", "1099-DA"), self._form("form0002", "1099-DA")]
        reports = [{"id": "rpt0001", "name": "PregeneratedGainLossPDF", "year": 2024,
                    "file": {"file_type": "PDF", "url": "https://tax-center-forms-production.s3.amazonaws.com/r.pdf"}}]
        got = site.tax_rawdocs({2025: forms}, reports)
        self.assertEqual([d.document_id for d in got], ["form0001", "form0002", "rpt0001"])

    def test_a_second_form_of_a_year_leaves_the_first_s_key_as_it_was(self):
        first = self._form("form0001", "1099-DA")
        alone = site.tax_rawdocs({2025: [first]}, [])[0]
        beside = site.tax_rawdocs({2025: [first, self._form("form0002", "1099-DA")]}, [])[0]
        self.assertNotEqual(alone.title, beside.title, "the first form's title takes the suffix")
        key = lambda r: docs.Document(title=r.title, category="Tax Document", date=r.date_text,
                                      document_id=r.document_id).key
        self.assertEqual(key(alone), key(beside))

    def test_a_tax_document_is_fetched_by_the_id_its_record_keeps(self):
        """The first form was listed alone, so its title has no suffix, and
        a second form of that year came since."""
        forms = [self._form("form0001", "1099-DA"), self._form("form0002", "1099-DA")]
        ok, asked = self._fetch("1099-DA Tax Year 2025", forms, document_id="form0001")
        self.assertTrue(ok)
        self.assertEqual(asked, ["https://tax-center-forms-production.s3.amazonaws.com/form0001.pdf"])
        ok, asked = self._fetch("1099-DA Tax Year 2025", forms, document_id="form0009")
        self.assertFalse(ok, "an id the list no longer names fetches nothing")
        self.assertEqual(asked, [])

    def test_a_listed_tax_document_is_recorded_and_fetched_by_its_id(self):
        app, _failures = built_app(self)
        first = self._form("form0001", "1099-DA")
        for r in site.tax_rawdocs({2025: [first]}, []):
            app._record_rawdoc(r, site.BILLING_URL)
        for r in site.tax_rawdocs({2025: [first, self._form("form0002", "1099-DA")]}, []):
            app._record_rawdoc(r, site.BILLING_URL)
        self.assertEqual(sorted(app.discovery.data), ["id:form0001", "id:form0002"])
        doc = docs.Document.from_dict(app.discovery.get("id:form0001"))
        self.assertEqual(doc.title, "1099-DA Tax Year 2025")
        handed = []

        def hands_over(page, dl_dir, iso, out_path, title="", trace=None, document_id=""):
            handed.append((title, document_id))
            return False
        with mock.patch.object(site, "goto_documents", lambda page: True), \
                mock.patch.object(site, "download_bill", hands_over), \
                mock.patch.object(docs, "write_shaped", lambda *a, **kw: None):
            app.download_one(mock.Mock(url=site.TAX_URL), doc, "2025-12-31 Coinbase 1099-DA Tax Form.pdf")
        self.assertEqual(handed, [("1099-DA Tax Year 2025", "form0001")])

    def test_tax_pdf_link_off_host_is_refused(self):
        page = object()
        orig = site._tax_forms_for
        site._tax_forms_for = lambda p, y: [{"id": "abc", "form_type": "1099-DA",
                                            "files": [{"file_type": "pdf", "url": "https://evil.example/x.pdf"}]}]
        try:
            trace = []
            with tempfile.TemporaryDirectory() as td:
                ok = site._fetch_tax_pdf(page, "1099-DA Tax Year 2025", Path(td) / "out.pdf", trace)
            self.assertFalse(ok)
            self.assertEqual(trace[-1]["note"], "document link refused")
            self.assertNotIn("url", trace[-1]); self.assertEqual(trace[-1]["host"], "evil.example")
        finally:
            site._tax_forms_for = orig


class StatementDownload(unittest.TestCase):
    """The one PDF button is pressed, through the core, and a download from
    any host but the exact one is refused."""

    ROWS = [{"label": "Last 30 days", "button": "PDF", "i": 0},
            {"label": "September 2026", "button": "PDF", "i": 1},
            {"label": "August 2026", "button": "PDF", "i": 2}]

    def make_page(self, dl_url, rows=None):
        """A statements page with these rows, whose PDF buttons, pressed,
        hand the browser one download from `dl_url`. Each button reads its
        own row's label as the page shows it (page.row_says), so a test can
        draw a row anew under a button."""
        site_ref = site
        class Download:
            url = dl_url; suggested_filename = "x.pdf"; cancelled = False; event_bytes = b"%PDF-1.4 fake"
            def cancel(self): self.cancelled = True
            def save_as(self, path): Path(path).write_bytes(self.event_bytes)
        dl = Download()
        class Resp:
            ok, status = True, 200
            def body(self): return b"%PDF-1.4 fetched"
        class Req:
            def __init__(self): self.asked = []
            def get(self, url, **kw): self.asked.append((url, kw.get("max_redirects"))); return Resp()
        class Ctx:
            def __init__(self): self.request, self.pages = Req(), []
        class El:
            """One PDF button held as an element. The core's own reads of it
            find nothing, as on a page that lets no script answer, and its
            row label is read from the page."""
            def __init__(self, page, i): self.page, self.i = page, i
            def evaluate(self, js, arg=None):
                if js == site_ref._LABEL_OF_JS:
                    return self.page.row_says.get(self.i, self.page.rows[self.i]["label"])
                return None
            def scroll_into_view_if_needed(self, **kw): pass
            def dispose(self): self.page.disposed += 1
            def click(self, **kw):
                self.page.clicked += 1
                for ev, cb in list(self.page.handlers):
                    if ev == "download":
                        for d in [dl] + self.page.also:
                            cb(d)
        class Button:
            def __init__(self, page, i): self.page, self.i = page, i
            def element_handle(self, **kw): return El(self.page, self.i)
        class Loc:
            def __init__(self, n, page): self.n, self.page = n, page
            def count(self): return self.n
            def is_visible(self): return self.n > 0
            @property
            def first(self): return self
            def nth(self, i): return Button(self.page, i)
        class Page:
            url = site_ref.STATEMENTS_URL
            main_frame = object()
            def __init__(self):
                self.clicked, self.disposed, self.handlers, self.context = 0, 0, [], Ctx()
                self.rows, self.row_says = list(rows if rows is not None else StatementDownload.ROWS), {}
                # Downloads a press hands over beside its own, and one that is
                # on its way as the capture starts listening.
                self.also, self.early = [], None
            def on(self, ev, cb):
                self.handlers.append((ev, cb))
                if ev == "download" and self.early:
                    cb(self.early)
            def remove_listener(self, ev, cb): self.handlers.remove((ev, cb))
            def locator(self, sel):
                return Loc(0, self) if sel == site_ref._LOAD_MORE else Loc(len(self.rows), self)
            def evaluate(self, js): return list(self.rows)
            def wait_for_timeout(self, ms): pass
        return Page(), dl

    S3 = "https://statements-report-persistent-production.s3.amazonaws.com/a__pdf.pdf"

    def test_off_host_download_is_refused_and_cancelled(self):
        page, dl = self.make_page("https://evil.example/statement.pdf")
        with tempfile.TemporaryDirectory() as td:
            trace = []
            ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", trace)
            self.assertFalse(ok); self.assertTrue(dl.cancelled); self.assertEqual(page.clicked, 1)
            self.assertFalse((Path(td) / "out.pdf").exists())
            self.assertEqual(trace[-1], {"note": "download refused", "host": "evil.example"})

    def test_exact_host_download_is_taken(self):
        page, dl = self.make_page(self.S3)
        with tempfile.TemporaryDirectory() as td:
            trace = []
            ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", trace)
            self.assertTrue(ok); self.assertEqual(page.clicked, 1)
            self.assertTrue((Path(td) / "out.pdf").read_bytes().startswith(b"%PDF-"))
        self.assertEqual(page.handlers, [], "every listener was removed")
        self.assertEqual(page.disposed, 1, "the held button was let go")

    def test_present_row_needs_no_load_more(self):
        page, dl = self.make_page(self.S3)
        with mock.patch.object(site.pressing, "click") as load_more:
            with tempfile.TemporaryDirectory() as td:
                ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", [])
                self.assertTrue(ok)
        self.assertEqual(load_more.call_count, 0)
        self.assertEqual(page.handlers, [], "the download listener was removed")

    def test_empty_event_file_is_not_a_document_and_nothing_else_is_taken(self):
        page, dl = self.make_page(self.S3)
        dl.event_bytes = b""
        with tempfile.TemporaryDirectory() as td:
            dl_dir = Path(td) / "dl"; dl_dir.mkdir()
            (dl_dir / "stray.pdf").write_bytes(b"%PDF-1.4 not ours")
            trace = []
            ok = site._catch_statement(page, dl_dir, "September 2026", Path(td) / "out.pdf", trace)
            self.assertFalse(ok, "an empty event file and a stranger in the folder: nothing is taken")
            self.assertFalse((Path(td) / "out.pdf").exists())
            self.assertEqual(page.context.request.asked, [], "the document is never asked for a second time")
            self.assertTrue((dl_dir / "stray.pdf").exists())

    def test_off_host_event_is_never_fetched(self):
        page, dl = self.make_page("https://evil.example/a.pdf")
        with tempfile.TemporaryDirectory() as td:
            site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", [])
            self.assertEqual(page.context.request.asked, [])

    def test_ambiguous_row_is_not_pressed(self):
        page, dl = self.make_page(self.S3, rows=[{"label": "August 2026", "button": "PDF", "i": 0},
                                                 {"label": "August 2026", "button": "PDF", "i": 1}])
        with tempfile.TemporaryDirectory() as td:
            ok = site._catch_statement(page, Path(td) / "dl", "August 2026", Path(td) / "out.pdf", [])
            self.assertFalse(ok); self.assertEqual(page.clicked, 0)

    def test_unsafe_button_label_is_not_pressed(self):
        page, dl = self.make_page(self.S3, rows=[{"label": "August 2026", "button": "Generate", "i": 0}])
        with tempfile.TemporaryDirectory() as td:
            ok = site._catch_statement(page, Path(td) / "dl", "August 2026", Path(td) / "out.pdf", [])
            self.assertFalse(ok); self.assertEqual(page.clicked, 0)

    def test_the_press_goes_through_the_core_with_the_guard_and_the_row(self):
        page, dl = self.make_page(self.S3)
        real = site.pressing.press_once
        asked = []

        def recorded(pg, el, **kw):
            asked.append(dict(kw, check_said=kw["check"]()))
            return real(pg, el, **kw)
        with mock.patch.object(site.pressing, "press_once", recorded):
            with tempfile.TemporaryDirectory() as td:
                ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", [])
        self.assertTrue(ok)
        self.assertEqual(len(asked), 1)
        self.assertIs(asked[0]["guard"], site.is_safe_control)
        self.assertEqual(asked[0]["check_said"], "", "the row reads its month at the press")
        self.assertTrue(asked[0]["brought"](), "a download that came is what the press brought")

    def test_a_row_that_reads_another_month_at_the_press_is_not_pressed(self):
        """The list was read when the button was found, and the row under it
        reads August by the press. Nothing is pressed."""
        page, dl = self.make_page(self.S3)
        page.row_says[1] = "August 2026"
        with tempfile.TemporaryDirectory() as td:
            trace = []
            ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", trace)
        self.assertFalse(ok); self.assertEqual(page.clicked, 0)
        self.assertEqual(trace[-1]["note"], "the PDF button's row reads another month now")

    def test_press_once_s_check_says_when_the_row_changed(self):
        page, dl = self.make_page(self.S3)
        said = []

        def drawn_anew_then_pressed(pg, el, **kw):
            pg.row_says[1] = "August 2026"
            said.append(kw["check"]())
            return site.pressing.Pressed(site.pressing.GONE)
        with mock.patch.object(site.pressing, "press_once", drawn_anew_then_pressed):
            with tempfile.TemporaryDirectory() as td:
                ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", [])
        self.assertFalse(ok)
        self.assertEqual(said, ["its row reads another month"])

    def test_a_turned_off_button_leaves_the_statement_and_the_run_goes_on(self):
        page, dl = self.make_page(self.S3)
        off = site.pressing.TurnedOff("press a control", "the control is turned off", ["off"])
        with mock.patch.object(site.pressing, "press_once", side_effect=off):
            with tempfile.TemporaryDirectory() as td:
                trace = []
                ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", trace)
        self.assertFalse(ok)
        self.assertIn("turned off", trace[-1]["note"])
        self.assertEqual(page.handlers, [])

    def test_something_over_the_button_stops_the_run(self):
        """A prompt drawn over the list is no challenge to
        detect_security_challenge while rows show, and the core's own look
        at the press stops the run."""
        page, dl = self.make_page(self.S3)
        covered = site.pressing.Covered("press a control", "something on the page is over the control",
                                        ["covered"], after_a_press=True)
        with mock.patch.object(site.pressing, "press_once", side_effect=covered):
            with tempfile.TemporaryDirectory() as td:
                with self.assertRaises(site.pressing.Covered):
                    site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", [])
        self.assertEqual(page.handlers, [])

    def _with_load_more(self, page, adds):
        """Give the page a Load more that shows, and rows `adds` drawn once
        it is pressed through the core."""
        real_locator = page.locator

        class LM:
            def count(s): return 1
            def is_visible(s): return True
            def is_enabled(s, **kw): return True
            @property
            def first(s): return s
            def inner_text(s, **kw): return "Load more"
            def get_attribute(s, a): return None
        page.locator = lambda sel: LM() if sel == site._LOAD_MORE else real_locator(sel)
        presses = []

        def click(pg, loc, **kw):
            presses.append(kw)
            pg.rows.extend(adds)
        return presses, click

    def test_load_more_is_pressed_through_the_core(self):
        page, dl = self.make_page(self.S3)
        presses, click = self._with_load_more(page, [{"label": "July 2026", "button": "PDF", "i": 3}])
        with mock.patch.object(site.pressing, "click", click):
            with tempfile.TemporaryDirectory() as td:
                ok = site._catch_statement(page, Path(td) / "dl", "July 2026", Path(td) / "out.pdf", [])
        self.assertTrue(ok)
        self.assertEqual(len(presses), 1)
        self.assertEqual(presses[0]["css"], site._LOAD_MORE)
        self.assertEqual(presses[0]["step"], "load more of the list")

    def test_something_over_load_more_stops_the_run(self):
        """A stop at the press of Load more is not taken for a list that
        stopped partway, which would only leave the statement for review."""
        page, dl = self.make_page(self.S3)
        self._with_load_more(page, [])
        covered = site.pressing.Covered("load more of the list", "something on the page is over the control",
                                        ["covered"])
        with mock.patch.object(site.pressing, "click", side_effect=covered):
            with tempfile.TemporaryDirectory() as td:
                with self.assertRaises(site.pressing.Covered):
                    site._catch_statement(page, Path(td) / "dl", "July 2026", Path(td) / "out.pdf", [])
        self.assertEqual(page.clicked, 0)


    def test_a_second_download_after_one_press_takes_neither(self):
        """An earlier month's download, still on its way, comes during this
        press beside this one's own. Which is which cannot be told."""
        page, dl = self.make_page(self.S3)
        _, other = self.make_page(self.S3)
        page.also = [other]
        with tempfile.TemporaryDirectory() as td:
            trace = []
            ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", trace)
            self.assertFalse(ok)
            self.assertFalse((Path(td) / "out.pdf").exists())
        self.assertTrue(dl.cancelled and other.cancelled)
        self.assertEqual(page.clicked, 1)
        self.assertEqual(trace[-1]["downloads"], 2)

    def test_a_download_on_its_way_before_the_press_presses_nothing(self):
        page, dl = self.make_page(self.S3)
        _, early = self.make_page(self.S3)
        page.early = early
        with tempfile.TemporaryDirectory() as td:
            trace = []
            ok = site._catch_statement(page, Path(td) / "dl", "September 2026", Path(td) / "out.pdf", trace)
            self.assertFalse(ok)
            self.assertFalse((Path(td) / "out.pdf").exists())
        self.assertEqual(page.clicked, 0)
        self.assertTrue(early.cancelled)
        self.assertEqual(page.handlers, [])


class StatementsPageAfresh(unittest.TestCase):
    """The statements page is loaded again before every statement's press,
    even when the tab shows it already, so no earlier month's page is left
    polling for its PDF."""

    def make_page(self):
        class Loc:
            def count(self): return 0
        class Page:
            url = site.STATEMENTS_URL
            def __init__(self): self.loads = []
            def goto(self, url, **kw): self.loads.append(url)
            def locator(self, sel): return Loc()
            def wait_for_selector(self, sel, **kw): return True
        return Page()

    def test_reload_statements_loads_the_page_it_is_on(self):
        page = self.make_page()
        self.assertTrue(site.reload_statements(page))
        self.assertEqual(page.loads, [site.STATEMENTS_URL])

    def test_goto_documents_still_leaves_a_page_that_shows_its_list(self):
        page = self.make_page()
        self.assertTrue(site.goto_documents(page))
        self.assertEqual(page.loads, [])

    def test_every_statement_is_pressed_on_a_page_loaded_for_it(self):
        page = self.make_page()
        said = []

        def caught(pg, dl_dir, label, out_path, trace):
            said.append(("pressed", label, len(pg.loads)))
            return True
        with mock.patch.object(site, "_catch_statement", caught):
            with tempfile.TemporaryDirectory() as td:
                for title, iso in (("Monthly Statement September 2026", "2026-09-30"),
                                   ("Monthly Statement August 2026", "2026-08-31")):
                    self.assertTrue(site.download_bill(page, Path(td), iso, Path(td) / "out.pdf",
                                                       title=title, trace=[]))
        self.assertEqual(said, [("pressed", "September 2026", 1), ("pressed", "August 2026", 2)])


def pdf_saying(text: str) -> bytes:
    """A one page PDF whose text is `text`, written by hand so its words
    can be read back. Every word given here is made up."""
    stream = ("BT /F1 11 Tf 40 740 Td (%s) Tj ET" % text).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def built_app(case):
    """The app as its own main builds it, over a made-up config and output
    folder that go when `case` ends, with nothing that reaches a browser.
    Returns the run and the list its failure files would be written to."""
    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    case.addCleanup(td.cleanup)
    root = Path(td.name)
    cfg = root / "config.json"
    cfg.write_text(json.dumps({"owner": "Dana Example", "output_dir": str(root / "out"),
                               "profile_dir": str(root / "profile"), "min_pdf_bytes": 100,
                               "document_types": ["Statement", "Tax Document"]}), encoding="utf-8")
    with mock.patch.object(docs.App, "_setup_logging", lambda self: None):
        app = docs.App(docs.build_parser().parse_args(["--config", str(cfg)]))
    app._dl_dir = root / ".dl"
    app.check_session = lambda page: None
    failures = []
    app.write_failure = lambda *a, **kw: failures.append(a)
    app._journal = mock.MagicMock()
    return app, failures


SEPTEMBER = "Invented monthly statement for the period September 1, 2026 to September 30, 2026"
OCTOBER = "Invented monthly statement for the period October 1, 2026 to October 31, 2026"
NO_MONTH = "Invented monthly statement of account activity and balances for this holder"


class StatementMonth(unittest.TestCase):
    """A saved statement is filed under its month only when its own text
    names that month more plainly than the month before or after it."""

    def test_the_identity_is_the_month_and_its_neighbors(self):
        mine, rivals = site.statement_identity("Monthly Statement January 2026")
        self.assertEqual((mine.period, mine.start, mine.date), ("2026-01", "2026-01-01", "2026-01-31"))
        self.assertEqual([(r.period, r.start, r.date) for r in rivals],
                         [("2025-12", "2025-12-01", "2025-12-31"), ("2026-02", "2026-02-01", "2026-02-28")])
        self.assertIsNone(site.statement_identity("1099-DA Tax Year 2025"))
        self.assertIsNone(site.statement_identity(""))

    def test_each_text_gets_its_verdict(self):
        from paperpull_core import identity
        mine, rivals = site.statement_identity("Monthly Statement September 2026")
        for text, outcome in ((SEPTEMBER, identity.VERIFIED), (OCTOBER, identity.REFUSED),
                              (NO_MONTH, identity.UNCHECKED)):
            got = identity.distinguish(None, mine, rivals, text=text)
            self.assertEqual(got.outcome, outcome, text)

    def run_download_one(self, text: str):
        """The app's own download_one for the September statement, with the
        site handing over a PDF that says `text`. Returns the run, the
        document, where it was to be saved and the failures it wrote."""
        app, failures = built_app(self)
        doc = docs.Document(title="Monthly Statement September 2026", category="Statement",
                            summary="Monthly Statement", date="2026-09-30")
        saved_to = []

        def hands_over(page, dl_dir, iso, out_path, title="", trace=None, **kw):
            Path(out_path).write_bytes(pdf_saying(text))
            saved_to.append(Path(out_path))
            return True
        with mock.patch.object(site, "goto_documents", lambda page: True), \
                mock.patch.object(site, "download_bill", hands_over):
            app.download_one(object(), doc, "2026-09-30 Coinbase Monthly Statement.pdf")
        return app, doc, saved_to[0], failures

    def test_a_statement_naming_its_month_is_filed(self):
        app, doc, out, failures = self.run_download_one(SEPTEMBER)
        rec = app.progress.get(doc.key)
        self.assertEqual(rec["state"], docs.State.COMPLETED.value)
        self.assertTrue(rec["downloaded_ok"])
        self.assertTrue(out.exists())
        self.assertEqual(failures, [])

    def test_a_statement_naming_another_month_is_not_kept(self):
        app, doc, out, failures = self.run_download_one(OCTOBER)
        rec = app.progress.get(doc.key)
        self.assertEqual(rec["state"], docs.State.NEEDS_MANUAL_REVIEW.value)
        self.assertFalse(rec.get("downloaded_ok"))
        self.assertEqual(rec["pdf_path"], "")
        self.assertIn("names another month", rec["notes"])
        self.assertFalse(out.exists(), "another month's statement was left under this month's name")
        self.assertEqual(list(app.paths.manual_review.glob("*.pdf")), [])
        self.assertFalse(app._already_done(doc), "the next run does not ask for this month again")
        self.assertEqual(failures[0][0], "check the saved statement")

    def test_a_statement_whose_month_cannot_be_told_goes_to_review(self):
        app, doc, out, failures = self.run_download_one(NO_MONTH)
        rec = app.progress.get(doc.key)
        self.assertEqual(rec["state"], docs.State.NEEDS_MANUAL_REVIEW.value)
        self.assertFalse(out.exists(), "a statement not checked was filed")
        kept = Path(rec["pdf_path"])
        self.assertEqual(kept.parent, app.paths.manual_review)
        self.assertTrue(kept.exists())
        self.assertIn("could not be checked", rec["notes"])


class DiagnosePressesNothing(unittest.TestCase):
    """Diagnose says it downloads and presses nothing. It pressed Load more
    up to sixty times, once itself and once more through the listing."""

    def test_diagnose_leaves_load_more_alone(self):
        app, _failures = built_app(self)
        # Diagnose hands the owner's name to the word list, for this run only.
        self.addCleanup(site.set_private_words, [])
        rows = [row("Last 30 days", 0), row("September 2026", 1), row("August 2026", 2)]

        class LM:
            presses = 0
            def count(s): return 1
            def is_visible(s): return True
            def is_enabled(s, **kw): return True
            @property
            def first(s): return s
            def inner_text(s, **kw): return "Load more"
            def get_attribute(s, a): return None
            def click(s, **kw): LM.presses += 1

        class Loc:
            def __init__(s, n): s.n = n
            def count(s): return s.n
            @property
            def first(s): return s

        class Page:
            url = site.STATEMENTS_URL
            def title(self): return "Statements"
            def locator(self, sel):
                if sel == site._LOAD_MORE:
                    return LM()
                return Loc(0 if "password" in sel else 3)
            def evaluate(self, js, *a): return list(rows)
            def wait_for_timeout(self, ms): pass

        def answers(page, url):
            if url.startswith(site.TAX_OWNER_API):
                return {"first_transaction_date": "2026-08-03"}
            if url.startswith(site.TAX_FORMS_API):
                return {"data": [], "next_cursor": ""}
            return {"data": []}
        app.page = lambda: Page()
        with mock.patch.object(site, "goto_documents", lambda page: True), \
                mock.patch.object(site, "survey", lambda page: {}), \
                mock.patch.object(site, "_get_json", answers), \
                mock.patch.object(site.pressing, "click") as click, \
                mock.patch.object(site.pressing, "press_once") as press_once:
            app.cmd_diagnose()
        self.assertEqual((click.call_count, press_once.call_count, LM.presses), (0, 0, 0))
        info = json.loads((app.paths.diagnostics / "diagnose-documents.json").read_text(encoding="utf-8"))
        self.assertNotIn("error", info)
        self.assertEqual(len(info["documents_recognized"]), 2, "the two month rows shown")


class Persisted(unittest.TestCase):
    def test_persistable_strips_query_and_fragment(self):
        self.assertEqual(docs.persistable("https://x.s3.amazonaws.com/a.pdf?X-Amz-Signature=abc&token=1#f"),
                         "https://x.s3.amazonaws.com/a.pdf")
        self.assertEqual(docs.persistable("/v2/tax/forms/abc?token=1"), "/v2/tax/forms/abc")
        self.assertEqual(docs.persistable(""), ""); self.assertEqual(docs.persistable(None), "")

    def test_every_stored_url_goes_through_persistable(self):
        src = (HERE / "coinbase_docs.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("persistable("), 1 + 5, "def + five call sites")
        for raw in ['href=r.href,', '"href": r.href}', 'source_url=source_url)', '{"source_url": source_url}', '"Source URL": doc.href,']:
            self.assertNotIn(raw, src, raw)

    def test_the_launchers_are_the_current_ones(self):
        """login.bat runs the app on gui/.venv when it has no venv of its
        own, as every app's does, and the Mac launchers are what the
        generator makes from the .bat files."""
        bat = (HERE / "login.bat").read_text(encoding="utf-8")
        self.assertIn(r'set "PY=..\..\gui\.venv\Scripts\python.exe"', bat)
        tool = HERE.parents[1] / "tools" / "make_unix_launchers.py"
        if not tool.exists():
            self.skipTest("a folder outside a checkout has no generator")
        import importlib.util
        import shutil
        spec = importlib.util.spec_from_file_location("make_unix_launchers", tool)
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        with tempfile.TemporaryDirectory() as td:
            app = Path(td) / "coinbase"
            app.mkdir()
            for name in ("login.bat", "setup.bat"):
                shutil.copy2(HERE / name, app)
            made = {f.name: f.read_text(encoding="utf-8") for f in gen.build(app)}
        self.assertEqual(sorted(made), ["login.command", "setup.command"])
        for name, text in made.items():
            self.assertEqual(text, (HERE / name).read_text(encoding="utf-8"), name)

    def test_its_port_is_its_own_and_named_the_same_everywhere(self):
        """The example config, the sign-in fallback and login.bat name one
        port, and no other app's example config names it."""
        from urllib.parse import urlsplit
        port = urlsplit(json.loads((HERE / "config.example.json").read_text(encoding="utf-8"))
                        ["cdp_url"]).port
        self.assertEqual(port, 9286)
        src = (HERE / "coinbase_docs.py").read_text(encoding="utf-8")
        self.assertIn('self.config.get("cdp_url", ""), "%d")' % port, src)
        self.assertIn("port %d)" % port, (HERE / "login.bat").read_text(encoding="utf-8"))
        others = []
        for cfg in sorted(HERE.parent.glob("*/config.example.json")):
            if cfg.parent == HERE:
                continue
            url = json.loads(cfg.read_text(encoding="utf-8")).get("cdp_url") or ""
            if urlsplit(url).port == port:
                others.append(cfg.parent.name)
        self.assertEqual(others, [], "another app signs in on this port")

    def test_rules_classify_coinbase_titles(self):
        from paperpull_core import doc_types
        rules = doc_types.load_rules(HERE / "document_rules.json")
        for title, cat, summ in [("1099-DA Tax Year 2025", "Tax Document", "1099-DA Tax Form"),
                                 ("1099-MISC Tax Year 2024", "Tax Document", "1099-MISC Tax Form"),
                                 ("Gain Loss Report Tax Year 2021", "Tax Document", "Gain Loss Report"),
                                 ("1042-S Tax Year 2025", "Tax Document", "1042-S Tax Form"),
                                 ("Tax Form Tax Year 2025", "Tax Document", "Tax Document"),
                                 ("Monthly Statement September 2026", "Statement", "Monthly Statement")]:
            c, s, _ = doc_types.classify_document(title, rules)
            self.assertEqual((c, s), (cat, summ), title)



# ---------------------------------------------------------------------------
# Coinbase One Card statements. Every id, token and date here is invented.
# ---------------------------------------------------------------------------

CARD_SID = "sid-invented-0001-aaaa"
CARD_OTHER_SID = "sid-invented-0002-bbbb"
CARD_ACCOUNT = "acct-invented-9999-zzzz"
CARD_TOKEN = "tokInvented77"
CARD_SIGNATURE = "SigInventedQQ"
CARD_KEYPAIR = "KPInventedWW"


def card_url(iso, token=CARD_TOKEN, host="statements.cardless.com"):
    return ("https://%s/%s_%s_Coinbase.pdf?Expires=1999999999&Signature=%s&Key-Pair-Id=%s"
            % (host, token, iso, CARD_SIGNATURE, CARD_KEYPAIR))


def card_pdf(closing="Sep 12, 2026", words="Coinbase One Card"):
    """A card statement's text as the live one reads, its period and its
    new balance, with no amount."""
    return pdf_saying("New balance as of %s Payment due by Oct 7, 2026 %s Aug 13 - %s"
                      % (closing, words, closing))


class CardResponse:
    """A graphql-direct answer as a response event hands it over. Its body
    is gone once the tab has gone on to the statement, the way a navigation
    evicts bodies, so it is readable only inside the handler."""

    def __init__(self, page, body, method="POST", op="", variables=None, url=None):
        self.page, self._body = page, body
        self.url = url or site.GRAPHQL_URL + "?operationName=x"
        self.request = mock.Mock(method=method, post_data_json={
            "operationName": op, "variables": variables or {}})
        self.status = 200
        self.headers = {"content-type": "application/json"}

    def json(self):
        if self.page.navigated:
            raise RuntimeError("Response body is unavailable for redirect responses")
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def statements_answer(dates, sids=None):
    sids = sids or ["sid-invented-%04d" % i for i in range(len(dates))]
    return {"data": {"viewer": {"creditCard": {"statements": {"edges": [
        {"node": {"statementId": s, "statementDate": d + "T00:00:00Z", "amount": "123.45"}}
        for s, d in zip(sids, dates)]}}}}}


def accounts_answer(accounts=({"id": CARD_ACCOUNT},)):
    return {"data": {"viewer": {"creditCard": {"accounts": list(accounts), "id": "x"}, "id": "y"}}}


def row_answer(url):
    return {"data": {"viewer": {"creditCard": {"statementUrl": url, "id": "x"}, "id": "y"}}}


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


def label_of(iso):
    return "%s %s" % (MONTH_NAMES[int(iso[5:7]) - 1], iso[:4])


class CardPage:
    """The statements page, its Credit Card tab, the card list it draws and
    the signed PDF address a row's PDF button sends the tab to. Spies on
    every press, every fetch and every go back."""

    def __init__(self, dates=("2026-10-12", "2026-09-12", "2026-08-12"), sids=None,
                 rows=None, links=("Transactions", "Credit Card"), lands_on="list"):
        self.url = site.STATEMENTS_URL
        self.main_frame = object()
        self.dates = list(dates)
        self.sids = list(sids or ["sid-invented-%04d" % i for i in range(len(self.dates))])
        self.rows_drawn = rows if rows is not None else [label_of(d) for d in self.dates]
        self.links = list(links)
        self.hrefs = {}            # a link's href by its text, None for none
        self.lands_on = lands_on
        self.handlers = []
        self.navigated = False
        self.clicked = []
        self.backs = 0
        self.loads = []
        self.waits = []
        self.shown = []            # the card rows, once the tab is open
        self.load_more = 0
        self.list_answers = None   # what the Credit Card tab's open answers
        self.press_answers = None  # what a PDF button's press answers, by row label
        self.press_lands = None    # where a press sends the tab, by row label
        self.popup_on_press = False
        self.download_on_press = None
        self.row_says = {}
        self.fetched = []
        self.fetch_status = 200
        self.fetch_body = card_pdf()
        page = self

        class Req:
            def get(s, url, **kw):
                page.fetched.append((url, kw.get("max_redirects")))
                return mock.Mock(status=page.fetch_status, ok=page.fetch_status == 200,
                                 body=lambda: page.fetch_body)

            def post(s, url, **kw):
                page.fetched.append(("POST", url))
                raise AssertionError("the app asked for something itself")
        self.request = Req()
        self.context = mock.Mock(request=self.request, pages=[])

    # -- events ---------------------------------------------------------
    def on(self, ev, cb): self.handlers.append((ev, cb))
    def remove_listener(self, ev, cb): self.handlers.remove((ev, cb))

    def emit(self, ev, thing):
        for e, cb in list(self.handlers):
            if e == ev:
                cb(thing)

    # -- what the tab shows --------------------------------------------
    def evaluate(self, js, *a):
        if js == site._CARD_ROWS_JS:
            if site._no_fragment(self.url) != site.CARD_LIST_URL:
                return []
            return [{"label": lab, "button": "\U000f0382 PDF", "i": 10 + i}
                    for i, lab in enumerate(self.shown)]
        return []

    def wait_for_timeout(self, ms): self.waits.append(ms)
    def wait_for_selector(self, sel, **kw): return True

    def goto(self, url, **kw):
        self.loads.append(url)
        self.url, self.navigated, self.shown = url, False, []

    def go_back(self, **kw):
        self.backs += 1
        self.url, self.navigated = site.CARD_LIST_URL, False
        self.shown = list(self.rows_drawn)

    def locator(self, sel):
        page = self
        if sel.startswith(site.CARD_LINK_CSS):
            rest = sel[len(site.CARD_LINK_CSS):]

            def fits(text):
                href = page.hrefs.get(text)
                if rest == ":not([href])":
                    return href is None
                if rest.startswith("[href='"):
                    return href == rest[len("[href='"):-2]
                return rest == ""

            class Link:
                def __init__(s, text): s.text = text
                def inner_text(s, **kw): return s.text
                def get_attribute(s, name): return page.hrefs.get(s.text)

            class Links:
                def __init__(s, texts): s.texts = texts
                def count(s): return len(s.texts)
                def nth(s, i): return Link(s.texts[i])
                def inner_text(s, **kw): return s.texts[0]
                def get_attribute(s, name): return page.hrefs.get(s.texts[0])
                def filter(s, has_text=None):
                    return Links([x for x in s.texts if has_text.search(x)])
            return Links([x for x in page.links if fits(x)])
        if sel == site._LOAD_MORE:
            return mock.Mock(count=lambda: page.load_more)
        if sel == "button":
            class Button:
                def __init__(s, i): s.i = i
                def element_handle(s, **kw): return CardButton(page, s.i - 10)
            return mock.Mock(nth=lambda i: Button(i))
        return mock.Mock(count=lambda: 0)

    # -- the Credit Card tab's press -----------------------------------
    def open_card_tab(self):
        """What pressing the Credit Card link does on the live page."""
        if self.lands_on != "list":
            self.url = self.lands_on
            return
        self.url = site.CARD_LIST_URL
        answers = self.list_answers
        if answers is None:
            answers = [CardResponse(self, {"errors": [{"extensions": {
                           "code": "PERSISTED_QUERY_NOT_FOUND"}}]}, method="GET"),
                       CardResponse(self, accounts_answer()),
                       CardResponse(self, statements_answer(self.dates, self.sids))]
        for a in answers:
            self.emit("response", a)
        self.shown = list(self.rows_drawn)

    def press_pdf(self, label):
        """What pressing a row's PDF button does on the live page: the page
        asks for the statement's address and the tab goes there."""
        iso = self.dates[[label_of(d) for d in self.dates].index(label)] \
            if label in [label_of(d) for d in self.dates] else "2026-01-12"
        sid = self.sids[self.dates.index(iso)] if iso in self.dates else "none"
        url = card_url(iso)
        answers = (self.press_answers or {}).get(label)
        if answers is None:
            answers = [CardResponse(self, {"data": None, "errors": [{"extensions": {
                           "code": "PERSISTED_QUERY_NOT_FOUND"}}]}, method="GET",
                           op="CreditCardStatementRowQuery", variables={"statementId": sid}),
                       CardResponse(self, row_answer(url), op="CreditCardStatementRowQuery",
                                    variables={"accountUuid": CARD_ACCOUNT, "statementId": sid})]
        for a in answers:
            self.emit("response", a)
        if self.popup_on_press:
            self.emit("popup", mock.Mock())
        if self.download_on_press is not None:
            self.emit("download", self.download_on_press)
        lands = (self.press_lands or {}).get(label, url)
        if lands:
            self.url, self.navigated, self.shown = lands, True, []


class CardButton:
    """One card row's PDF button, held as an element. The core's own reads
    of it find nothing, as on a page that lets no script answer."""

    def __init__(self, page, i): self.page, self.i = page, i

    def evaluate(self, js, arg=None):
        if js == site._CARD_LABEL_OF_JS:
            return self.page.row_says.get(self.i, self.page.shown[self.i]
                                          if 0 <= self.i < len(self.page.shown) else "")
        return None

    def scroll_into_view_if_needed(self, **kw): pass
    def dispose(self): pass

    def click(self, **kw):
        label = self.page.shown[self.i]
        self.page.clicked.append(label)
        self.page.press_pdf(label)


def clicks_the_tab(page, loc, **kw):
    """pressing.click, as the Credit Card link answers it."""
    page.clicked.append(("link", loc.inner_text(), kw["css"], kw["step"]))
    page.open_card_tab()


class CardHosts(unittest.TestCase):
    """statements.cardless.com is taken only as the address the page was
    handed, and is on none of the app's own host lists."""

    def test_the_card_statement_address(self):
        self.assertTrue(site.is_card_statement_url(card_url("2026-09-12"), "2026-09-12"))
        for url, iso in [(card_url("2026-09-12"), "2026-08-12"),
                         (card_url("2026-09-12", host="evil.example"), "2026-09-12"),
                         (card_url("2026-09-12", host="statements.cardless.com.evil.test"), "2026-09-12"),
                         (card_url("2026-09-12", host="x.statements.cardless.com"), "2026-09-12"),
                         (card_url("2026-09-12").replace("https:", "http:"), "2026-09-12"),
                         (card_url("2026-09-12").replace(".com/", ".com:8443/"), "2026-09-12"),
                         (card_url("2026-09-12").replace("https://", "https://u@"), "2026-09-12"),
                         (card_url("2026-09-12").replace("_Coinbase.pdf", "_Other.pdf"), "2026-09-12"),
                         (card_url("2026-09-12").replace(CARD_TOKEN, "a/b"), "2026-09-12"),
                         ("https://statements.cardless.com/2026-09-12_Coinbase.pdf", "2026-09-12"),
                         ("", "2026-09-12"), (None, "2026-09-12"), (card_url("2026-09-12"), "")]:
            self.assertFalse(site.is_card_statement_url(url, iso), (url, iso))

    def test_the_card_host_is_on_no_host_list_of_the_app(self):
        url = card_url("2026-09-12")
        self.assertFalse(site.is_safe_url(url))
        self.assertFalse(site.is_list_url(url))
        self.assertFalse(site.is_download_url(url))
        self.assertNotIn("statements.cardless.com", site.ALLOWED_HOSTS | site.DOWNLOAD_HOSTS)

    def test_the_app_never_asks_graphql_direct_itself(self):
        self.assertFalse(site.is_list_url(site.GRAPHQL_URL))
        self.assertFalse(site.is_list_url(site.CARD_LIST_URL))
        with self.assertRaises(RuntimeError):
            site._get_json(object(), site.GRAPHQL_URL)

    def test_the_card_pdf_button_label(self):
        for label in ["PDF", "\U000f0382 PDF", "PDF", " PDF "]:
            self.assertTrue(site.is_card_pdf_label(label), repr(label))
        for label in ["Download PDF", "PDF CSV", "Generate", "CSV", "HTML", "", None,
                      "Credit Card", "\U000f0382", "PDF | Generate"]:
            self.assertFalse(site.is_card_pdf_label(label), repr(label))

    def test_the_closing_date_is_read_the_way_the_statement_prints_it(self):
        rx = site.closing_date_re("2026-09-12")
        for text in ["as of Sep 12, 2026", "Aug 13 - Sept 12, 2026", "September 12, 2026"]:
            self.assertTrue(rx.search(text), text)
        for text in ["Sep 13, 2026", "Sep 12, 2025", "Oct 12, 2026", "Sep 1, 2026", "Sep 122, 2026"]:
            self.assertFalse(rx.search(text), text)


class CardList(unittest.TestCase):
    """The card list is the page's own answer, heard, matched row for row."""

    def collect(self, page):
        with mock.patch.object(site.pressing, "click", clicks_the_tab):
            return site._collect_card(page)

    def test_the_list_heard_is_one_document_per_statement(self):
        page = CardPage()
        got = self.collect(page)
        self.assertEqual([(d.title, d.date_text) for d in got], [
            ("Coinbase One Card Statement October 2026", "2026-10-12"),
            ("Coinbase One Card Statement September 2026", "2026-09-12"),
            ("Coinbase One Card Statement August 2026", "2026-08-12")])
        self.assertEqual(page.clicked, [("link", "Credit Card", site.CARD_LINK_CSS + ":not([href])",
                                         "open the credit card statements")])
        self.assertEqual(page.handlers, [], "the listener was removed")
        self.assertEqual(page.fetched, [], "nothing was asked for")
        for d in got:
            self.assertEqual(d.document_id, "", "a statement id is never kept")
            self.assertNotIn("sid-", repr(d))

    def test_a_count_that_disagrees_stops_the_list(self):
        page = CardPage(rows=[label_of("2026-10-12"), label_of("2026-09-12")])
        with self.assertRaises(site.ListStopped) as stop:
            self.collect(page)
        self.assertIn("2", str(stop.exception))
        self.assertEqual(stop.exception.docs, [])

    def test_months_that_disagree_stop_the_list(self):
        page = CardPage(rows=[label_of("2026-10-12"), label_of("2026-09-12"), "July 2026"])
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_a_row_without_a_month_or_a_month_twice_stops_the_list(self):
        for rows in ([label_of("2026-10-12"), label_of("2026-09-12"), ""],
                     [label_of("2026-10-12"), label_of("2026-10-12"), label_of("2026-08-12")]):
            with self.assertRaises(site.ListStopped):
                self.collect(CardPage(rows=rows))

    def test_an_answer_not_heard_stops_the_list(self):
        page = CardPage()
        page.list_answers = [CardResponse(page, {"errors": [{"extensions": {
            "code": "PERSISTED_QUERY_NOT_FOUND"}}]}, method="GET")]
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_a_get_answer_is_not_heard(self):
        page = CardPage()
        page.list_answers = [CardResponse(page, statements_answer(page.dates), method="GET")]
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_an_answer_from_another_address_is_not_heard(self):
        page = CardPage()
        page.list_answers = [CardResponse(page, statements_answer(page.dates),
                                          url="https://accounts.coinbase.com/graphql/query")]
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_two_lists_that_differ_stop_the_list(self):
        page = CardPage()
        page.list_answers = [CardResponse(page, statements_answer(page.dates)),
                             CardResponse(page, statements_answer(page.dates[:2]))]
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_a_statement_the_list_cannot_read_stops_the_list(self):
        page = CardPage()
        for bad in ({"statementId": "", "statementDate": "2026-09-12T00:00:00Z"},
                    {"statementId": "s1", "statementDate": "2026-13-12T00:00:00Z"},
                    {"statementId": "s1"}, {"statementId": 7, "statementDate": "2026-09-12T00:00:00Z"}):
            answer = {"data": {"viewer": {"creditCard": {"statements": {"edges": [{"node": bad}]}}}}}
            page = CardPage(dates=["2026-09-12"])
            page.list_answers = [CardResponse(page, answer)]
            with self.assertRaises(site.ListStopped):
                self.collect(page)

    def test_no_credit_card_tab_lists_nothing(self):
        page = CardPage(links=("Transactions",))
        self.assertEqual(self.collect(page), [])
        self.assertEqual(page.clicked, [])

    def test_two_credit_card_tabs_stop_the_list(self):
        with self.assertRaises(site.ListStopped):
            self.collect(CardPage(links=("Credit Card", "Credit Card")))

    def test_a_tab_that_opens_another_page_stops_the_list(self):
        """Checked after the press as well as before it, and a tab that took
        the page anywhere else stops the run rather than the list."""
        page = CardPage(lands_on="https://accounts.coinbase.com/statements/other")
        with self.assertRaises(site.pressing.Stop) as stop:
            self.collect(page)
        self.assertTrue(stop.exception.after_a_press)
        self.assertEqual(page.handlers, [])

    def test_a_credit_card_link_to_another_address_is_not_the_tab(self):
        """The product page's own "Credit Card" link has the address
        /creditcard. Only the sub-navigation's, with no address or the card
        list's, is the tab."""
        self.assertTrue(site.is_card_tab("Credit Card", None))
        self.assertTrue(site.is_card_tab(" Credit  Card ", ""))
        self.assertTrue(site.is_card_tab("Credit Card", "/statements/credit-card"))
        self.assertTrue(site.is_card_tab("Credit Card", site.CARD_LIST_URL))
        for text, href in (("Credit Card", "/creditcard"), ("Credit Card", "https://evil.example/"),
                           ("Credit Cards", None), ("Transactions", None), ("", None),
                           ("Apply for Credit Card", None)):
            self.assertFalse(site.is_card_tab(text, href), (text, href))
        page = CardPage()
        page.hrefs = {"Credit Card": "/creditcard"}
        self.assertEqual(self.collect(page), [])
        self.assertEqual(page.clicked, [], "a Credit Card link to another address is not pressed")
        page = CardPage()
        page.hrefs = {"Credit Card": "/statements/credit-card"}
        self.assertEqual(len(self.collect(page)), 3)
        self.assertEqual(page.clicked[0][2], site.CARD_LINK_CSS + "[href='/statements/credit-card']",
                         "pressed through a selector only that link fits")

    def test_the_tab_is_looked_for_only_on_the_statements_page(self):
        page = CardPage()
        page.url = "https://accounts.coinbase.com/settings"
        with self.assertRaises(site.ListStopped):
            self.collect(page)
        self.assertEqual(page.clicked, [])

    def test_a_tab_the_press_cannot_find_once_alone_is_not_pressed(self):
        """Found by its text and address, then found again for the press
        by a selector only it fits and its exact text. Anything but one
        there, and nothing is pressed."""
        page = CardPage(links=("Transactions", "Credit Card", "Credit Card "))
        page.hrefs = {"Credit Card ": "/statements/credit-card"}
        with self.assertRaises(site.ListStopped):
            self.collect(page)
        self.assertEqual(page.clicked, [])

    def test_an_account_without_a_card_lists_nothing(self):
        for answer in ({"data": {"viewer": {"creditCard": None}}}, accounts_answer(())):
            page = CardPage(dates=[], rows=[])
            page.list_answers = [CardResponse(page, answer)]
            self.assertEqual(self.collect(page), [], answer)

    def test_no_card_is_believed_only_from_a_clean_answer(self):
        for status, body in ((500, {"data": {"viewer": {"creditCard": None}}}),
                             (200, {"data": {"viewer": {"creditCard": None}}, "errors": [{"m": 1}]}),
                             (200, ValueError("not json"))):
            page = CardPage(dates=[], rows=[])
            answer = CardResponse(page, body)
            answer.status = status
            page.list_answers = [answer]
            with self.assertRaises(site.ListStopped, msg=(status, body)):
                self.collect(page)
        page = CardPage(dates=[], rows=[])
        page.list_answers = [CardResponse(page, ValueError("not json")),
                             CardResponse(page, {"data": {"viewer": {"creditCard": None}}})]
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_an_empty_list_heard_is_empty(self):
        page = CardPage(dates=[], rows=[])
        self.assertEqual(self.collect(page), [])

    def test_a_load_more_on_the_card_list_stops_it(self):
        page = CardPage()
        page.load_more = 1
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_the_list_answered_twice_stops_even_when_the_same(self):
        page = CardPage()
        page.list_answers = [CardResponse(page, statements_answer(page.dates, page.sids)),
                             CardResponse(page, statements_answer(page.dates, page.sids))]
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_a_list_answer_that_is_not_ok_stops_the_list(self):
        for status, body in ((500, statements_answer(["2026-09-12"])),
                             (200, dict(statements_answer(["2026-09-12"]), errors=[{"message": "x"}]))):
            page = CardPage(dates=["2026-09-12"])
            answer = CardResponse(page, body)
            answer.status = status
            page.list_answers = [answer]
            with self.assertRaises(site.ListStopped):
                self.collect(page)

    def test_an_answer_that_cannot_be_read_beside_the_list_stops_it(self):
        page = CardPage()
        page.list_answers = [CardResponse(page, ValueError("not json")),
                             CardResponse(page, statements_answer(page.dates, page.sids))]
        with self.assertRaises(site.ListStopped):
            self.collect(page)

    def test_the_list_says_whether_it_has_another_page(self):
        def with_page_info(info):
            answer = statements_answer(["2026-10-12", "2026-09-12"])
            answer["data"]["viewer"]["creditCard"]["statements"]["pageInfo"] = info
            return answer
        for info, whole in (({"hasNextPage": False}, True), ({"hasNextPage": True}, False),
                            ({}, False), ("no", False), ({"hasNextPage": None}, False)):
            page = CardPage(dates=["2026-10-12", "2026-09-12"])
            page.list_answers = [CardResponse(page, with_page_info(info))]
            if whole:
                self.assertEqual(len(self.collect(page)), 2, info)
            else:
                with self.assertRaises(site.ListStopped, msg=info):
                    self.collect(page)

    def test_a_list_without_a_page_info_has_to_miss_no_month(self):
        page = CardPage(dates=["2026-10-12", "2026-08-12"])
        with self.assertRaises(site.ListStopped):
            self.collect(page)
        page = CardPage(dates=["2026-01-12", "2025-12-12"])
        self.assertEqual(len(self.collect(page)), 2, "a year's turn is no gap")

    def test_card_statements_come_after_the_other_families(self):
        page = CardPage()
        monthly = [site.RawDoc(title="Monthly Statement September 2026", date_text="2026-09-30")]
        tax = [site.RawDoc(title="1099-DA Tax Year 2025", date_text="2025-12-31", kind="tax")]
        card = [site.RawDoc(title="Coinbase One Card Statement September 2026", date_text="2026-09-12")]
        with mock.patch.object(site, "goto_documents", lambda p: True), \
                mock.patch.object(site, "_first_transaction", lambda p: "2026-08-01"), \
                mock.patch.object(site, "_collect_statements", lambda p, f: list(monthly)), \
                mock.patch.object(site, "_collect_tax", lambda p, f: list(tax)), \
                mock.patch.object(site, "_collect_card", lambda p: list(card)):
            self.assertEqual(site.collect_download_docs(page), monthly + tax + card)

            def stops(p):
                raise site.ListStopped("the card list stopped", card[:0])
            with mock.patch.object(site, "_collect_card", stops):
                with self.assertRaises(site.ListStopped) as stopped:
                    site.collect_download_docs(page)
            self.assertEqual(stopped.exception.docs, monthly + tax,
                             "what the other families listed is kept")


class CardDownload(unittest.TestCase):
    """One card statement: the row's own PDF button, the address the page
    was handed, fetched in the tab, checked, and back to the list."""

    ISO = "2026-09-12"
    LABEL = "September 2026"

    def stopped(self, page, iso=ISO, label=LABEL):
        """The take, which has to stop the run, with nothing fetched or
        saved, every listener gone and the tab back on the list."""
        with self.assertRaises(site.pressing.Stop) as stop:
            self.take(page, iso, label)
        self.assertTrue(stop.exception.after_a_press)
        self.assertEqual(page.fetched, [])
        self.assertEqual(page.handlers, [])
        self.assertEqual(site._no_fragment(page.url), site.CARD_LIST_URL)
        return stop.exception

    def take(self, page, iso=ISO, label=LABEL):
        trace = []
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.pdf"
            with mock.patch.object(site.pressing, "click", clicks_the_tab):
                ok = site._take_card_statement(page, label, iso, out, trace)
            saved = out.read_bytes() if out.exists() else None
        return ok, saved, trace

    def test_the_statement_is_taken_from_the_address_the_page_was_handed(self):
        page = CardPage()
        ok, saved, trace = self.take(page)
        self.assertTrue(ok, trace)
        self.assertTrue(saved.startswith(b"%PDF-"))
        self.assertIn(self.LABEL, page.clicked, "the row's own PDF button was pressed")
        self.assertEqual([c for c in page.clicked if c == self.LABEL], [self.LABEL])
        self.assertEqual(page.fetched, [(card_url(self.ISO), 0)],
                         "the exact address, once, no redirect followed")
        self.assertEqual(page.backs, 1, "the tab went back to the list")
        self.assertEqual(site._no_fragment(page.url), site.CARD_LIST_URL)
        self.assertEqual(page.handlers, [], "every listener was removed")

    def test_the_answer_is_read_before_the_tab_moves_on(self):
        """The body of the page's answer is gone once the tab is at the
        statement, so it has to have been read as it came."""
        page = CardPage()
        ok, _saved, trace = self.take(page)
        self.assertTrue(ok, trace)
        self.assertTrue(page.navigated is False, "back on the list")

    def test_a_press_that_asks_for_another_statement_takes_nothing(self):
        page = CardPage()
        page.press_answers = {self.LABEL: [CardResponse(
            page, row_answer(card_url(self.ISO)), op="CreditCardStatementRowQuery",
            variables={"statementId": CARD_OTHER_SID})]}
        self.assertIn("another statement", self.stopped(page).reason)

    def test_an_address_on_another_host_is_not_asked(self):
        page = CardPage()
        bad = card_url(self.ISO, host="evil.example")
        page.press_answers = {self.LABEL: [CardResponse(
            page, row_answer(bad), op="CreditCardStatementRowQuery",
            variables={"statementId": page.sids[1]})]}
        page.press_lands = {self.LABEL: bad}
        self.assertIn("another host", self.stopped(page).reason)

    def test_an_address_of_another_date_is_not_asked(self):
        page = CardPage()
        other = card_url("2026-08-12")
        page.press_answers = {self.LABEL: [CardResponse(
            page, row_answer(other), op="CreditCardStatementRowQuery",
            variables={"statementId": page.sids[1]})]}
        page.press_lands = {self.LABEL: other}
        self.assertIn("another date", self.stopped(page).reason)

    def test_a_tab_that_lands_elsewhere_takes_nothing(self):
        for lands in ("https://accounts.coinbase.com/statements/credit-card/other",
                      card_url(self.ISO, token="tokOther")):
            page = CardPage()
            page.press_lands = {self.LABEL: lands}
            self.assertIn("not at the address", self.stopped(page).reason, lands)

    def test_a_tab_that_never_moves_takes_nothing(self):
        page = CardPage()
        page.press_lands = {self.LABEL: ""}
        self.assertIn("not at the address", self.stopped(page).reason)

    def test_two_answers_to_one_press_take_nothing(self):
        page = CardPage()
        sid = page.sids[1]
        page.press_answers = {self.LABEL: [
            CardResponse(page, row_answer(card_url(self.ISO)), op="CreditCardStatementRowQuery",
                         variables={"statementId": sid}),
            CardResponse(page, row_answer(card_url(self.ISO, token="tokTwo")),
                         op="CreditCardStatementRowQuery", variables={"statementId": sid})]}
        self.assertIn("more than once", self.stopped(page).reason)

    def test_an_answer_that_does_not_name_the_statement_stops_the_run(self):
        """The row was pressed, and without one clean answer naming its
        address the press cannot be bound to its statement."""
        def answered(page, status=200, body=None):
            answer = CardResponse(page, body, op="CreditCardStatementRowQuery",
                                  variables={"statementId": page.sids[1]})
            answer.status = status
            return [answer]
        for status, body in ((200, RuntimeError("no body")), (500, row_answer(card_url(self.ISO))),
                             (200, dict(row_answer(card_url(self.ISO)), errors=[{"m": 1}])),
                             (200, {"data": {"viewer": {"creditCard": {"id": "x"}}}}),
                             (200, {"data": {"viewer": {"creditCard": {"statementUrl": 7}}}})):
            page = CardPage()
            page.press_answers = {self.LABEL: answered(page, status, body)}
            self.stopped(page)
        page = CardPage()
        page.press_answers = {self.LABEL: []}
        self.assertIn("did not answer", self.stopped(page).reason)

    def test_html_where_the_pdf_should_be_is_not_saved(self):
        page = CardPage()
        page.fetch_body = b"<!doctype html><html><body>viewer</body></html>"
        ok, saved, trace = self.take(page)
        self.assertFalse(ok)
        self.assertIsNone(saved)

    def test_a_pdf_that_is_not_this_statement_is_not_saved(self):
        for body in (card_pdf(words="Coinbase Card"), card_pdf(closing="Aug 12, 2026")):
            page = CardPage()
            page.fetch_body = body
            ok, saved, trace = self.take(page)
            self.assertFalse(ok)
            self.assertIsNone(saved)

    def test_an_answer_that_is_not_ok_is_not_saved(self):
        page = CardPage()
        page.fetch_status = 302
        ok, saved, trace = self.take(page)
        self.assertFalse(ok)
        self.assertIsNone(saved)

    def test_a_popup_takes_nothing(self):
        page = CardPage()
        page.popup_on_press = True
        self.assertIn("popup", self.stopped(page).reason)

    def test_a_download_takes_nothing_and_is_cancelled(self):
        page = CardPage()
        dl = mock.Mock()
        page.download_on_press = dl
        self.assertIn("download", self.stopped(page).reason)
        dl.cancel.assert_called_once_with()

    def test_a_statement_the_list_no_longer_names_stops_the_run(self):
        page = CardPage()
        with self.assertRaises(site.pressing.Stop) as stop:
            self.take(page, iso="2026-07-12", label="July 2026")
        self.assertFalse(stop.exception.after_a_press)
        self.assertEqual([c for c in page.clicked if isinstance(c, str)], [])

    def test_a_list_that_cannot_be_read_again_stops_the_run(self):
        """Listed whole at discovery, and read again for the statement. A
        list that is not whole now stops the run, rather than leave this
        statement for review and go on to the next."""
        page = CardPage(rows=[label_of("2026-10-12"), label_of("2026-09-12")])
        with self.assertRaises(site.pressing.Stop) as stop:
            self.take(page)
        self.assertIn("card statements", stop.exception.lines[0])
        self.assertEqual([c for c in page.clicked if isinstance(c, str)], [])
        page = CardPage(links=("Transactions",))
        with self.assertRaises(site.pressing.Stop):
            self.take(page)

    def test_a_row_that_reads_another_month_at_the_press_is_not_pressed(self):
        page = CardPage()
        page.row_says[1] = "August 2026"
        ok, saved, trace = self.take(page)
        self.assertFalse(ok)
        self.assertEqual([c for c in page.clicked if isinstance(c, str)], [])

    def test_a_turned_off_button_leaves_the_statement(self):
        page = CardPage()
        off = site.pressing.TurnedOff("press a control", "the control is turned off", ["off"])
        with mock.patch.object(site.pressing, "press_once", side_effect=off):
            ok, saved, trace = self.take(page)
        self.assertFalse(ok)
        self.assertIn("turned off", " ".join(t.get("note", "") for t in trace))
        self.assertEqual(page.handlers, [])

    def test_the_press_goes_through_the_core_with_its_own_guard(self):
        page = CardPage()
        real = site.pressing.press_once
        asked = []

        def recorded(pg, el, **kw):
            asked.append(dict(kw, check_said=kw["check"]()))
            return real(pg, el, **kw)
        with mock.patch.object(site.pressing, "press_once", recorded):
            ok, saved, trace = self.take(page)
        self.assertTrue(ok, trace)
        self.assertEqual(len(asked), 1)
        self.assertIs(asked[0]["guard"], site.is_card_pdf_label)
        self.assertEqual(asked[0]["check_said"], "")

    def test_a_list_that_does_not_come_back_loads_the_statements_page(self):
        page = CardPage()
        page.go_back = lambda **kw: setattr(page, "backs", page.backs + 1)
        ok, saved, trace = self.take(page)
        self.assertTrue(ok, "the statement checked is kept")
        self.assertEqual(page.loads, [site.STATEMENTS_URL], "the tab is not left at the address")
        self.assertIn("the card list did not show again", " ".join(t.get("note", "") for t in trace))

    def test_nothing_private_reaches_the_trace_or_the_log(self):
        page = CardPage(sids=[CARD_OTHER_SID, CARD_SID, "sid-invented-0003-cccc"])
        with self.assertLogs("coinbase_docs.site", level="DEBUG") as logs:
            site.log.debug("start")
            ok, saved, trace = self.take(page)
            page2 = CardPage(sids=[CARD_OTHER_SID, CARD_SID, "sid-invented-0003-cccc"])
            page2.fetch_body = b"<html></html>"
            self.take(page2)
        self.assertTrue(ok)
        said = json.dumps(trace) + "\n".join(logs.output)
        for secret in (CARD_SID, CARD_OTHER_SID, CARD_ACCOUNT, CARD_TOKEN, CARD_SIGNATURE,
                       CARD_KEYPAIR, "Expires", "123.45", "cardless.com/"):
            self.assertNotIn(secret, said, secret)

    def test_download_bill_takes_a_card_statement_on_a_page_loaded_for_it(self):
        page = CardPage()
        page.url = site.CARD_LIST_URL
        said = []

        def taken(pg, label, iso, out_path, trace):
            said.append((label, iso, list(pg.loads)))
            return True
        with mock.patch.object(site, "_take_card_statement", taken):
            with tempfile.TemporaryDirectory() as td:
                self.assertTrue(site.download_bill(
                    page, Path(td), self.ISO, Path(td) / "o.pdf",
                    title="Coinbase One Card Statement September 2026", trace=[]))
                self.assertFalse(site.download_bill(
                    page, Path(td), "2026-08-12", Path(td) / "o.pdf",
                    title="Coinbase One Card Statement September 2026", trace=[]))
        self.assertEqual(said, [(self.LABEL, self.ISO, [site.STATEMENTS_URL])])


class CardRun(unittest.TestCase):
    """What the run does with a card statement, through the app's own
    process and download_one."""

    def docs_for(self, app, *isos):
        out = []
        for iso in isos:
            out.append(docs.Document(title="Coinbase One Card Statement %s" % label_of(iso),
                                     category="Statement", summary="One Card Statement", date=iso))
        return out

    def test_a_stop_after_a_press_presses_no_second_statement(self):
        app, _failures = built_app(self)
        asked = []

        def stops(page, dl_dir, iso, out_path, title="", trace=None, **kw):
            asked.append(iso)
            raise site._left_the_list("take a card statement", "a popup came")
        app._on_its_site = lambda: object()
        app._delay = lambda *a: None
        with mock.patch.object(site, "goto_documents", lambda page: True), \
                mock.patch.object(site, "download_bill", stops):
            with self.assertRaises(site.pressing.Stop):
                app.process(self.docs_for(app, "2026-10-12", "2026-09-12"))
        self.assertEqual(asked, ["2026-10-12"], "the run went on to the next statement")

    def test_a_failed_attempt_names_no_signed_address(self):
        app, _failures = built_app(self)
        doc = self.docs_for(app, "2026-09-12")[0]
        page = mock.Mock(url=card_url("2026-09-12"))
        with mock.patch.object(site, "goto_documents", lambda page: True), \
                mock.patch.object(site, "download_bill", lambda *a, **kw: False):
            app.download_one(page, doc, "2026-09-12 Coinbase One Card Statement.pdf")
        said = (app.paths.diagnostics / "download-attempt.json").read_text(encoding="utf-8")
        for secret in (CARD_TOKEN, CARD_SIGNATURE, CARD_KEYPAIR, "Expires", "_Coinbase.pdf"):
            self.assertNotIn(secret, said, secret)
        self.assertIn("statements.cardless.com", said.replace("\\", ""))

    def test_where_landed_keeps_coinbase_s_own_address(self):
        self.assertEqual(site.where_landed(mock.Mock(url=site.CARD_LIST_URL)),
                         site.redact(site.CARD_LIST_URL))
        self.assertEqual(site.where_landed(mock.Mock(url=card_url("2026-09-12"))),
                         "statements.cardless.com")
        self.assertEqual(site.where_landed(mock.Mock(url="")), "")


class CardRecords(unittest.TestCase):
    def test_rules_and_file_name(self):
        from paperpull_core import doc_types
        rules = doc_types.load_rules(HERE / "document_rules.json")
        c, s, _ = doc_types.classify_document("Coinbase One Card Statement September 2026", rules)
        self.assertEqual((c, s), ("Statement", "One Card Statement"))
        self.assertEqual(docs.build_pdf_filename("2026-09-12", s, ""),
                         "2026-09-12 Coinbase One Card Statement.pdf")

    def test_a_card_statement_is_recorded_without_its_ids(self):
        app, _failures = built_app(self)
        page = CardPage(sids=[CARD_OTHER_SID, CARD_SID, "sid-invented-0003-cccc"])
        with mock.patch.object(site.pressing, "click", clicks_the_tab):
            for r in site._collect_card(page):
                app._record_rawdoc(r, site.BILLING_URL)
        app.discovery.save()
        self.assertEqual(sorted(app.discovery.data), [
            "Statement:2026-08-12:Coinbase One Card Statement August 2026:Unnamed",
            "Statement:2026-09-12:Coinbase One Card Statement September 2026:Unnamed",
            "Statement:2026-10-12:Coinbase One Card Statement October 2026:Unnamed"])
        stored = app.paths.discovery_json.read_text(encoding="utf-8")
        for secret in (CARD_SID, CARD_OTHER_SID, CARD_ACCOUNT, "cardless"):
            self.assertNotIn(secret, stored)

    def test_a_card_statement_is_filed_under_its_closing_date(self):
        app, failures = built_app(self)
        doc = docs.Document(title="Coinbase One Card Statement September 2026", category="Statement",
                            summary="One Card Statement", date="2026-09-12")

        def hands_over(page, dl_dir, iso, out_path, title="", trace=None, **kw):
            Path(out_path).write_bytes(card_pdf())
            return True
        with mock.patch.object(site, "goto_documents", lambda page: True), \
                mock.patch.object(site, "download_bill", hands_over):
            app.download_one(object(), doc, "2026-09-12 Coinbase One Card Statement.pdf")
        rec = app.progress.get(doc.key)
        self.assertEqual(rec["state"], docs.State.COMPLETED.value, rec.get("notes"))
        self.assertEqual(Path(rec["pdf_path"]).name, "2026-09-12 Coinbase One Card Statement.pdf")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
