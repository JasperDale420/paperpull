"""Statements Robinhood serves as HTML are rendered to PDF through the
core's inert print path; anything else that is not a PDF is still refused,
and nothing half-made is left behind."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import robinhood_site as site  # noqa: E402
from paperpull_core import receipt_pdf  # noqa: E402

HTML = b"<!DOCTYPE html><html><head><title>Statement</title></head><body>January 2026</body></html>"
TITLE = "January 2026 \u2013 Individual Investing Account Statement"
LABEL = "Download HTML"


def fake_render(ok=True, partial=False):
    calls = []

    def render(page, html, out):
        calls.append(html)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"%PDF-1.4 rendered" if ok else b"not a pdf")
        if partial:
            raise RuntimeError("renderer died after writing")
    return render, calls


def yes(path, title):
    return True


def test_a_pdf_is_kept_as_it_is(tmp_path):
    out = tmp_path / "s.pdf"
    assert site.keep_fetched(None, b"%PDF-1.4 real", TITLE, out, label="Download PDF")
    assert out.read_bytes() == b"%PDF-1.4 real"


def test_an_html_statement_behind_a_download_html_control_is_rendered(tmp_path):
    render, calls = fake_render()
    out = tmp_path / "s.pdf"
    assert site.keep_fetched(None, HTML, TITLE, out, label=LABEL, render=render, check=yes)
    assert out.read_bytes().startswith(b"%PDF-")
    assert len(calls) == 1 and "January 2026" in calls[0]
    assert not list(tmp_path.glob("*.rendering"))


def test_the_document_title_alone_does_not_open_the_html_path(tmp_path):
    render, calls = fake_render()
    out = tmp_path / "s.pdf"
    assert not site.keep_fetched(None, HTML, TITLE + " Download HTML", out, label="Download PDF", render=render, check=yes)
    assert calls == [] and not out.exists()


def test_a_render_that_is_not_a_pdf_leaves_no_file(tmp_path):
    render, _ = fake_render(ok=False)
    out = tmp_path / "s.pdf"
    assert not site.keep_fetched(None, HTML, TITLE, out, label=LABEL, render=render, check=yes)
    assert not out.exists() and not list(tmp_path.iterdir())


def test_a_renderer_that_writes_then_dies_leaves_nothing(tmp_path):
    render, _ = fake_render(partial=True)
    out = tmp_path / "s.pdf"
    assert not site.keep_fetched(None, HTML, TITLE, out, label=LABEL, render=render, check=yes)
    assert not list(tmp_path.iterdir())


def test_a_rendering_that_does_not_name_the_month_is_refused(tmp_path):
    render, _ = fake_render()
    out = tmp_path / "s.pdf"
    assert not site.keep_fetched(None, HTML, TITLE, out, label=LABEL, render=render, check=lambda p, t: False)
    assert not list(tmp_path.iterdir())


def test_empty_and_other_bodies_are_refused(tmp_path):
    render, calls = fake_render()
    out = tmp_path / "s.pdf"
    for body in (b"", b"{\"error\": 1}", b"<svg/>"):
        assert not site.keep_fetched(None, body, TITLE, out, label=LABEL, render=render, check=yes)
    assert calls == [] and not out.exists()


def test_the_month_check_reads_the_title():
    assert site._MONTH_YEAR_IN_TITLE_RE.search(TITLE).groups() == ("January", "2026")
    assert site.rendered_names_statement(Path("/nonexistent.pdf"), "Form 1099")  # no month: not checked


def test_inert_html_strips_what_could_run():
    html = ("<html><head><base href='https://evil.test/'><script>x()</script>"
            "<link rel=stylesheet href=//evil.test/a.css><meta http-equiv=refresh content='0;url=https://evil.test'>"
            "</head><body onload='x()'><iframe src='https://evil.test'></iframe>"
            "<form action='https://evil.test'><input></form><a href='javascript:alert(1)'>a</a>"
            "<img src='/img.png' onerror='x()'><p style='color:red'>January 2026</p></body></html>")
    out = site.receipt_pdf.inert_html(html, "https://robinhood.com/") if hasattr(site, "receipt_pdf") else receipt_pdf.inert_html(html, "https://robinhood.com/")
    low = out.lower()
    for bad in ("<script", "<iframe", "<form", "<link", "<meta", "evil.test", "onload", "onerror", "javascript:"):
        assert bad not in low, bad
    assert '<base href="https://robinhood.com/">' in out and "January 2026" in out and "<img" in out and "style=" in out


def test_abbreviated_month_titles_are_checked_too():
    m = site._MONTH_YEAR_IN_TITLE_RE.search("Jan 2026 - Individual Statement")
    assert m and m.group(2) == "2026"


def test_a_download_html_control_is_discovered_like_a_pdf_one():
    """The collect script lists a statement behind a Download HTML link or
    button, with the control words stripped from the title."""
    from playwright.sync_api import sync_playwright
    page_html = (
        "<html><body>"
        "<a download href='#'>January 2026 \u2013 Individual Investing Account Statement Download HTML</a>"
        "<div><h3>Form 1099 2025</h3><button>Download HTML</button></div>"
        "<a download href='#'>Something Download CSV</a>"
        "</body></html>")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(page_html)
            got = page.evaluate(site._COLLECT_JS, False)
        finally:
            browser.close()
    titles = sorted(str(d.get("title") or d.get("own") or "") for d in got)
    assert "January 2026 \u2013 Individual Investing Account Statement" in titles, titles
    assert "Form 1099 2025" in titles, titles
    assert not any("Download" in x or "CSV" in x for x in titles), titles


def test_the_inert_render_fetches_nothing_and_runs_nothing(tmp_path):
    """Styles and images naming an outside address are not fetched, and no
    script runs, in the fresh JavaScript-off context the statement is drawn in."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from playwright.sync_api import sync_playwright
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path); self.send_response(200); self.end_headers(); self.wfile.write(b"x")

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    html = (f"<html><head><style>@import url(http://127.0.0.1:{port}/a.css); body{{background:url(http://127.0.0.1:{port}/b.png)}}</style>"
            f"<script>fetch('http://127.0.0.1:{port}/js')</script></head>"
            f"<body onload=\"fetch('http://127.0.0.1:{port}/onload')\"><img src='http://127.0.0.1:{port}/i.png'>"
            "<p>January 2026 statement text</p></body></html>")
    out = tmp_path / "r.pdf"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body>work page</body></html>")
            receipt_pdf.print_html_inert_to_pdf(page, html, out, base="https://robinhood.com/")
            assert len(browser.contexts) == 1, "the render context was closed"
        finally:
            browser.close()
    srv.shutdown()
    assert out.read_bytes().startswith(b"%PDF-")
    assert hits == [], hits
    assert "January" in receipt_pdf.pdf_text(out) and "2026" in receipt_pdf.pdf_text(out)


def test_records_titled_with_the_old_control_words_move_under_the_cleaned_key():
    """A statement listed before the title was cleaned sits under one key,
    never beside a second record for the same statement."""
    import robinhood_docs as docs
    legacy = {"title": "January 2026 \u2013 Individual Investing Account Statement Download HTML",
              "category": "Statement", "summary": "Statement", "date": "2026-01-31",
              "account": "Individual", "state": "NEEDS_MANUAL_REVIEW", "document_id": ""}
    old_key = docs.Document.from_dict(legacy).key
    records = {old_key: dict(legacy)}
    assert docs.migrate_html_titles(records) == 1
    assert len(records) == 1
    (key, rec), = records.items()
    assert "Download" not in rec["title"] and "Download" not in key
    assert docs.Document.from_dict(rec).key == key
    # Run again: nothing to do, nothing changes.
    assert docs.migrate_html_titles(records) == 0
    # Already re-listed under the clean key and saved: the saved one stays.
    clean = dict(rec, state="COMPLETED", pdf_path="/x/y.pdf")
    both = {old_key: dict(legacy), key: clean}
    assert docs.migrate_html_titles(both) == 1
    assert list(both) == [key] and both[key]["pdf_path"] == "/x/y.pdf"


def test_both_load_time_moves_run_on_one_store():
    """A store with a cut key AND a legacy HTML title gets both moves, each
    on its own, through the same loop App.__init__ runs."""
    import robinhood_docs as docs

    class Store:
        def __init__(self, data): self.data = data; self.saved = 0
        def save(self, backup=False): self.saved += 1
    legacy = {"title": "January 2026 \u2013 Individual Investing Account Statement Download HTML",
              "category": "Statement", "summary": "Statement", "date": "2026-01-31",
              "account": "Individual", "state": "NEEDS_MANUAL_REVIEW", "document_id": ""}
    long_acct = {"title": "February 2026 \u2013 Statement", "category": "Statement", "summary": "Statement",
                 "date": "2026-02-28", "account": "Invented Cash Rewards World Elite Mastercard (...1234)",
                 "state": "COMPLETED", "document_id": ""}
    full_key = docs.Document.from_dict(long_acct).key
    assert full_key.endswith("-1234")
    cut_key = full_key[:-len("-1234")]
    store = Store({docs.Document.from_dict(legacy).key: dict(legacy), cut_key: dict(long_acct)})

    def load_like_the_app(store):
        moved = docs.migrate_legacy_keys(store.data)
        if docs.migrate_html_titles(store.data):
            moved = True
        if moved:
            store.save(backup=True)
    load_like_the_app(store)
    assert store.saved == 1
    assert not any("Download" in k for k in store.data), list(store.data)
    assert cut_key not in store.data and full_key in store.data
    load_like_the_app(store)
    assert store.saved == 1, "nothing left to move"


def test_a_record_keyed_by_its_document_id_keeps_that_key():
    """--minimal histories drop document_id but keep the id: key; the title
    is cleaned and the key stands, so fresh discovery finds it done."""
    import robinhood_docs as docs
    rec = {"title": "March 2026 \u2013 Individual Investing Account Statement Download HTML",
           "category": "Statement", "summary": "Statement", "date": "2026-03-31",
           "account": "Individual", "state": "COMPLETED", "pdf_path": "/x/m.pdf"}
    records = {"id:0b1e-uuid": dict(rec)}
    assert docs.migrate_html_titles(records) == 1
    assert list(records) == ["id:0b1e-uuid"]
    assert "Download" not in records["id:0b1e-uuid"]["title"]
    assert records["id:0b1e-uuid"]["state"] == "COMPLETED"


def test_the_month_check_reads_names_or_numeric_dates_and_not_look_alikes():
    assert site.names_month("Statement period 01/01/2026 to 01/31/2026", "January", "2026")
    assert site.names_month("as of 2026-02-28", "February", "2026")
    assert site.names_month("Jan 2026 statement", "January", "2026")
    assert site.names_month("January 31, 2026", "Jan", "2026")
    assert not site.names_month("Decrease in value 2025", "December", "2025"), "Dec inside Decrease"
    assert not site.names_month("12/31/2024 to 12/31/2024", "December", "2025"), "wrong year"
    assert not site.names_month("02/28/2026", "January", "2026"), "wrong month"
    assert not site.names_month("", "January", "2026")
    assert not site.names_month("January 2025 statement total 2026", "January", "2026"), "name and year must be bound"
    assert site.names_month("Jan. 31st, 2026", "January", "2026")
    assert not site.names_month("January 31 2025 and later in 2026", "January", "2026")
    assert not site.names_month("Sept. 2026", "January", "2026"), "sept is September only"
    assert site.names_month("Sept. 2026", "September", "2026")
