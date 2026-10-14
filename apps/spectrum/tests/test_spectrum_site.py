"""The checks that have to pass before this app touches a real account.
No browser, no network, invented values only."""
import ast
import base64
import io
import json
import logging
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import spectrum_site as site  # noqa: E402
import spectrum_docs as docs  # noqa: E402
from paperpull_core import pressing  # noqa: E402

HOST = "www.spectrum.net"
GRAPH = site.GRAPH_URL
# Invented account details, which must never leave through a log, the
# console, a record, a file name or a diagnostic.
CUR = "8350990000012345"
PRI = "8350990000067890"
STREET = "Quuxberry"
ADDRESS = "4321 Quuxberry Lane Apt 9B"


def text_pdf(lines) -> bytes:
    """A real one-page PDF whose text pypdf reads back, lines top to bottom."""
    ops = "BT /F1 11 Tf 72 740 Td 14 TL " + " ".join(
        "(%s) Tj T*" % str(t).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for t in lines) + " ET"
    stream = ops.encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def short(day):
    y, m, d = day.split("-")
    return "%s/%s/%s" % (m, d, y[2:])


def spectrum_pdf(day, *extra):
    """An invented Spectrum statement for `day`: its date, a service period
    and a total, with any extra lines."""
    return text_pdf(["Spectrum", "Statement Date %s" % short(day), "Service From %s" % short(day),
                     "Amount Due $80.00 Thank you for choosing us", *extra])


PDF = spectrum_pdf("2026-09-26")
OTHER_PDF = spectrum_pdf("2026-09-26", "another copy")
BLOB_URL = f"blob:https://{HOST}/0b1e-4c2d"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
CUR_DATES = ["2026-09-26", "2026-08-26", "2026-02-26"]
PRI_DATES = ["2026-02-26", "2026-01-26"]


def sid(account, day, kind="PDF_DOCUMENT", code="1"):
    return base64.b64encode(f"Account={account}|StatementDate={day}|StatementCode={code}|{kind}".encode()).decode()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def list_answer(cur=CUR_DATES, prior=PRI_DATES, prior_account=PRI):
    transfers = []
    if prior is not None:
        transfers.append({"accountNumber": prior_account, "serviceAddress": {"address1": ADDRESS},
                          "statements": [{"id": sid(prior_account, d), "date": d + "T00:00:00-05:00"} for d in prior]})
    return {"data": {"viewer": {"account": {"statementList": {
        "statements": [{"id": sid(CUR, d), "date": d + "T00:00:00-05:00"} for d in cur],
        "accountTransfers": transfers, "icoms48HourDelayShowMessage": False}}}}}


def row_text(day):
    y, m, d = day.split("-")
    return f"Statement {MONTHS[int(m) - 1]} {int(d)}, {y} {ADDRESS}"


def pdf_answer(data=PDF):
    return {"data": {"viewer": {"account": {"statementPdf": b64(data)}}}}


class Resp:
    def __init__(self, op, variables=None, status=200, answer=None, url=GRAPH, method="POST"):
        body = {"operationName": op, "query": "query x", "variables": variables or {}}
        self.request = SimpleNamespace(url=url, method=method, post_data=json.dumps(body))
        self.status = status
        self._answer = answer if answer is not None else {"data": {}}

    def json(self):
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def blob_result(data=PDF, blobs=1, kind="application/pdf", url=BLOB_URL, opened=None):
    return {"blobs": [{"type": kind, "size": len(data), "url": url}] * blobs,
            "opened": [url] if opened is None else opened, "b64": b64(data) if blobs == 1 else ""}


def day_of(statement_id):
    raw = statement_id + "=" * (-len(statement_id) % 4)
    return re.search(r"StatementDate=([0-9-]+)", base64.b64decode(raw).decode()).group(1)


def good_press(page):
    """What the page does on the press: asks for the PDF of the statement
    it shows and makes one Blob of it."""
    data = spectrum_pdf(day_of(page.detail_sid))
    page.emit("response", Resp(site.PDF_OPERATION, {"statementId": page.detail_sid, "otpCheck": True},
                               answer=pdf_answer(data)))
    page.blob = blob_result(data)


def digital(account, day):
    """The page's own unpadded DIGITAL_DOCUMENT id for a statement, with a
    code of its own."""
    return sid(account, day, "DIGITAL_DOCUMENT", code="7").rstrip("=")


def lands(page, index):
    """What a press of a statement row does: the page moves in place to that
    statement's own page, and asks for it and, unless it is the newest, for
    the newest as well."""
    family, account, day = page.order()[index]
    page.detail_sid = digital(account, day)
    page.url = (f"https://{HOST}{site.DETAIL_PATH}?pdfStatementId={page.detail_sid}&isNextBill=false"
                f"&isAccountTransfer={'true' if family == 'prior' else 'false'}")
    current = sorted(d for f, _, d in page.order() if f == "current")
    compare = [] if not current or (family, day) == ("current", current[-1]) else [
        Resp(site.DETAIL_OPERATION, {"statementId": digital(CUR, current[-1])})]
    responses = page.detail(page.detail_sid) if page.detail else (
        [Resp(site.DETAIL_OPERATION, {"statementId": page.detail_sid, "showComparison": True})] + compare)
    for r in responses:
        page.emit("response", r)


class Btn:
    def __init__(self, page, items):
        self.page, self.items = page, items

    def count(self):
        return len(self.items)

    @property
    def first(self):
        return Btn(self.page, self.items[:1])

    def inner_text(self, **kw):
        return self.items[0][0]

    def is_enabled(self, **kw):
        return self.items[0][1]


class Page:
    """Spectrum's billing pages as the survey saw them, with invented values."""

    def __init__(self, answer=None, list_responses=None, rows=None, settle=None, body="",
                 detail=None, buttons=(("View Printable Statement", True),), on_press=good_press,
                 dl_dir=None, on_row=lands):
        self.answer = list_answer() if answer is None else answer
        self.list_responses = list_responses
        self.rows = rows
        self.settle = settle or {}
        self.body = body
        self.detail = detail
        self.buttons = list(buttons)
        self.on_press = on_press
        self.on_row = on_row
        self.dl_dir = dl_dir
        self.clicks = []           # (css, step, what) of every press
        self.row_presses = []      # the index of each statement row pressed
        self.row_changes = {}      # index: words a row shows when read again
        self.fail_wait = None      # page -> True when a wait should raise
        self.fail_on = set()       # events whose listener cannot be added
        self.url = f"https://{HOST}/account-summary"
        self.listeners = {}
        self.gotos, self.presses, self.blob, self.armed = [], 0, {"blobs": [], "opened": []}, False
        self.detail_sid = ""
        self.restored = False
        page = self

        class Ctx:
            pages = [page]
        self.context = Ctx()

    # events
    def on(self, event, fn):
        if event in self.fail_on:
            raise RuntimeError("could not listen")
        self.listeners.setdefault(event, []).append(fn)

    def remove_listener(self, event, fn):
        self.listeners[event].remove(fn)

    def emit(self, event, obj):
        for fn in list(self.listeners.get(event, [])):
            fn(obj)

    def live_listeners(self):
        return sum(len(v) for v in self.listeners.values())

    # navigation
    def goto(self, url, **kw):
        self.gotos.append(url)
        path = url.split("?")[0][len(site.BASE):]
        self.url = self.settle.get(path, url)
        if path == site.STATEMENTS_PATH:
            responses = self.list_responses
            if responses is None:
                responses = [Resp(site.LIST_OPERATION, answer=self.answer)]
            for r in responses:
                self.emit("response", r)
        elif path == site.DETAIL_PATH:
            self.detail_sid = dict(re.findall(r"([^?&=]+)=([^&]*)", url.split("?", 1)[1])).get("pdfStatementId", "")
            from urllib.parse import unquote
            self.detail_sid = unquote(self.detail_sid)
            responses = self.detail(self.detail_sid) if self.detail else [
                Resp(site.DETAIL_OPERATION, {"statementId": self.detail_sid, "showComparison": True})]
            for r in responses:
                self.emit("response", r)

    def wait_for_timeout(self, ms):
        if self.fail_wait and self.fail_wait(self):
            raise RuntimeError("Timeout waiting on %s for account %s" % (self.url, CUR))

    def order(self):
        """(family, account, date) of each statement, in the list answer's order."""
        sl = self.answer["data"]["viewer"]["account"]["statementList"]
        out = [("current", CUR, s["date"][:10]) for s in sl["statements"]]
        out += [("prior", t["accountNumber"], s["date"][:10]) for t in sl["accountTransfers"] for s in t["statements"]]
        return out

    def row_texts(self):
        if self.rows is not None:
            return list(self.rows)
        texts = [row_text(d) for _, _, d in self.order()]
        if texts and self.order()[0][0] == "current":
            texts[0] += " Auto Pay: Oct 10 $80.00"
        return texts + ["Make Early Payment"]

    def evaluate(self, js, arg=None):
        if js == site._ROWS_JS:
            return self.row_texts()
        if js == site._ARM_JS:
            self.armed = True
            return None
        if js == site._RESTORE_JS:
            self.restored = self.armed
            self.armed = False
            return True
        if js == site._COUNT_JS:
            return len(self.blob.get("blobs") or [])
        if js == site._RESULT_JS:
            return self.blob
        raise AssertionError("unexpected script")

    def locator(self, sel):
        page = self
        if sel == site.STATEMENT_ROW:
            class Rows:
                def nth(self, i):
                    return Row(page, i)
            return Rows()

        class L:
            def count(self):
                return 0

            def inner_text(self, **kw):
                return page.body
        return L()

    def get_by_role(self, role, name=None):
        assert role == "button"
        return Btn(self, [b for b in self.buttons if name.search(b[0])])

    def title(self):
        return "Spectrum"


class Row:
    def __init__(self, page, index):
        self.page, self.index = page, index

    def inner_text(self, **kw):
        return self.page.row_changes.get(self.index, self.page.row_texts()[self.index])


def fake_click(page, el, **kw):
    """A spy for pressing.click: records each press and plays the page's
    answer to it."""
    page.clicks.append((kw["css"], kw["step"], kw["what"]))
    if isinstance(el, Row):
        assert kw["css"] == pressing.LINKS and kw["step"] == "choose a statement"
        page.row_presses.append(el.index)
        page.on_row(page, el.index)
        return
    assert kw["css"] == pressing.BUTTONS and kw["step"] == "take a statement"
    page.presses += 1
    page.on_press(page)


class WithFakeClick(unittest.TestCase):
    def setUp(self):
        self._click = pressing.click
        pressing.click = fake_click
        site._KEPT.clear()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        pressing.click = self._click

    def take(self, page, family="current", day="2026-09-26", dl_dir=""):
        if dl_dir == "":
            dl_dir = self.tmp / "staging"
            dl_dir.mkdir(exist_ok=True)
        out = self.tmp / ("%s-%s.pdf" % (family, day))
        trace = []
        ok = site.download_bill(page, dl_dir, day, out, title=site.TITLE, trace=trace,
                                document_id="%s:%s" % (family, day))
        return ok, out, trace


# ---------------------------------------------------------------------------

class Hosts(unittest.TestCase):
    def test_only_the_site_over_https(self):
        for u in [f"https://{HOST}/", site.STATEMENTS_URL, f"https://{HOST}/billing/bill-details?x=1"]:
            self.assertTrue(site.is_safe_url(u), u)
        for u in ["https://spectrum.net/", "https://apis.spectrum.net/selfservice/graph", "https://id.spectrum.net/login",
                  f"http://{HOST}/", f"https://{HOST}:8443/", f"https://u@{HOST}/", f"https://{HOST}.evil.test/",
                  f"https://{HOST}@evil.test/", f"blob:https://{HOST}/x", "", None]:
            self.assertFalse(site.is_safe_url(u), repr(u))

    def test_blob_origin(self):
        self.assertTrue(site.is_download_url(BLOB_URL))
        for u in ["blob:https://apis.spectrum.net/x", f"blob:http://{HOST}/x", f"blob:https://{HOST}:8443/x",
                  f"https://{HOST}/x.pdf", "blob:null/x", f"blob:https://u@{HOST}/x", "", None]:
            self.assertFalse(site.is_download_url(u), repr(u))

    def test_the_statement_route_is_one_exact_shape(self):
        good = sid(CUR, "2026-09-26")
        url = site.detail_url(good, False)
        self.assertTrue(url.startswith(f"https://{HOST}/billing/bill-details?pdfStatementId="))
        self.assertTrue(site.is_detail_url(url, good))
        self.assertTrue(site.is_detail_url(site.detail_url(good, True), good))
        self.assertFalse(site.is_detail_url(url, sid(CUR, "2026-08-26")), "another statement's id")
        base = f"https://{HOST}/billing/bill-details"
        q = f"pdfStatementId={good}&isNextBill=false&isAccountTransfer=false"
        for bad in [f"http://{HOST}/billing/bill-details?{q}", f"https://{HOST}:443x/billing/bill-details?{q}",
                    f"https://{HOST}:8443/billing/bill-details?{q}", f"https://u:p@{HOST}/billing/bill-details?{q}",
                    f"{base}?{q}#frag", f"{base}/?{q}", f"https://{HOST}/billing/statements?{q}",
                    f"https://apis.spectrum.net/billing/bill-details?{q}", f"{base}?{q}&extra=1",
                    f"{base}?{q}&isNextBill=false", f"{base}?pdfStatementId={good}&isNextBill=false",
                    f"{base}?pdfStatementId={good}&isNextBill=true&isAccountTransfer=false",
                    f"{base}?pdfStatementId={good}&isNextBill=false&isAccountTransfer=maybe",
                    f"{base}?pdfStatementId=notbase64!&isNextBill=false&isAccountTransfer=false",
                    f"{base}?pdfStatementId={sid(CUR, '2026-09-26', 'DIGITAL_DOCUMENT')}&isNextBill=false&isAccountTransfer=false",
                    base, ""]:
            self.assertFalse(site.is_detail_url(bad), bad)
        # Padding "=" in an id survives the address both ways (the id's
        # text is ASCII digits, letters, "=" and "|", so its base64 never
        # holds "+" or "/").
        padded = next(sid(CUR[:n], "2026-09-26") for n in range(4, 17) if sid(CUR[:n], "2026-09-26").endswith("="))
        self.assertTrue(site.is_detail_url(site.detail_url(padded, True), padded))
        self.assertEqual(site.detail_url("", False), "")
        self.assertEqual(site.detail_url("bad id", False), "")

    def test_sign_in_shapes(self):
        for u in [f"https://{HOST}/login", f"https://{HOST}/login?loggedOut=true", "https://id.spectrum.net/x",
                  f"https://{HOST}/error/session-expired"]:
            p = Page()
            p.url = u
            self.assertTrue(site.looks_signed_out(p), u)
        p = Page()
        p.url = site.STATEMENTS_URL
        self.assertFalse(site.looks_signed_out(p))


class Guard(unittest.TestCase):
    def test_only_the_one_label(self):
        for label in ["View Printable Statement", "view printable statement", "  View   Printable\nStatement "]:
            self.assertTrue(site.is_safe_control(label), label)

    def test_everything_else_is_refused(self):
        for label in ["View Printable Statement and Pay", "View Printable Statement | Make Early Payment",
                      "Make Early Payment | View Printable Statement", "View Printable Statements",
                      "Print Statement", "View Statement", "Download Statement", "Statement", "Internet Statement",
                      "Expand All", "Back", "Make Early Payment", "Auto Pay", "Payment Methods", "Go Paperless",
                      "Enroll", "Settings", "Manage Mobile Devices", "Upgrade", "Add for $5/mo", "Change Plan",
                      "Cancel Service", "Services", "Schedule Appointment", "Chat with Spectrum", "Sign Out",
                      "Log Out", "Send one-time code", "Verify", "Enter Code", "Order now", "Shop Deals",
                      "Call 1-800", "", "   ", None]:
            self.assertFalse(site.is_safe_control(label), repr(label))


class Ids(unittest.TestCase):
    def test_decoding(self):
        self.assertEqual(site.decode_statement_id(sid(CUR, "2026-09-26")), (CUR, "2026-09-26"))

    def test_anything_else_stops_without_saying_the_id(self):
        for bad in ["", None, "not base64!", sid(CUR, "2026-09-26")[:-1], b64(b"Account=1|PDF_DOCUMENT"),
                    sid(CUR, "2026-09-26", "DIGITAL_DOCUMENT"), sid(CUR, "2026-02-30"), sid("12ab", "2026-09-26"),
                    sid(CUR, "2026-09-26", code="x"), sid(CUR, "2026-09-26") + "&x"]:
            with self.assertRaises(site.ListStopped) as stop:
                site.decode_statement_id(bad)
            self.assertNotIn(CUR, str(stop.exception))
            self.assertNotIn("Account", str(stop.exception))

    def test_day_of_the_answer(self):
        self.assertEqual(site.iso_day("2026-09-26T00:00:00-05:00"), "2026-09-26")
        self.assertEqual(site.iso_day("2026-09-26"), "2026-09-26")
        for bad in ["2026-02-30T00:00:00-05:00", "Sep 26, 2026", "2026-09-26x", "", None]:
            self.assertIsNone(site.iso_day(bad), repr(bad))
        self.assertEqual(site.row_day("Statement September 26, 2026 Auto Pay: Oct 1"), "2026-09-26")
        self.assertIsNone(site.row_day("Statement Sept 26, 2026"))


class Families(unittest.TestCase):
    def entries(self, answer):
        return site.entries_of(site.statement_list_of(answer))

    def test_current_and_prior(self):
        got = self.entries(list_answer())
        self.assertEqual([e.document_id for e in got],
                         ["current:2026-09-26", "current:2026-08-26", "current:2026-02-26",
                          "prior:2026-02-26", "prior:2026-01-26"])
        self.assertNotIn(CUR, repr(got), "an entry's repr carries no id")
        docs_ = site.raw_docs(got)
        self.assertEqual({(d.title, d.account, d.kind, d.href) for d in docs_},
                         {(site.TITLE, "Internet", "statement", ""),
                          (site.TITLE, "Internet (prior account)", "statement", "")})
        blob = json.dumps([d.__dict__ for d in docs_])
        for word in (CUR, PRI, STREET, sid(CUR, "2026-09-26")):
            self.assertNotIn(word, blob)

    def test_private_words_are_registered(self):
        self.entries(list_answer())
        words = site.private_words()
        for word in (CUR, PRI, STREET, "4321", sid(CUR, "2026-09-26"), sid(PRI, "2026-01-26")):
            self.assertIn(word, words)
        self.assertNotIn("Apt", words, "a short address part is not a word")
        self.assertIn("CAPTCHA", site.redact("CAPTCHA Statement"))

    def test_what_stops_the_list(self):
        same_twice = list_answer(cur=["2026-09-26", "2026-09-26"])
        prior_twice = list_answer(prior=["2026-01-26", "2026-01-26"])
        mismatch = list_answer()
        mismatch["data"]["viewer"]["account"]["statementList"]["statements"][0]["date"] = "2026-09-25T00:00:00-05:00"
        other_acct = list_answer(prior_account=PRI)
        other_acct["data"]["viewer"]["account"]["statementList"]["accountTransfers"][0]["accountNumber"] = "8350990000000001"
        two_accts = list_answer()
        two_accts["data"]["viewer"]["account"]["statementList"]["statements"][1]["id"] = sid(PRI, "2026-08-26")
        digital = list_answer()
        digital["data"]["viewer"]["account"]["statementList"]["statements"][0]["id"] = sid(CUR, "2026-09-26", "DIGITAL_DOCUMENT")
        bad_date = list_answer()
        bad_date["data"]["viewer"]["account"]["statementList"]["statements"][0]["date"] = "September 26"
        for name, answer in [("same date twice in current", same_twice), ("same date twice in prior", prior_twice),
                             ("id date differs", mismatch), ("transfer names another account", other_acct),
                             ("current names two accounts", two_accts), ("digital id", digital),
                             ("bad date", bad_date), ("no statementList", {"data": {"viewer": {}}}),
                             ("not an object", [])]:
            with self.assertRaises(site.ListStopped, msg=name) as stop:
                self.entries(answer)
            for word in (CUR, PRI, STREET):
                self.assertNotIn(word, str(stop.exception), name)

    def test_errors_in_the_answer_are_a_signed_out_session(self):
        with self.assertRaises(site.SessionExpired):
            self.entries({"errors": [{"message": "token " + CUR}], "data": None})

    def test_one_date_across_families_is_two_statements(self):
        got = self.entries(list_answer(cur=["2026-02-26"], prior=["2026-02-26"]))
        self.assertEqual([e.document_id for e in got], ["current:2026-02-26", "prior:2026-02-26"])

    def test_an_empty_list_is_empty(self):
        self.assertEqual(self.entries(list_answer(cur=[], prior=None)), [])


class Listing(unittest.TestCase):
    def test_reads_the_one_answer_and_takes_nothing_off_the_page(self):
        page = Page()
        got = site.collect_download_docs(page)
        self.assertEqual(len(got), 5)
        self.assertEqual(page.gotos, [site.STATEMENTS_URL])
        self.assertEqual(page.live_listeners(), 0, "the listener is taken off")

    def test_cardinality(self):
        other = Resp("fetchAccountGlobal", answer={"data": {}})
        wrong_host = Resp(site.LIST_OPERATION, answer=list_answer(), url="https://apis.spectrum.net/selfservice/graphauxiliary")
        get = Resp(site.LIST_OPERATION, answer=list_answer(), method="GET")
        for name, responses in [("none", []), ("only other operations", [other]),
                                ("two", [Resp(site.LIST_OPERATION, answer=list_answer())] * 2),
                                ("another address", [wrong_host]), ("a GET", [get]),
                                ("not JSON", [Resp(site.LIST_OPERATION, answer=ValueError("x"))]),
                                ("a 500", [Resp(site.LIST_OPERATION, status=500)])]:
            page = Page(list_responses=responses)
            with self.assertRaises(site.ListStopped, msg=name):
                site.read_statement_list(page)
            self.assertEqual(page.live_listeners(), 0, name)

    def test_signed_out_answers(self):
        for name, page in [
                ("401", Page(list_responses=[Resp(site.LIST_OPERATION, status=401)])),
                ("401 on another operation", Page(list_responses=[Resp("fetchAccountGlobal", status=401),
                                                                   Resp(site.LIST_OPERATION, answer=list_answer())])),
                ("errors", Page(list_responses=[Resp(site.LIST_OPERATION, answer={"errors": [{"m": 1}]})])),
                ("session check", Page(list_responses=[
                    Resp("x", url="https://apis.spectrum.net/auth/oauth/v2/validateSession?ts=1", method="GET",
                         answer={"isValidSession": False}),
                    Resp(site.LIST_OPERATION, answer=list_answer())])),
                ("sign-in page", Page(settle={site.STATEMENTS_PATH: f"https://{HOST}/login"})),
                ("id host", Page(settle={site.STATEMENTS_PATH: "https://id.spectrum.net/x"})),
                ("idle dialog", Page(body="Your session is about to expire. Stay signed in?"))]:
            with self.assertRaises(site.SessionExpired, msg=name):
                site.read_statement_list(page)
            self.assertEqual(page.live_listeners(), 0, name)

    def test_a_passcode_prompt(self):
        page = Page(body="Enter the one-time passcode we sent")
        with self.assertRaises(site.OtpRequired):
            site.read_statement_list(page)

    def test_the_drawn_rows_must_agree(self):
        for rows in ([row_text(d) for d in CUR_DATES + PRI_DATES[:1]],
                     [row_text(d) for d in CUR_DATES + ["2026-01-27", "2026-02-26"]],
                     [row_text(d) for d in CUR_DATES + PRI_DATES] + ["Statement soon"]):
            with self.assertRaises(site.ListStopped):
                site.read_statement_list(Page(rows=rows))

    def test_settled_elsewhere(self):
        with self.assertRaises(site.ListStopped):
            site.read_statement_list(Page(settle={site.STATEMENTS_PATH: f"https://{HOST}/account-summary"}))


class Binding(WithFakeClick):
    def test_success_saves_the_one_statement(self):
        page = Page()
        ok, out, trace = self.take(page)
        self.assertTrue(ok)
        self.assertEqual(out.read_bytes(), PDF)
        self.assertEqual(page.presses, 1)
        self.assertTrue(page.restored, "the page's own window.open and createObjectURL are back")
        self.assertFalse(page.armed)
        self.assertEqual(page.live_listeners(), 0)
        self.assertTrue(site.is_detail_url(page.gotos[-1], sid(CUR, "2026-09-26")))
        self.assertEqual(page.row_presses, [], "the newest is opened by its address, no row pressed")

    def test_no_single_entry_means_no_press(self):
        for family, day in [("current", "2026-09-25"), ("prior", "2026-09-26")]:
            page = Page()
            ok, out, _ = self.take(page, family, day)
            self.assertFalse(ok)
            self.assertEqual(page.presses, 0)
            self.assertEqual(page.gotos, [site.STATEMENTS_URL])
        real = site.read_statement_list
        e = site.Entry("current", "2026-09-26", sid(CUR, "2026-09-26"))
        site.read_statement_list = lambda page: [e, e]
        try:
            page = Page()
            ok, out, _ = self.take(page)
        finally:
            site.read_statement_list = real
        self.assertFalse(ok)
        self.assertEqual(page.presses, 0)

    def test_a_record_that_is_not_one_is_refused(self):
        page = Page()
        out = self.tmp / "x.pdf"
        for doc_id, day in [("current:2026-09-26", "2026-09-25"), (sid(CUR, "2026-09-26"), "2026-09-26"),
                            ("", "2026-09-26"), ("other:2026-09-26", "2026-09-26")]:
            self.assertFalse(site.download_bill(page, None, day, out, document_id=doc_id))
        self.assertEqual(page.gotos, [])

    def test_the_page_must_ask_for_this_statement(self):
        cases = {
            "another statement": lambda s: [Resp(site.DETAIL_OPERATION, {"statementId": sid(CUR, "2026-08-26")})],
            "none": lambda s: [],
            "twice": lambda s: [Resp(site.DETAIL_OPERATION, {"statementId": s})] * 2,
        }
        for name, detail in cases.items():
            page = Page(detail=detail)
            ok, out, _ = self.take(page)
            self.assertFalse(ok, name)
            self.assertEqual(page.presses, 0, name)
            self.assertFalse(out.exists(), name)
            self.assertEqual(page.live_listeners(), 0, name)

    def test_the_statement_page_signed_out(self):
        page = Page(detail=lambda s: [Resp(site.DETAIL_OPERATION, {"statementId": s}, status=401)])
        with self.assertRaises(site.SessionExpired):
            self.take(page)
        self.assertEqual(page.presses, 0)
        page = Page(detail=lambda s: [Resp(site.DETAIL_OPERATION, {"statementId": s}, answer={"errors": [1]})])
        with self.assertRaises(site.SessionExpired):
            self.take(page)
        page = Page(settle={site.DETAIL_PATH: f"https://{HOST}/login"})
        with self.assertRaises(site.SessionExpired):
            self.take(page)
        self.assertEqual(page.presses, 0)

    def test_the_button_must_be_one_enabled_exact_label(self):
        for buttons in [(), (("View Printable Statement", True),) * 2, (("View Printable Statement", False),),
                        (("View Printable Statement and Pay", True),), (("View Printable Statements", True),)]:
            page = Page(buttons=buttons)
            ok, out, _ = self.take(page)
            self.assertFalse(ok, buttons)
            self.assertEqual(page.presses, 0, buttons)


class Acceptance(WithFakeClick):
    def stops(self, on_press, dl_dir="", **kw):
        page = Page(on_press=on_press, **kw)
        with self.assertRaises(pressing.Stop) as stop:
            self.take(page, dl_dir=dl_dir)
        self.assertTrue(stop.exception.after_a_press)
        self.assertEqual(page.presses, 1)
        self.assertEqual(page.live_listeners(), 0)
        self.assertTrue(page.restored, "the hook is taken off after a refusal too")
        self.assertFalse(list(self.tmp.glob("*.pdf")))
        for word in (CUR, PRI, STREET):
            self.assertNotIn(word, " ".join(stop.exception.lines))
        return stop.exception

    def test_the_matrix(self):
        def answer_only(p):
            p.emit("response", Resp(site.PDF_OPERATION, {"statementId": p.detail_sid}, answer=pdf_answer()))

        def blob_only(p):
            p.blob = blob_result()

        def bytes_differ(p):
            good_press(p)
            p.blob = blob_result(OTHER_PDF)

        def two_blobs(p):
            good_press(p)
            p.blob = blob_result(blobs=2)

        def two_answers(p):
            good_press(p)
            p.emit("response", Resp(site.PDF_OPERATION, {"statementId": p.detail_sid}, answer=pdf_answer()))

        def other_statement(p):
            p.emit("response", Resp(site.PDF_OPERATION, {"statementId": sid(CUR, "2026-08-26")}, answer=pdf_answer()))
            p.blob = blob_result()

        def not_a_pdf(p):
            p.emit("response", Resp(site.PDF_OPERATION, {"statementId": p.detail_sid}, answer=pdf_answer(b"<html>")))
            p.blob = blob_result(b"<html>")

        def wrong_type(p):
            good_press(p)
            p.blob = blob_result(kind="text/html")

        def wrong_origin(p):
            good_press(p)
            p.blob = blob_result(url="blob:https://apis.spectrum.net/x")

        def opened_elsewhere(p):
            good_press(p)
            p.blob = blob_result(opened=["https://www.spectrum.net/billing"])

        def new_page(p):
            good_press(p)
            p.context.pages.append(SimpleNamespace(close=lambda: None))

        def popup(p):
            good_press(p)
            p.emit("popup", object())

        def left_route(p):
            good_press(p)
            p.url = f"https://{HOST}/billing"

        for f in (answer_only, blob_only, bytes_differ, two_blobs, two_answers, other_statement, not_a_pdf,
                  wrong_type, wrong_origin, opened_elsewhere, new_page, popup, left_route):
            self.stops(f)

    def test_a_download_event_stops_the_run_and_is_dropped_unread(self):
        class Download:
            def __init__(self):
                self.dropped = []

            def cancel(self):
                self.dropped.append("cancel")

            def delete(self):
                self.dropped.append("delete")

            def __getattr__(self, name):
                raise AssertionError("the download's %s was read" % name)
        seen = Download()

        def download(p):
            good_press(p)
            p.emit("download", seen)
        self.stops(download)
        self.assertEqual(seen.dropped, ["cancel", "delete"])

    def test_a_file_in_the_folder_stops_the_run_and_is_removed(self):
        dl = self.tmp / "dl"
        dl.mkdir()
        (dl / "Statement.pdf").write_bytes(b"left from before")
        import os
        os.utime(dl / "Statement.pdf", ns=(1, 1))

        def new_file(p):
            good_press(p)
            (dl / "Other.pdf.crdownload").write_bytes(b"x")

        def same_name(p):
            good_press(p)
            (dl / "Statement.pdf").write_bytes(b"written over by the press")
        self.stops(new_file, dl_dir=dl)
        self.assertEqual(sorted(x.name for x in dl.iterdir()), ["Statement.pdf"])
        self.stops(same_name, dl_dir=dl)
        self.assertEqual(list(dl.iterdir()), [], "nothing a download left stays in the folder")

    def test_an_unreadable_folder_stops_the_run(self):
        self.stops(good_press, dl_dir=self.tmp / "no such folder")

    def test_a_passcode_after_the_press(self):
        def otp(p):
            p.body = "We sent a one-time passcode to your phone"
        page = Page(on_press=otp)
        with self.assertRaises(site.OtpRequired):
            self.take(page)
        self.assertEqual(page.live_listeners(), 0)
        self.assertTrue(page.restored, "the hook is taken off when the press raises")

    def test_a_button_turned_off_at_the_press_leaves_the_statement(self):
        def turned_off(page, el, **kw):
            raise pressing.TurnedOff("take a statement", "the control is turned off", ["off"])
        pressing.click = turned_off
        page = Page()
        ok, out, trace = self.take(page)
        self.assertFalse(ok)
        self.assertFalse(out.exists())
        self.assertEqual(trace[-1], {"note": "the button is turned off"})
        self.assertTrue(page.restored)
        self.assertEqual(page.live_listeners(), 0)

    def test_the_hook_is_taken_off_when_the_press_itself_stops(self):
        def covered(page, el, **kw):
            raise pressing.Covered("take a statement", "covered", ["covered"])
        pressing.click = covered
        page = Page()
        with self.assertRaises(pressing.Stop):
            self.take(page)
        self.assertTrue(page.restored)
        self.assertEqual(page.live_listeners(), 0)

    def test_a_sign_out_after_the_press(self):
        def expired(p):
            p.emit("response", Resp(site.PDF_OPERATION, {"statementId": p.detail_sid}, status=401))
        with self.assertRaises(site.SessionExpired):
            self.take(Page(on_press=expired))

    def test_accept_alone(self):
        ok = dict(new_pages=0, popups=0, downloads=0, on_route=True)
        self.assertEqual(site.accept([(True, PDF)], blob_result(), **ok), (PDF, ""))
        self.assertEqual(site.accept([(True, PDF)], blob_result(kind="application/pdf;base64"), **ok)[0], PDF)
        for pdfs, res, kw in [([], blob_result(), ok), ([(True, PDF)], {}, ok), ([(False, PDF)], blob_result(), ok),
                              ([(True, b"")], blob_result(), ok), ([(True, PDF)], blob_result(OTHER_PDF), ok),
                              ([(True, PDF)], blob_result(), {**ok, "downloads": 1}),
                              ([(True, PDF)], blob_result(), {**ok, "new_pages": 1}),
                              ([(True, PDF)], blob_result(), {**ok, "popups": 1}),
                              ([(True, PDF)], blob_result(), {**ok, "on_route": False})]:
            data, why = site.accept(pdfs, res, **kw)
            self.assertEqual(data, b"")
            self.assertTrue(why)


class InPage(WithFakeClick):
    """An older statement is chosen by pressing its own row, so the page
    prints that one and not the newest."""

    def test_an_older_statement_is_chosen_by_its_row(self):
        page = Page()
        ok, out, trace = self.take(page, "current", "2026-08-26")
        self.assertTrue(ok, trace)
        self.assertEqual(out.read_bytes(), spectrum_pdf("2026-08-26"))
        self.assertEqual(page.gotos, [site.STATEMENTS_URL], "the statement's page is never opened by address")
        self.assertEqual(page.row_presses, [1])
        self.assertEqual([c[1] for c in page.clicks], ["choose a statement", "take a statement"])
        self.assertEqual(page.clicks[0][2], "the statement row dated 2026-08-26")
        self.assertTrue(page.restored)
        self.assertEqual(page.live_listeners(), 0)

    def test_a_prior_account_statement_is_chosen_by_its_row(self):
        page = Page()
        ok, out, trace = self.take(page, "prior", "2026-01-26")
        self.assertTrue(ok, trace)
        self.assertEqual(page.row_presses, [4])
        self.assertIn("isAccountTransfer=true", page.url)

    def test_with_no_current_statement_every_statement_goes_by_its_row(self):
        page = Page(answer=list_answer(cur=[]))
        ok, out, trace = self.take(page, "prior", "2026-02-26")
        self.assertTrue(ok, trace)
        self.assertEqual(page.row_presses, [0])
        self.assertIsNone(site.newest([site.Entry("prior", "2026-02-26", sid(PRI, "2026-02-26"))]))

    def test_two_statements_of_one_date_are_left_unpressed(self):
        for rows in (None, [row_text(d) for d in ["2026-09-26", "2026-08-26", "2026-02-26", "2026-02-26",
                                                   "2026-01-26"]]):
            for family in ("current", "prior"):
                page = Page(rows=rows)
                ok, out, trace = self.take(page, family, "2026-02-26")
                self.assertFalse(ok)
                self.assertEqual(page.clicks, [], family)
                self.assertFalse(out.exists())
                self.assertIn("another statement carries the same date", json.dumps(trace))

    def test_the_row_guard(self):
        day = "2026-08-26"
        self.assertTrue(site.is_safe_row(row_text(day), day))
        self.assertTrue(site.is_safe_row("Statement August 26, 2026", day))
        for text in ["Pay Bill", "Make Early Payment", "Statement August 26, 2026 Auto Pay: Sep 10 $80.00",
                     "Statement August 26, 2026 AutoPay", "Statement August 26, 2026 Go Paperless",
                     "Statement August 26, 2026 Equipment", "Statement August 26, 2026 Settings",
                     "Statement August 26, 2026 Sign Out", "Statement August 26, 2026 1 Mobile Ave",
                     "Statement August 26, 2026 Manage", "Statement August 26, 2026 Order now",
                     "Statement August 26, 2026 Enroll", "Statement August 26, 2026 Sign in",
                     "Statement August 25, 2026", "Statement Aug 26, 2026", "Statements", "", None]:
            self.assertFalse(site.is_safe_row(text, day), text)
        self.assertFalse(site.is_safe_row(row_text(day), "2026-02-30"))

    def test_a_row_the_guard_refuses_is_not_pressed(self):
        rows = [row_text(d) for d in ["2026-09-26"]] + ["Statement August 26, 2026 1 Mobile Ave"] + [
            row_text(d) for d in ["2026-02-26", "2026-02-26", "2026-01-26"]]
        page = Page(rows=rows)
        ok, out, trace = self.take(page, "current", "2026-08-26")
        self.assertFalse(ok)
        self.assertEqual(page.clicks, [])

    def test_a_row_that_changed_before_the_press_is_not_pressed(self):
        for words in ("Statement August 26, 2026 Make a Payment", "Statement July 26, 2026", ""):
            page = Page()
            page.row_changes = {1: words}
            ok, out, _ = self.take(page, "current", "2026-08-26")
            self.assertFalse(ok, words)
            self.assertEqual(page.clicks, [], words)

    def test_a_row_turned_off_leaves_the_statement(self):
        def off(page, el, **kw):
            raise pressing.TurnedOff("choose a statement", "the control is turned off", ["off"])
        pressing.click = off
        page = Page()
        ok, out, trace = self.take(page, "current", "2026-08-26")
        self.assertFalse(ok)
        self.assertEqual(page.presses, 0)
        self.assertEqual(page.live_listeners(), 0)

    def test_no_row_press_without_the_download_folder(self):
        page = Page()
        with self.assertRaises(pressing.NotPressed):
            self.take(page, "current", "2026-08-26", dl_dir=None)
        self.assertEqual(page.clicks, [])

    def stops_after_the_row(self, on_row, **kw):
        page = Page(on_row=on_row, **kw)
        with self.assertRaises(pressing.Stop) as stop:
            self.take(page, "current", "2026-08-26")
        self.assertTrue(stop.exception.after_a_press)
        self.assertEqual(stop.exception.step, "choose a statement")
        self.assertEqual(page.presses, 0, "View Printable Statement is not pressed")
        self.assertEqual(page.live_listeners(), 0)
        self.assertFalse(list(self.tmp.glob("*.pdf")))
        for word in (CUR, PRI, STREET):
            self.assertNotIn(word, " ".join(stop.exception.lines))
        return page

    def test_anything_unexpected_after_the_row_press_stops_the_run(self):
        def other_statement(p, i):
            lands(p, 0)

        def off_the_page(p, i):
            lands(p, i)
            p.url = f"https://{HOST}/billing"

        def wrong_transfer_flag(p, i):
            lands(p, i)
            p.url = p.url.replace("isAccountTransfer=false", "isAccountTransfer=true")

        def extra_query(p, i):
            lands(p, i)
            p.url += "&autoAddOffer=1"

        def tab(p, i):
            lands(p, i)
            p.context.pages.append(SimpleNamespace(close=lambda: None))

        def popup(p, i):
            lands(p, i)
            p.emit("popup", object())

        def download(p, i):
            lands(p, i)
            p.emit("download", SimpleNamespace(cancel=lambda: None, delete=lambda: None))
        for f in (other_statement, off_the_page, wrong_transfer_flag, extra_query, tab, popup, download):
            self.stops_after_the_row(f)

    def test_the_page_must_ask_for_the_chosen_statement_once_and_only_known_ones(self):
        mine = digital(CUR, "2026-08-26")
        cases = {
            "none": [],
            "twice": [Resp(site.DETAIL_OPERATION, {"statementId": mine})] * 2,
            "only the newest": [Resp(site.DETAIL_OPERATION, {"statementId": digital(CUR, "2026-09-26")})],
            "a statement not listed": [Resp(site.DETAIL_OPERATION, {"statementId": mine}),
                                       Resp(site.DETAIL_OPERATION, {"statementId": digital(CUR, "2025-01-26")})],
            "an id that does not read": [Resp(site.DETAIL_OPERATION, {"statementId": mine}),
                                         Resp(site.DETAIL_OPERATION, {"statementId": "x!"})],
            "the comparison failed": [Resp(site.DETAIL_OPERATION, {"statementId": mine}),
                                      Resp(site.DETAIL_OPERATION, {"statementId": digital(CUR, "2026-09-26")},
                                           status=500)],
            "this one failed": [Resp(site.DETAIL_OPERATION, {"statementId": mine}, status=500)],
        }
        for name, responses in cases.items():
            self.stops_after_the_row(lands, detail=lambda s, r=responses: r)

    def test_a_comparison_with_errors_is_a_signed_out_session(self):
        mine = digital(CUR, "2026-08-26")
        page = Page(detail=lambda s: [Resp(site.DETAIL_OPERATION, {"statementId": mine}),
                                      Resp(site.DETAIL_OPERATION, {"statementId": digital(CUR, "2026-09-26")},
                                           answer={"errors": [1]})])
        with self.assertRaises(site.SessionExpired):
            self.take(page, "current", "2026-08-26")
        self.assertEqual(page.presses, 0)

    def test_signed_out_after_the_row_press(self):
        def to_login(p, i):
            p.url = f"https://{HOST}/login"
        page = Page(on_row=to_login)
        with self.assertRaises(site.SessionExpired):
            self.take(page, "current", "2026-08-26")
        self.assertEqual(page.presses, 0)

    def test_no_printable_button_after_the_row_press_stops_the_run(self):
        for buttons in [(), (("View Printable Statement", True),) * 2, (("View Printable Statement and Pay", True),)]:
            self.stops_after_the_row(lands, buttons=buttons)


class BoundToTheStatement(WithFakeClick):
    """The PDF the press brings is kept only when it is the chosen
    statement's, beyond the page's own request having named it."""

    def stops(self, data, before=None):
        def press(p):
            p.emit("response", Resp(site.PDF_OPERATION, {"statementId": p.detail_sid}, answer=pdf_answer(data)))
            p.blob = blob_result(data)
        if before:
            before()
        page = Page(on_press=press)
        with self.assertRaises(pressing.Stop) as stop:
            self.take(page, "current", "2026-08-26")
        self.assertEqual(stop.exception.step, "take a statement")
        self.assertTrue(stop.exception.after_a_press)
        self.assertFalse((self.tmp / "current-2026-08-26.pdf").exists())
        self.assertEqual(page.live_listeners(), 0)
        return stop.exception

    def test_the_newest_in_its_place_is_refused(self):
        self.stops(spectrum_pdf("2026-09-26"))

    def test_a_pdf_without_the_date_or_without_text_is_refused(self):
        self.stops(text_pdf(["Spectrum", "Amount Due $80.00", "Thank you for choosing Spectrum Internet"]))
        self.assertIn("no text", self.stops(text_pdf([])).lines[0])

    def test_the_newest_printing_this_date_as_its_previous_one_is_caught_by_its_bytes(self):
        # The newest may print this statement's date as its previous
        # statement. The text check then ties, which keeps a file, so the
        # newest saved by an earlier run is what refuses it, beside the
        # page's own request having to name this statement.
        newest = text_pdf(["Statement Date 09/26/26", "Previous statement 08/26/26",
                           "Service From 09/26/26 through 10/25/26"])
        entries = site.entries_of(site.statement_list_of(list_answer()))
        e = [x for x in entries if x.document_id == "current:2026-08-26"][0]
        self.assertEqual(site.bind(newest, e, entries, self.tmp / "x.pdf"), "", "the text alone cannot tell")
        self.stops(newest, before=lambda: (self.tmp / "2026-09-26 Spectrum Internet Statement.pdf").write_bytes(newest))

    def test_bytes_already_saved_are_refused_even_from_an_earlier_run(self):
        data = spectrum_pdf("2026-08-26")
        self.stops(data, before=lambda: (self.tmp / "2026-09-26 Spectrum Internet Statement.pdf").write_bytes(data))

    def test_a_download_again_may_meet_its_own_copy(self):
        data = spectrum_pdf("2026-08-26")
        mine = self.tmp / "2026-08-26 Spectrum Internet Statement.pdf"
        mine.write_bytes(data)
        (self.tmp / "staging").mkdir()
        out = self.tmp / "current-2026-08-26.pdf"
        ok = site.download_bill(Page(), self.tmp / "staging", "2026-08-26", out,
                                document_id="current:2026-08-26", own_files=[str(mine)])
        self.assertTrue(ok)
        self.assertEqual(out.read_bytes(), data)
        site._KEPT.clear()
        out.unlink()
        with self.assertRaises(pressing.Stop):
            site.download_bill(Page(), self.tmp / "staging", "2026-08-26", out,
                               document_id="current:2026-08-26",
                               own_files=[str(self.tmp / "another.pdf")])

    def test_bytes_kept_for_another_statement_in_this_run_are_refused(self):
        import hashlib
        data = spectrum_pdf("2026-08-26")
        self.stops(data, before=lambda: site._KEPT.update({hashlib.sha256(data).hexdigest(): "current:2026-09-26"}))

    def test_the_statement_kept_is_remembered(self):
        ok, out, _ = self.take(Page(), "current", "2026-08-26")
        self.assertTrue(ok)
        self.assertIn("current:2026-08-26", site._KEPT.values())

    def test_nothing_of_the_text_reaches_the_trace(self):
        ok, out, trace = self.take(Page(), "current", "2026-08-26")
        said = json.dumps(trace)
        for word in ("08/26/26", "Amount Due", "Service From", "80.00"):
            self.assertNotIn(word, said)


class Addresses(unittest.TestCase):
    def test_a_plus_in_a_query_value_stays_a_plus(self):
        self.assertEqual(site._query_pairs("a=b+c%2Bd&e="), [("a", "b+c+d"), ("e", "")])
        for bad in ("a", "=b", "a=b&&c=d"):
            with self.assertRaises(ValueError):
                site._query_pairs(bad)

    def test_lands_on_takes_the_pages_own_spelling_and_family(self):
        e = site.Entry("current", "2026-08-26", sid(CUR, "2026-08-26"))
        base = f"https://{HOST}{site.DETAIL_PATH}?pdfStatementId=%s&isNextBill=false&isAccountTransfer=%s"
        self.assertTrue(site.lands_on(base % (digital(CUR, "2026-08-26"), "false"), e))
        self.assertTrue(site.lands_on(base % (sid(CUR, "2026-08-26"), "false"), e))
        self.assertFalse(site.lands_on(base % (digital(CUR, "2026-08-26"), "true"), e))
        self.assertFalse(site.lands_on(base % (digital(PRI, "2026-08-26"), "false"), e))
        self.assertFalse(site.lands_on(base % (digital(CUR, "2026-07-26"), "false"), e))
        p = site.Entry("prior", "2026-01-26", sid(PRI, "2026-01-26"))
        self.assertTrue(site.lands_on(base % (digital(PRI, "2026-01-26"), "true"), p))
        self.assertFalse(site.lands_on(base % (digital(PRI, "2026-01-26"), "false"), p))
        self.assertFalse(site.lands_on(f"https://{HOST}/billing/statements", e))


class AfterAPress(WithFakeClick):
    """A failure of any kind once a press was made stops the run, and the
    page is left with no listener and no hook of ours."""

    def test_a_read_that_fails_after_the_row_press_stops_the_run(self):
        page = Page()
        page.fail_wait = lambda p: bool(p.row_presses)
        with self.assertRaises(pressing.Stop) as stop:
            self.take(page, "current", "2026-08-26")
        self.assertEqual(stop.exception.step, "choose a statement")
        self.assertTrue(stop.exception.after_a_press)
        self.assertEqual(page.presses, 0)
        self.assertEqual(page.live_listeners(), 0)
        self.assertNotIn(CUR, " ".join(stop.exception.lines))
        self.assertNotIn("pdfStatementId", " ".join(stop.exception.lines))

    def test_a_read_that_fails_after_the_printable_press_stops_the_run(self):
        for family, day in (("current", "2026-09-26"), ("current", "2026-08-26")):
            page = Page()
            page.fail_wait = lambda p: p.presses > 0
            with self.assertRaises(pressing.Stop) as stop:
                self.take(page, family, day)
            self.assertEqual(stop.exception.step, "take a statement")
            self.assertTrue(page.restored)
            self.assertEqual(page.live_listeners(), 0)
            self.assertFalse(list(self.tmp.glob("*.pdf")))
            self.assertNotIn(CUR, " ".join(stop.exception.lines))

    def test_a_statement_that_cannot_be_written_stops_the_run(self):
        page = Page()
        out = self.tmp / "a file" / "x.pdf"
        (self.tmp / "a file").write_bytes(b"not a folder")
        with self.assertRaises(pressing.Stop) as stop:
            site.download_bill(page, self.tmp, "2026-09-26", out, document_id="current:2026-09-26")
        self.assertEqual(stop.exception.step, "take a statement")

    def test_a_listener_that_cannot_be_added_leaves_none(self):
        for event in ("response", "popup", "download"):
            for day in ("2026-09-26", "2026-08-26"):
                page = Page()
                page.fail_on = {event} if event != "response" else set()
                if event == "response":
                    real = page.on

                    def on(ev, fn, real=real, page=page):
                        if ev == "response" and page.gotos and page.url.startswith(site.BASE + site.DETAIL_PATH):
                            raise RuntimeError("could not listen")
                        real(ev, fn)
                    page.on = on
                try:
                    self.take(page, "current", day)
                except (RuntimeError, pressing.Stop):
                    pass
                self.assertEqual(page.live_listeners(), 0, (event, day))
                self.assertFalse(page.armed, (event, day))

    def test_every_answer_is_read_before_it_is_counted(self):
        mine = digital(CUR, "2026-08-26")
        err = {"errors": [1]}
        for responses in ([Resp(site.DETAIL_OPERATION, {"statementId": mine})] * 2
                          + [Resp(site.DETAIL_OPERATION, {"statementId": mine}, answer=err)],
                          [Resp(site.DETAIL_OPERATION, {"statementId": digital(CUR, "2026-09-26")}, answer=err)],
                          [Resp(site.DETAIL_OPERATION, {"statementId": "x!"}, status=401)]):
            page = Page(detail=lambda s, r=responses: r)
            with self.assertRaises(site.SessionExpired):
                self.take(page, "current", "2026-08-26")
            self.assertEqual(page.presses, 0)

    def test_the_docs_layer_keeps_no_words_of_a_failure_and_stops_at_a_press(self):
        app = make_app(self.tmp, "--all")
        for r in site.raw_docs(site.entries_of(site.statement_list_of(list_answer()))):
            app._record_rawdoc(r, site.BILLING_URL)
        app.discovery.save()
        app._staged = True
        app._on_its_site = lambda: Page()
        app._delay = lambda *a, **k: None
        seen = io.StringIO()
        handler = logging.StreamHandler(seen)
        root = logging.getLogger()
        root.addHandler(handler)
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise RuntimeError("at %s for %s %s" % (sid(CUR, "2026-09-26"), CUR, ADDRESS))
        original = site.download_bill
        site.download_bill = boom
        try:
            with redirect_stdout(seen):
                app.process(app._select(limit=1))
        finally:
            site.download_bill = original
            root.removeHandler(handler)
        written = seen.getvalue()
        for p in app.paths.root.rglob("*"):
            if p.is_file() and p.suffix in (".json", ".csv", ".txt", ".log"):
                written += p.read_text(encoding="utf-8", errors="ignore")
        for word in (CUR, STREET, sid(CUR, "2026-09-26")):
            self.assertNotIn(word, written, word)
        calls.clear()

        def stop(*a, **k):
            calls.append(1)
            raise pressing.no_answer("take a statement", "the press made something other than one statement", "x")
        site.download_bill = stop
        try:
            with redirect_stdout(io.StringIO()), self.assertRaises(pressing.Stop):
                app.process(app._select())
        finally:
            site.download_bill = original
        self.assertEqual(calls, [1], "nothing after the stop is touched")


# ---------------------------------------------------------------------------

def make_app(tmp, *args):
    config = json.loads((HERE / "config.example.json").read_text())
    config.update({"owner": "Tester Example", "output_dir": str(Path(tmp) / "out")})
    cfg = Path(tmp) / "config.json"
    cfg.write_text(json.dumps(config))
    return docs.App(docs.build_parser().parse_args([*args, "--config", str(cfg)]))


class Orchestrator(WithFakeClick):
    def listed(self, app):
        for r in site.raw_docs(site.entries_of(site.statement_list_of(list_answer()))):
            app._record_rawdoc(r, site.BILLING_URL)
        app.discovery.save()

    def test_records_and_names(self):
        app = make_app(self.tmp, "--discover")
        self.listed(app)
        text = app.paths.discovery_json.read_text()
        for word in (CUR, PRI, STREET, "Account=", "pdfStatementId", "?"):
            self.assertNotIn(word, text)
        cur = app.discovery.get("id:Internet:current:2026-02-26")
        pri = app.discovery.get("id:Internet (prior account):prior:2026-02-26")
        self.assertEqual((cur["summary"], cur["href"]), ("Internet Statement", ""))
        self.assertEqual(pri["summary"], "Internet Statement Prior Account")
        names = {docs.build_pdf_filename(r["date"], r["summary"], "", record=docs.Document.from_dict(r))
                 for r in (cur, pri)}
        self.assertEqual(len(names), 2, "two statements of one day get two names")
        for n in names:
            self.assertNotRegex(n, r"\d{6,}")
        self.assertEqual(docs.told_apart_by(cur), "current account")
        self.assertEqual(docs.told_apart_by(pri), "prior account")
        self.assertEqual(app.config["pilot_count"], 1)

    def run_process(self, app, raises):
        self.listed(app)
        app._staged = True
        app._on_its_site = lambda: Page()
        app._delay = lambda *a, **k: None

        def boom(*a, **k):
            raise raises
        original = site.download_bill
        site.download_bill = boom
        try:
            with redirect_stdout(io.StringIO()) as said, self.assertRaises(SystemExit):
                app.process(app._select())
        finally:
            site.download_bill = original
        self.assertEqual(app.progress.data, {})
        self.assertEqual(app.stats["manual_review"] + app.stats["failed"], 0)
        self.assertFalse(list(app.paths.diagnostics.glob("*failure*")))
        return said.getvalue()

    def test_a_signed_out_session_stops_and_records_nothing(self):
        said = self.run_process(make_app(self.tmp, "--all"), site.SessionExpired("x"))
        self.assertIn("signed out", said)

    def test_a_passcode_stops_and_records_nothing(self):
        said = self.run_process(make_app(self.tmp, "--all"), site.OtpRequired("x"))
        self.assertIn("one-time passcode", said)
        self.assertNotIn("Press Enter", said)

    def test_discovery_boundaries(self):
        for page, words in [(Page(settle={site.STATEMENTS_PATH: f"https://{HOST}/login"}), "signed out"),
                            (Page(list_responses=[Resp(site.LIST_OPERATION, status=401)]), "signed out"),
                            (Page(body="enter the code we sent"), "one-time passcode")]:
            tmp = tempfile.mkdtemp()
            app = make_app(tmp, "--discover")
            app.page = lambda page=page: page
            out = io.StringIO()
            with redirect_stdout(out), self.assertRaises(SystemExit):
                app.cmd_discover()
            self.assertEqual(app.discovery.data, {})
            self.assertIn(words, out.getvalue())
            self.assertNotIn("Press Enter", out.getvalue())
            self.assertFalse(list(app.paths.diagnostics.glob("*failure*")))

    def test_resume_meets_the_same_boundary(self):
        app = make_app(self.tmp, "--resume")
        self.listed(app)
        from paperpull_core import listing
        listing.read_whole(app)
        app._on_its_site = lambda: Page()
        app._delay = lambda *a, **k: None
        app._staged = True
        original = site.download_bill
        site.download_bill = lambda *a, **k: (_ for _ in ()).throw(site.OtpRequired("x"))
        try:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                app.cmd_resume()
        finally:
            site.download_bill = original
        self.assertEqual(app.stats["manual_review"] + app.stats["failed"], 0)

    def test_nothing_is_pressed_without_the_download_folder(self):
        page = Page()
        with self.assertRaises(pressing.NotPressed) as stop:
            self.take(page, dl_dir=None)
        self.assertEqual(page.presses, 0)
        self.assertFalse(page.armed)
        self.assertIn("download folder", stop.exception.lines[0])
        app = make_app(self.tmp, "--all")
        self.listed(app)
        app._delay = lambda *a, **k: None
        page = Page()
        app._on_its_site = lambda: page
        self.assertFalse(app._staged, "unconfirmed until the browser accepts the folder")
        with redirect_stdout(io.StringIO()), self.assertRaises(pressing.Stop):
            app.process(app._select(limit=1))
        self.assertEqual(page.presses, 0)
        self.assertEqual(app.progress.data, {})

    def test_no_census_of_the_pages_requests(self):
        src = (HERE / "spectrum_docs.py").read_text()
        tree = ast.parse(src)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertNotIn("Requests", names)
        self.assertNotIn("api_census", src)
        self.assertNotIn("_requests", names)
        app = make_app(self.tmp, "--all")
        self.assertFalse(hasattr(app, "requests"))

    def test_a_download_again_hands_over_the_statements_own_copy(self):
        app = make_app(self.tmp, "--all", "--redownload")
        self.listed(app)
        app._staged = True
        app._delay = lambda *a, **k: None
        app._on_its_site = lambda: Page()
        doc = [d for d in app._select() if d.date == "2026-08-26"][0]
        app.progress.update(doc.key, {**doc.to_dict(), "pdf_path": "/saved/before.pdf", "downloaded_ok": True})
        got = {}

        def spy(*a, **k):
            got.update(k)
            return False
        original = site.download_bill
        site.download_bill = spy
        try:
            with redirect_stdout(io.StringIO()):
                app.download_one(Page(), doc, "x.pdf")
        finally:
            site.download_bill = original
        self.assertEqual(got["own_files"], ["/saved/before.pdf"])

    def test_pilot_gate(self):
        from paperpull_core.models import State

        class D:
            def __init__(self, k):
                self.key = k
        self.assertFalse(docs.pilot_failed({"a": {"state": State.COMPLETED.value}}, [D("a")]))
        self.assertTrue(docs.pilot_failed({}, [D("a")]))


class Privacy(WithFakeClick):
    """Nothing that names the account reaches a log line, the console, a
    record, a trace, a failure file or a file name, on success and on a
    refused press."""

    def test_success_and_failure_paths(self):
        app = make_app(self.tmp, "--all")
        seen = io.StringIO()
        handler = logging.StreamHandler(seen)
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        old = root.level
        root.setLevel(logging.DEBUG)
        try:
            with redirect_stdout(seen):
                for r in site.collect_download_docs(Page()):
                    app._record_rawdoc(r, site.BILLING_URL)
                app.discovery.save()
                from paperpull_core import listing
                listing.read_whole(app)
                app._delay = lambda *a, **k: None
                app._staged = True
                app._dl_dir.mkdir(parents=True)   # as set_download_dir makes it
                app._on_its_site = lambda: Page()
                app.process(app._select(limit=1))
                with self.assertRaises(site.ListStopped) as stop:
                    site.read_statement_list(Page(answer=list_answer(cur=["2026-09-26", "2026-09-26"])))
                self.assertNotIn(CUR, str(stop.exception))
                # A refused press: the run stops through main's own path.
                app._on_its_site = lambda: Page(on_press=lambda p: p.emit(
                    "response", Resp(site.PDF_OPERATION, {"statementId": p.detail_sid}, answer=pdf_answer())))
                todo = [d for d in app._select() if not app._already_done(d)][:1]
                with self.assertRaises(pressing.Stop) as press_stop:
                    app.process(todo)
                with self.assertRaises(SystemExit):
                    pressing.stop_run(app, press_stop.exception)
        finally:
            root.removeHandler(handler)
            root.setLevel(old)
        out = app.paths.root
        written = seen.getvalue()
        for p in out.rglob("*"):
            if p.is_file() and p.suffix in (".json", ".csv", ".txt", ".log"):
                written += p.read_text(encoding="utf-8", errors="ignore")
        names = " ".join(p.name for p in out.rglob("*"))
        self.assertTrue(list(app.paths.diagnostics.glob("*")), "the stop wrote its failure file")
        for word in (CUR, PRI, STREET, ADDRESS, sid(CUR, "2026-09-26"), sid(PRI, "2026-02-26"),
                     "Account=", "pdfStatementId"):
            self.assertNotIn(word, written, word)
            self.assertNotIn(word, names, word)
        pdfs = list(out.rglob("*.pdf"))
        self.assertEqual(len(pdfs), 1)
        self.assertEqual(pdfs[0].name, "2026-09-26 Spectrum Internet Statement.pdf")


class Source(unittest.TestCase):
    def test_one_press_only_through_pressing_click(self):
        tree = ast.parse((HERE / "spectrum_site.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        presses = [n for n in calls if n.func.attr == "click"]
        self.assertEqual(len(presses), 2, "a statement row and View Printable Statement")
        for n in presses:
            self.assertEqual(ast.unparse(n.func), "pressing.click")
        css = sorted(ast.unparse(k.value) for n in presses for k in n.keywords if k.arg == "css")
        self.assertEqual(css, ["pressing.BUTTONS", "pressing.LINKS"])
        # Each press sits behind its own predicate in the same function.
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and any(n in ast.walk(fn) for n in presses):
                called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
                self.assertTrue({"is_safe_row", "row_for"} & called or fn.name == "_press_for_statement", fn.name)
        for verb in ("dblclick", "tap", "fill", "type", "press", "select_option", "check", "uncheck",
                     "set_input_files", "dispatch_event", "press_sequentially", "press_once", "submit"):
            self.assertNotIn(verb, {n.func.attr for n in calls}, verb)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for raw in ("mouse", "keyboard", "touchscreen", "storage_state", "cookies",
                    "all_headers", "headers", "header_value", "body", "text"):
            self.assertNotIn(raw, attrs, raw)
        scripts = site._ARM_JS + site._RESULT_JS + site._ROWS_JS + site._COUNT_JS + site._RESTORE_JS
        for word in (".click(", "localStorage", "sessionStorage", "document.cookie", "fetch(", "XMLHttpRequest"):
            self.assertNotIn(word, scripts, word)

    def test_the_docs_module_presses_nothing(self):
        tree = ast.parse((HERE / "spectrum_docs.py").read_text(encoding="utf-8"))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for verb in ("click", "fill", "type", "press", "select_option", "check"):
            self.assertNotIn(verb, called, verb)

    def test_no_value_reaches_a_log_or_trace(self):
        src = (HERE / "spectrum_site.py").read_text()
        for line in src.splitlines():
            if ("log." in line or "_note(" in line) and re.search(
                    r"page\.url|statement_id|\.request|post_data|detail_url\(|answer_of\(|\.json\(", line):
                self.fail("a value reaches a log or trace: " + line.strip()[:100])

    def test_rules(self):
        from paperpull_core import doc_types
        rules = doc_types.load_rules(HERE / "document_rules.json")
        self.assertEqual(doc_types.classify_document(site.TITLE, rules)[:2], ("Statement", "Internet Statement"))

    def test_config(self):
        config = json.loads((HERE / "config.example.json").read_text())
        self.assertEqual(config["cdp_url"], "http://127.0.0.1:9291")
        self.assertEqual((config["pilot_count"], config["owner"]), (1, ""))


if __name__ == "__main__":
    unittest.main()


def test_a_pdf_id_and_a_digital_id_of_one_statement_are_the_same_statement():
    import base64
    mk = lambda kind, code="1", date="2026-09-26": base64.b64encode(
        f"Account=123456789012|StatementDate={date}|StatementCode={code}|{kind}_DOCUMENT".encode()).decode()
    assert site.same_statement(mk("PDF"), mk("DIGITAL"))
    assert site.same_statement(mk("PDF"), mk("DIGITAL").rstrip("="))  # the page's id comes unpadded
    assert site.same_statement(mk("PDF"), mk("PDF"))
    assert site.same_statement(mk("PDF"), mk("DIGITAL", code="2")), "the code is the document's, not the statement's"
    assert not site.same_statement(mk("PDF"), mk("DIGITAL", date="2026-08-26"))
    assert not site.same_statement(mk("PDF"), "not base64!")
    assert not site.same_statement(None, mk("PDF"))
    # the list's own ids stay PDF_DOCUMENT only
    import unittest
    with unittest.TestCase().assertRaises(site.ListStopped):
        site.decode_statement_id(mk("DIGITAL"))
