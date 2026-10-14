"""A NetBenefits login with several employers and plans, offline.

The page is a spy that answers the way the surveyed site did. A "Choose a
Plan" page lists every plan as a plain link whatever plan the site is
remembering. The Statements address shows the remembered plan's
Statements page, or a "Select Account" page when no plan is remembered.
One plan's address goes to the Fidelity retail sign-in. Every address the
app loads, every look ahead and every statement it makes is written down,
with the plan the site had chosen at the time, so a test can say what was
never done as well as what was."""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  (binds this provider's AppSpec)
import netbenefits_site as site
import netbenefits_docs as docs
from paperpull_core import receipt_pdf
from paperpull_core.models import State
from paperpull_core.testkit import receipt_app, text_pdf

BARE = site.URLS["documents"]
CHOOSER = site.CHOOSER_URL
LINK = "/mybenefits/DefinedContributions/navigation/dc/OnlineStatement?plan=%s&"
OPTION = "/mybenefits/savings2/navigation/dc/OnlineStatement?&client=%s&plan=%s"
EMP_A, EMP_B = "900000001", "900000002"
P403, P457, PDCP, PXR = "41403", "41457", "41400", "41401"
IDS = (EMP_A, EMP_B, P403, P457, PDCP, PXR)
NAMES = {PXR: "NORTH-STAR MEDICAL GROUP", P403: "ABC 403(B) PLAN", PDCP: "ABC DCP", P457: "ABC 457(B) PLAN"}
CLIENTS = {PXR: EMP_B, P403: EMP_A, PDCP: EMP_A, P457: EMP_A}
NB_LOGIN = "https://nb.fidelity.com/static/mybenefits/netbenefitslogin/"


def plan_of(path):
    ids = site.plan_ids(path)
    return ids[1] if ids else None


class Locator:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    def count(self):
        if "client-name" in self.sel:
            return 1 if self.page.employer else 0
        if "password" in self.sel:
            return 1 if self.page.url.startswith(NB_LOGIN) else 0
        if "frmRequest" in self.sel:
            return 1 if self.page.kind in ("statements", "single") else 0
        return 1

    def inner_text(self, timeout=None):
        if "client-name" in self.sel:
            return self.page.employer
        return self.page.body()


class Page:
    """Plans by their number: 'ok' is chosen by the look ahead, 'link' only
    by loading its own address, 'retail' redirects to the retail sign-in
    (as one plan's did on the surveyed login), 'sticky' is never chosen and
    leaves the site on the plan chosen before, 'login' is chosen and then
    the site signs the session out, 'other' answers with another plan's
    page. `links` is what the Choose a Plan page lists (None, no such page),
    `remembered` the plan the site starts on (None, the Select Account
    page)."""

    def __init__(self, behaviors=None, links=None, remembered=None, single_heading=None):
        self.behaviors = dict(behaviors or {})
        self.links = links
        self.in_context = remembered
        self.single_heading = single_heading
        self.signed_in = True
        self.signs_out = False
        self.url, self.kind = BARE, "blank"
        self.gotos, self.probes, self.made = [], [], []
        self.names = dict(NAMES)
        self.bare_probes, self.bare_redirects = 0, False
        self.employer = ""

    def body(self):
        if self.kind == "chooser":
            return "Choose a Plan " + " ".join(t for t, _ in self.links or [])
        if self.kind == "statements":
            return "Home %s (%s) Statements Time period Get statement" % (
                self.names[self.in_context], self.in_context)
        if self.kind == "single":
            return "%s (12345) Statements Get statement" % self.single_heading
        if self.kind == "select":
            return "Select Account The page you've chosen requires you to select an account."
        return ""

    def title(self):
        return "Fidelity NetBenefits"

    def locator(self, sel):
        return Locator(self, sel)

    def wait_for_timeout(self, ms):
        pass

    def goto(self, url, **kw):
        self.gotos.append(url)
        if self.signs_out:
            self.url, self.kind = NB_LOGIN, "login"
            return
        self.url = url
        if url == CHOOSER:
            self.kind = "chooser" if self.links is not None else "single" if self.single_heading else "select"
            return
        if url == BARE:
            if self.in_context:
                self.kind = "statements"
            else:
                self.kind = "single" if self.single_heading else "select"
            return
        raise AssertionError("a plan's own address was loaded: " + url)

    def evaluate(self, js, arg=None):
        if js == site._READ_CHOOSER_JS:
            if self.kind != "chooser":
                return None
            return [{"text": t, "value": v, "disabled": False} for t, v in self.links]
        if js == site._READ_CHOICES_JS:
            if self.kind != "select":
                return None
            return [{"text": "Select an account", "value": "", "disabled": True}] + [
                {"text": NAMES[p], "value": OPTION % (CLIENTS[p], p), "disabled": False} for p in NAMES]
        if js == site._SIGNED_IN_JS:
            return self.signed_in
        if js == site._MAKE_JS:
            self.made.append(self.in_context)
            return {"status": 200, "isStatement": True, "title": "Statement Details"}
        if js == site._PROBE_JS:
            plan = plan_of(arg["path"])
            if plan is None:                       # the Statements address itself
                self.bare_probes += 1
                return {"kind": "page", "status": 200, "origin": True,
                        "redirected": self.bare_redirects, "password": False}
            self.probes.append(plan)
            how = self.behaviors.get(plan, "ok")
            if how == "retail" or not self.signed_in:
                return {"kind": "redirect"}
            if how == "other":                     # an answer from somewhere else on the site
                return {"kind": "page", "status": 200, "origin": True, "redirected": True,
                        "password": False}
            if how in ("ok", "login"):
                self.in_context = plan
            if how == "login":
                self.signs_out = True
            # As live: a plain 200 at the link itself whose body is only the
            # selector, never the Statements page, whether or not it chose.
            return {"kind": "page", "status": 200, "origin": True, "redirected": False,
                    "password": False}
        raise AssertionError("an unexpected script ran")


def chooser_links(*plans):
    return [(NAMES[p], LINK % p) for p in (plans or NAMES)]


def multi_page(remembered=P457, **behaviors):
    return Page(behaviors, links=chooser_links(), remembered=remembered)


# Which plans have which quarters. A statement belongs to the plan the
# site had chosen when it was made, as on the real site.
HAVE = {P457: {"04/01/2026-06/30/2026", "01/01/2026-03/31/2026"},
        PXR: {"04/01/2026-06/30/2026"}}
# What each plan's statement names, when that is not just its own plan.
STATEMENT_NAMES = {}


def render_text(pg, html, out):
    body = re.sub(r"<[^>]+>", "", html.split("<body>", 1)[-1])
    Path(out).write_bytes(text_pdf(body.split("|")))


PLAN_OF_LABEL = {"ABC 457(b) Plan": P457, "North-Star Medical Group": PXR}


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
    """make_statement for the site. Like the real one it asks the tab for
    the plan in use first, so a statement is never made with another plan
    chosen, and the answer comes from the plan the site has chosen."""
    def make(pg, date_range, want_html=False):
        plan = site._plan_in_use.get("plan")
        if plan and not site._shows_plan(pg, plan):
            reason = site.open_plan(pg, plan)
            if reason != site.OPENED:
                raise site.PlanDidNotOpen(reason)
        chosen = page.in_context if plan else None
        calls.append((chosen, date_range, want_html))
        have = single if chosen is None else HAVE.get(chosen, set())
        if date_range not in have:
            return {"status": 200, "title": "Online Statement error page", "isStatement": False, "html": ""}
        named = STATEMENT_NAMES.get(chosen, [NAMES.get(chosen, "SOME CORP. RETIREMENT SAVINGS PLAN")])
        return {"status": 200, "title": "Statement Details", "isStatement": True,
                # The statement's own header names whichever employer the
                # site remembers, which is never the label.
                "plan": "NORTH-STAR MEDICAL GROUP, INC. 401(K) PROFIT SHARING PLAN", "period": date_range,
                "html": "<html><head></head><body>%s|Statement Period %s</body></html>" % (
                    "|".join(named), date_range)}
    return make


def app_on(tmp_path, page):
    app = receipt_app(docs, tmp_path, owner="")
    app.page = lambda: page
    app._on_its_site = lambda: page
    return app


def no_ids_in(text):
    return not any(i in text for i in IDS)


def labels_by_date(app):
    return sorted((r["account"], r["date"]) for r in app.discovery.data.values())


def plans_of(page):
    return {plan_of(p["path"]): p for p in site.read_plan_choices(page)}


# -- the plan's name ----------------------------------------------------------------

def test_a_plan_name_reads_like_one():
    assert site.option_label("ABC 457(B) PLAN") == "ABC 457(b) Plan"
    assert site.option_label("NORTH-STAR MEDICAL GROUP") == "North-Star Medical Group"
    assert site.option_label("ABC DCP") == "ABC DCP"
    assert site.option_label("LAKESIDE COLLEGE OF ARTS 401(K) (12345)") == "Lakeside College of Arts 401(k)"
    assert site.option_label("ACME INC. 401(K) PLAN") == "Acme Inc. 401(k) Plan"
    assert site.option_label("Acme 401(k) Plan") == "Acme 401(k) Plan"
    assert site.option_label("  ") == ""


def test_a_plan_name_carrying_its_number_loses_it():
    plan = site.plan_from_option("ABC PLAN %s" % P457, LINK % P457)
    assert plan["label"] == "ABC Plan" and no_ids_in(plan["label"])
    glued = site.plan_from_option("Employer%sPlan" % P457, OPTION % (EMP_A, P457))
    assert no_ids_in(glued["label"])
    overlap = site.plan_from_option("Employer12345Plan", OPTION % ("123", "12345"))
    assert "123" not in overlap["label"] and "45" not in overlap["label"]


# -- the lists of plans -------------------------------------------------------------

def test_the_choose_a_plan_page_lists_every_plan_whatever_the_site_remembers():
    page = multi_page(remembered=P457)
    plans = site.read_plan_choices(page)
    assert [p["label"] for p in plans] == ["North-Star Medical Group", "ABC 403(b) Plan",
                                           "ABC DCP", "ABC 457(b) Plan"]
    assert all(re.fullmatch(r"[0-9a-f]{64}", p["digest"]) for p in plans)
    assert page.gotos == [CHOOSER, BARE]               # read, nothing pressed, no plan loaded
    assert page.probes == [] and page.made == []
    for p in plans:
        assert no_ids_in(p["label"]) and no_ids_in(p["digest"])


def test_a_choose_a_plan_page_with_one_plan_is_a_login_with_one_plan():
    page = Page(links=chooser_links(P457), single_heading="SOME CORP. RETIREMENT SAVINGS PLAN")
    assert site.read_plan_choices(page) is None


def test_with_no_choose_a_plan_page_the_select_account_page_is_read():
    page = Page(links=None, remembered=None)
    plans = site.read_plan_choices(page)
    assert {p["label"] for p in plans} == {"North-Star Medical Group", "ABC 403(b) Plan",
                                           "ABC DCP", "ABC 457(b) Plan"}


def test_a_plan_keeps_one_key_whichever_list_it_came_from():
    by_link = site.plan_from_option(NAMES[P457], LINK % P457)
    by_option = site.plan_from_option(NAMES[P457], OPTION % (EMP_A, P457))
    assert by_link["digest"] == by_option["digest"]


def test_the_plan_ids_are_private_words():
    site.read_plan_choices(Page(links=None, remembered=None))
    said = site.redact("client %s plan %s and %s" % (EMP_A, P457, PXR))
    assert no_ids_in(said)


def test_a_link_off_the_statements_page_is_never_opened():
    plan = site.plan_from_option("RETAIL PLAN", "https://digital.fidelity.com/x?plan=2")
    assert plan["path"] == "" and plan["review"] == site.LEAVES_THE_SITE
    other = site.plan_from_option("OTHER", "/mybenefits/moneymovement/navigation/dc/loanwith?plan=2")
    assert other["path"] == ""
    page = multi_page()
    assert site.open_plan(page, plan) == site.LEAVES_THE_SITE
    assert page.gotos == [] and page.probes == []
    assert site.plan_from_option("Select an account", "") is None


def test_a_plan_list_read_back_must_still_be_what_this_app_wrote():
    plan = site.plan_from_option(NAMES[P457], LINK % P457)
    assert site.checked_plan(dict(plan)) == dict({k: plan[k] for k in ("label", "digest", "path", "heading")},
                                                 shared_name=False)
    assert site.checked_plan(dict(plan, digest="0" * 64)) is None
    assert site.checked_plan(dict(plan, path="https://digital.fidelity.com" + plan["path"])) is None


# -- choosing a plan ----------------------------------------------------------------

def test_a_plan_that_redirects_off_the_host_is_left_for_the_owner_and_never_loaded():
    page = multi_page(**{P403: "retail"})
    plans = plans_of(page)
    assert site.open_plan(page, plans[P403]) == site.REDIRECTS
    assert page.probes == [P403]
    assert not any(plan_of(u[len(site.WPS):]) == P403 for u in page.gotos if u.startswith(site.WPS))
    assert site._plan_in_use["plan"] is None


def test_a_plan_opens_by_its_look_ahead_and_the_statements_page_must_name_it():
    page = multi_page()
    plans = plans_of(page)
    page.gotos.clear()
    assert site.open_plan(page, plans[PXR]) == site.OPENED
    assert page.gotos == [BARE] and page.in_context == PXR
    assert site._plan_in_use["plan"]["digest"] == plans[PXR]["digest"]


def test_a_plan_the_look_ahead_does_not_choose_is_left_and_its_link_never_loaded():
    page = multi_page(**{PDCP: "link"})
    plans = plans_of(page)
    page.gotos.clear()
    assert site.open_plan(page, plans[PDCP]) == site.DID_NOT_OPEN
    assert page.gotos == [BARE] and page.in_context == P457


def test_a_heading_that_names_another_plan_refuses_the_plan():
    # The site stays on the remembered plan whatever is asked of it.
    page = multi_page(remembered=P457, **{PXR: "sticky"})
    plans = plans_of(page)
    assert site.open_plan(page, plans[PXR]) == site.DID_NOT_OPEN
    assert site._plan_in_use["plan"] is None


def test_two_plans_with_one_name_are_told_apart_by_their_number():
    page = Page({P457: "sticky"}, links=[("ABC 401(K) PLAN", LINK % P403), ("ABC 401(K) PLAN", LINK % P457)],
                remembered=P403)
    page.names.update({P403: "ABC 401(K) PLAN", P457: "ABC 401(K) PLAN"})
    plans = plans_of(page)
    assert all(p["shared_name"] for p in plans.values())
    assert site.open_plan(page, plans[P403]) == site.OPENED
    # The other plan's page is never shown, this one's is, under the same name.
    assert site.open_plan(page, plans[P457]) == site.DID_NOT_OPEN


def test_a_link_that_already_shows_its_number_still_opens():
    page = multi_page()
    plan = site.plan_from_option("ABC 457(B) PLAN (%s)" % P457, LINK % P457)
    plan["shared_name"] = True
    page.kind = "statements"
    assert site.open_plan(page, plan) == site.OPENED


def test_a_plan_address_answered_from_elsewhere_on_the_site_is_left():
    page = multi_page(**{PDCP: "other"})
    plans = plans_of(page)
    page.gotos.clear()
    assert site.open_plan(page, plans[PDCP]) == site.REDIRECTS
    assert page.gotos == []


def test_the_selector_answer_is_not_the_statements_page_and_the_heading_decides():
    # The live shape: the look ahead answers 200 with the selector page and
    # chooses the plan. The plan opens because the Statements page names it.
    page = multi_page(remembered=P457)
    plans = plans_of(page)
    assert site.open_plan(page, plans[P403]) == site.OPENED
    assert page.in_context == P403
    # One that answers the same but is not chosen is refused by the heading.
    page.behaviors[PDCP] = "sticky"
    assert site.open_plan(page, plans[PDCP]) == site.DID_NOT_OPEN


def test_a_sign_in_page_on_a_later_plan_is_session_expired():
    page = multi_page(**{P457: "login"})
    plans = plans_of(page)
    assert site.open_plan(page, plans[PXR]) == site.OPENED
    with pytest.raises(site.SessionExpired):
        site.open_plan(page, plans[P457])


def test_a_redirect_from_a_signed_out_session_is_session_expired_not_a_plan_to_review():
    page = multi_page()
    plans = plans_of(page)
    page.signed_in = False
    with pytest.raises(site.SessionExpired):
        site.open_plan(page, plans[PXR])


def test_the_keep_awake_reload_chooses_the_plan_in_use_again():
    page = multi_page()
    plans = plans_of(page)
    site.open_plan(page, plans[PDCP])
    page.probes.clear()
    site._last_navigation["at"] -= site.KEEP_AWAKE_SECONDS + 1
    site._keep_awake(page)
    assert page.probes == [PDCP] and page.in_context == PDCP


def test_a_statement_is_never_made_while_the_tab_shows_another_plan():
    page = multi_page()
    plans = plans_of(page)
    site.open_plan(page, plans[PDCP])
    page.in_context = PXR                         # chosen elsewhere, the tab reloaded
    page.probes.clear()
    site._last_navigation["at"] = time.monotonic()
    site.make_statement(page, "04/01/2026-06/30/2026")
    assert page.probes == [PDCP] and page.made == [PDCP]


def test_a_plan_that_will_not_open_again_is_its_own_failure_not_a_statement(monkeypatch, tmp_path):
    page = multi_page()
    plans = plans_of(page)
    site.open_plan(page, plans[PDCP])
    page.behaviors[PDCP] = "retail"
    site._last_navigation["at"] -= site.KEEP_AWAKE_SECONDS + 1
    with pytest.raises(site.PlanDidNotOpen) as e:
        site._keep_awake(page)
    assert e.value.reason == site.REDIRECTS
    monkeypatch.setattr(site, "make_statement", lambda *a, **k: (_ for _ in ()).throw(site.PlanDidNotOpen("x")))
    site._plan_in_use["plan"] = plans[PDCP]
    assert not site.download_document(page, "Quarterly Statement", "2026-06-30", tmp_path / "s.pdf",
                                      item_hint="04/01/2026-06/30/2026", plan=plans[PDCP])


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

def test_a_remembered_plan_no_longer_names_every_statement(monkeypatch, tmp_path, caplog):
    """The live run that found this: the site remembered one plan, the app
    took the login for one with a single plan, and every statement was
    named after the employer in the statement's own header."""
    caplog.set_level("INFO")
    page = multi_page(remembered=P457, **{P403: "retail"})
    calls = []
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, calls))
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    assert labels_by_date(app) == [("ABC 457(b) Plan", "2026-03-31"), ("ABC 457(b) Plan", "2026-06-30"),
                                   ("North-Star Medical Group", "2026-06-30")]
    recs = list(app.discovery.data.values())
    assert all(r["plan"] for r in recs) and all(r["occurrence"] == 0 for r in recs)
    for rec in recs:                              # made with the plan it is filed under chosen
        assert (PLAN_OF_LABEL[rec["account"]], rec["item_id"], False) in calls
    assert app.stats["manual_review"] == 1        # ABC 403(b), left for the owner
    names = [docs.build_pdf_filename(r["date"], r["summary"], "", record=docs.Document.from_dict(r))
             for r in recs]
    assert "2026-06-30 NetBenefits Quarterly Statement - ABC 457(b) Plan.pdf" in names
    assert "2026-06-30 NetBenefits Quarterly Statement - North-Star Medical Group.pdf" in names
    for text in names + list(app.discovery.data) + [app.paths.discovery_json.read_text(), caplog.text]:
        assert no_ids_in(text)
    held = json.loads((app.paths.root / "plans.json").read_text())
    assert len(held) == 4 and no_ids_in(json.dumps([h["label"] for h in held.values()]))


def test_a_plan_whose_heading_does_not_match_gets_no_statement(monkeypatch, tmp_path):
    page = multi_page(remembered=P457, **{PXR: "sticky"})
    calls = []
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, calls))
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    assert "North-Star Medical Group" not in {r["account"] for r in app.discovery.data.values()}
    assert app.stats["manual_review"] == 1


def test_a_login_with_one_plan_is_listed_as_before(monkeypatch, tmp_path):
    page = Page(links=chooser_links(P457), single_heading="SOME CORP. RETIREMENT SAVINGS PLAN")
    calls = []
    monkeypatch.setattr(site, "make_statement",
                        statements_by_plan(page, calls, single={"04/01/2026-06/30/2026"}))
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    assert list(app.discovery.data) == ["Statement:2026-06-30:Quarterly Statement"]
    assert app.discovery.data["Statement:2026-06-30:Quarterly Statement"]["plan"] == ""
    assert page.probes == []
    assert not (app.paths.root / "plans.json").exists() or json.loads(
        (app.paths.root / "plans.json").read_text()) == {}


def test_a_plan_left_for_review_is_written_down_before_a_later_sign_out(monkeypatch, tmp_path):
    page = multi_page(**{P403: "retail", P457: "login"})
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, []))
    app = app_on(tmp_path, page)
    with pytest.raises(site.SessionExpired):
        app.cmd_discover(quiet=True)
    held = json.loads((tmp_path / "out" / "plans.json").read_text())
    assert [h.get("last_result") for h in held.values() if h["label"] == "ABC 403(b) Plan"] == [site.REDIRECTS]
    assert [r["account"] for r in app.discovery.data.values()] == ["North-Star Medical Group"]


def test_a_plan_that_stops_opening_partway_is_left_and_the_next_plan_still_listed(monkeypatch, tmp_path):
    page = multi_page()
    good = statements_by_plan(page, [])

    def make(pg, date_range, want_html=False):
        if page.in_context == PXR:
            raise site.PlanDidNotOpen(site.REDIRECTS)
        return good(pg, date_range, want_html)
    monkeypatch.setattr(site, "make_statement", make)
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    assert {r["account"] for r in app.discovery.data.values()} == {"ABC 457(b) Plan"}
    assert app.stats["manual_review"] == 1


def test_a_later_run_without_a_list_uses_the_plans_it_kept(monkeypatch, tmp_path):
    page = multi_page()
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, []))
    app_on(tmp_path, page).cmd_discover(quiet=True)
    page.links = None                                # no Choose a Plan page this time
    site._plan_in_use["plan"] = None
    again = app_on(tmp_path, page)
    again.cmd_discover(quiet=True)
    assert len(again.discovery.data) == 3


def test_records_with_plans_and_no_list_stop_rather_than_file_one_plan(monkeypatch, tmp_path):
    page = multi_page()
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, []))
    app_on(tmp_path, page).cmd_discover(quiet=True)
    (tmp_path / "out" / "plans.json").unlink()
    page.links = None
    app = app_on(tmp_path, page)
    with pytest.raises(SystemExit):
        app.cmd_discover(quiet=True)
    assert app.stats["plans_unlisted"] == 1


def test_each_statement_is_made_with_its_own_plan_chosen(monkeypatch, tmp_path):
    page = multi_page()
    calls = []
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, calls))
    monkeypatch.setattr(receipt_pdf, "print_html_to_pdf", render_text)
    app_on(tmp_path, page).cmd_discover(quiet=True)
    # A Resume, in a process that has listed nothing.
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
    page.links = None
    app = app_on(tmp_path, page)
    app.process(app._select())
    assert app.stats["plans_unlisted"] == 1 and app.stats["failed"] == 0
    assert not app.progress.data


def test_a_statements_address_that_redirects_is_not_loaded():
    page = multi_page()
    plans = plans_of(page)
    page.gotos.clear()
    page.bare_redirects = True
    assert site.open_plan(page, plans[PXR]) == site.REDIRECTS
    assert page.bare_probes == 1 and page.gotos == []


def test_the_look_ahead_compares_addresses_without_the_pages_url_constructor():
    """The page shadows the global URL, so new URL() there throws, and the
    script once read every plan as redirected because of it."""
    assert "URL(" not in site._PROBE_JS
    assert "here.origin + path" in site._PROBE_JS



# -- a statement that covers several plans ------------------------------------------

SHARED = {P403, PDCP, P457}
Q2 = "04/01/2026-06/30/2026"


def three_plans_one_statement(monkeypatch, tmp_path, employer=""):
    monkeypatch.setitem(HAVE, P403, {Q2})
    monkeypatch.setitem(HAVE, PDCP, {Q2})
    monkeypatch.setitem(HAVE, P457, {Q2})
    combined = ["ABC 403(B) PLAN", "ABC DCP", "ABC 457(B) PLAN", "LAKESIDE COLLEGE OF ARTS"]
    for p in SHARED:
        monkeypatch.setitem(STATEMENT_NAMES, p, combined)
    page = multi_page()
    page.employer = employer
    calls = []
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, calls))
    monkeypatch.setattr(receipt_pdf, "print_html_to_pdf", render_text)
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    return app, page, calls


def test_a_combined_statement_is_saved_once_for_the_plans_it_covers(monkeypatch, tmp_path):
    app, page, calls = three_plans_one_statement(monkeypatch, tmp_path)
    q2 = [app.discovery.data[k] for k in app.discovery.data if app.discovery.data[k]["date"] == "2026-06-30"]
    assert len(q2) == 4                                   # three ABC plans and North-Star
    calls.clear()
    app.process(app._select())
    saved = sorted(p.name for p in (tmp_path / "out" / "Statements").iterdir())
    assert saved == ["2026-06-30 NetBenefits Quarterly Statement - ABC 403(b), ABC 457(b) and ABC DCP Plans.pdf",
                     "2026-06-30 NetBenefits Quarterly Statement - North-Star Medical Group.pdf"]
    # Made once for the three, once for North-Star.
    assert len([c for c in calls if c[2]]) == 2
    done = [app.progress.data[k] for k in app.progress.data]
    assert len(done) == 4 and all(r["downloaded_ok"] for r in done)
    covered = [r for r in done if "covered by the combined statement" in (r.get("notes") or "")]
    assert len(covered) == 2 and all(saved[0] in r["notes"] and not r["pdf_path"] for r in covered)
    assert app.stats["manual_review"] == 0 and app.stats["failed"] == 0
    # A later run makes nothing again.
    calls.clear()
    app.process(app._select())
    assert not [c for c in calls if c[2]]


def test_a_combined_statement_takes_the_employers_name_when_the_statement_carries_it(monkeypatch, tmp_path):
    app, page, calls = three_plans_one_statement(monkeypatch, tmp_path, employer="LAKESIDE COLLEGE OF ARTS")
    app.process(app._select())
    saved = sorted(p.name for p in (tmp_path / "out" / "Statements").iterdir())
    assert "2026-06-30 NetBenefits Quarterly Statement - Lakeside College of Arts Plans.pdf" in saved


def test_a_remembered_employer_the_statement_does_not_carry_is_not_believed(monkeypatch, tmp_path):
    app, page, calls = three_plans_one_statement(monkeypatch, tmp_path, employer="NORTH-STAR MEDICAL GROUP, INC.")
    app.process(app._select())
    saved = sorted(p.name for p in (tmp_path / "out" / "Statements").iterdir())
    assert "2026-06-30 NetBenefits Quarterly Statement - ABC 403(b), ABC 457(b) and ABC DCP Plans.pdf" in saved


def test_a_statement_for_one_plan_is_filed_under_that_plan_as_before(monkeypatch, tmp_path):
    page = multi_page()
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, []))
    monkeypatch.setattr(receipt_pdf, "print_html_to_pdf", render_text)
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    app.process(app._select())
    rec = next(r for r in app.progress.data.values() if r["account"] == "North-Star Medical Group")
    assert rec["pdf_filename"] == "2026-06-30 NetBenefits Quarterly Statement - North-Star Medical Group.pdf"
    assert not rec.get("covers")


def test_a_statement_naming_only_another_plan_is_refused(monkeypatch, tmp_path):
    monkeypatch.setitem(STATEMENT_NAMES, P457, ["NORTH-STAR MEDICAL GROUP"])
    page = multi_page()
    monkeypatch.setattr(site, "make_statement", statements_by_plan(page, []))
    monkeypatch.setattr(receipt_pdf, "print_html_to_pdf", render_text)
    app = app_on(tmp_path, page)
    app.cmd_discover(quiet=True)
    app.process(app._select())
    saved = sorted(p.name for p in (tmp_path / "out" / "Statements").iterdir())
    assert not any("ABC 457(b)" in n for n in saved)
    refused = [r for r in app.progress.data.values() if r["account"] == "ABC 457(b) Plan"]
    assert refused and all(r["state"] == State.NEEDS_MANUAL_REVIEW.value for r in refused)
    assert app.stats["wrong_document"] == 2


def test_plan_names_are_found_however_the_pdf_spaced_them():
    plans = [site.plan_from_option(NAMES[p], LINK % p) for p in (P403, PDCP, P457, PXR)]
    named = site.plans_named("Your plans: ABC403(B)  and abc dcp\nABC 457(B) Plan", plans)
    assert [p["label"] for p in named] == ["ABC 403(b) Plan", "ABC DCP", "ABC 457(b) Plan"]


def test_a_later_process_finds_the_combined_statement_and_makes_nothing(monkeypatch, tmp_path):
    app, page, calls = three_plans_one_statement(monkeypatch, tmp_path)
    app.process(app._select())
    # The covered plans' records are gone, as for plans listed after the
    # statement was saved, and a new process is started.
    for key in [k for k, r in app.progress.data.items() if "covered by" in (r.get("notes") or "")]:
        del app.progress.data[key]
    app.progress.save()
    site._plan_in_use["plan"] = None
    again = app_on(tmp_path, page)
    calls.clear()
    again.process(again._select())
    assert not [c for c in calls if c[2]]
    assert sum("covered by" in (r.get("notes") or "") for r in again.progress.data.values()) == 2


def test_download_again_makes_the_combined_statement_again(monkeypatch, tmp_path):
    app, page, calls = three_plans_one_statement(monkeypatch, tmp_path)
    app.process(app._select())
    site._plan_in_use["plan"] = None
    again = app_on(tmp_path, page)
    again.args.redownload = True
    calls.clear()
    again.process(again._select())
    assert len([c for c in calls if c[2]]) == 2          # once for the three, once for North-Star


def test_two_plans_with_one_name_never_cover_each_other():
    twins = [site.plan_from_option("ABC 401(K) PLAN", LINK % P403),
             site.plan_from_option("ABC 401(K) PLAN", LINK % P457),
             site.plan_from_option("ABC DCP", LINK % PDCP)]
    named = site.plans_named("ABC 401(K) PLAN statement", twins, chosen=twins[0]["digest"])
    assert [p["digest"] for p in named] == [twins[0]["digest"]]
