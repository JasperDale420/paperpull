"""The documents page read and pressed in a real browser, inside shadow roots.

Banno draws every control inside open shadow roots of its web components,
so the page here is made up in that shape: a bannoweb-documents component
whose shadow root holds an enrollment card of jha-button components (each a
textless inner <button> in its own shadow root, with Accept, Enroll and OK
slotted in) and one jha-list-item per document holding the row's <button>.
Loading it makes the page ask the bank's list, accounts and settings
addresses, and pressing a row makes it fetch that document's PDF, turn it
into a Blob and download it under the document's file name, as the live
page does. Every address is answered here by the browser's own routing, the
browser is refused every other host, and every value is invented, so
nothing here can reach a real site.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import simmons_site as site  # noqa: E402
from paperpull_core.testkit import text_pdf  # noqa: E402

BASE = "https://login.simmonsbank.com"
USER = "0a1b2c3d-1111-4222-8333-944455556666"
ACCOUNT = "7e7e7e7e-2222-4333-8444-955566667777"
ACCT = "RE ARM 88123456 (x3456)"
DOCS = [
    {"documentId": "11111111-aaaa-4bbb-8ccc-000000000001", "documentType": "statement",
     "documentTitle": "Loan Periodic Statement October 2031", "description": None,
     "date": "2031-10-13", "documentFilename": "RE ARM 88123456-STATEMENT-10-13-2026-1.pdf",
     "accountIds": [ACCOUNT]},
    {"documentId": "11111111-aaaa-4bbb-8ccc-000000000002", "documentType": "notice",
     "documentTitle": "Prime Rate Notice August 2031", "description": None,
     "date": "2031-08-20", "documentFilename": "RE ARM 88123456-NOTICE-08-20-2026-2.pdf",
     "accountIds": [ACCOUNT]},
]
TYPE = {"statement": "Statement", "notice": "Notice", "tax": "Tax"}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Documents</title></head>
<body><bannoweb-documents></bannoweb-documents>
<script>
window.presses = 0; window.accepts = 0;
const API = '/a/consumer/api';
const USER = %(user)s;
customElements.define('jha-button', class extends HTMLElement {
  constructor() { super(); const r = this.attachShadow({mode: 'open'});
    r.innerHTML = '<button type="button"><slot></slot></button>';
    r.querySelector('button').onclick = () => { window.accepts++; }; }
});
customElements.define('jha-list-item', class extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: 'open'}).innerHTML = '<div><slot></slot></div>'; }
});
customElements.define('bannoweb-documents', class extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: 'open'}); }
  async connectedCallback() {
    const r = this.shadowRoot;
    r.innerHTML = '<section><h2>eStatements</h2><jha-button>Accept</jha-button>' +
      '<jha-list-item role="listitem">Notify at</jha-list-item><jha-button>Enroll</jha-button>' +
      '<jha-button>OK</jha-button></section><div id="list"></div>';
    await fetch(API + '/users/' + USER + '/accounts');
    const settings = await (await fetch(API + '/users/' + USER + '/documents/settings/institution')).json();
    const all = await (await fetch(API + '/v0/users/' + USER + '/documents/all?startDate=' +
      settings.settings.documentStartDate)).json();
    const list = r.getElementById('list');
    for (const d of all.documents) {
      const item = document.createElement('jha-list-item');
      item.setAttribute('role', 'listitem');
      const b = document.createElement('button');
      b.type = 'button';
      b.innerHTML = '<span>' + d.documentTitle + '</span><br><span>' + %(types)s[d.documentType] +
        ', ' + %(acct)s + '</span>';
      b.onclick = async () => {
        window.presses++;
        const blob = await (await fetch(API + '/users/' + USER + '/documents/' + d.documentId)).blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob); a.download = d.documentFilename;
        document.body.appendChild(a); a.click(); a.remove();
      };
      item.appendChild(b);
      list.appendChild(item);
    }
  }
});
</script></body></html>"""


def pdf_of(d):
    iso = d["date"]
    return text_pdf(["Simmons Bank", d["documentTitle"],
                     "Statement Date %s/%s/%s" % (iso[5:7], iso[8:10], iso[:4])]
                    + ["Line %d" % n for n in range(12)])


def answer(route):
    url = route.request.url
    path = url.split("?", 1)[0][len(BASE):] if url.startswith(BASE) else None
    body, kind = None, "application/json"
    if path == "/documents":
        body = (PAGE % {"user": json.dumps(USER), "types": json.dumps(TYPE),
                        "acct": json.dumps(ACCT)}).encode()
        kind = "text/html; charset=utf-8"
    elif path == "/a/consumer/api/users/%s/accounts" % USER:
        body = json.dumps({"accounts": [{"id": ACCOUNT, "name": "RE ARM 88123456",
                                         "closedAccountAvailableUntil": "2032-06-30"}]}).encode()
    elif path == "/a/consumer/api/users/%s/documents/settings/institution" % USER:
        body = json.dumps({"settings": {"documentStartDate": "2023-10-15"}}).encode()
    elif path == "/a/consumer/api/v0/users/%s/documents/all" % USER:
        body = json.dumps({"documents": DOCS}).encode()
    else:
        for d in DOCS:
            if path == "/a/consumer/api/users/%s/documents/%s" % (USER, d["documentId"]):
                body, kind = pdf_of(d), "application/pdf"
    if body is None:
        return route.abort()
    return route.fulfill(status=200, body=body, headers={"content-type": kind})


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    try:
        driver = pw.sync_playwright().start()
        launched = driver.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip("no browser to drive: %s" % e)
    yield launched
    launched.close()
    driver.stop()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(accept_downloads=True, viewport={"width": 1000, "height": 800})
    ctx.route("**/*", answer)
    site.set_private_words([])
    yield ctx.new_page()
    site.set_private_words([])
    ctx.close()


def test_the_rows_are_read_through_the_shadow_roots_and_nothing_else_is_a_row(page):
    run = site.Run()
    listing = site.read_listing(page, run)
    assert [r.text for r in listing.rows] == [
        "%s %s, %s" % (d["documentTitle"], TYPE[d["documentType"]], ACCT) for d in DOCS]
    assert [e.title for e in listing.entries] == [d["documentTitle"] for d in DOCS]
    assert run.available_until == "2032-06-30"
    # The enrollment card's buttons are each a textless <button> in a
    # jha-button's own shadow root, its words slotted in from outside, as the
    # survey saw them live. They are found, and are no row.
    texts = site._button_texts(page)
    assert len(texts) == 5 and texts.count("") == 3
    assert page.evaluate("window.presses + window.accepts") == 0


@pytest.mark.parametrize("style", ["opacity: 0", "display: none"])
def test_a_hidden_copy_of_a_row_stops_the_list(page, style):
    """A row in the page but not seen, its list item transparent or not laid
    out, is no row a person could press, and the list stops rather than
    count it or press it."""
    page.goto(BASE + "/documents")
    page.wait_for_function("() => document.querySelector('bannoweb-documents').shadowRoot"
                           ".querySelectorAll('jha-list-item button').length === 2")
    page.evaluate("""(style) => {
      const list = document.querySelector('bannoweb-documents').shadowRoot.getElementById('list');
      const copy = list.firstElementChild.cloneNode(true);
      copy.setAttribute('style', style);
      list.appendChild(copy);
    }""", style)
    with pytest.raises(site.ListStopped):
        site.read_rows(page)


def test_a_copy_hidden_by_visibility_has_no_words_and_is_no_row(page):
    """The browser gives a button under visibility: hidden no text at all,
    so such a copy reads as no row, is never counted, and cannot be the one
    button a row's whole text finds."""
    page.goto(BASE + "/documents")
    page.wait_for_function("() => document.querySelector('bannoweb-documents').shadowRoot"
                           ".querySelectorAll('jha-list-item button').length === 2")
    page.evaluate("""() => {
      const list = document.querySelector('bannoweb-documents').shadowRoot.getElementById('list');
      const copy = list.firstElementChild.cloneNode(true);
      copy.setAttribute('style', 'visibility: hidden');
      list.appendChild(copy);
    }""")
    assert len(site.read_rows(page)) == 2
    label = site.read_rows(page)[0].text
    assert site._row_button(page, label) is not None


def test_the_bound_row_is_pressed_once_and_its_download_taken(page, tmp_path):
    run = site.Run()
    d = DOCS[1]
    out = tmp_path / "out" / "2031-08-20 Simmons Bank Notice - Prime Rate Notice.pdf"
    trace = []
    got = site.download_document(page, run, tmp_path / "dl", out,
                                 record_id=site.record_hash(d["documentId"]), iso_date=d["date"],
                                 title=d["documentTitle"], kind=d["documentType"], trace=trace)
    assert got is True, trace
    assert out.read_bytes() == pdf_of(d)
    assert page.evaluate("window.presses") == 1 and page.evaluate("window.accepts") == 0
    assert {"note": "document taken", "how": "download", "where": "event"} in trace
    assert site.pdf_names_document(out.read_bytes(), d["documentType"], d["documentTitle"],
                                   d["date"])[0]
