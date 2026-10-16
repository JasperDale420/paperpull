"""Wells Fargo's Statements & Documents page as a tester's checking account
showed it, and the session a typed address ended (#27).

A recording on a checking account of four, 2026-10-15, showed the page the
app had only guessed at. The statements sit in a section whose heading is
a button, "Statements and Disclosures", and show once it is pressed. Inside
is an account picker, a combobox whose entries read "<nickname> ...<last
four>", the one shown with "Selected" after it, and choosing one lists that
account's statements. Each statement is a link with no address that opens
the PDF in the same tab.

The app had found none of it. Diagnose saw no statement on the page, typed
the statements address into the signed-in tab, and Wells Fargo answered
with a session timeout and the sign-off page, so the app had ended the
tester's session. A first survey had walked the same typed addresses and
ended on the accounts summary.

So the page is reached by its own link and never a typed address, the
section is opened only when it says it is closed, each account in the
picker is read in turn and its documents carry its label, and a download
chooses its document's account first. Everything here is invented, and it
drives a real browser against made-up pages served at Wells Fargo's own
addresses, since what a press lands on and where a tab goes are what a fake
page object gets wrong.
"""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import wellsfargo_docs as app_mod
import wellsfargo_site as site
from paperpull_core import pressing

SIGNED_IN = "https://connect.secure.wellsfargo.com"
SUMMARY = SIGNED_IN + "/accounts/start?_x=T0K3N"
EDOCS = SIGNED_IN + "/edocs/documents?_x=T0K3N"

MAIN = "Main ...1111"
SAVINGS = "Way2Save Savings ...2222"
CARD = "Active Cash Card ...3333"

EDOCS_PAGE = """<!doctype html><meta charset="utf-8"><title>Statements</title>
<style>.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
li{padding:6px;list-style:none;cursor:pointer} #box{border:1px solid #888;padding:6px;width:320px}</style>
<main>
<div role="heading" aria-level="2"><div role="button" tabindex="0" id="head" aria-expanded="false"
  aria-controls="sec">Statements and Disclosures</div></div>
<div role="region" id="sec" aria-labelledby="head" hidden>
  <span id="lbl">Account</span>
  <div role="combobox" id="box" tabindex="0" aria-haspopup="listbox" aria-expanded="false"
    aria-labelledby="lbl"><span id="shown"></span><ul role="listbox" id="lst" aria-label="Accounts"
    style="display:none"></ul></div>
  <form id="docs" action="/edocs/documents/view" method="post"></form>
</div>
</main>
<script>
const CFG = __CFG__;
let current = 0;
window.pressed = []; window.headPresses = 0; window.lists = 0;
function render() {
  const acct = CFG.accounts[current];
  document.getElementById('shown').textContent = acct.label;
  const lst = document.getElementById('lst'); lst.innerHTML = '';
  CFG.accounts.forEach((a, i) => {
    const li = document.createElement('li'); li.setAttribute('role', 'option'); li.tabIndex = -1;
    li.dataset.i = i; li.textContent = a.label;
    if (i === current) { const s = document.createElement('span'); s.className = 'sr';
      s.textContent = 'Selected'; li.appendChild(s); }
    lst.appendChild(li);
  });
  const form = document.getElementById('docs'); form.innerHTML = '';
  acct.docs.forEach(d => {
    const div = document.createElement('div'); const a = document.createElement('a');
    a.setAttribute('role', 'link'); a.tabIndex = 0; a.dataset.doc = acct.id + ':' + d;
    a.textContent = 'Statement ' + d + ' (123K, PDF)'; div.appendChild(a); form.appendChild(div);
  });
}
async function list(path) {
  window.lists++;
  await fetch(path + '?F=J&_x=T0K3N', {method: 'POST', body: '{}'});
}
document.getElementById('head').addEventListener('click', async () => {
  window.headPresses++;
  const head = document.getElementById('head'), sec = document.getElementById('sec');
  if (head.getAttribute('aria-expanded') === 'true') {
    head.setAttribute('aria-expanded', 'false'); sec.hidden = true; return; }
  head.setAttribute('aria-expanded', 'true');
  await list('/edocs/documents/statement/list');
  render(); sec.hidden = false;
});
const box = document.getElementById('box');
function setOpen(o) { box.setAttribute('aria-expanded', o ? 'true' : 'false');
  document.getElementById('lst').style.display = o ? 'block' : 'none'; }
box.addEventListener('click', async (e) => {
  const li = e.target.closest('[role=option]');
  if (li) {
    setOpen(false);
    const i = +li.dataset.i, a = CFG.accounts[i];
    if (CFG.silent.includes(a.id)) return;
    await list('/edocs/documents/statement/list/account/' + a.id + '/period/default');
    await new Promise(r => setTimeout(r, 200));
    current = i; render(); return;
  }
  setOpen(box.getAttribute('aria-expanded') !== 'true');
});
box.addEventListener('keydown', e => { if (e.key === 'Escape') setOpen(false); });
document.getElementById('docs').addEventListener('click', e => {
  const a = e.target.closest('a[role=link]');
  if (a) { e.preventDefault(); window.pressed.push(a.dataset.doc); }
});
if (CFG.expanded) { document.getElementById('head').setAttribute('aria-expanded', 'true');
  render(); document.getElementById('sec').hidden = false; }
</script>"""

SUMMARY_PAGE = """<!doctype html><meta charset="utf-8"><title>Account Summary</title>
<main><h1>Account Summary</h1><p>Main ...1111</p>%s</main>"""
DOCUMENTS_LINK = '<a href="/edocs/documents?_x=T0K3N">Statements &amp; Documents</a>'


def _account(label, docs, ident=None):
    return {"id": ident or "a" + label[-4:], "label": label, "docs": list(docs)}


class Site:
    """Wells Fargo's addresses, served from here. Every address the tab is
    sent to is kept, so a typed one would show."""

    def __init__(self, accounts, expanded=False, silent=(), link=True):
        self.cfg = {"accounts": accounts, "expanded": expanded, "silent": list(silent)}
        self.link = link
        self.navigations = []
        self.posts = []

    def handle(self, route):
        req = route.request
        parts = urlsplit(req.url)
        if req.is_navigation_request():
            self.navigations.append(req.url)
        if parts.hostname not in ("connect.secure.wellsfargo.com", "www.wellsfargo.com"):
            return route.abort()
        if req.method == "POST":
            self.posts.append(parts.path)
            return route.fulfill(status=200, content_type="application/json", body='"ok"')
        if parts.path == "/accounts/start":
            return route.fulfill(content_type="text/html",
                                 body=SUMMARY_PAGE % (DOCUMENTS_LINK if self.link else ""))
        if parts.path == "/edocs/documents":
            return route.fulfill(content_type="text/html",
                                 body=EDOCS_PAGE.replace("__CFG__", json.dumps(self.cfg)))
        if parts.path.startswith("/edocs/documents/download/"):
            return route.fulfill(content_type="text/html", body="<p>the statement</p>")
        if parts.path == "/edocs/support/error/session_timeout":
            return route.fulfill(content_type="text/html", body="<p>Your session has ended</p>")
        return route.fulfill(content_type="text/html", body="<p>Wells Fargo</p>")


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    driver = pw.sync_playwright().start()
    try:
        b = driver.chromium.launch(headless=True)
    except Exception as e:                       # no browser on this machine
        driver.stop()
        pytest.skip("no browser to drive: %s" % e)
    yield b
    b.close()
    driver.stop()


@pytest.fixture
def open_site(browser):
    contexts = []

    def opener(fake: Site, url: str):
        ctx = browser.new_context()
        contexts.append(ctx)
        ctx.route("**/*", fake.handle)
        pg = ctx.new_page()
        pg.goto(url)
        fake.navigations.clear()
        return pg
    yield opener
    for ctx in contexts:
        ctx.close()


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """Nothing to expand or scroll on these pages, and no overlay to put
    away."""
    monkeypatch.setattr(site, "expand_all", lambda pg: None)
    monkeypatch.setattr(site, "scroll_full_page", lambda pg, *a, **k: None)
    monkeypatch.setattr(site, "dismiss_overlay", lambda pg: None)
    monkeypatch.setattr(site, "_SHOW_MS", 4000)
    monkeypatch.setattr(site, "_SETTLE_MS", 300)


@pytest.fixture
def presses(monkeypatch):
    """Stands in for the catch. It presses the control the way the real
    catch does and saves a PDF naming the statement the press landed on."""
    def fake(pg, el, label, out_path, trace=None, dl_dir=None):
        before = len(pg.evaluate("window.pressed"))
        el.click(timeout=3000)
        landed = pg.evaluate("window.pressed")[before:]
        Path(out_path).write_bytes(b"%PDF-1.4\n% " + ",".join(landed).encode() + b"\n%%EOF\n")
        return bool(landed)
    monkeypatch.setattr(site, "_catch_pdf", fake)


def _pressed(pg):
    return pg.evaluate("window.pressed")


# -- reaching the page --------------------------------------------------------

def test_a_signed_in_page_is_left_by_its_own_link_never_a_typed_address(open_site):
    fake = Site([_account(MAIN, ["09/30/26"])])
    pg = open_site(fake, SUMMARY)
    assert site.goto_documents(pg) is True
    assert fake.navigations == [EDOCS], fake.navigations
    assert pg.evaluate("window.headPresses") == 1
    assert site._shows_documents(pg)


def test_the_statements_page_already_open_is_never_navigated(open_site):
    fake = Site([_account(MAIN, ["09/30/26", "08/31/26"])])
    pg = open_site(fake, EDOCS)
    assert site.goto_documents(pg) is True
    assert fake.navigations == []
    assert pg.evaluate("window.headPresses") == 1
    assert pg.get_attribute("#head", "aria-expanded") == "true"


def test_a_section_that_says_it_is_open_is_never_pressed(open_site):
    """Pressing an open section closes it."""
    fake = Site([_account(MAIN, ["09/30/26"])], expanded=True)
    pg = open_site(fake, EDOCS)
    assert site.goto_documents(pg) is True
    assert pg.evaluate("window.headPresses") == 0
    assert pg.get_attribute("#head", "aria-expanded") == "true"


def test_a_tab_off_the_signed_in_site_stops_with_what_to_do(open_site):
    fake = Site([_account(MAIN, ["09/30/26"])])
    pg = open_site(fake, "https://www.wellsfargo.com/help")
    with pytest.raises(pressing.Stop) as stop:
        site.goto_documents(pg)
    assert "yourself" in " ".join(stop.value.lines)
    assert fake.navigations == []


def test_a_signed_in_page_with_no_link_stops_rather_than_type_an_address(open_site):
    fake = Site([_account(MAIN, ["09/30/26"])], link=False)
    pg = open_site(fake, SUMMARY)
    with pytest.raises(pressing.Stop):
        site.goto_documents(pg)
    assert fake.navigations == []


def test_the_page_a_session_ended_on_reads_as_signed_out(open_site):
    fake = Site([_account(MAIN, ["09/30/26"])])
    pg = open_site(fake, SIGNED_IN + "/edocs/support/error/session_timeout")
    assert site.looks_signed_out(pg)
    assert site.goto_documents(pg) is False
    assert fake.navigations == []


def test_a_statement_left_open_in_the_tab_goes_back_to_its_list(open_site, monkeypatch):
    fake = Site([_account(MAIN, ["09/30/26"])])
    pg = open_site(fake, EDOCS)
    pg.goto(SIGNED_IN + "/edocs/documents/download/d1?_x=T0K3N")
    monkeypatch.setattr(site, "_shows_a_pdf", lambda p: "/download/" in (p.url or ""))
    assert site.goto_documents(pg) is True
    assert urlsplit(pg.url).path == "/edocs/documents"
    assert site._shows_documents(pg)


# -- the accounts ---------------------------------------------------------------

def test_an_entry_of_the_picker_reads_as_its_account():
    assert site.account_label("Main ...1111 Selected") == MAIN
    assert site.account_label("Main ...1111Selected") == MAIN
    assert site.account_label("Way2Save Savings ...2222") == SAVINGS
    assert site.account_label("Accounts") == ""
    assert site.account_tail(CARD) == "3333"
    assert site.is_account_option(CARD)
    for words in ("Transfer ...1234", "Pay bills ...1234", "Sign off ...1234", "Statements"):
        assert not site.is_account_option(words), words


def test_each_account_in_the_picker_is_read_with_its_label(open_site):
    fake = Site([_account(MAIN, ["09/30/26", "08/31/26"]),
                 _account(SAVINGS, ["09/30/26"]),
                 _account(CARD, ["09/15/26"])])
    pg = open_site(fake, EDOCS)
    assert site.goto_documents(pg)
    docs = site.collect_download_docs(pg)
    got = sorted((d.account, d.date_text) for d in docs)
    assert got == sorted([(MAIN, "2026-09-30"), (MAIN, "2026-08-31"),
                          (SAVINGS, "2026-09-30"), (CARD, "2026-09-15")]), got
    keys = {app_mod.Document(category="Statement", date=d.date_text, title=d.title,
                             account=d.account).key for d in docs}
    assert len(keys) == 4


def test_one_account_in_the_picker_keeps_the_keys_records_have(open_site):
    fake = Site([_account(MAIN, ["09/30/26", "08/31/26"])])
    pg = open_site(fake, EDOCS)
    assert site.goto_documents(pg)
    docs = site.collect_download_docs(pg)
    assert sorted(d.date_text for d in docs) == ["2026-08-31", "2026-09-30"]
    assert all(d.account == "" for d in docs)


def test_two_accounts_ending_in_the_same_digits_stop_the_list(open_site):
    fake = Site([_account(MAIN, ["09/30/26"], "a1"),
                 _account("Kids Savings ...1111", ["09/30/26"], "a2")])
    pg = open_site(fake, EDOCS)
    assert site.goto_documents(pg)
    with pytest.raises(pressing.Stop):
        site.collect_download_docs(pg)


def test_an_account_whose_list_never_comes_stops_the_run(open_site):
    """Its statements read then would be another account's."""
    fake = Site([_account(MAIN, ["09/30/26"]), _account(SAVINGS, ["08/31/26"])],
                silent=["a2222"])
    pg = open_site(fake, EDOCS)
    assert site.goto_documents(pg)
    with pytest.raises(pressing.NoAnswer):
        site.collect_download_docs(pg)


def test_the_key_keeps_the_last_four_so_a_new_nickname_finds_its_records():
    one = app_mod.Document(category="Statement", date="2026-09-30",
                           title="Account Statement - September 30, 2026", account=MAIN)
    renamed = app_mod.Document(category="Statement", date="2026-09-30",
                               title="Account Statement - September 30, 2026",
                               account="Everyday Checking ...1111")
    assert one.key == renamed.key
    assert one.key.endswith(":1111")
    # A document of no account keeps the key every record had before #27.
    none = app_mod.Document(category="Statement", date="2026-09-30",
                            title="Account Statement - September 30, 2026")
    assert none.key == "Statement:2026-09-30:Account Statement - September 30, 2026:Unnamed"


# -- the download ---------------------------------------------------------------

def _download(pg, tmp_path, account, iso, trace):
    return site.download_bill(pg, tmp_path, iso, tmp_path / "out.pdf",
                              title="Account Statement - " + iso, trace=trace,
                              account=account)


def test_a_download_chooses_its_documents_account_first(open_site, presses, tmp_path):
    fake = Site([_account(MAIN, ["09/30/26"]), _account(SAVINGS, ["09/30/26"])])
    pg = open_site(fake, EDOCS)
    trace: list = []
    assert _download(pg, tmp_path, SAVINGS, "2026-09-30", trace) is True
    assert _pressed(pg) == ["a2222:09/30/26"]
    assert b"a2222:09/30/26" in (tmp_path / "out.pdf").read_bytes()


def test_a_document_of_no_account_on_a_page_of_several_is_refused(open_site, presses, tmp_path):
    fake = Site([_account(MAIN, ["09/30/26"]), _account(SAVINGS, ["09/30/26"])])
    pg = open_site(fake, EDOCS)
    trace: list = []
    assert _download(pg, tmp_path, "", "2026-09-30", trace) is False
    assert _pressed(pg) == []
    assert trace[-1]["why"] == "the document names no account and the page lists several"


def test_a_document_whose_account_left_the_picker_is_refused(open_site, presses, tmp_path):
    fake = Site([_account(MAIN, ["09/30/26"]), _account(SAVINGS, ["09/30/26"])])
    pg = open_site(fake, EDOCS)
    trace: list = []
    assert _download(pg, tmp_path, CARD, "2026-09-30", trace) is False
    assert _pressed(pg) == []
    assert trace[-1]["why"] == "the document's account is not in the picker"


def test_a_document_of_no_account_on_a_page_of_one_is_taken(open_site, presses, tmp_path):
    fake = Site([_account(MAIN, ["09/30/26"])])
    pg = open_site(fake, EDOCS)
    assert _download(pg, tmp_path, "", "2026-09-30", []) is True
    assert _pressed(pg) == ["a1111:09/30/26"]
