"""A NetBenefits login with several employers and plans, offline.

The page is a spy that answers the way the surveyed site did: the
Statements address shows a "Select Account" page when no plan is chosen,
each option's value is that plan's own Statements address, and one plan's
address goes to the Fidelity retail sign-in. Every address the app opens,
every navigation it lets through and every script it runs is written down,
so a test can say what was never done as well as what was."""
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  (binds this provider's AppSpec)
import netbenefits_site as site
import netbenefits_docs as docs
from paperpull_core import receipt_pdf
from paperpull_core.testkit import receipt_app, sample_pdf

BARE = site.URLS["documents"]
PATH = "/mybenefits/savings2/navigation/dc/OnlineStatement?&client=%s&plan=%s"
EMP_A, EMP_B = "900000001", "900000002"
P403, P457, PXR = "41403", "41457", "41401"
IDS = (EMP_A, EMP_B, P403, P457, PXR)
OPTIONS = [
    {"text": "Select an account", "value": "", "disabled": True},
    {"text": "ABC 403(B) PLAN", "value": PATH % (EMP_A, P403), "disabled": False},
    {"text": "NORTH-STAR MEDICAL GROUP", "value": PATH % (EMP_B, PXR), "disabled": False},
    {"text": "ABC 457(B) PLAN", "value": PATH % (EMP_A, P457), "disabled": False},
]
RETAIL_SIGNIN = "https://digital.fidelity.com/prgw/digital/signin/retail"
NB_LOGIN = "https://nb.fidelity.com/static/mybenefits/netbenefitslogin/"


class Locator:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    def count(self):
        if "password" in self.sel:
            return 1 if self.page.url.startswith(NB_LOGIN) else 0
        if "frmRequest" in self.sel:
            return 1 if self.page.showing else 0
        return 1

    def inner_text(self, timeout=None):
        return self.page.body()


class Page:
    """Plans by their address: 'ok' answers with its Statements page and
    asking for it chooses the plan, 'retail' redirects to the retail sign-in
    (as one plan's did on the surveyed login), 'sticky' answers with its page
    but leaves the site on the plan chosen before, 'login' answers but the
    Statements page then comes back as the sign-in page, 'other' answers
    with another plan's page."""

    def __init__(self, behaviors, choose=True, signed_in=True, single_heading=None):
        self.behaviors = behaviors
        self.choose = choose
        self.signed_in = signed_in
        self.single_heading = single_heading
        self.url = BARE
        self.gotos, self.probes = [], []
        self.showing = None          # the plan whose Statements page is shown
        self.in_context = None       # the plan the site would make a statement for
        self.heading_of = {}
        self.signs_out = False
        self.made = []

    def body(self):
        if self.showing:
            return "Home Accounts & Benefits %s (%s) Statements Time period Get statement" % (
                self.heading_of[self.showing], self.showing.rsplit("=", 1)[1])
        if self.single_heading:
            return "%s (12345) Statements Get statement" % self.single_heading
        return "Select Account The page you've chosen requires you to select an account."

    def title(self):
        return "Fidelity NetBenefits"

    def locator(self, sel):
        return Locator(self, sel)

    def wait_for_timeout(self, ms):
        pass

    def goto(self, url, **kw):
        # The app only ever loads the bare Statements address.
        assert url == BARE, url
        self.gotos.append(url)
        if self.signs_out:
            self.url, self.showing = NB_LOGIN, None
            return
        self.url = BARE
        self.showing = self.in_context

    def evaluate(self, js, arg=None):
        if js == site._READ_CHOICES_JS:
            return [dict(o) for o in OPTIONS] if self.choose and not self.showing else None
        if js == site._SIGNED_IN_JS:
            return self.signed_in
        if js == site._MAKE_JS:
            self.made.append(self.in_context)
            return {"status": 200, "isStatement": True, "title": "Statement Details"}
        if js == site._PROBE_JS and arg["path"] == BARE[len(site.WPS):]:
            return {"kind": "page", "status": 200, "form": False, "password": False, "named": False}
        if js == site._PROBE_JS:
            self.probes.append(arg["path"])
            how = self.behaviors[arg["path"]]
            if how == "retail" or not self.signed_in:
                return {"kind": "redirect"}
            heading = self.heading_of[arg["path"]] if how != "other" else "SOMEONE ELSE PLAN"
            text = "%s (%s) Statements" % (heading, arg["path"].rsplit("=", 1)[1])
            if how in ("ok", "login"):
                self.in_context = arg["path"]
            if how == "login":
                self.signs_out = True
            return {"kind": "page", "status": 200, "form": True, "password": False,
                    "named": bool(re.search(arg["heading"], text, re.I))}
        raise AssertionError("an unexpected script ran")


def multi_page(**behaviors_by_plan):
    names = {P403: "ABC 403(B) PLAN", PXR: "NORTH-STAR MEDICAL GROUP", P457: "ABC 457(B) PLAN"}
    clients = {P403: EMP_A, P457: EMP_A, PXR: EMP_B}
    behaviors = {PATH % (clients[p], p): behaviors_by_plan.get(p, "ok") for p in names}
    page = Page(behaviors)
    page.heading_of = {PATH % (clients[p], p): n for p, n in names.items()}
    return page


# Which plans have which quarters. A plan is told by the plan the site has
# in context when the statement is made, as the real site tells it.
HAVE = {P457: {"04/01/2026-06/30/2026", "01/01/2026-03/31/2026"},
        PXR: {"04/01/2026-06/30/2026"}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    site._plan_in_use["plan"] = None
    site._last_navigation["at"] = 0.0
    site.set_private_words([])
    real = site.periods
    monkeypatch.setattr(site, "periods",
                        lambda kind, today=None, first_year=site.FIRST_YEAR: real(kind, date(2026, 9, 18), 2025))
    yield
    site.set_private_words([])


def statements_by_plan(page, calls, single=None):
    def make(pg, date_range, want_html=False):
        plan = page.in_context.rsplit("=", 1)[1] if page.in_context else None
        calls.append((plan, date_range, want_html))
        have = single if plan is None else HAVE.get(plan, set())
        if date_range not in have:
            return {"status": 200, "title": "Online Statement error page", "isStatement": False, "html": ""}
        return {"status": 200, "title": "Statement Details", "isStatement": True,
                "plan": "SOME CORP. RETIREMENT SAVINGS PLAN (12345)", "period": date_range,
                "html": "<html><head></head><body>%s %s</body></html>" % (plan, date_range)}
    return make


def app_on(tmp_path, page):
    app = receipt_app(docs, tmp_path, owner="")
    app.page = lambda: page
    app._on_its_site = lambda: page
    return app


def no_ids_in(text):
    return not any(i in text for i in IDS)


# -- the plan's name ----------------------------------------------------------------

def test_an_option_reads_like_a_plan_name():
    assert site.option_label("ABC 457(B) PLAN") == "ABC 457(b) Plan"
    assert site.option_label("NORTH-STAR MEDICAL GROUP") == "North-Star Medical Group"
    assert site.option_label("ABC DCP") == "ABC DCP"
    assert site.option_label("LAKESIDE COLLEGE OF ARTS 401(K) (12345)") == "Lakeside College of Arts 401(k)"
    assert site.option_label("ACME INC. 401(K) PLAN") == "Acme Inc. 401(k) Plan"
    assert site.option_label("Acme 401(k) Plan") == "Acme 401(k) Plan"
    assert site.option_label("  ") == ""


# -- the Select Account page --------------------------------------------------------

def test_the_select_account_page_is_read_and_nothing_is_pressed():
    page = multi_page()
    plans = site.read_plan_choices(page)
    assert [p["label"] for p in plans] == ["ABC 403(b) Plan", "North-Star Medical Group", "ABC 457(b) Plan"]
    assert all(re.fullmatch(r"[0-9a-f]{64}", p["digest"]) for p in plans)
    assert len({p["digest"] for p in plans}) == 3
    assert page.gotos == [BARE]                        # only the bare address, Go never used
    for p in plans:
        assert no_ids_in(p["label"]) and no_ids_in(p["digest"])


def test_the_plan_ids_are_private_words():
    site.read_plan_choices(multi_page())
    said = site.redact("client %s plan %s and %s" % (EMP_A, P457, PXR))
    assert no_ids_in(said)


def test_an_option_off_the_statements_page_is_never_opened():
    plan = site.plan_from_option("RETAIL PLAN", "https://digital.fidelity.com/x?client=1&plan=2")
    assert plan["path"] == "" and plan["review"] == site.LEAVES_THE_SITE
    other = site.plan_from_option("OTHER", "/mybenefits/moneymovement/navigation/dc/loanwith?client=1&plan=2")
    assert other["path"] == ""
    page = multi_page()
    assert site.open_plan(page, plan) == site.LEAVES_THE_SITE
    assert page.gotos == [] and page.probes == []
    assert site.plan_from_option("Select an account", "") is None


def test_a_plan_list_read_back_must_still_be_what_this_app_wrote():
    plan = site.plan_from_option("ABC 457(B) PLAN", PATH % (EMP_A, P457))
    assert site.checked_plan(dict(plan)) == dict({k: plan[k] for k in ("label", "digest", "path", "heading")},
                                                 shared_name=False)
    assert site.checked_plan(dict(plan, digest="0" * 64)) is None
    assert site.checked_plan(dict(plan, path="https://digital.fidelity.com" + plan["path"])) is None


# -- opening a plan -----------------------------------------------------------------

def test_a_plan_that_redirects_is_left_for_the_owner_and_never_followed():
    page = multi_page(**{P403: "retail"})
    plans = site.read_plan_choices(page)
    assert site.open_plan(page, plans[0]) == site.REDIRECTS
    assert page.probes == [plans[0]["path"]]
    assert page.gotos == [BARE]                       # looked at, never navigated to
    assert site._plan_in_use["plan"] is None


def test_a_plan_the_site_does_not_switch_to_is_not_taken_for_the_one_before():
    page = multi_page(**{P457: "sticky"})
    plans = site.read_plan_choices(page)
    assert site.open_plan(page, plans[1]) == site.OPENED
    assert site.open_plan(page, plans[2]) == site.DID_NOT_OPEN
    assert site._plan_in_use["plan"] is None


def test_two_plans_with_one_name_are_told_apart_by_their_number(monkeypatch):
    twins = [{"text": "ABC 401(K) PLAN", "value": PATH % (EMP_A, P403), "disabled": False},
             {"text": "ABC 401(K) PLAN", "value": PATH % (EMP_A, P457), "disabled": False}]
    monkeypatch.setattr(sys.modules[__name__], "OPTIONS", twins)
    page = multi_page(**{P457: "sticky"})
    page.heading_of = {t["value"]: t["text"] for t in twins}
    plans = site.read_plan_choices(page)
    assert all(p["shared_name"] for p in plans)
    assert site.open_plan(page, plans[0]) == site.OPENED
    # The second plan's page is never shown, the first's is, under the same name.
    assert site.open_plan(page, plans[1]) == site.DID_NOT_OPEN


def test_a_plan_name_carrying_its_number_loses_it():
    plan = site.plan_from_option("ABC PLAN %s" % P457, PATH % (EMP_A, P457))
    assert plan["label"] == "ABC Plan" and no_ids_in(plan["label"])
    glued = site.plan_from_option("Employer%sPlan" % P457, PATH % (EMP_A, P457))
    assert no_ids_in(glued["label"])
    overlap = site.plan_from_option("Employer12345Plan", PATH % ("123", "12345"))
    assert "123" not in overlap["label"] and "45" not in overlap["label"]


def test_an_option_that_already_shows_its_number_still_opens():
    named = [{"text": "ABC 401(K) PLAN (%s)" % P457, "value": PATH % (EMP_A, P457), "disabled": False}]
    page = multi_page()
    page.heading_of = {named[0]["value"]: "ABC 401(K) PLAN"}
    page.behaviors = {named[0]["value"]: "ok"}
    plan = site.plan_from_option(named[0]["text"], named[0]["value"])
    plan["shared_name"] = True
    assert site.open_plan(page, plan) == site.OPENED


def test_a_statement_is_never_made_while_the_tab_shows_another_plan(monkeypatch):
    page = multi_page()
    plans = site.read_plan_choices(page)
    site.open_plan(page, plans[2])
    # Something else chose another plan on the site and reloaded the tab.
    page.in_context = page.showing = plans[1]["path"]
    page.probes.clear()
    site._last_navigation["at"] = __import__("time").monotonic()
    site.make_statement(page, "04/01/2026-06/30/2026")
    assert page.probes == [plans[2]["path"]]
    assert page.made == [plans[2]["path"]]


def test_a_page_that_is_not_the_plans_own_is_not_opened():
    page = multi_page(**{P457: "other"})
    plans = site.read_plan_choices(page)
    assert site.open_plan(page, plans[2]) == site.NOT_ITS_PAGE
    assert page.gotos == [BARE]


def test_a_plan_opens_with_its_heading_and_form_and_its_address_is_never_loaded():
    page = multi_page()
    plans = site.read_plan_choices(page)
    assert site.open_plan(page, plans[1]) == site.OPENED
    assert page.gotos == [BARE, BARE] and page.showing == plans[1]["path"]
    assert site._plan_in_use["plan"]["digest"] == plans[1]["digest"]


def test_a_sign_in_page_on_a_later_plan_is_session_expired():
    page = multi_page(**{P457: "login"})
    plans = site.read_plan_choices(page)
    assert site.open_plan(page, plans[1]) == site.OPENED
    with pytest.raises(site.SessionExpired):
        site.open_plan(page, plans[2])


def test_a_redirect_from_a_signed_out_session_is_session_expired_not_a_plan_to_review():
    page = multi_page()
    plans = site.read_plan_choices(page)
    page.signed_in = False
    with pytest.raises(site.SessionExpired):
        site.open_plan(page, plans[1])
    assert page.gotos == [BARE]


def test_the_keep_awake_reload_chooses_the_plan_in_use_again():
    page = multi_page()
    plans = site.read_plan_choices(page)
    site.open_plan(page, plans[2])
    page.probes.clear()
    site._last_navigation["at"] -= site.KEEP_AWAKE_SECONDS + 1
    site._keep_awake(page)
    assert page.probes == [plans[2]["path"]] and page.showing == plans[2]["path"]


def test_a_plan_that_will_not_open_again_is_its_own_failure_not_a_statement(monkeypatch, tmp_path):
    page = multi_page()
    plans = site.read_plan_choices(page)
    site.open_plan(page, plans[2])
    page.behaviors[plans[2]["path"]] = "retail"
    site._last_navigation["at"] -= site.KEEP_AWAKE_SECONDS + 1
    with pytest.raises(site.PlanDidNotOpen) as e:
        site._keep_awake(page)
    assert e.value.reason == site.REDIRECTS
    monkeypatch.setattr(site, "make_statement", lambda *a, **k: (_ for _ in ()).throw(site.PlanDidNotOpen("x")))
    site._plan_in_use["plan"] = plans[2]
    assert not site.download_document(page, "Quarterly Statement", "2026-06-30", tmp_path / "s.pdf",
                                      item_hint="04/01/2026-06/30/2026", plan=plans[2])


def test_a_statement_request_that_is_redirected_is_session_expired(monkeypatch):
    class P:
        url = "https://workplaceservices.fidelity.com/mybenefits/savings2/navigation/dc/OnlineStatement"

        def locator(self, *_):
            return type("L", (), {"count": lambda s: 0})()

        def evaluate(self, js, arg=None):
            return {"status": 0, "stage": "token", "signedOut": True}
    monkeypatch.setattr(site, "_keep_awake", lambda page: None)
    with pytest.raises(site.SessionExpired):
        site.make_statement(P(), "01/01/2026-03/31/2026")


# -- discovery and download through the app -----------------------------------------

def test_discover_files_each_plan_under_its_own_name(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    page = multi_page(**{P403: "retail"})
    calls = []
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, calls))
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    recs = list(app.discovery.data.values())
    assert sorted((r["account"], r["date"]) for r in recs) == [
        ("ABC 457(b) Plan", "2026-03-31"), ("ABC 457(b) Plan", "2026-06-30"),
        ("North-Star Medical Group", "2026-06-30")]
    assert len(app.discovery.data) == 3              # one quarter, two plans, two keys
    assert all(r["occurrence"] == 0 for r in recs)
    assert app.stats["manual_review"] == 1           # ABC 403(b), left for the owner
    assert not any(P403 in u for u in page.gotos)
    names = [docs.build_pdf_filename(r["date"], r["summary"], "", record=docs.Document.from_dict(r))
             for r in recs]
    assert "2026-06-30 NetBenefits Quarterly Statement - ABC 457(b) Plan.pdf" in names
    assert "2026-06-30 NetBenefits Quarterly Statement - North-Star Medical Group.pdf" in names
    for text in names + list(app.discovery.data) + [app.paths.discovery_json.read_text(), caplog.text]:
        assert no_ids_in(text)
    held = json.loads((app.paths.root / "plans.json").read_text())
    assert len(held) == 3 and no_ids_in(json.dumps([h["label"] for h in held.values()]))


def test_a_plan_left_for_review_is_written_down_before_a_later_sign_out(monkeypatch, tmp_path):
    page = multi_page(**{P403: "retail", P457: "login"})
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, []))
    app = app_on(tmp_path, page)
    with pytest.raises(site.SessionExpired):
        app.cmd_discover(quiet=True)
    held = json.loads((tmp_path / "out" / "plans.json").read_text())
    assert [h.get("last_result") for h in held.values() if h["label"] == "ABC 403(b) Plan"] == [site.REDIRECTS]


def test_a_plan_that_stops_opening_partway_is_left_and_the_next_plan_still_listed(monkeypatch, tmp_path):
    page = multi_page()
    calls = []
    good = statements_by_plan(page, calls)

    def make(pg, date_range, want_html=False):
        if page.in_context and page.in_context.endswith(PXR):
            raise site.PlanDidNotOpen(site.REDIRECTS)
        return good(pg, date_range, want_html)
    monkeypatch.setattr(site, "make_statement", make)
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    assert sorted({r["account"] for r in app.discovery.data.values()}) == ["ABC 457(b) Plan"]
    assert app.stats["manual_review"] == 1


def test_a_login_with_one_plan_is_listed_as_before(monkeypatch, tmp_path):
    page = Page({}, choose=False, single_heading="SOME CORP. RETIREMENT SAVINGS PLAN")
    calls = []
    monkeypatch.setattr(site, "make_statement",
                        statements_by_plan(page, calls, single={"04/01/2026-06/30/2026"}))
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    assert list(app.discovery.data) == ["Statement:2026-06-30:Quarterly Statement"]
    rec = app.discovery.data["Statement:2026-06-30:Quarterly Statement"]
    assert rec["summary"] == "Quarterly Statement - Some Corp. Retirement Savings Plan"
    assert rec["plan"] == ""
    assert not (app.paths.root / "plans.json").exists() or json.loads(
        (app.paths.root / "plans.json").read_text()) == {}
    assert page.probes == []
    name = docs.build_pdf_filename(rec["date"], rec["summary"], "", record=docs.Document.from_dict(rec))
    assert name == "2026-06-30 NetBenefits Quarterly Statement - Some Corp. Retirement Savings Plan.pdf"


def test_a_sign_out_on_a_later_plan_keeps_the_earlier_plans(monkeypatch, tmp_path):
    page = multi_page(**{P457: "login"})
    calls = []
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, calls))
    app = app_on(tmp_path, page)
    with pytest.raises(site.SessionExpired):
        app.cmd_discover(quiet=True)
    assert [r["account"] for r in app.discovery.data.values()] == ["North-Star Medical Group"]


def test_a_later_run_finds_the_plans_again_when_the_site_remembers_one(monkeypatch, tmp_path):
    page = multi_page()
    calls = []
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, calls))
    app_on(tmp_path, page).cmd_discover(quiet=True)
    page.choose = False                              # the site now shows the last plan opened
    site._plan_in_use["plan"] = None
    again = app_on(tmp_path, page)
    again.cmd_discover(quiet=True)
    assert len(again.discovery.data) == 3


def test_records_with_plans_and_no_list_stop_rather_than_file_one_plan(monkeypatch, tmp_path):
    page = multi_page()
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, []))
    app_on(tmp_path, page).cmd_discover(quiet=True)
    (tmp_path / "out" / "plans.json").unlink()
    page.choose = False
    app = app_on(tmp_path, page)
    with pytest.raises(SystemExit):
        app.cmd_discover(quiet=True)
    assert app.stats["plans_unlisted"] == 1


def test_each_statement_is_made_with_its_own_plan_open(monkeypatch, tmp_path):
    page = multi_page()
    calls = []
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, calls))
    monkeypatch.setattr(receipt_pdf, "print_html_to_pdf",
                        lambda pg, html, out: Path(out).write_bytes(sample_pdf()))
    app_on(tmp_path, page).cmd_discover(quiet=True)
    # A Resume, in a process that has listed nothing, on a site that now
    # remembers the last plan opened.
    page.choose = False
    site._plan_in_use["plan"] = None
    app = app_on(tmp_path, page)
    calls.clear()
    app.process(app._select())
    made = [(plan, r) for plan, r, html in calls if html]
    assert sorted(made) == sorted([(P457, "04/01/2026-06/30/2026"), (P457, "01/01/2026-03/31/2026"),
                                   (PXR, "04/01/2026-06/30/2026")])
    saved = sorted(p.name for p in (tmp_path / "out" / "Statements").iterdir())
    assert saved == ["2026-03-31 NetBenefits Quarterly Statement - ABC 457(b) Plan.pdf",
                     "2026-06-30 NetBenefits Quarterly Statement - ABC 457(b) Plan.pdf",
                     "2026-06-30 NetBenefits Quarterly Statement - North-Star Medical Group.pdf"]
    assert app.stats["failed"] == 0 and app.stats["statements"] == 3


def test_a_plan_missing_from_the_list_stops_the_run_and_fails_nothing(monkeypatch, tmp_path):
    page = multi_page()
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, []))
    app_on(tmp_path, page).cmd_discover(quiet=True)
    (tmp_path / "out" / "plans.json").unlink()
    page.choose = False
    app = app_on(tmp_path, page)
    app.process(app._select())
    assert app.stats["plans_unlisted"] == 1 and app.stats["failed"] == 0
    assert not app.progress.data
