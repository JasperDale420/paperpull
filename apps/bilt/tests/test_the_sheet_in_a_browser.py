"""The wallet script read in a real browser: what it counts as a control
for more statements, and where it looks for one.

The page is made up in the shape the live wallet showed, the card under
its "View balance summary" button, a Recent activity "View all" link, and
the statements sheet drawn apart from the wallet. Every value is invented,
and the browser is refused the network, so nothing here can reach a real
site. The sheet can show a control that would bring more statements,
which the live sheet did not, and the script has to count one on the
sheet and leave alone the wallet's own "View all", "More" and "Learn
more", which are not about the list.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bilt_site as site  # noqa: E402

CARD = "0a1b2c3d-1111-4222-8333-944455556666"
DATES = ["October 06, 2026", "September 06, 2026", "August 06, 2026"]

WALLET = """
<nav><button type="button">Rewards</button><button type="button">More</button></nav>
<main id="wallet">
 <section id="card">
  <div><span>Bilt Blue Card</span> <span>•••• 4821</span> <button type="button">View</button></div>
  <button type="button" aria-label="View balance summary"><svg width="10" height="10"></svg></button>
  <p>Recent activity <a href="/wallet/transactions?cardId=%(card)s">View all</a></p>
  <a href="#">Learn more</a> <button type="button" aria-label="More">...</button>
 </section>
 %(inline)s
</main>
%(portal)s
"""

SHEET = """
<div id="sheet" role="dialog">
 <h2>Statements</h2>
 <ul>%(rows)s</ul>
 %(extra)s
</div>
"""

ROW = '<li><button type="button" aria-label="Download statement for %s">%s</button></li>'


def page_of(extra="", inline=False):
    sheet = SHEET % {"rows": "".join(ROW % (d, d) for d in DATES), "extra": extra}
    return "<!doctype html><html><body>%s</body></html>" % (WALLET % {
        "card": CARD, "inline": sheet if inline else "", "portal": "" if inline else sheet})


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
def wallet(browser):
    ctx = browser.new_context()
    ctx.route("**/*", lambda route: route.abort())
    site.set_private_words([])
    yield ctx.new_page()
    site.set_private_words([])
    ctx.close()


def read(pg, html):
    pg.set_content(html)
    return pg.evaluate(site._STATE_JS)


@pytest.mark.parametrize("inline", [False, True])
def test_a_sheet_with_no_control_for_more_counts_none(wallet, inline):
    """The nav's and the card's own "More", the card's "Learn more" and
    Recent activity's "View all" sit outside the sheet, or say nothing of
    statements."""
    state = read(wallet, page_of(inline=inline))
    assert state["pagers"] == []
    assert site.sheet_dates(state) == ["2026-08-06", "2026-09-06", "2026-10-06"]
    assert [c["last4"] for c in state["cards"]] == ["4821"]


@pytest.mark.parametrize("extra", [
    '<button type="button">Load more</button>',
    '<div role="button">Show older statements</div>',
    '<a href="#">View all statements</a>',
    '<button type="button" aria-label="Next page"><svg width="10" height="10"></svg></button>',
    '<button type="button" title="Older statements">&gt;</button>',
])
@pytest.mark.parametrize("inline", [False, True])
def test_a_control_for_more_on_the_sheet_is_counted(wallet, extra, inline):
    state = read(wallet, page_of(extra, inline=inline))
    assert state["pagers"] == [1]
    with pytest.raises(site.ListStopped):
        site._no_pager(state)


def test_a_hidden_control_for_more_is_not_counted(wallet):
    state = read(wallet, page_of('<button type="button" style="display:none">Load more</button>'))
    assert state["pagers"] == []



# The wallet as a signed-out visit drew it on 2026-10-15, at /wallet itself.
SIGNED_OUT = """<!doctype html><html><body>
<nav><a href="/">Bilt</a><button type="button">Sign up / Log in</button></nav>
<main>
 <h1>Complete login to continue</h1>
 <p>Welcome to Bilt. Log in or sign up.</p>
 <div role="tablist"><button role="tab">Phone</button><button role="tab">Email</button></div>
 <input type="text" aria-label="Phone number">
 <button type="button">Continue</button>
 <button type="button">Sign in with passkey</button>
</main>
</body></html>"""


def signed_out_by(pg, html):
    pg.set_content(html)
    state = pg.evaluate(site._STATE_JS)
    alone = pg.evaluate(site._SIGNIN_JS)
    assert set(alone) == {"signin", "summary"}
    assert alone["signin"] == state["signin"]
    assert site.shows_sign_in(alone) == site.shows_sign_in(state)
    return site.shows_sign_in(state), state


def test_the_sign_in_drawn_on_the_wallet_is_read_as_signed_out(wallet):
    out, state = signed_out_by(wallet, SIGNED_OUT)
    assert out is True
    assert state["password"] is False and state["cards"] == [] and state["summary"] == []


@pytest.mark.parametrize("keep", [
    '<h1>Complete login to continue</h1>',
    '<p>Welcome to Bilt. Log in or sign up.</p>',
    '<button type="button">Sign up / Log in</button>',
])
def test_each_sign_of_the_sign_in_is_enough_alone(wallet, keep):
    out, _state = signed_out_by(wallet, "<!doctype html><html><body>%s</body></html>" % keep)
    assert out is True


@pytest.mark.parametrize("html", [
    '<p style="display:none">Complete login to continue</p>',
    '<button type="button" style="visibility:hidden">Sign up / Log in</button>',
    '<p>Complete login to continue later</p>',
    '<p>Log in</p><button type="button">Log in</button>',
    '<button type="button">Sign up / Log in now</button>',
])
def test_a_hidden_or_other_wording_is_not_the_sign_in(wallet, html):
    out, _state = signed_out_by(wallet, "<!doctype html><html><body>%s</body></html>" % html)
    assert out is False


def test_the_sign_in_words_on_the_wallet_beside_its_card_are_not_signed_out(wallet):
    """A merchant or a row of the card's own content reading the words is
    not the sign-in while the balance summary button shows."""
    html = page_of('<p>Log in or sign up</p><p>Recent activity Log in</p>')
    out, state = signed_out_by(wallet, html)
    assert state["signin"] is True and out is False
    assert [c["last4"] for c in state["cards"]] == ["4821"]
    out, _state = signed_out_by(wallet, page_of())
    assert out is False
