"""A U.S. Bank failure file says which part of the statement list is missing.

A tester's Pilot gave up on its first statement a tenth of a second after it
began, before anything was pressed (#106). The download finds a statement's
row by the statement list, its section, the section's heading, the row and
the row's Download button, and the failure file counted only the general
selectors, every one of which matched. So it could not say which of those
parts the page lacked. Now each of them is counted, under its own name, and
the names are the ones the download itself uses. Everything here is
invented.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import usbank_site as site
from paperpull_core import failure

PARTS = ("statement_section", "statements_heading", "statement_row", "row_download")


def test_the_counted_parts_are_the_ones_the_download_finds_a_row_by():
    sel = site.SEL
    assert site.FALLBACK["document_view"] == sel["doc_view"]
    assert site.FALLBACK["account_dropdown"] == sel["account"]
    assert site.FALLBACK["year_filter"] == sel["year_filter"]
    assert site.FALLBACK["list_of_statements"] == sel["list"]
    section = "%s %s" % (sel["list"], sel["section"])
    assert site.FALLBACK["statement_section"] == section
    assert site.FALLBACK["statements_heading"] == section + " h3"
    assert site.FALLBACK["statement_row"] == "%s %s" % (section, sel["row"])
    assert site.FALLBACK["row_download"].startswith(site.FALLBACK["statement_row"] + " button")


# The list as the download expects it, a section headed by its statements,
# one row and its Download button. Without the button, the row is all there
# is.
LIST = ("<div data-testid='list-of-statements'><div class='document-list'>"
        "<h3>Checking statements</h3><ul><li class='download-items'>September 30, 2031"
        "%s</li></ul></div></div>")
BUTTON = "<button aria-label='Download September 30, 2031 statement'>Download</button>"


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


def _counts(browser, tmp_path, html):
    page = browser.new_page()
    try:
        page.set_content("<!doctype html><meta charset='utf-8'><main>%s</main>" % html)
        path = failure.write_failure(tmp_path, command="pilot", step="capture the document",
                                     reason="the document would not render", page=page,
                                     selectors=site.FALLBACK, provider="U.S. Bank")
    finally:
        page.close()
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return {s["name"]: s.get("matched") for s in report["selectors"]}


def test_a_failure_file_counts_each_part_of_the_list(browser, tmp_path):
    counts = _counts(browser, tmp_path, LIST % BUTTON)
    for name in PARTS:
        assert counts.get(name) == 1, (name, counts)


def test_a_row_with_no_download_button_shows_as_one(browser, tmp_path):
    counts = _counts(browser, tmp_path, LIST % "")
    assert counts["statement_row"] == 1 and counts["row_download"] == 0, counts
