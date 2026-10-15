"""Each statement row is read by its own month, or by nothing.

The statements page lists "Last 30 days" first and then one row per month,
each with HTML, PDF and CSV buttons. A PDF button's month is read upward
from the button, from the nearest element whose text starts with a month.
A row whose own text began with something else, a badge say, climbed on to
the list around every row, whose text starts with "Last 30 days", and was
read as that row. The list skipped "Last 30 days" however often it came, so
that month was dropped without a word. Now an element holding a second PDF
button is the list, not a row, and the climb stops there with no label,
which stops the listing.

Every row here is invented and the browser is refused the network.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import coinbase_site as site

BUTTONS = ("<div><button type='button'>HTML</button>"
           "<button type='button' data-testid='statements-pdf-v2'>PDF</button>"
           "<button type='button'>CSV</button></div>")


def statements(*rows: str) -> str:
    return ("<!doctype html><html><body><main><div class='list'>%s</div></main></body></html>"
            % "".join("<div class='row'>%s%s</div>" % (r, BUTTONS) for r in rows))


@pytest.fixture()
def page():
    pw = pytest.importorskip("playwright.sync_api")
    try:
        driver = pw.sync_playwright().start()
        browser = driver.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip("no browser to drive: %s" % e)
    ctx = browser.new_context()
    ctx.route("**/*", lambda route: route.abort())
    yield ctx.new_page()
    browser.close()
    driver.stop()


def test_each_row_reads_its_own_month(page):
    page.set_content(statements("<span>Last 30 days</span>", "<span>September 2026</span>",
                                "<span>August 2026</span>"))
    assert [r["label"] for r in site._read_rows(page)] == ["Last 30 days", "September 2026",
                                                           "August 2026"]


def test_a_row_that_does_not_start_with_its_month_reads_nothing(page):
    page.set_content(statements("<span>Last 30 days</span>", "<span>September 2026</span>",
                                "<span>New</span> <span>August 2026</span>"))
    assert [r["label"] for r in site._read_rows(page)] == ["Last 30 days", "September 2026", ""]


def test_that_row_stops_the_listing_rather_than_dropping_its_month(page, monkeypatch):
    monkeypatch.setattr(site, "LOAD_MORE_LOOK_AGAIN_MS", 10)
    page.set_content(statements("<span>Last 30 days</span>", "<span>September 2026</span>",
                                "<span>New</span> <span>August 2026</span>"))
    with pytest.raises(site.ListStopped) as stopped:
        site._collect_statements(page, "2026-08-03", date(2026, 10, 11))
    assert [d.date_text for d in stopped.value.docs] == ["2026-09-30"]


# The Credit Card tab's list, one row per month, its text "<Month YYYY>" and
# a button of an icon glyph and "PDF", with no data-testid. Invented.
CARD_BUTTON = "<div><button type='button'>\U000f0382 PDF</button></div>"


def card_statements(*rows: str) -> str:
    return ("<!doctype html><html><body><main><h1>Credit Card Statements</h1>"
            "<div class='list'>%s</div><button type='button'>Generate</button></main></body></html>"
            % "".join("<div class='row'><div>%s</div>%s</div>" % (r, CARD_BUTTON) for r in rows))


def test_each_card_row_reads_its_own_month(page):
    page.set_content(card_statements("October 2026", "September 2026", "August 2026"))
    rows = site._card_rows(page)
    assert [r["label"] for r in rows] == ["October 2026", "September 2026", "August 2026"]
    assert all(site.is_card_pdf_label(r["button"]) for r in rows)
    pressed = page.locator("button").nth(rows[1]["i"]).element_handle()
    assert pressed.evaluate(site._CARD_LABEL_OF_JS) == "September 2026"


def test_a_card_row_that_does_not_start_with_its_month_reads_nothing(page):
    """It climbs to the list, which holds a second card PDF button, and
    stops there, rather than read as the first row's month."""
    page.set_content(card_statements("October 2026", "<span>New</span> September 2026"))
    assert [r["label"] for r in site._card_rows(page)] == ["October 2026", ""]


def test_the_transactions_pdf_buttons_are_not_card_rows(page):
    page.set_content(statements("<span>September 2026</span>"))
    assert site._card_rows(page) == []
