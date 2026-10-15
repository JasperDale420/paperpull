"""Each account's statement is listed, kept, named and taken as its own (#96).

An E*TRADE sign-in can hold several accounts, and the documents list shows
each document's account in a column of its own. The list's own
answer, searchItems, carries it as displayMultipleAccounts. Discovery read
it, put it through the redaction meant for diagnostic files, which turns a
masked "...1234" into "...####", and then dropped it, so no record, file
name or index row said which account a statement was from. Two accounts'
statements of one title and day were one document, and of two rows naming
it the download refused both, or took whichever one the page showed.

Now the label is kept whole in the record, the key holds the masked digits
it ends with, a sign-in of several accounts puts the label in the file name
and the index's Document Summary, and the download takes only the link
naming its own account, by its masked tail. For a sign-in of one account,
records kept before move to the key the account gives them, so nothing
saved is fetched again and every name stays. With several, which account
a record kept before holds cannot be told, so it stays as it is and each
account's statement is taken again under a name that says the account.

Everything here is invented.
"""
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import etrade_docs
import etrade_site as site
from paperpull_core import doc_types, naming, renaming
from paperpull_core.models import State

DAY = "2025-08-31"
SHORT = "08/31/25"
BROKERAGE = "Brokerage Statement"
RETIREMENT = "Retirement Statement"
FIRST = "Invented Brokerage -1111"
SECOND = "Invented Roth IRA -2222"


def _entry(title, account, guid, day=DAY):
    return {"documentGuid": guid, "documentId": "id-" + guid, "documentTypeName": "STATEMENTS",
            "documentTitle": title, "documentDate": day + "T00:00:00.000Z",
            "displayMultipleAccounts": account}


def _raw(title, account, guid, day=DAY):
    raw = site.RawDoc(title=title, account=account, date_text=day, kind="statement")
    raw.guid = "%s|id-%s" % (guid, guid)
    return raw


# --- what the list says ---------------------------------------------------------

def test_the_list_keeps_each_accounts_label_whole():
    """The masked digits are what say which account it is, and the
    redaction for diagnostic files masked them again."""
    got = site._docs_from_api({"defaultDocumentList": [
        _entry(BROKERAGE, "Invented  Brokerage ...1111", "g1"), _entry(BROKERAGE, SECOND, "g2")]})
    assert [d["account"] for d in got] == ["Invented Brokerage ...1111", SECOND]
    assert [d["guid"] for d in got] == ["g1|id-g1", "g2|id-g2"]


def test_two_accounts_statements_of_one_day_are_both_listed(monkeypatch):
    """Kept by date and title alone, the second account's was dropped."""
    body = {"defaultDocumentList": [_entry(BROKERAGE, FIRST, "g1"), _entry(BROKERAGE, SECOND, "g2")]}

    def capturing(page, capture):
        capture.append(body)
        return True
    monkeypatch.setattr(site, "goto_docs_capturing", capturing)
    monkeypatch.setattr(site, "widen_date_filter", lambda page, wider, trace=None: False)
    docs = site.collect_download_docs(None)
    assert [(d.title, d.account) for d in docs] == [(BROKERAGE, FIRST), (BROKERAGE, SECOND)]
    assert [d.guid for d in docs] == ["g1|id-g1", "g2|id-g2"]
    assert site._LISTED_ACCOUNTS[(DAY, "brokerage statement")] == {FIRST.lower(), SECOND.lower()}
    assert "Invented" not in json.dumps(site.DISCOVERY_TRACE)


@pytest.mark.parametrize("label, tails", [
    ("Invented Brokerage -1111", ["1111"]),
    ("Invented Brokerage - 1111", ["1111"]),
    ("Individual ...1111", ["1111"]),
    ("Invented Card (...1111)", ["1111"]),
    ("Kids 2031 Fund ****1111", ["1111"]),
    ("Invented Brokerage -1111, Invented Roth IRA -2222", ["1111", "2222"]),
    ("Joint Account", []),
    ("Account 1111", []),
])
def test_an_account_is_known_by_the_masked_digits_its_label_ends_with(label, tails):
    assert site.account_tails(label) == tails


def test_a_nickname_changed_on_etrade_keeps_the_key():
    """The words before the digits are a nickname a person can change, and a
    key that moved with it would take every statement again."""
    def key(account):
        return etrade_docs.Document(category="Statement", date=DAY, title=BROKERAGE,
                                    account=account).key
    assert key(FIRST) == key("A Name Changed Later -1111") == "Statement:%s:%s:1111" % (DAY, BROKERAGE)
    assert key(FIRST) != key("Invented Brokerage -3333")
    assert key("Joint Account").endswith(":Joint Account")


# --- discovery, through the app's own path --------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    """The app's own discovery, with the page's list stood in for."""
    a = etrade_docs.App.__new__(etrade_docs.App)
    a.args = SimpleNamespace(start_date=None, end_date=None, redownload=False, dry_run=False,
                             max_docs=None, type=None, year=None, apply=False)
    a.config = {"output_dir": str(tmp_path), "document_types": ["Statement", "Tax Document"],
                "min_pdf_bytes": 2000, "max_path_length": 240, "owner": "",
                "delay_min_seconds": 0, "delay_max_seconds": 0}
    a.paths = storage.Paths(tmp_path)
    a.paths.ensure()
    a.progress = storage.JsonStore(a.paths.progress_json, a.paths.backups)
    a.discovery = storage.JsonStore(a.paths.discovery_json, a.paths.backups)
    a.progress.load()
    a.discovery.load()
    a.index_csv = storage.CsvFile(a.paths.document_index_csv, storage.DOCUMENT_INDEX_COLUMNS,
                                  a.paths.backups)
    a.rules = doc_types.load_rules()
    a.stats = {"skipped_out_of_scope": 0, "discovered": 0, "failed": 0}
    a.page = lambda: None
    a.check_session = lambda page: None
    monkeypatch.setattr(site, "goto_documents", lambda page: True)
    listed: list = []
    monkeypatch.setattr(site, "collect_download_docs", lambda page: list(listed))

    def discover(*raws):
        listed[:] = raws
        a.cmd_discover(quiet=True)
        return a
    a.listing = discover
    return a


def _name(rec):
    return storage.build_pdf_filename(rec["date"], rec["summary"], "", record=rec)


def test_each_accounts_statement_of_one_day_is_its_own_record_and_name(app):
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(BROKERAGE, SECOND, "g2"))
    recs = sorted(app.discovery.data.values(), key=lambda r: r["account"])
    assert [r["account"] for r in recs] == [FIRST, SECOND], app.discovery.data
    assert len({etrade_docs.Document.from_dict(r).key for r in recs}) == 2
    assert [r["guid"] for r in recs] == ["g1|id-g1", "g2|id-g2"]
    assert [r["summary"] for r in recs] == ["Account Statement - " + FIRST, "Account Statement - " + SECOND]
    assert [_name(r) for r in recs] == ["%s ETRADE Account Statement - %s.pdf" % (DAY, FIRST),
                                        "%s ETRADE Account Statement - %s.pdf" % (DAY, SECOND)]
    assert [naming.fields_of(r)["account"] for r in recs] == [FIRST, SECOND]


def test_a_sign_in_of_one_account_keeps_the_names_it_had(app):
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(BROKERAGE, FIRST, "g3", day="2025-07-31"))
    recs = list(app.discovery.data.values())
    assert [r["account"] for r in recs] == [FIRST, FIRST]
    assert {r["summary"] for r in recs} == {"Account Statement"}
    assert _name(recs[0]) == "%s ETRADE Account Statement.pdf" % DAY
    assert naming.fields_of(recs[0])["account"] == FIRST


# --- records kept before ---------------------------------------------------------

def _legacy_key(title, day=DAY):
    return "Statement:%s:%s:Unnamed" % (day, title)


def _key(title, tail, day=DAY):
    return "Statement:%s:%s:%s" % (day, title, tail)


def _saved_before(app, title, name, day=DAY, done=True):
    """A statement the app listed and saved before #96, under the key with an
    key with no account, "Unnamed", its file on disk and its index row."""
    doc = etrade_docs.Document(title=title, category="Statement", summary="Account Statement",
                               date=day, confidence="High", source_url=site.BILLING_URL)
    assert doc.key == _legacy_key(title, day)
    path = app.paths.folder_for("Statement") / name
    if done:
        path.write_bytes(b"%PDF-1.4\n% invented\n" + b"0" * 2100)
        doc.pdf_path, doc.pdf_filename = str(path), path.name
        doc.downloaded_ok = True
        doc.state = State.COMPLETED.value
        app._write_row(doc, "Downloaded", "Completed")
    else:
        doc.state = State.NEEDS_MANUAL_REVIEW.value
    app.progress.data[doc.key] = doc.to_dict()
    app.discovery.data[doc.key] = dict(doc.to_dict(), state=doc.state)
    app.progress.save()
    app.discovery.save()
    return path


def test_a_statement_saved_before_moves_to_its_accounts_key_and_is_never_taken_again(app):
    """A sign-in of one account. Every record moves to the key its account
    gives it, keeps what it was, and no file is renamed."""
    july = _saved_before(app, BROKERAGE, "2025-07-31 ETRADE Account Statement.pdf", day="2025-07-31")
    august = _saved_before(app, BROKERAGE, "%s ETRADE Account Statement.pdf" % DAY)
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(BROKERAGE, FIRST, "g3", day="2025-07-31"))
    for day, path in (("2025-07-31", july), (DAY, august)):
        assert _legacy_key(BROKERAGE, day) not in app.progress.data
        assert _legacy_key(BROKERAGE, day) not in app.discovery.data
        rec = app.progress.data[_key(BROKERAGE, "1111", day)]
        assert (rec["downloaded_ok"], rec["state"], rec["pdf_path"]) == (True, "Completed", str(path))
        assert (rec["account"], rec["summary"]) == (FIRST, "Account Statement")
    assert all(app._already_done(d) for d in app._select())
    assert len(app._select()) == 2
    result = renaming.run_for(app, apply_changes=True, say=lambda *_a: None)
    assert result.renamed == 0 and july.exists() and august.exists()
    backups = sorted(p.name.split(".")[0] for p in app.paths.backups.iterdir())
    assert "progress" in backups and "discovery" in backups


def test_a_history_moved_with_minimal_moves_the_same(app):
    """--minimal keeps no title or date in a record, so it is moved by its
    key alone."""
    app.progress.data[_legacy_key(BROKERAGE)] = {"state": "Completed", "downloaded_ok": True,
                                                 "pdf_path": ""}
    app.listing(_raw(BROKERAGE, FIRST, "g1"))
    assert app.progress.data[_key(BROKERAGE, "1111")]["downloaded_ok"] is True
    assert app._already_done(etrade_docs.Document.from_dict(app.discovery.data[_key(BROKERAGE, "1111")]))


def test_several_accounts_records_kept_before_stay_and_each_statement_is_taken_again(app):
    """The reporter's case. Two accounts' statements of one day with titles
    of their own were both saved, named alike, " (2)" telling them apart.
    With more than one account, nothing proves which account a record kept
    before holds, so each stays done under its old key and its file keeps
    its name, and each account's statement is taken again under a name
    that says the account (review of #96)."""
    brokerage = _saved_before(app, BROKERAGE, "%s ETRADE Account Statement.pdf" % DAY)
    retirement = _saved_before(app, RETIREMENT, "%s ETRADE Account Statement (2).pdf" % DAY)
    before = json.loads(json.dumps(app.progress.data))
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(RETIREMENT, SECOND, "g2"))
    assert app.progress.data == before
    taken = sorted((d.account, _name(d.to_dict())) for d in app._select() if not app._already_done(d))
    assert taken == [(FIRST, "%s ETRADE Account Statement - %s.pdf" % (DAY, FIRST)),
                     (SECOND, "%s ETRADE Account Statement - %s.pdf" % (DAY, SECOND))]
    result = renaming.run_for(app, apply_changes=True, say=lambda *_a: None)
    assert result.renamed == 0 and brokerage.exists() and retirement.exists()


def test_lists_that_leave_one_accounts_statement_out_credit_nothing_to_another(app):
    """The review's case. Before #96 two accounts' Brokerage Statements of a
    day were one record, saved once with either account's PDF. These lists
    leave the second account's August and July statements out and show its
    June Retirement Statement. Moved to the first account's key, the record
    kept before would mark the first account's statement saved when it may
    hold the second's."""
    days = ("2025-08-31", "2025-07-31", "2025-06-30")
    for day in days:
        _saved_before(app, BROKERAGE, "%s ETRADE Account Statement.pdf" % day, day=day)
    _saved_before(app, RETIREMENT, "2025-06-30 ETRADE Account Statement (2).pdf", day="2025-06-30")
    before = json.loads(json.dumps(app.progress.data))
    app.listing(_raw(BROKERAGE, FIRST, "a1", day="2025-08-31"), _raw(BROKERAGE, FIRST, "a2", day="2025-07-31"),
                _raw(RETIREMENT, SECOND, "b3", day="2025-06-30"), _raw(BROKERAGE, FIRST, "a3", day="2025-06-30"))
    assert app.progress.data == before
    taken = sorted((d.date, d.title, d.account) for d in app._select() if not app._already_done(d))
    assert taken == sorted([(day, BROKERAGE, FIRST) for day in days] + [("2025-06-30", RETIREMENT, SECOND)])


def test_two_accounts_with_one_masked_tail_never_share_a_key(app):
    """Their tails cannot tell them apart, so each is keyed by its whole
    label, and each is counted as an account of its own."""
    twin = "Invented Roth IRA -1111"
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(BROKERAGE, twin, "g2"))
    assert sorted(app.discovery.data) == sorted([_key(BROKERAGE, FIRST), _key(BROKERAGE, twin)])
    assert sorted(r["summary"] for r in app.discovery.data.values()) == [
        "Account Statement - " + FIRST, "Account Statement - " + twin]


def test_two_accounts_sharing_digits_on_one_day_keep_two_keys_and_a_label_with_none_adds_none(app, caplog):
    """The one case the digits cannot keep two keys apart, two labels with
    one tail on documents of the same date and title, beside a tax form
    whose label shows no digits. That label is no account of its own."""
    caplog.set_level(logging.INFO)
    twin = "Invented Roth IRA -1111"
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(BROKERAGE, twin, "g2"),
                _raw("Form 1099 Composite", "Multiple Accounts", "g3", day="2025-02-16"))
    assert {_key(BROKERAGE, FIRST), _key(BROKERAGE, twin)} <= set(app.discovery.data)
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "2 account(s)" in said, said


def test_one_account_worded_two_ways_moves_its_history_one_to_one(app):
    """The second review's case. Older documents keep the nickname the
    account had then. Same digits on documents that never share a date and
    title are one account, keyed by the digits, so its history moves and
    nothing is taken again or renamed."""
    old = "Old Nickname -1111"
    days = ("2025-06-30", "2025-07-31", "2025-08-31")
    files = [_saved_before(app, BROKERAGE, "%s ETRADE Account Statement.pdf" % day, day=day) for day in days]
    app.listing(_raw(BROKERAGE, old, "g1", day=days[0]), _raw(BROKERAGE, old, "g2", day=days[1]),
                _raw(BROKERAGE, FIRST, "g3", day=days[2]))
    for day, path in zip(days, files):
        rec = app.progress.data[_key(BROKERAGE, "1111", day)]
        assert (rec["downloaded_ok"], rec["pdf_path"], rec["summary"]) == (True, str(path), "Account Statement")
    assert not [d for d in app._select() if not app._already_done(d)]
    result = renaming.run_for(app, apply_changes=True, say=lambda *_a: None)
    assert result.renamed == 0 and all(p.exists() for p in files)


def test_a_label_with_no_digits_beside_an_accounts_own_keeps_it_one_account(app):
    """A tax form labeled "Multiple Accounts" made a sign-in of one account
    count as two, so its history was not moved and was taken again."""
    days = ("2025-07-31", "2025-08-31")
    for day in days:
        _saved_before(app, BROKERAGE, "%s ETRADE Account Statement.pdf" % day, day=day)
    app.listing(_raw(BROKERAGE, FIRST, "g1", day=days[0]), _raw(BROKERAGE, FIRST, "g2", day=days[1]),
                _raw("Form 1099 Composite", "Multiple Accounts", "g3", day="2025-02-16"))
    taken = [(d.title, d.summary) for d in app._select() if not app._already_done(d)]
    assert taken == [("Form 1099 Composite", "1099 Tax Form")], taken


@pytest.mark.parametrize("done", [False, True])
def test_a_title_and_day_two_accounts_share_is_left_where_it_is(app, done):
    """Which account's statement a record of both kept cannot be told, so
    it stays, and each account's statement is listed under its own key.
    Not done, the listing's own record of both goes, since a download of
    it found two rows naming it and refused on every run."""
    _saved_before(app, BROKERAGE, "%s ETRADE Account Statement.pdf" % DAY, done=done)
    before = dict(app.progress.data[_legacy_key(BROKERAGE)])
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(BROKERAGE, SECOND, "g2"))
    assert app.progress.data[_legacy_key(BROKERAGE)] == before
    assert (_legacy_key(BROKERAGE) in app.discovery.data) is done
    taken = sorted(d.account for d in app._select() if not app._already_done(d))
    assert taken == [FIRST, SECOND]


@pytest.mark.parametrize("old_done", [True, False])
def test_a_finished_record_wins_a_key_both_want(app, old_done):
    _saved_before(app, BROKERAGE, "%s ETRADE Account Statement.pdf" % DAY, done=old_done)
    new_key = _key(BROKERAGE, "1111")
    app.progress.data[new_key] = {"category": "Statement", "date": DAY, "title": BROKERAGE,
                                  "account": FIRST, "state": "Completed" if not old_done else "Failed",
                                  "downloaded_ok": not old_done}
    app.progress.save()
    app.listing(_raw(BROKERAGE, FIRST, "g1"))
    assert _legacy_key(BROKERAGE) not in app.progress.data
    assert app.progress.data[new_key]["downloaded_ok"] is True


# --- what is written down --------------------------------------------------------

def test_only_a_count_of_accounts_is_logged(app, caplog):
    caplog.set_level(logging.INFO)
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(BROKERAGE, SECOND, "g2"))
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "2 account(s)" in said, said
    for leaked in ("Invented", "1111", "2222", "Roth"):
        assert leaked not in said, (leaked, said)


def test_a_failed_download_is_logged_by_its_date_and_title(app, caplog):
    """An error writing the file names its path, and a file's name says
    the account, so neither the key nor the error's words are logged."""
    app.listing(_raw(BROKERAGE, FIRST, "g1"), _raw(BROKERAGE, SECOND, "g2"))
    doc = next(d for d in app._select() if d.account == FIRST)

    def fails(page, d, filename):
        raise OSError(13, "invented failure", str(app.paths.folder_for("Statement") / filename))
    app._on_its_site = lambda: None
    app.download_one = fails
    app._delay = lambda *a, **k: None
    caplog.set_level(logging.INFO)
    app.process([doc])
    said = [r.getMessage() for r in caplog.records if "Failed on" in r.getMessage()]
    assert said and DAY in said[0] and BROKERAGE in said[0], said
    for leaked in (doc.key, "Invented", "1111"):
        assert leaked not in caplog.text, (leaked, caplog.text)


def test_the_pilot_report_names_a_document_by_its_date_kind_and_title(app, capsys):
    app.listing(_raw(BROKERAGE, FIRST, "g1"))
    doc = app._select()[0]
    app._record(doc, State.FAILED, notes="invented")
    capsys.readouterr()
    app._pilot_report([doc])
    out = capsys.readouterr().out
    assert "%s Statement %s: Failed" % (DAY, BROKERAGE) in out, out
    assert doc.key not in out and "1111" not in out, out


def test_the_download_is_handed_the_documents_account(app, monkeypatch, tmp_path):
    app.listing(_raw(BROKERAGE, FIRST, "g1"))
    doc = app._select()[0]
    asked: list = []

    def download_bill(page, dl_dir, iso, out_path, title="", trace=None, **kw):
        asked.append(kw.get("account"))
        return False
    monkeypatch.setattr(site, "download_bill", download_bill)
    app._dl_dir = tmp_path / "dl"
    app.write_failure = lambda *a, **k: None
    app.stats["manual_review"] = 0
    app.download_one(SimpleNamespace(url=""), doc, "x.pdf")
    assert asked == [FIRST]


# --- the download takes only its own account's link ------------------------------

LISTENER = ("<script>window.hits = [];"
            "document.addEventListener('click', e => { const g = e.target.closest('[data-guid]');"
            " window.hits.push(g ? g.dataset.guid : e.target.tagName.toLowerCase());"
            " e.preventDefault(); }, true);</script>")

# The document's link in a web component, the way his table draws it, named
# "<title> PDF for <account>" unless the row says otherwise.
DOC_LINK_JS = """
<script>
class AcctLink extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const sr = this.attachShadow({mode: 'open'});
    const a = document.createElement('a');
    a.href = '#';
    a.textContent = this.dataset.title;
    if (this.dataset.label) a.setAttribute('aria-label', this.dataset.label);
    a.dataset.guid = this.dataset.guid;
    sr.appendChild(a);
  }
}
if (!customElements.get('acct-link')) customElements.define('acct-link', AcctLink);
</script>
"""


def _label(title, account):
    return "%s PDF for %s" % (title, account) if account else ""


def _table(rows):
    """Table rows shaped like his, the account in a column of its own and
    the link in a shadow root."""
    return "<table><tbody>%s</tbody></table>%s%s" % ("".join(
        "<tr><td>%s</td><td>%s</td><td><acct-link data-title='%s' data-label='%s' data-guid='%s'>"
        "</acct-link></td></tr>" % (SHORT, account, title, _label(title, named), guid)
        for title, account, named, guid in rows), DOC_LINK_JS, LISTENER)


def _divs(rows, shape="div"):
    """Rows that are list items or plain divs, the link in the light DOM."""
    tag, outer = ("li", "ul") if shape == "li" else ("div", "div")
    return "<%s>%s</%s>%s" % (outer, "".join(
        "<%s class='doc'><span>%s</span> <span><a href='#' data-guid='%s'%s>%s</a></span> <span>%s</span></%s>"
        % (tag, SHORT, guid, " aria-label='%s'" % _label(title, named) if named else "", title, account, tag)
        for title, account, named, guid in rows), outer, LISTENER)


BOTH = [(BROKERAGE, FIRST, FIRST, "g-first"), (BROKERAGE, SECOND, SECOND, "g-second")]


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    try:
        driver = pw.sync_playwright().start()
        b = driver.chromium.launch(headless=True)
    except Exception as e:                       # no browser on this machine
        pytest.skip("no browser to drive: %s" % e)
    yield b
    b.close()
    driver.stop()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Stands in for the catch. It presses the element the way the real
    catch does and remembers where the press landed, and a press landing
    on a guid in `brings` hands over that guid's PDF."""
    hits: list = []
    brings: set = set()
    monkeypatch.setattr(site, "goto_documents", lambda pg: True)
    monkeypatch.setattr(site, "expand_all", lambda pg: None)

    def fake(pg, el, label, out_path, trace=None, dl_dir=None, pressed=None, unsure=None):
        before = len(pg.evaluate("window.hits"))
        try:
            el.click(timeout=3000)
        except Exception as e:
            hits.append("click failed " + type(e).__name__)
            return False
        landed = pg.evaluate("window.hits")[before:]
        hits.extend(landed)
        handed = [h for h in landed if h in brings]
        if handed:
            Path(out_path).write_bytes(b"%PDF-1.4\n% invented " + handed[0].encode() + b"\n%%EOF\n")
            return True
        return False
    monkeypatch.setattr(site, "_catch_pdf", fake)

    def download(pg, account, title=BROKERAGE):
        out = tmp_path / "Statements" / "document.pdf"
        trace: list = []
        got = site.download_bill(pg, tmp_path / "dl", DAY, out, title, trace, account=account)
        return got, out, trace
    return hits, brings, download


def _lists(monkeypatch, *accounts, title=BROKERAGE, titles=None):
    """What the lists the page loaded held on DAY."""
    monkeypatch.setattr(site, "_LISTED", {DAY: len(accounts)})
    monkeypatch.setattr(site, "_LISTED_TITLES", {DAY: set(titles or [title])})
    monkeypatch.setattr(site, "_LISTED_ACCOUNTS", {(DAY, title.lower()): {a.lower() for a in accounts}},
                        raising=False)


def _says_no_account(trace):
    text = json.dumps(trace)
    for leaked in ("Invented", "1111", "2222", "Roth"):
        assert leaked not in text, (leaked, text)


@pytest.mark.parametrize("shape", ["table", "div", "li"])
@pytest.mark.parametrize("account, guid", [(FIRST, "g-first"), (SECOND, "g-second")])
def test_each_account_takes_the_link_naming_its_own_account(page, run, monkeypatch, shape, account, guid):
    """Both rows name the document, so it used to be refused, or the one
    row the page showed was taken whichever account's it was."""
    _lists(monkeypatch, FIRST, SECOND)
    page.set_content(_table(BOTH) if shape == "table" else _divs(BOTH, shape))
    hits, brings, download = run
    brings.update({"g-first", "g-second"})
    got, out, trace = download(page, account)
    assert got and hits == [guid], (hits, trace)
    assert guid.encode() in out.read_bytes()
    _says_no_account(trace)


@pytest.mark.parametrize("shape", ["table", "div", "li"])
def test_a_row_that_names_no_account_is_refused_when_two_accounts_share_the_day(
        page, run, monkeypatch, shape):
    """The lists held both accounts' statements, and the one row the page
    shows names the document with no account, so it may be either one's.
    It was taken as the only row naming the document."""
    _lists(monkeypatch, FIRST, SECOND)
    rows = [(BROKERAGE, SECOND, "", "g-second")]
    page.set_content(_table(rows) if shape == "table" else _divs(rows, shape))
    hits, brings, download = run
    brings.update({"g-first", "g-second"})
    got, out, trace = download(page, FIRST)
    assert not got and not out.exists() and hits == [], hits
    _says_no_account(trace)


@pytest.mark.parametrize("shape", ["table", "div", "li"])
def test_the_only_row_shown_is_never_taken_for_another_account(page, run, monkeypatch, shape):
    """The lists held both accounts' statements, and the page shows only the
    second's, the first on another page of the list."""
    _lists(monkeypatch, FIRST, SECOND)
    rows = BOTH[1:]
    page.set_content(_table(rows) if shape == "table" else _divs(rows, shape))
    hits, brings, download = run
    brings.update({"g-first", "g-second"})
    got, out, trace = download(page, FIRST)
    assert not got and not out.exists() and hits == [], hits


@pytest.mark.parametrize("shape", ["table", "div", "li"])
def test_a_link_naming_another_account_is_never_taken_even_alone(page, run, monkeypatch, shape):
    """The lists gave the day to the first account alone, and the one link
    the page shows names the second."""
    _lists(monkeypatch, FIRST)
    rows = BOTH[1:]
    page.set_content(_table(rows) if shape == "table" else _divs(rows, shape))
    hits, brings, download = run
    brings.update({"g-first", "g-second"})
    got, out, trace = download(page, FIRST)
    assert not got and hits == [], hits


@pytest.mark.parametrize("shape", ["table", "div", "li"])
def test_one_accounts_links_that_name_no_account_are_still_taken(page, run, monkeypatch, shape):
    """The positive twin. With the day the lists gave this account alone, a
    link naming the document with no account is still its own, as before."""
    _lists(monkeypatch, FIRST)
    rows = [(BROKERAGE, FIRST, "", "g-first")]
    page.set_content(_table(rows) if shape == "table" else _divs(rows, shape))
    hits, brings, download = run
    brings.add("g-first")
    got, out, trace = download(page, FIRST)
    assert got and hits == ["g-first"], (hits, trace)


def test_each_step_chooses_by_the_account_on_its_own(page, monkeypatch, tmp_path):
    """The row link, the control and the row walk each read the account, so
    none of them takes the other account's link when an earlier one
    passes it by."""
    _lists(monkeypatch, FIRST, SECOND)
    monkeypatch.setattr(site, "_FOR", {"date": DAY, "title": BROKERAGE.lower(), "account": SECOND.lower()},
                        raising=False)

    def guid(el):
        return el.evaluate("e => (e.closest('[data-guid]') || e).dataset.guid || ''")
    page.set_content(_table(BOTH))
    el, _label_ = site._row_link_for(page, DAY, BROKERAGE, [])
    assert el is not None and guid(el) == "g-second"
    page.set_content(_divs(BOTH))
    el, _label_ = site._control_for(page, DAY, BROKERAGE, [])
    assert el is not None and guid(el) == "g-second"
    pressed: list = []
    monkeypatch.setattr(site, "_catch_pdf", lambda pg, el, *a, **k: pressed.append(guid(el)) or True)
    page.set_content(_divs(BOTH, "li"))
    assert site._try_every_way(page, tmp_path, DAY, tmp_path / "out.pdf", BROKERAGE, [])
    assert pressed == ["g-second"]


# --- the account as a link writes it ----------------------------------------------

SHAPES = ["table", "div", "li"]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("day", ["alone", "shared"])
@pytest.mark.parametrize("named", ["Invented Brokerage - 1111", "INDIVIDUAL BROKERAGE -1111",
                                   "Invented Brokerage ...1111"])
def test_a_link_writing_the_account_another_way_is_still_its_own(page, run, monkeypatch, shape, day, named):
    """Nothing says a link writes the account exactly as the list does. It
    was compared whole, so a link spacing, capitalizing or masking it
    another way was refused, and every statement would have gone to
    review (review of #96). The masked tails decide."""
    if day == "alone":
        _lists(monkeypatch, FIRST)
        rows = [(BROKERAGE, FIRST, named, "g-first")]
    else:
        _lists(monkeypatch, FIRST, SECOND)
        rows = [(BROKERAGE, SECOND, SECOND, "g-second"), (BROKERAGE, FIRST, named, "g-first")]
    page.set_content(_table(rows) if shape == "table" else _divs(rows, shape))
    hits, brings, download = run
    brings.update({"g-first", "g-second"})
    got, out, trace = download(page, FIRST)
    assert got and hits == ["g-first"], (hits, trace)


@pytest.mark.parametrize("shape", SHAPES)
def test_two_accounts_with_one_tail_are_told_apart_by_their_whole_labels(page, run, monkeypatch, shape):
    twin = "Invented Roth IRA -1111"
    _lists(monkeypatch, FIRST, twin)
    rows = [(BROKERAGE, FIRST, "Invented Brokerage - 1111", "g-first"),
            (BROKERAGE, twin, "INVENTED ROTH IRA - 1111", "g-twin")]
    page.set_content(_table(rows) if shape == "table" else _divs(rows, shape))
    hits, brings, download = run
    brings.update({"g-first", "g-twin"})
    got, out, trace = download(page, twin)
    assert got and hits == ["g-twin"], (hits, trace)


# --- a row of another account the lists left out ------------------------------------

@pytest.mark.parametrize("shape", SHAPES)
def test_a_row_showing_another_accounts_tail_is_never_taken_when_its_link_names_none(
        page, run, monkeypatch, shape):
    """The review's case. The lists gave the day to the first account
    alone, leaving the second's statement out, and the page shows only the
    second's row, its link naming no account. It was saved as the first's."""
    _lists(monkeypatch, FIRST)
    rows = [(BROKERAGE, SECOND, "", "g-second")]
    page.set_content(_table(rows) if shape == "table" else _divs(rows, shape))
    hits, brings, download = run
    brings.update({"g-first", "g-second"})
    got, out, trace = download(page, FIRST)
    assert not got and not out.exists() and hits == [], (hits, trace)
    _says_no_account(trace)


@pytest.mark.parametrize("shape", SHAPES)
def test_a_row_showing_another_accounts_label_is_never_taken_when_neither_shows_digits(
        page, run, monkeypatch, shape):
    """Labels with no masked digits, the other account named by the lists
    on another day."""
    joint, family = "Joint Account", "Family Joint"
    monkeypatch.setattr(site, "_LISTED", {DAY: 1, "2025-07-31": 1})
    monkeypatch.setattr(site, "_LISTED_TITLES", {DAY: {BROKERAGE}, "2025-07-31": {BROKERAGE}})
    monkeypatch.setattr(site, "_LISTED_ACCOUNTS", {(DAY, BROKERAGE.lower()): {joint.lower()},
                                                   ("2025-07-31", BROKERAGE.lower()): {family.lower()}},
                        raising=False)
    rows = [(BROKERAGE, family, "", "g-family")]
    page.set_content(_table(rows) if shape == "table" else _divs(rows, shape))
    hits, brings, download = run
    brings.add("g-family")
    got, out, trace = download(page, joint)
    assert not got and hits == [], (hits, trace)
