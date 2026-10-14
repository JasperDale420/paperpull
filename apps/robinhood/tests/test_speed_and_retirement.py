"""Checks for the control finder, the statement sources, View More and a
document taken from the page it is on. No browser.

The pages here are made up, and so are the statements, in the shape the
statement links take."""
import sys, unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import storage  # noqa: E402  binds this provider's AppSpec
import robinhood_docs  # noqa: E402
import robinhood_site as site  # noqa: E402
from paperpull_core import doc_types  # noqa: E402
from paperpull_core import storage as core_storage  # noqa: E402
from paperpull_core.models import State  # noqa: E402

INDIVIDUAL = "https://robinhood.com/account/reports-statements/individual"
RETIREMENT = "https://robinhood.com/account/reports-statements/retirement"
CRYPTO = "https://robinhood.com/account/reports-statements/crypto"
TAX = "https://robinhood.com/account/reports-statements/tax"


@pytest.fixture(autouse=True)
def default_names():
    storage.set_filename_owner("")
    core_storage.set_filename_patterns({})
    yield
    core_storage.set_filename_patterns({})


class FakePage:
    """evaluate() answers the page-side pass; the marked element verifies (or not)."""
    def __init__(self, hit, verify=True, marked=1):
        self.hit, self.verify, self.marked = hit, verify, marked
        self.evaluated = []
    def evaluate(self, js, arg=None):
        self.evaluated.append(arg); return self.hit
    def locator(self, sel):
        page = self
        class Handle:
            def evaluate(self, js, arg=None): return page.verify
        class El:
            def element_handle(self, **kw): return Handle() if page.marked else None
        class L:
            def count(self): return page.marked
            @property
            def first(self): return El()
        return L()


class FindControl(unittest.TestCase):
    def test_plain_download_link_is_found_once(self):
        page = FakePage({"own": "Monthly Statement - March 2014", "hasDl": True})
        self.assertIsNotNone(site.find_control(page, "Monthly Statement - March 2014"))
        self.assertEqual(page.evaluated[0][:2], ["Monthly Statement - March 2014", ""], "one pass, needle cut to 30 chars")
        self.assertEqual(len(page.evaluated[0]), 4, "needle, year, one-time token, mark attribute")

    def test_guard_refuses_forbidden_and_csv_and_non_download(self):
        long = "Download PDF " + "x" * 200 + " then Transfer funds"
        for own, dl in [("Download CSV", False), ("Transfer funds Download", False), ("Monthly Statement", False),
                        ("Sell and download", False), (long, False)]:
            page = FakePage({"i": 0, "own": own, "hasDl": dl})
            self.assertIsNone(site.find_control(page, "Monthly Statement"), own)

    def test_nothing_found_or_changed_page_presses_nothing(self):
        self.assertIsNone(site.find_control(FakePage(None), "Monthly Statement"))
        hit = {"own": "Download PDF", "hasDl": False}
        self.assertIsNone(site.find_control(FakePage(hit, verify=False), "Form 1099"), "the live element no longer matches")
        self.assertIsNone(site.find_control(FakePage(hit, marked=0), "Form 1099"), "a re-render dropped the mark")
        self.assertIsNone(site.find_control(FakePage(hit, marked=2), "Form 1099"), "two marks, so nothing is pressed")
        self.assertIsNone(site.find_control(FakePage(hit), ""))

    def test_year_is_passed_to_the_page(self):
        page = FakePage({"own": "Download PDF", "hasDl": False})
        self.assertIsNotNone(site.find_control(page, "Form 1099-B", year="2012"))
        self.assertEqual(page.evaluated[0][1], "2012")
        self.assertTrue(site.has_control(page, "Form 1099-B", "2012"))


class Challenge(unittest.TestCase):
    def test_challenge_text_reads_the_page_even_with_rows(self):
        class Page:
            def __init__(self, body): self.body = body
            def title(self): return "Statements | Robinhood"
            def locator(self, sel):
                page = self
                class L:
                    def inner_text(self, **kw): return page.body
                    def count(self): return 68
                return L()
        self.assertIsNone(site.challenge_text(Page("March 2014 Statement Download")))
        self.assertIn("challenge", site.challenge_text(Page("Please enter the code we sent to your phone")).lower())
        self.assertIsNotNone(site.challenge_text(Page("Too many requests, try again later")))
        late = ("March 2014 Statement Download\n" * 80) + "Enter the code we sent"
        self.assertGreater(len(late), 1500)
        self.assertIsNotNone(site.challenge_text(Page(late)), "a prompt after a long list is still a prompt")


class Duplicates(unittest.TestCase):
    def test_only_the_crypto_page_is_refused_a_duplicate(self):
        import robinhood_docs as docs
        listed = {("March 2014 Statement", "2014-03-31")}
        key = ("March 2014 Statement", "2014-03-31")
        self.assertTrue(docs.is_crypto_duplicate("https://robinhood.com/account/reports-statements/crypto", key, listed))
        self.assertFalse(docs.is_crypto_duplicate("https://robinhood.com/account/reports-statements/retirement", key, listed))
        self.assertFalse(docs.is_crypto_duplicate("https://robinhood.com/account/reports-statements/individual", key, listed))
        self.assertFalse(docs.is_crypto_duplicate("https://robinhood.com/account/reports-statements/crypto", ("other", "2014-03-31"), listed))
        self.assertFalse(docs.is_crypto_duplicate("https://robinhood.com/account/reports-statements/crypto", key, None))


class Sources(unittest.TestCase):
    def test_retirement_page_is_read(self):
        urls = dict(site.STATEMENT_PAGES)
        self.assertEqual(urls["https://robinhood.com/account/reports-statements/retirement"], "Retirement")
        self.assertEqual(site.account_for("https://robinhood.com/account/reports-statements/retirement"), "Retirement")
        self.assertEqual(site.account_for("https://robinhood.com/account/reports-statements/individual"), "")
        self.assertEqual([lbl for _, lbl in site.document_source_urls()],
                         ["statements", "retirement statements", "crypto statements", "tax"])
        for u in site.STATEMENT_URLS + [site.TAX_URL]:
            self.assertTrue(site.is_safe_url(u), u)


# -- View More, pressed to the end of the list or said to be short -------------

class _MorePage:
    """A list behind a View More link. It takes `presses` presses to reach
    the end of it, never when None, and a press raises when `covered`."""

    def __init__(self, presses=0, covered=False):
        self.left, self.covered = presses, covered
        self.tried, self.timeouts, self.fronted = 0, [], 0

    def bring_to_front(self):
        self.fronted += 1

    def wait_for_timeout(self, ms):
        pass

    def get_by_role(self, role, name=None):
        page = self

        class First:
            def is_visible(self):
                return True

            def inner_text(self, **kw):
                return "View More"

            def click(self, **kw):
                page.tried += 1
                page.timeouts.append(kw.get("timeout"))
                if page.covered:
                    raise TimeoutError("another element would receive the click")
                if page.left is not None:
                    page.left -= 1

        class Found:
            first = First()

            def count(self):
                return 1 if role == "link" and page.left != 0 else 0
        return Found()


def test_view_more_is_pressed_to_the_end_of_the_list():
    page = _MorePage(presses=3)
    assert site.expand_all(page) is True
    assert page.tried == 3 and page.left == 0
    assert page.fronted == 0, "the tab is brought forward where the page is opened"


def test_a_view_more_that_cannot_be_pressed_says_the_list_is_short():
    """Pressed once and given up on, never sixty times, and never said to
    be the end of the list."""
    page = _MorePage(presses=3, covered=True)
    assert site.expand_all(page) is False
    assert page.tried == 1
    # A press keeps the thirty seconds the context gives it, not the eight
    # it was cut to.
    assert all(t is None or t >= 30000 for t in page.timeouts), page.timeouts


def test_a_view_more_that_never_ends_says_the_list_is_short():
    page = _MorePage(presses=None)
    assert site.expand_all(page) is False
    assert page.tried == 60


# -- a document taken from the page it is on ----------------------------------

def _text_pdf(path: Path, lines) -> None:
    """A one page PDF whose lines pypdf reads back."""
    stream = "".join("BT /F1 12 Tf 72 %d Td (%s) Tj ET\n" % (720 - 20 * i, line)
                     for i, line in enumerate(lines)).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out) + b" " * 4000)


class _Tab:
    """A tab that goes where it is sent, unless the site sends it on, and
    shows the headings `headings` and the statement titles `titles` give
    for the address it is at."""

    def __init__(self, url="", lands=None, headings=None, titles=None):
        self.url, self.lands = url, dict(lands or {})
        self.headings = dict(headings or {})
        self.titles = dict(titles or {})
        self.opened, self.fronted = [], 0

    def evaluate(self, js, arg=None):
        if js == site._COLLECT_JS and self.url in self.titles:
            return [{"title": t, "is_pdf": True} for t in self.titles[self.url]]
        return self.headings.get(self.url) if js == site._HEADINGS_JS else None

    def goto(self, url, **kw):
        self.opened.append(url)
        self.url = self.lands.get(url, url)

    def wait_for_timeout(self, ms):
        pass

    def bring_to_front(self):
        self.fronted += 1


def _app(tmp_path):
    app = object.__new__(robinhood_docs.App)
    app.args = SimpleNamespace(redownload=False, start_date=None, end_date=None,
                               year=None, type=None, max_docs=None, dry_run=False)
    app.config = {"max_path_length": 240, "min_pdf_bytes": 1000, "owner": "",
                  "document_types": ["Statement", "Tax Document"],
                  "delay_min_seconds": 0, "delay_max_seconds": 0, "pilot_count": 5}
    app.paths = storage.Paths(tmp_path)
    app.paths.ensure()
    app.progress = storage.JsonStore(app.paths.progress_json)
    app.discovery = storage.JsonStore(app.paths.discovery_json)
    app.index_csv = storage.CsvFile(app.paths.document_index_csv,
                                    storage.DOCUMENT_INDEX_COLUMNS)
    app.rules = doc_types.load_rules()
    app.stats = {"mode": "", "discovered": 0, "manual_review": 0, "new_files": [],
                 "dates": [], "statements": 0, "tax_documents": 0,
                 "insurance_documents": 0, "other": 0, "validation_failures": 0,
                 "duplicate_filenames": 0, "failed": 0, "skipped_out_of_scope": 0,
                 "skipped_completed": 0, "wrong_document": 0, "crypto_refused": 0,
                 "notes": []}
    app._journal = SimpleNamespace(checkpoint=lambda *a, **k: None,
                                   op=lambda *a, **k: None)
    app.check_session = lambda page, challenge=None: False
    app.write_failure = lambda *a, **k: None
    return app


def _statement(title="January 2010 Monthly Statement", date="2010-01-31",
               source=INDIVIDUAL, account=""):
    summary = ("%s Monthly Statement" % account) if account else "Monthly Statement"
    return robinhood_docs.Document(title=title, category=doc_types.STATEMENT,
                                   summary=summary, date=date, source_url=source,
                                   account=account)


def _presses(monkeypatch, lines=None):
    """Where each press was made. A press saves a statement that prints
    its own title and the page it came from, or `lines` when given."""
    pressed = []

    def press(page, title, out_path, year=""):
        pressed.append(page.url)
        _text_pdf(Path(out_path), lines or ["Robinhood Securities LLC", title, page.url])
        return True
    monkeypatch.setattr(site, "download_named", press)
    return pressed


def _retirement(title="January 2010 Monthly Statement", date="2010-01-31"):
    return _statement(title, date, source=RETIREMENT, account="Retirement")


def _statements_saved(app):
    return sorted(p.name for p in app.paths.folder_for(doc_types.STATEMENT).glob("*.pdf"))


def _take(app, page, doc):
    app.download_one(page, doc, storage.build_pdf_filename(doc.date, doc.summary, "",
                                                           record=doc))
    return app.progress.get(doc.key) or {}


def test_the_tab_is_brought_forward_once_for_each_page_it_opens(tmp_path, monkeypatch):
    """Not before every press. In the person's own browser that pulled the
    app's tab in front of theirs every few seconds."""
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    monkeypatch.setattr(site, "has_control", lambda page, title, year="": True)
    page = _Tab()
    _take(app, page, _statement("February 2010 Monthly Statement", "2010-02-28"))
    _take(app, page, _statement())
    assert page.opened == [INDIVIDUAL] and pressed == [INDIVIDUAL, INDIVIDUAL]
    assert page.fronted == 1


def _listed(app, *items):
    """Each (page, date) in discovery as a statement that page listed."""
    for source, date in items:
        doc = _statement("Statement of %s" % date, date, source=source,
                         account=site.account_for(source))
        app.discovery.update(doc.key, doc.to_dict())


def test_documents_are_taken_page_by_page_newest_first(tmp_path):
    app = _app(tmp_path)
    _listed(app, (CRYPTO, "2014-03-31"), (INDIVIDUAL, "2014-03-31"), (TAX, "2013-12-31"),
            (RETIREMENT, "2014-03-31"), (INDIVIDUAL, "2014-02-28"), (RETIREMENT, "2014-02-28"),
            (CRYPTO, "2014-02-28"), (INDIVIDUAL, ""), ("", "2014-01-31"))
    assert [(d.source_url, d.date) for d in app._select()] == [
        (INDIVIDUAL, "2014-03-31"), (INDIVIDUAL, "2014-02-28"), (INDIVIDUAL, ""),
        (RETIREMENT, "2014-03-31"), (RETIREMENT, "2014-02-28"),
        (CRYPTO, "2014-03-31"), (CRYPTO, "2014-02-28"), (TAX, "2013-12-31"), ("", "2014-01-31")]


def test_the_pilot_takes_the_newest_across_every_page(tmp_path, monkeypatch):
    app = _app(tmp_path)
    _listed(app, (INDIVIDUAL, "2014-03-31"), (INDIVIDUAL, "2014-02-28"), (RETIREMENT, "2014-03-31"),
            (CRYPTO, "2014-03-31"), (INDIVIDUAL, "2014-01-31"))
    app.config["pilot_count"] = 3
    taken = []
    monkeypatch.setattr(app, "cmd_discover", lambda quiet=False: 0)
    monkeypatch.setattr(app, "process", lambda docs, dry_run=False: taken.extend(docs))
    monkeypatch.setattr(app, "_pilot_report", lambda docs: None)
    app.cmd_pilot()
    assert [(d.source_url, d.date) for d in taken] == [
        (INDIVIDUAL, "2014-03-31"), (RETIREMENT, "2014-03-31"), (CRYPTO, "2014-03-31")]
    assert [d.source_url for d in app._select()][:3] == [INDIVIDUAL] * 3, \
        "a run without a limit takes a page at a time"


def test_max_docs_takes_the_newest_across_every_page(tmp_path):
    """--max-docs N in Run All took N statements of the individual page
    first, once a run took its documents page by page."""
    app = _app(tmp_path)
    _listed(app, (INDIVIDUAL, "2014-02-28"), (CRYPTO, "2014-03-31"), (INDIVIDUAL, "2014-01-31"),
            (RETIREMENT, "2014-03-31"), (INDIVIDUAL, "2014-03-31"))
    app.args.max_docs = 3
    assert [(d.source_url, d.date) for d in app._select()] == [
        (INDIVIDUAL, "2014-03-31"), (RETIREMENT, "2014-03-31"), (CRYPTO, "2014-03-31")]


def _shows_the_control(monkeypatch, shows=True):
    monkeypatch.setattr(site, "has_control", lambda page, title, year="": shows)


def test_a_page_kept_open_that_shows_the_control_is_not_opened_again(tmp_path, monkeypatch):
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    _shows_the_control(monkeypatch)
    page = _Tab(url=INDIVIDUAL)
    assert _take(app, page, _statement())["downloaded_ok"]
    assert page.opened == [] and pressed == [INDIVIDUAL]


def test_a_page_kept_open_that_no_longer_shows_the_control_is_opened_again(
        tmp_path, monkeypatch):
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    _shows_the_control(monkeypatch, shows=False)
    page = _Tab(url=INDIVIDUAL)
    _take(app, page, _statement())
    assert page.opened == [INDIVIDUAL] and pressed == [INDIVIDUAL]


def test_the_page_is_opened_again_after_a_sign_in(tmp_path, monkeypatch):
    """Signing in again at a console leaves the tab on the documents page,
    so the page the document is on is opened again, though the address
    looked like it before the check."""
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    _shows_the_control(monkeypatch)
    asked = iter([True])
    app.check_session = lambda page, challenge=None: next(asked, False)
    page = _Tab(url=INDIVIDUAL)
    _take(app, page, _statement())
    assert page.opened == [INDIVIDUAL] and pressed == [INDIVIDUAL]


def test_a_document_of_another_page_opens_its_own(tmp_path, monkeypatch):
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    _shows_the_control(monkeypatch)
    page = _Tab(url=TAX)
    _take(app, page, _statement())
    assert page.opened == [INDIVIDUAL] and pressed == [INDIVIDUAL]


def _prompt_over_the_rows(app, monkeypatch, answer):
    """A page kept open, still showing the statement's control, with a
    prompt over its rows that only its whole text shows. The app's own
    check runs, with `answer` from the console, None for nobody there."""
    from paperpull_core import browser as browser_launcher
    del app.check_session
    monkeypatch.setattr(site, "has_control", lambda page, title, year="": True)
    monkeypatch.setattr(site, "detect_security_challenge", lambda page: None)
    monkeypatch.setattr(site, "challenge_text",
                        lambda page: "Security challenge detected: 'enter the code'")
    monkeypatch.setattr(browser_launcher, "ask_or_none", lambda prompt: answer)


def test_a_prompt_over_a_kept_page_stops_the_run(tmp_path, monkeypatch):
    """The check trusts a page that shows rows, and the prompt only sent
    the page to be opened again, after which the check trusted the rows
    once more and the press went ahead."""
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    _prompt_over_the_rows(app, monkeypatch, answer=None)
    page = _Tab(url=INDIVIDUAL)
    with pytest.raises(SystemExit) as stopped:
        _take(app, page, _statement())
    assert stopped.value.code == 0
    assert pressed == [] and page.opened == []


def test_a_prompt_answered_at_the_console_opens_the_page_again(tmp_path, monkeypatch):
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    _prompt_over_the_rows(app, monkeypatch, answer="")
    page = _Tab(url=INDIVIDUAL)
    rec = _take(app, page, _statement())
    assert page.opened == [INDIVIDUAL] and pressed == [INDIVIDUAL]
    assert rec["downloaded_ok"]


# -- a retirement statement is the retirement account's own ------------------

def test_a_retirement_statement_is_not_pressed_on_another_address(tmp_path, monkeypatch):
    """An account holder whose retirement address opened another page
    would have the statement of that title there pressed and filed as
    retirement."""
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    expanded = []
    monkeypatch.setattr(site, "expand_all", lambda page: expanded.append(page.url) or True)
    page = _Tab(lands={RETIREMENT: INDIVIDUAL}, headings={INDIVIDUAL: ["Individual investing"]})
    doc = _retirement()
    rec = _take(app, page, doc)
    assert pressed == [] and expanded == [], "nothing is pressed on another page"
    assert rec["state"] == State.NEEDS_MANUAL_REVIEW.value and not rec.get("downloaded_ok")
    assert "opened another address" in rec["notes"]
    assert not app._already_done(doc)
    assert _statements_saved(app) == []


def test_a_retirement_statement_is_not_pressed_on_a_page_naming_no_retirement_account(
        tmp_path, monkeypatch):
    app = _app(tmp_path)
    pressed = _presses(monkeypatch)
    page = _Tab(headings={RETIREMENT: ["Individual investing"]})
    doc = _retirement()
    rec = _take(app, page, doc)
    assert page.opened == [RETIREMENT] and pressed == []
    assert rec["state"] == State.NEEDS_MANUAL_REVIEW.value
    assert "did not name a retirement account" in rec["notes"]
    assert not app._already_done(doc)
    assert _statements_saved(app) == []


IRA_TITLE = "January 2010 %s Consolidated IRA Statement" % chr(0x2013)


def test_a_retirement_statement_is_pressed_on_a_page_its_statements_name(
        tmp_path, monkeypatch):
    """The only heading is the account holder's name, and every statement
    the page lists names the IRA, so the page is the retirement account's
    own and the statement is pressed and saved there. Where one statement
    names the individual account, nothing is pressed."""
    app = _app(tmp_path)
    pressed = _presses(monkeypatch, lines=["Robinhood Securities LLC",
                                           "January 2010 Consolidated IRA Statement",
                                           RETIREMENT])
    page = _Tab(headings={RETIREMENT: ["Dana Example"]}, titles={RETIREMENT: [IRA_TITLE]})
    rec = _take(app, page, _retirement(title=IRA_TITLE))
    assert pressed == [RETIREMENT]
    assert rec["state"] == State.COMPLETED.value and rec["downloaded_ok"], rec.get("notes")

    other = "February 2010 %s Individual Investing Account Statement" % chr(0x2013)
    page = _Tab(headings={RETIREMENT: ["Dana Example"]},
                titles={RETIREMENT: [IRA_TITLE.replace("January", "February"), other]})
    doc = _retirement(title=IRA_TITLE.replace("January", "February"), date="2010-02-28")
    rec = _take(app, page, doc)
    assert pressed == [RETIREMENT], "nothing is pressed on a page one statement says is not its"
    assert "did not name a retirement account" in rec["notes"]
    assert not app._already_done(doc)


def test_a_retirement_statement_that_is_the_individual_file_is_refused(tmp_path, monkeypatch):
    """The page named a retirement account and handed over the individual
    statement of the month all the same. Nothing on the page could tell,
    and the file can. The individual statement's digest is kept with its
    record, so this holds after its file is deleted once imported."""
    app = _app(tmp_path)
    same = ["Robinhood Securities LLC", "Account statement", "January 2010"]
    pressed = _presses(monkeypatch, lines=same)
    page = _Tab(headings={RETIREMENT: ["Retirement"]})
    individual = _statement()
    saved = _take(app, page, individual)
    assert saved["downloaded_ok"] and saved["pdf_sha256"]
    Path(saved["pdf_path"]).unlink()
    doc = _retirement()
    rec = _take(app, page, doc)
    assert pressed == [INDIVIDUAL, RETIREMENT]
    assert rec["state"] == State.NEEDS_MANUAL_REVIEW.value and rec["pdf_path"] == ""
    assert "same file as the individual statement" in rec["notes"]
    assert not app._already_done(doc), "the copy in Manual Review must not count as done"
    assert app.stats["wrong_document"] == 1
    assert _statements_saved(app) == []
    assert [p.name for p in app.paths.manual_review.glob("*.pdf")] == [
        "2010-01-31 Robinhood Retirement Monthly Statement.pdf"]
    assert app.progress.get(individual.key)["downloaded_ok"], "the individual one stands"


def test_a_retirement_statement_rendered_again_from_the_individual_html_is_refused(
        tmp_path, monkeypatch):
    """A month served as HTML is printed to PDF on each run, and the print
    stamps its own creation time, so one statement rendered twice is two
    different files. The retirement page that handed over the individual
    statement's HTML is still caught, by the digest of the PDF's text, and
    this holds after the individual file is deleted."""
    app = _app(tmp_path)
    # As short as a real HTML month's first page, which is all it holds.
    same = ["1 Example Plaza, Suite 100, Example City, ZZ 00000 help@robinhood.com",
            "01/01/2010 to 01/31/2010 # # Individual Account #:00000 1 Invented Street"]
    assert len(" ".join(same)) >= robinhood_docs.MIN_TEXT_FOR_DIGEST
    stamp = iter(range(1, 10))

    def press(page, title, out_path, year=""):
        _text_pdf(Path(out_path), same)
        with open(out_path, "ab") as f:
            f.write(b"%% render %d\n" % next(stamp))
        return True
    monkeypatch.setattr(site, "download_named", press)
    page = _Tab(headings={RETIREMENT: ["Retirement"]})
    individual = _statement()
    saved = _take(app, page, individual)
    assert saved["downloaded_ok"] and saved["text_sha256"]
    Path(saved["pdf_path"]).unlink()
    rec = _take(app, page, _retirement())
    assert rec["state"] == State.NEEDS_MANUAL_REVIEW.value and rec["pdf_path"] == ""
    assert "same file as the individual statement" in rec["notes"]
    assert app.stats["wrong_document"] == 1


def test_a_near_empty_text_says_nothing_of_whose_statement_it_is(tmp_path, monkeypatch):
    """Two statements of one date whose text is only a logo's words differ
    in their bytes, and both are kept. So little text gives no digest."""
    app = _app(tmp_path)
    stamp = iter(range(1, 10))

    def press(page, title, out_path, year=""):
        _text_pdf(Path(out_path), ["Robinhood", "1"])
        with open(out_path, "ab") as f:
            f.write(b"%% render %d\n" % next(stamp))
        return True
    monkeypatch.setattr(site, "download_named", press)
    page = _Tab(headings={RETIREMENT: ["Retirement"]})
    saved = _take(app, page, _statement())
    assert saved["downloaded_ok"] and saved["text_sha256"] == ""
    rec = _take(app, page, _retirement())
    assert rec["state"] == State.COMPLETED.value, rec.get("notes")


def test_statements_whose_text_differs_are_both_saved(tmp_path, monkeypatch):
    """The text digest catches only the same text. A retirement statement
    printing its own account is kept beside the individual one."""
    app = _app(tmp_path)
    _presses(monkeypatch)
    page = _Tab(headings={RETIREMENT: ["Retirement"]})
    _take(app, page, _statement())
    rec = _take(app, page, _retirement())
    assert rec["state"] == State.COMPLETED.value, rec.get("notes")


def test_an_older_individual_record_is_read_from_its_file(tmp_path, monkeypatch):
    """A statement saved before the digest was kept is compared by its
    file, while the file is there."""
    app = _app(tmp_path)
    same = ["Robinhood Securities LLC", "Account statement", "January 2010"]
    _presses(monkeypatch, lines=same)
    page = _Tab(headings={RETIREMENT: ["Retirement"]})
    individual = _statement()
    _take(app, page, individual)
    app.progress.update(individual.key, {"pdf_sha256": ""})
    rec = _take(app, page, _retirement())
    assert rec["state"] == State.NEEDS_MANUAL_REVIEW.value
    assert app.stats["wrong_document"] == 1


def test_a_retirement_statement_of_its_own_is_saved_beside_the_individual_one(
        tmp_path, monkeypatch):
    app = _app(tmp_path)
    _presses(monkeypatch)
    page = _Tab(headings={RETIREMENT: ["Retirement"]})
    _take(app, page, _statement())
    rec = _take(app, page, _retirement())
    assert rec["state"] == State.COMPLETED.value and rec["downloaded_ok"]
    assert app.stats["wrong_document"] == 0
    assert _statements_saved(app) == ["2010-01-31 Robinhood Monthly Statement.pdf",
                                      "2010-01-31 Robinhood Retirement Monthly Statement.pdf"]


if __name__ == "__main__":
    unittest.main()
