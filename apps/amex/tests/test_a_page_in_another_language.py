"""American Express in another language, as a German account showed it (#107).

A tester's German account names its links and sections in German, and the
app looked for English words. From the dashboard it found no "Statements &
Activity" link and listed nothing, and with the older statements' section
collapsed, every older statement's Download was not found. The tester found
what is the same in every language. The links point at /activity and
/activity/statements, each with a query of its own, and the section's
heading controls content-older-statements.

So where the English words find nothing, a link is found by its address,
and a section's heading by the part it opens. An English page is reached
the way it always was. Everything here is invented, and it drives a real
browser against made-up pages served at American Express's own address, so
where each press lands and where the tab goes are the browser's own.
"""
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import amex_site as site

BASE = "https://global.americanexpress.com"
RECENT = ["2031-09-25", "2031-08-25"]
OLDER = ["2031-07-25", "2031-06-25"]


def _button(section: str, date: str) -> str:
    return ('<button data-test-id="axp-pdf-statements/common/Table/%s-statements/%s/'
            'download-button">Herunterladen</button>' % (section, date))


STATEMENTS = """<!doctype html><meta charset="utf-8"><title>PDF-Abrechnungen</title>
<main>
<h1>PDF-Abrechnungen</h1>
<section>%(recent)s</section>
<button id="header-older-statements" aria-controls="content-older-statements"
  aria-expanded="%(expanded)s">Vergangene Abrechnungen</button>
<section id="content-older-statements" %(hidden)s>%(older)s</section>
</main>
<script>
window.headPresses = 0;
document.getElementById('header-older-statements').addEventListener('click', (e) => {
  window.headPresses++;
  const open = e.target.getAttribute('aria-expanded') === 'true';
  e.target.setAttribute('aria-expanded', open ? 'false' : 'true');
  document.getElementById('content-older-statements').hidden = open;
});
</script>"""

DASHBOARD_TO_STATEMENTS = """<!doctype html><meta charset="utf-8"><title>Startseite</title>
<main><h1>Willkommen</h1>
<a href="/activity/statements?account_key=K0NT0">Abrechnung(en) runterladen</a></main>"""

DASHBOARD_TO_ACTIVITY = """<!doctype html><meta charset="utf-8"><title>Startseite</title>
<main><h1>Willkommen</h1>
<a href="/activity?inav=menu_myacct_acctsum">Kontobewegungen &amp; Abrechnungen</a></main>"""

ACTIVITY = """<!doctype html><meta charset="utf-8"><title>Kontobewegungen</title>
<main><h1>Kontobewegungen</h1>
<a href="/activity/statements?account_key=K0NT0">PDF-Abrechnungen</a></main>"""

# The English dashboard as the app has always met it, with a link to the
# statements' address beside the English one.
DASHBOARD_ENGLISH = """<!doctype html><meta charset="utf-8"><title>Home</title>
<main><h1>Welcome</h1>
<a href="/activity?inav=x">Statements &amp; Activity</a>
<a href="/activity/statements?account_key=K0NT0">Download statements</a></main>"""

ACTIVITY_ENGLISH = """<!doctype html><meta charset="utf-8"><title>Activity</title>
<main><h1>Activity</h1>
<a data-testid="goToPdfLinkLg" href="/activity/statements?account_key=K0NT0">Go to PDF Statements</a>
</main>"""


class Site:
    def __init__(self, dashboard=DASHBOARD_TO_STATEMENTS, activity=ACTIVITY, expanded=False):
        self.dashboard, self.activity, self.expanded = dashboard, activity, expanded
        self.navigations = []

    def handle(self, route):
        req = route.request
        parts = urlsplit(req.url)
        if req.is_navigation_request():
            self.navigations.append(parts.path + ("?" + parts.query if parts.query else ""))
        if parts.hostname != "global.americanexpress.com":
            return route.abort()
        if parts.path == "/dashboard":
            return route.fulfill(content_type="text/html", body=self.dashboard)
        if parts.path == "/activity":
            return route.fulfill(content_type="text/html", body=self.activity)
        if parts.path == "/activity/statements":
            return route.fulfill(content_type="text/html", body=STATEMENTS % {
                "recent": "".join(_button("recent", d) for d in RECENT),
                "older": "".join(_button("older", d) for d in OLDER),
                "expanded": "true" if self.expanded else "false",
                "hidden": "" if self.expanded else "hidden"})
        return route.fulfill(content_type="text/html", body="<p>American Express</p>")


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

    def opener(fake: Site, path: str):
        ctx = browser.new_context()
        contexts.append(ctx)
        ctx.route("**/*", fake.handle)
        pg = ctx.new_page()
        pg.goto(BASE + path)
        fake.navigations.clear()
        return pg
    yield opener
    for ctx in contexts:
        ctx.close()


def test_a_dashboard_in_another_language_reaches_the_statements_by_its_link(open_site):
    fake = Site(dashboard=DASHBOARD_TO_STATEMENTS)
    pg = open_site(fake, "/dashboard")
    assert site.goto_documents(pg) is True
    assert fake.navigations == ["/activity/statements?account_key=K0NT0"]


def test_an_activity_link_in_another_language_leads_on_to_the_statements(open_site):
    fake = Site(dashboard=DASHBOARD_TO_ACTIVITY)
    pg = open_site(fake, "/dashboard")
    assert site.goto_documents(pg) is True
    assert fake.navigations == ["/activity?inav=menu_myacct_acctsum",
                                "/activity/statements?account_key=K0NT0"]


def test_an_english_dashboard_is_reached_as_it_always_was(open_site):
    """The English words come first, so an English page takes the way it
    always took, though a link to the statements' address is there too."""
    fake = Site(dashboard=DASHBOARD_ENGLISH, activity=ACTIVITY_ENGLISH)
    pg = open_site(fake, "/dashboard")
    assert site.goto_documents(pg) is True
    assert fake.navigations == ["/activity?inav=x", "/activity/statements?account_key=K0NT0"]


def test_the_older_statements_open_by_the_part_their_heading_controls(open_site):
    """Every older statement's Download was not found while the section
    stayed collapsed. Its heading is pressed once, and an open one never."""
    fake = Site()
    pg = open_site(fake, "/activity/statements?account_key=K0NT0")
    assert site.goto_documents(pg) is True
    assert pg.evaluate("window.headPresses") == 1
    assert pg.get_attribute("#header-older-statements", "aria-expanded") == "true"
    for date in OLDER:
        assert pg.locator(site._by_test_id("$=", "/%s/download-button" % date)).first.is_visible()
    docs = site.collect_download_docs(pg)
    assert sorted(d.date_text for d in docs) == sorted(RECENT + OLDER)
    site.expand_sections(pg)
    assert pg.evaluate("window.headPresses") == 1


def test_an_open_older_section_is_never_pressed(open_site):
    fake = Site(expanded=True)
    pg = open_site(fake, "/activity/statements?account_key=K0NT0")
    assert site.goto_documents(pg) is True
    assert pg.evaluate("window.headPresses") == 0
    assert pg.get_attribute("#header-older-statements", "aria-expanded") == "true"
