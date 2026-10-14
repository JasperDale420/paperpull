"""ALL Fidelity NetBenefits selectors, URLs, and page behavior live here.

When NetBenefits changes, repair this file only.

STATUS, mapped 2026-09-18 against a signed-in session.

  NetBenefits is Fidelity's workplace-plan site (401(k) and the like) at
  workplaceservices.fidelity.com, signed in through nb.fidelity.com. It
  keeps no archive of issued statements. Its Statements page is a
  generator, pick a month, a quarter, year to date or a custom range and
  it builds the statement on the spot, as a web page with a "Download or
  Print This Statement" button that is window.print(). So there is no list
  to read. This app makes the statement for each completed period itself
  and renders the page to PDF, the way the print button would.

  THE CALLS, from inside the signed-in page, cookie session:
    * token   GET  /mybenefits/employerservices/api/txntoken
                   -> {txnTokenValue, success}. Fetched and used inside one
                   page.evaluate, so it never enters this process.
    * make    POST /mybenefits/savings2/sod/soddetail, form-encoded
                   txntoken, sodReqIndicator=HACK, dateRange=MM/DD/YYYY-MM/DD/YYYY,
                   ytdDateRange, sodPreview=N, consentReq=N
                   -> the statement page (title "... Statement Details"),
                      or an error page (title "... Online Statement error
                      page") for a period before the account existed.
  The statement HTML is rendered to PDF in a temporary tab of the same
  browser with <base href> on workplaceservices.fidelity.com so its
  stylesheet and logo load. Nothing on the page is clicked.

  PERIODS. Quarterly by default, monthly with statement_period in the
  config. Only completed periods, newest first, back to the first year the
  site's own picker offers (2016). Discovery makes each statement to learn
  whether the period exists, and stops walking older periods at the first
  error page that follows a real statement, since a plan has one start.
  A scoped run makes only the years it wants.

  IDENTITY is period kind + period end date + plan name. The page's own
  plan number is not part of any filename.

  SEVERAL PLANS, surveyed read-only 2026-10-14 on a login with two
  employers and four plans, and read live once since. The site remembers
  the plan last chosen, and the Statements address then shows that plan's
  Statements page at once, so that address cannot tell a login with one
  plan from one with several. The "Choose a Plan" page,
  /mybenefits/definedcontributions/navigation/dc/OnlineStatement?planChooser=yes,
  lists every plan whatever is remembered, as plain links whose text is
  the plan's name and whose address is that plan's Statements address
  (OnlineStatement?plan=<digits>&). It is read first, and a login it shows
  one plan for goes on exactly as a login with one plan always did. With
  no such page, the Statements address shows a "Select Account" page when
  no plan is remembered, a select named accountSelect whose options carry
  the same addresses with the client too, and that is read instead. Its Go
  button is never pressed. The plan's name comes from that list and from
  nowhere else. The statement's own header names whichever employer the
  site remembers, so it is never used as the plan's name.

  A plan is chosen by asking for its address from inside the page, a
  same-origin request that the browser refuses to follow to any other
  host. If the Statements page then shows that plan, by its form and its
  heading, the plan's name and its number in brackets, the plan is open,
  and if not it is left for the owner. The tab never loads a plan's own
  address, since a redirect hop cannot be refused there. A statement is made only while the tab shows the plan it is filed
  under. One plan on the surveyed login sent its address to the Fidelity
  retail sign-in on digital.fidelity.com, so a plan whose address leaves
  this host is left for the owner and never followed. The employer chooser
  and its Continue, which change a saved preference, are never used.

SAFETY (this is a retirement account):
  Strictly READ-ONLY. NetBenefits can change contributions, move money
  between investments, take a loan or a withdrawal, and change
  beneficiaries. This app must NEVER activate a control that does any of
  those, never submits a form other than the statement request above,
  never confirms a dialog, and never accepts terms on the user's behalf.
  An expired session raises SessionExpired so a run stops loudly rather
  than reporting an empty success.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import date as _date
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

from paperpull_core.controls import SETTINGS_CONTROL_RE, AUTH_CONTROL_RE
from paperpull_core.controls import escape_for_locator

# Everything on its way into a diagnostic file goes through here. It
# lives in core because seventeen apps each had their own copy and
# they drifted into three different versions.
from paperpull_core.redact import redact, set_private_words  # noqa: F401
from paperpull_core.redact import private_words
from paperpull_core.urls import is_safe_url as _host_allows
from paperpull_core.api_census import shape_of as _shape
from paperpull_core.dates import checked as _checked_date

log = logging.getLogger("netbenefits_docs.site")

# Every host this app will read from. Anything else is refused.
ALLOWED_HOSTS = ("fidelity.com",)

WPS = "https://workplaceservices.fidelity.com"
URLS = {
    "home": "https://nb.fidelity.com/public/nb/default/home",
    "login": "https://nb.fidelity.com/public/nb/default/home",
    "documents": f"{WPS}/mybenefits/savings2/navigation/dc/OnlineStatement",
}
TOKEN_PATH = "/mybenefits/employerservices/api/txntoken"
STATEMENT_PATH = "/mybenefits/savings2/sod/soddetail"
FIRST_YEAR = 2016        # the oldest year the Statements page's picker offers

LOGIN_URL_MARKERS = ["/login", "/logon", "/signin", "/sign-in", "/mfa",
                     "/verify", "/onboarding"]


class SessionExpired(RuntimeError):
    """Fidelity answered with a sign-in page instead of the thing asked for."""


def is_safe_url(url: str) -> bool:
    """True only for an https URL on one of this provider's own hosts.

    The check itself lives in the core, so all of them answer the same way.
    This app keeps the hosts, which is the part that really is its own."""
    return _host_allows(url, ALLOWED_HOSTS)


# ---------------------------------------------------------------------------
# HARD SAFETY GUARD. Tuned for a workplace plan. Never change contributions
# or investments, never borrow or withdraw, never change who gets the money.
# ---------------------------------------------------------------------------
FORBIDDEN_CONTROL_RE = re.compile(
    r"(contribut|deferral|\bdeduction|manage\s+investments?|\bexchange\b|rebalance|"
    r"reallocat|allocation\b|\bloan\b|borrow|repay|withdraw|distribution|\brmd\b|"
    r"required\s+minimum|rollover|roll\s+over|transfer\b|\bwire\b|move\s+money|"
    r"\bpay\b|payment|beneficiar|enroll|\bconvert|"
    r"direct\s+deposit|bank\s+account|financial\s+institution|routing|"
    # Word boundaries on both sides of the verb stems. "edit" inside
    # "Credit" and "chang" inside "Exchange" are the two that bit before.
    r"\bchang(e|es|ed|ing)\b|\bedit(s|ed|ing)?\b|\bupdat(e|es|ed|ing)\b|"
    r"\bset\s+up\b|enable|disable|\bdelet|\bremov(e|es|ed|ing|al)\b|"
    r"\bsubmit|\bconfirm\b|\bagree\b|\baccept\b|authoriz|\bapply\b|"
    r"request\b|\bstart\b|\bbegin\b|initiate|"
    r"log\s*out|logout|sign\s*out|password|username|"
    r"security\s+question|two-?step|contact\s+info|\baddress\b|\bemail\b|\bphone\b|"
    r"go\s+paperless|delivery\s+preferences|e-?delivery)",
    re.I)

# What a document control may look like. A control must match this AND not
# match the blocklist.
SAFE_DOC_CONTROL_RE = re.compile(
    r"(download|\bview\b|\bopen\b|\bprint\b|\bpdf\b|statement|document|"
    r"1099|5498|tax\s+form|tax\s+statement|tax\s+document|confirmation|"
    r"letter|notice|correspondence|history|annual|quarterly|monthly)", re.I)


def is_safe_control(name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    if (FORBIDDEN_CONTROL_RE.search(name) or SETTINGS_CONTROL_RE.search(name)
            or AUTH_CONTROL_RE.search(name)):
        return False
    return bool(SAFE_DOC_CONTROL_RE.search(name))


# Links diagnose may follow while surveying. Narrower than the guard on
# purpose, since these are followed without a person choosing each one.
SURVEY_LINK_RE = re.compile(
    r"^(documents?|statements?|my\s+statements?|tax\s+(forms?|statements?|documents?)|"
    r"messages?|message\s+cent(er|re)|mailbox|inbox|correspondence|"
    r"document\s+history|account\s+history|history)$", re.I)


SECURITY_CHALLENGE_MARKERS = [
    "one-time passcode", "one time passcode", "enter the code", "verification code",
    "we sent a code", "text message", "authenticator", "confirm your identity",
    "verify your identity", "are you a robot", "captcha", "access denied",
    "your session has expired", "session timed out", "signed out",
]

RATE_LIMIT_MARKERS = ["too many requests", "rate limit", "try again later",
                      "temporarily unavailable", "unusual traffic"]


def looks_signed_out(page) -> bool:
    try:
        url = (page.url or "").lower()
    except Exception:
        # A page that cannot say where it is is not a signed-in page. This
        # answered False, which reads as "carry on", and it is the answer
        # safe_selects asks before deciding whether any control on the page
        # may be touched at all. The other forty-three apps let this raise,
        # which that treats as a refusal, so False was the odd answer and
        # the only unsafe one.
        return True
    if any(m in url for m in LOGIN_URL_MARKERS):
        return True
    try:
        if page.locator("input[type='password']").count() > 0:
            return True
    except Exception:
        pass
    return False


def detect_security_challenge(page) -> Optional[str]:
    """Names the passcode, CAPTCHA or throttling prompt on screen, or None.
    Visible text only. A page's source can carry every string its scripts
    could ever show, "verification code" included, on a normal day."""
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        body = ""
    hay = title + "\n" + body[:2000]
    for m in SECURITY_CHALLENGE_MARKERS:
        if m in hay:
            return f"Security challenge detected: '{m}'"
    for m in RATE_LIMIT_MARKERS:
        if m in hay:
            return f"Possible rate limiting detected: '{m}'"
    return None


def on_documents_page(page) -> bool:
    """Any signed-in workplaceservices page. The statement calls are
    relative to that host and work from all of them."""
    try:
        url = page.url or ""
        return (is_safe_url(url) and urlsplit(url).hostname == "workplaceservices.fidelity.com"
                and not looks_signed_out(page))
    except Exception:
        return False


def goto_documents(page) -> bool:
    if on_documents_page(page):
        return True
    if URLS["documents"] and is_safe_url(URLS["documents"]):
        try:
            page.goto(URLS["documents"], wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.info("documents URL failed: %s", e)
    return on_documents_page(page)


def ensure_statements(page) -> bool:
    return goto_documents(page)


# -- statements, made to order --------------------------------------------------------

def _parse_date_from_page(text: str) -> str:
    """'02/09/2026' or '2026-02-09' -> '2026-02-09', else ''."""
    s = (text or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}(?!\d)", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(1)), int(m.group(2)))
    return ""


def parse_date(text):
    """The date this provider's page is showing, as YYYY-MM-DD.

    The reading is below, unchanged. This only refuses to believe a result
    that names a day which does not exist, because a reference number is
    shaped like a date and used to be taken for one."""
    return _checked_date(_parse_date_from_page(text), "")


def _month_end(y: int, m: int) -> _date:
    return _date(y + (m == 12), (m % 12) + 1, 1) - __import__("datetime").timedelta(days=1)


def periods(kind: str, today: Optional[_date] = None, first_year: int = FIRST_YEAR) -> List[dict]:
    """Completed periods, newest first. kind is 'quarterly' or 'monthly'.
    Each has a label, a title, an ISO end date and the site's dateRange."""
    today = today or _date.today()
    out = []
    if kind == "monthly":
        y, m = today.year, today.month
        while y >= first_year:
            end = _month_end(y, m)
            if end < today:
                start = _date(y, m, 1)
                out.append({"title": "Monthly Statement", "label": end.strftime("%B %Y"),
                            "date": end.isoformat(), "start": start.isoformat(),
                            "range": "%s-%s" % (start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y"))})
            m -= 1
            if m == 0:
                y, m = y - 1, 12
    else:
        y, q = today.year, (today.month - 1) // 3 + 1
        while y >= first_year:
            start = _date(y, 3 * (q - 1) + 1, 1)
            end = _month_end(y, 3 * q)
            if end < today:
                out.append({"title": "Quarterly Statement", "label": "Q%d %d" % (q, y),
                            "date": end.isoformat(), "start": start.isoformat(),
                            "range": "%s-%s" % (start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y"))})
            q -= 1
            if q == 0:
                y, q = y - 1, 4
    return out


# Runs inside the page. Gets the transaction token and makes the statement
# in one go, so the token never leaves the browser. Returns the page's
# title, whether it is a statement, the plan heading, and the HTML.
_MAKE_JS = """async ({tokenPath, statementPath, dateRange, want}) => {
  // location itself, not new URL(location.href): the page shadows the
  // global URL with something whose hostname is null.
  const here = location;
  if (here.protocol !== 'https:' || here.hostname !== 'workplaceservices.fidelity.com') {
    throw new Error('Refusing to make a statement from an off-host page');
  }
  // A redirect is refused, and a refused redirect here is the site
  // sending a signed-out session to its sign-in page.
  let tr;
  try {
    tr = await fetch(tokenPath, {credentials: 'include', headers: {'Accept': 'application/json'}, redirect: 'error'});
  } catch (e) { return {status: 0, stage: 'token', signedOut: true}; }
  if (tr.status !== 200) return {status: tr.status, stage: 'token'};
  const tj = await tr.json();
  const body = new URLSearchParams({txntoken: tj.txnTokenValue || '', sodReqIndicator: 'HACK',
                                    dateRange: dateRange, ytdDateRange: '', sodPreview: 'N', consentReq: 'N'});
  let r;
  try {
    r = await fetch(statementPath, {method: 'POST', credentials: 'include', redirect: 'error',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: body.toString()});
  } catch (e) { return {status: 0, stage: 'statement', signedOut: true}; }
  const html = await r.text();
  const title = (html.match(/<title>([^<]*)<\\/title>/i) || [, ''])[1].trim();
  const period = (html.match(/Statement Period:\\s*([0-9\\/]+\\s+to\\s+[0-9\\/]+)/) || [, ''])[1];
  // The plan's name is the first Header cell of the statement itself
  // ("Some Corp. Retirement Savings<BR>Plan"), or failing that the h1
  // that names the plan. Never the page's "Statement Details" heading.
  let plan = '';
  const cell = html.match(/<td[^>]*class="Header"[^>]*>([\\s\\S]{3,200}?)<\\/td>/i);
  if (cell) plan = cell[1].replace(/<[^>]+>/g, ' ');
  if (!plan.trim()) {
    for (const m of html.matchAll(/<h1[^>]*>([^<]{3,120})<\\/h1>/gi)) {
      if (/plan/i.test(m[1])) { plan = m[1]; break; }
    }
  }
  plan = plan.replace(/\\s+/g, ' ').trim();
  const isStatement = /Statement Details/i.test(title) && !!period;
  return {status: r.status, stage: 'statement', title: title, period: period, plan: plan,
          isStatement: isStatement, html: want === 'html' ? html : ''};
}"""


# NetBenefits times an idle session out on page activity, not on requests.
# A run that only fetches gets the "Are you still there?" dialog after a
# few minutes and then a sign-out, mid-run, with the page navigating out
# from under the call. Reloading the Statements page now and then is what
# a person scrolling would do for it, and it costs a second.
KEEP_AWAKE_SECONDS = 60
_last_navigation = {"at": 0.0}


def _keep_awake(page) -> None:
    now = time.monotonic()
    if now - _last_navigation["at"] < KEEP_AWAKE_SECONDS:
        return
    plan = _plan_in_use.get("plan")
    if plan:
        # On a login with several plans the plan is chosen again and the
        # reload must show it, so the plan the next statement is made for
        # stays the one this run opened. A plan that no longer comes back
        # is PlanDidNotOpen, never a statement filed under the wrong
        # plan's name.
        _last_navigation["at"] = now
        reason = open_plan(page, plan)
        if reason != OPENED:
            raise PlanDidNotOpen(reason)
        return
    try:
        page.goto(URLS["documents"], wait_until="domcontentloaded", timeout=30000)
        # Right after sign-in the Statements page arrives through a hop on
        # nb.fidelity.com. Wait for the page to settle on its own host
        # before anything runs inside it.
        for _ in range(20):
            if on_documents_page(page):
                break
            page.wait_for_timeout(750)
        page.wait_for_timeout(1000)
    except Exception as e:
        log.info("keep-awake reload failed: %s", e)
    _last_navigation["at"] = now


def make_statement(page, date_range: str, want_html: bool = False) -> dict:
    """The site's answer for one period. isStatement says whether it is a
    real statement or the error page a period before the plan gets."""
    if not on_documents_page(page):
        raise RuntimeError("not on a signed-in workplaceservices page")
    _keep_awake(page)
    if not on_documents_page(page):
        raise SessionExpired("the Statements page did not come back on its own host, sign in again")
    plan = _plan_in_use.get("plan")
    if plan and not _shows_plan(page, plan):
        # The tab no longer shows the plan this run chose, so the site may
        # make another plan's statement. It is chosen again first.
        reason = open_plan(page, plan)
        if reason != OPENED:
            raise PlanDidNotOpen(reason)
    args = {"tokenPath": TOKEN_PATH, "statementPath": STATEMENT_PATH,
            "dateRange": date_range, "want": "html" if want_html else ""}
    try:
        res = page.evaluate(_MAKE_JS, args)
    except Exception as e:
        # The site moves its own tab around (to the plan summary, to a
        # timeout page) without being asked. If that happened mid-call,
        # put the tab back on the Statements page and ask once more.
        if "Execution context was destroyed" not in str(e):
            raise
        log.info("the page navigated during a statement call, going back to the Statements page")
        _last_navigation["at"] = 0.0
        _keep_awake(page)
        if not on_documents_page(page):
            raise SessionExpired("the Statements page did not come back, sign in again")
        res = page.evaluate(_MAKE_JS, args)
    status = res.get("status")
    if res.get("signedOut") is True:
        raise SessionExpired("NetBenefits redirected the %s request, sign in again" % res.get("stage"))
    if status in (401, 403):
        raise SessionExpired("NetBenefits answered %s" % status)
    if status != 200:
        raise RuntimeError("NetBenefits answered %s at the %s step" % (status, res.get("stage")))
    return res


def plan_label(heading: str) -> str:
    """'SOME CORP. RETIREMENT SAVINGS PLAN (12345)' -> 'Some Corp. Retirement Savings Plan'.
    The plan number is dropped, it has no place in a filename."""
    s = re.sub(r"\(\s*\d+\s*\)", "", heading or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s.title() if s.isupper() else s


# -- several plans on one login ------------------------------------------------------

# The "Choose a Plan" page. It lists every plan on the login whatever plan
# the site is remembering, as plain links, and nothing on it is pressed.
CHOOSER_URL = WPS + "/mybenefits/definedcontributions/navigation/dc/OnlineStatement?planChooser=yes"

# The only addresses a plan may carry. Relative, so they stay on this
# host, and nothing after the ids. A Choose a Plan link names the plan
# alone, a Select Account option names the client and the plan.
_CHOOSER_PATH_RE = re.compile(
    r"^/mybenefits/(?:DefinedContributions|definedcontributions)/navigation/dc/"
    r"OnlineStatement\?plan=(\d{1,15})&?$")
_SELECT_PATH_RE = re.compile(
    r"^/mybenefits/savings2/navigation/dc/OnlineStatement\?&?client=(\d{1,15})&plan=(\d{1,15})$")


def plan_ids(path: str) -> Optional[tuple]:
    """(client, plan) from a plan's own address, client "" for a Choose a
    Plan link, or None for any other address."""
    path = (path or "").strip()
    m = _CHOOSER_PATH_RE.match(path)
    if m:
        return ("", m.group(1))
    m = _SELECT_PATH_RE.match(path)
    return (m.group(1), m.group(2)) if m else None

OPENED = "open"

# Why a plan was left for the owner. Fixed words, never an address or an
# error's own text, which could carry the plan's ids.
LEAVES_THE_SITE = "its address leaves the NetBenefits statements page"
REDIRECTS = "its statements address sends you elsewhere"
NOT_ITS_PAGE = "its address did not answer as a plan of this site"
DID_NOT_OPEN = "its Statements page did not open"


class PlanListUnavailable(RuntimeError):
    """This login has several plans and the site did not show their list."""


class PlanDidNotOpen(RuntimeError):
    """A plan that opened earlier in the run would not open again. `reason`
    is one of the fixed words above."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# What the plan last opened is, so the reload that keeps the session awake
# goes back to it and a statement is never made for another one.
_plan_in_use: dict = {"plan": None}

_READ_CHOOSER_JS = r"""() => {
  const text = (document.body && document.body.innerText) || '';
  if (!/choose a plan/i.test(text)) return null;
  return Array.from(document.querySelectorAll('a[href]'))
    .filter(a => /[?&]plan=\d/i.test(a.getAttribute('href') || ''))
    .map(a => ({text: (a.textContent || '').replace(/\s+/g, ' ').trim(),
                value: a.getAttribute('href') || '', disabled: false}));
}"""

_READ_CHOICES_JS = r"""() => {
  const sel = document.querySelector("select[name='accountSelect']");
  const text = (document.body && document.body.innerText) || '';
  if (!sel || !/requires you to select an account/i.test(text)) return null;
  return Array.from(sel.options).map(o => ({
    text: (o.textContent || '').replace(/\s+/g, ' ').trim(),
    value: o.getAttribute('value') || '', disabled: !!o.disabled}));
}"""

# Asks for a plan's address from inside the page, with the session's
# cookies, as following the link would, and that request is what chooses
# the plan on the site (live, 2026-10-14: it answers 200 with the selector
# page, not the Statements page, and the next load of the Statements
# address shows the plan). It is a same-origin request, so a redirect within
# this host is followed and a redirect to any other host is refused by the
# browser before that host is asked anything. Says only what kind of
# answer came back.
_PROBE_JS = r"""async ({path}) => {
  const here = location;
  if (here.protocol !== 'https:' || here.hostname !== 'workplaceservices.fidelity.com') {
    throw new Error('Refusing to look from an off-host page');
  }
  let r;
  try {
    r = await fetch(path, {credentials: 'include', mode: 'same-origin', redirect: 'follow'});
  } catch (e) { return {kind: 'redirect'}; }
  const html = await r.text();
  // Strings, not the URL constructor, which the page shadows. `path` is
  // always a path of this host, so the address asked for is origin + path.
  const asked = here.origin + path;
  return {kind: 'page', status: r.status, origin: r.type === 'basic',
          redirected: !!r.redirected || r.url !== asked,
          password: /type=["']?password\b/i.test(html)};
}"""

# Whether the session is still signed in, by the token call the statement
# request makes anyway.
_SIGNED_IN_JS = """async ({tokenPath}) => {
  try {
    const r = await fetch(tokenPath, {credentials: 'include', headers: {'Accept': 'application/json'}, redirect: 'error'});
    if (r.status !== 200) return false;
    const j = await r.json();
    return !!(j && j.txnTokenValue);
  } catch (e) { return false; }
}"""

_SMALL_WORDS = {"of", "and", "the", "for", "to", "in", "at", "on"}
_NOT_INITIALS = {"inc", "co", "ltd", "llp"}


def option_label(text: str) -> str:
    """A plan's name as the Select Account page gives it, ready for a filename.

    'ABC 457(B) PLAN' becomes 'ABC 457(b) Plan' and 'NORTH-STAR MEDICAL GROUP'
    becomes 'North-Star Medical Group'. A number in brackets is dropped, as
    plan_label drops it. Words of two or three capitals stay capitals,
    since a plan's name is full of initials, and a letter in brackets is
    the tax code's own, written small."""
    s = re.sub(r"\(\s*\d+\s*\)", "", text or "")
    s = re.sub(r"\s+", " ", s).strip()
    if not s.isupper():
        return s
    out = []
    for i, word in enumerate(s.split(" ")):
        letters = re.sub(r"[^A-Za-z]", "", word)
        if i and word.lower() in _SMALL_WORDS:
            out.append(word.lower())
        elif "-" not in word and 2 <= len(letters) <= 3 and letters.isalpha() \
                and letters.lower() not in _NOT_INITIALS and letters == word:
            out.append(word)
        else:
            out.append("-".join(part.capitalize() for part in word.split("-")))
    return " ".join(out)


def plan_digest(plan: str) -> str:
    """What a plan is known by in this app's records, the whole SHA-256 of
    its plan number, so the id is never written. The plan number alone, since
    a Choose a Plan link carries no client, and a plan must keep one key
    whichever list it was read from."""
    return hashlib.sha256(("plan:%s" % plan).encode("ascii")).hexdigest()


def _keep_private(ids) -> None:
    words = set(private_words())
    new = {i for i in ids if i} - words
    if new:
        set_private_words(sorted(words | new))


def plan_from_option(text: str, value: str) -> Optional[dict]:
    """One plan from one link of the Choose a Plan page or one option of
    the Select Account page.

    None for a placeholder, one with no name or no address. An address that
    is not this site's own Statements address for a plan makes a plan with
    no address, which is left for the owner and never opened."""
    label = option_label(text)
    value = (value or "").strip()
    if not label or not value:
        return None
    ids = plan_ids(value)
    if not ids:
        digest = hashlib.sha256(("label:" + label).encode("utf-8")).hexdigest()
        return {"label": label, "digest": digest, "path": "", "heading": text, "review": LEAVES_THE_SITE}
    _keep_private(ids)
    # A name that carries one of the plan's own numbers loses it, so it
    # never reaches a filename or a log.
    for number in sorted({i for i in ids if i}, key=len, reverse=True):
        label = re.sub(r"\s+", " ", label.replace(number, "")).strip(" -")
    return {"label": label or "Plan", "digest": plan_digest(ids[1]), "path": value, "heading": text}


def checked_plan(rec: dict) -> Optional[dict]:
    """A plan read back from this app's own list of plans, or None when it
    is not one this app would have written. Its address must still be this
    site's Statements address, and its digest must be that address's."""
    if not isinstance(rec, dict) or not rec.get("label") or not rec.get("digest"):
        return None
    path = rec.get("path") or ""
    plan = {"label": rec["label"], "digest": rec["digest"], "path": path,
            "heading": rec.get("heading") or rec["label"],
            "shared_name": bool(rec.get("shared_name"))}
    if not path:
        plan["review"] = rec.get("review") or LEAVES_THE_SITE
        return plan
    ids = plan_ids(path)
    if not ids or plan_digest(ids[1]) != rec["digest"]:
        return None
    _keep_private(ids)
    return plan


def _settle(page) -> None:
    for _ in range(20):
        if on_documents_page(page):
            break
        page.wait_for_timeout(750)


def _read_list(page, url: str, script: str) -> Optional[List[dict]]:
    """The plans one list page shows, or None when the page is not that list.
    The page is opened by its address and read with one script."""
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    _settle(page)
    if looks_signed_out(page):
        raise SessionExpired("NetBenefits showed its sign-in page, sign in again")
    raw = page.evaluate(script)
    if not isinstance(raw, list):
        # The script answers a list only on its own page.
        return None
    plans, seen = [], set()
    for o in raw:
        if not isinstance(o, dict) or o.get("disabled"):
            continue
        plan = plan_from_option(o.get("text") or "", o.get("value") or "")
        if plan is None or plan["digest"] in seen:
            continue
        seen.add(plan["digest"])
        plans.append(plan)
    names = [p["heading"].upper() for p in plans]
    for p in plans:
        p["shared_name"] = names.count(p["heading"].upper()) > 1
    return plans


def read_plan_choices(page) -> Optional[List[dict]]:
    """The plans of a login with several, or None for a login with one.

    The Choose a Plan page comes first. It lists every plan whatever plan
    the site is remembering, so a login it shows several plans for is a
    login with several, and one it shows exactly one plan for is a login
    with one plan, which goes on exactly as it always did. When it lists
    nothing, the Statements address is opened afresh and its Select Account
    page, shown only when the site remembers no plan, is read instead.
    Every link and option is read, nothing is pressed. The tab ends on the
    Statements address."""
    # Whatever plan was open may not be once these pages have loaded.
    _plan_in_use["plan"] = None
    chooser = _read_list(page, CHOOSER_URL, _READ_CHOOSER_JS)
    one_plan = bool(chooser) and len(chooser) == 1 and bool(chooser[0]["path"])
    select = _read_list(page, URLS["documents"], _READ_CHOICES_JS)
    if chooser and not one_plan:
        plans, where = chooser, "Choose a Plan"
    elif one_plan:
        return None
    elif select is None:
        return None
    elif not select:
        raise PlanListUnavailable("the Select Account page listed no plan")
    else:
        plans, where = select, "Select Account"
    log.info("NetBenefits lists %d plan(s) on its %s page, %d left for the owner",
             len(plans), where, sum(1 for p in plans if not p["path"]))
    return plans


def _heading_pattern(plan: dict) -> str:
    """The plan's heading on its Statements page, its name and then its
    number in brackets, as in 'ABC 457(B) PLAN (12345)'. When two plans on
    the login share a name, the number must be this plan's own, from its
    address, since the name alone would take one for the other."""
    name = re.sub(r"\(\s*\d+\s*\)", "", plan.get("heading") or plan["label"])
    name = re.sub(r"\s+", " ", name).strip()
    number = r"\d+"
    ids = plan_ids(plan.get("path") or "")
    if plan.get("shared_name") and ids:
        number = re.escape(ids[1])
    return re.escape(name).replace("\\ ", " ") + r"\s*\(\s*" + number + r"\s*\)"


def _shows_plan(page, plan: dict) -> bool:
    try:
        if page.locator("form[name='frmRequest']").count() == 0:
            return False
        text = re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=5000) or "")
    except Exception:
        return False
    return bool(re.search(_heading_pattern(plan), text, re.I))


def _signed_in(page) -> bool:
    try:
        return bool(page.evaluate(_SIGNED_IN_JS, {"tokenPath": TOKEN_PATH}))
    except Exception:
        return False


def open_plan(page, plan: dict) -> str:
    """Make `plan` the one the next statement is made for. OPENED, or the
    reason it was left for the owner.

    The plan's address is asked for from inside the page, with the
    session's cookies, as a same-origin request, and that request is what
    chooses the plan on the site. Its answer is the plan selector, not the
    Statements page, so only its shape is checked: a 200 from this origin
    at the address asked for. A redirect to any other host is refused by
    the browser before that host is asked anything, and such a plan, or any
    other answer, is left for the owner. If the session itself is gone by
    then, that is SessionExpired, not a plan to review. Then the tab
    reloads the Statements address, and that page must show this plan's
    statement form and its heading, the plan's name and its number in
    brackets, or the plan is left for the owner and no statement is made.
    The tab never loads the plan's own address. Nothing is pressed. The
    reason is one of the fixed words above, never an address or an
    error's text."""
    if not plan.get("path"):
        return plan.get("review") or LEAVES_THE_SITE
    if not plan_ids(plan["path"]):
        return LEAVES_THE_SITE
    _plan_in_use["plan"] = None
    if not on_documents_page(page):
        _settle(page)
        if not on_documents_page(page):
            raise SessionExpired("not on a signed-in workplaceservices page")
    probe = _look_ahead(page, plan["path"], plan["label"])
    if probe.get("kind") == "page" and probe.get("password"):
        raise SessionExpired("NetBenefits answered a plan with its sign-in page")
    # The answer is the plan selector, not the Statements page, so its body
    # is not read for the plan. It must be a plain 200 from this origin at
    # the address asked for. Whether the plan was chosen is decided by the
    # Statements page below.
    if not (probe.get("kind") == "page" and probe.get("status") == 200
            and probe.get("origin") is True and probe.get("redirected") is False):
        if not _signed_in(page):
            raise SessionExpired("NetBenefits signed this session out")
        return REDIRECTS if probe.get("kind") == "redirect" or probe.get("redirected") else NOT_ITS_PAGE
    # Only the Statements address is ever loaded, never the plan's own.
    # Playwright cannot refuse a redirect hop, so an address whose answer
    # could change between the look ahead and the load is not loaded.
    for address in (urlsplit(URLS["documents"]).path,):
        # It is asked for the same way first, so a day it redirects is seen
        # without following it.
        ahead = _look_ahead(page, address, plan["label"])
        if not (ahead.get("kind") == "page" and ahead.get("status") == 200 and not ahead.get("password")
                and ahead.get("origin") is True and ahead.get("redirected") is False):
            if not _signed_in(page):
                raise SessionExpired("NetBenefits signed this session out")
            return REDIRECTS
        try:
            page.goto(WPS + address, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.info("plan %s: loading a page failed (%s)", plan["label"], type(e).__name__)
        _settle(page)
        if looks_signed_out(page):
            raise SessionExpired("NetBenefits showed its sign-in page on a plan, sign in again")
        if not on_documents_page(page):
            log.warning("plan %s: the tab left the statements site", plan["label"])
            try:
                page.goto(URLS["documents"], wait_until="domcontentloaded", timeout=30000)
                _settle(page)
            except Exception as e:
                log.info("going back to the Statements page failed (%s)", type(e).__name__)
            if looks_signed_out(page) or not on_documents_page(page):
                raise SessionExpired("NetBenefits did not come back after a plan, sign in again")
            return LEAVES_THE_SITE
        if _shows_plan(page, plan):
            _plan_in_use["plan"] = plan
            _last_navigation["at"] = time.monotonic()
            return OPENED
    return DID_NOT_OPEN


def _look_ahead(page, path: str, label: str) -> dict:
    try:
        return page.evaluate(_PROBE_JS, {"path": path}) or {}
    except Exception as e:
        log.info("plan %s: the look ahead failed (%s)", label, type(e).__name__)
        return {"kind": "unreachable"}


# -- a statement that covers several plans -----------------------------------------

def _squeezed(text: str) -> str:
    """Upper case with every space gone, so a name matches however a PDF's
    text extraction happened to break or space it."""
    return re.sub(r"\s+", "", text or "").upper()


def _name_key(plan: dict) -> str:
    """What a plan is looked for by in a statement's text: its name from the
    plan list, without a bracketed number or a closing word "Plan", since a
    statement writes "ABC 457(B)" where the list says "ABC 457(B) PLAN"."""
    name = re.sub(r"\(\s*\d+\s*\)", "", plan.get("heading") or plan.get("label") or "")
    name = re.sub(r"\s+plan\s*$", "", name.strip(), flags=re.I)
    return _squeezed(name)


def plans_named(text: str, plans, chosen: str = "") -> List[dict]:
    """The plans of the list whose names a statement's text carries, in the
    list's order, each once.

    NetBenefits makes one combined statement for the plans of one employer,
    and every plan's own request gives that same statement. A plan whose
    name is in a longer one's (ABC inside ABC DCP) counts only where it
    stands on its own. Two plans with one name cannot be told apart by it,
    so such a name counts only for the plan `chosen` (its digest), the one
    the statement was made for, and never makes another plan covered."""
    hay = _squeezed(text)
    keyed = [(p, _name_key(p)) for p in plans if p.get("path")]
    keys = [k for _, k in keyed]
    out, seen = [], set()
    for plan, key in keyed:
        if not key or plan["digest"] in seen:
            continue
        if keys.count(key) > 1 and plan["digest"] != chosen:
            continue
        longer = [k for _, k in keyed if k != key and key in k]
        rest = hay
        for k in sorted(longer, key=len, reverse=True):
            rest = rest.replace(k, "")
        if key in rest:
            seen.add(plan["digest"])
            out.append(plan)
    return out


def employer_name(page) -> str:
    """The employer the site's header names (id client-name), or ''."""
    try:
        loc = page.locator("#client-name")
        if loc.count() == 0:
            return ""
        return re.sub(r"\s+", " ", loc.inner_text(timeout=3000) or "").strip()
    except Exception:
        return ""


def combined_label(plans, employer: str, text: str) -> str:
    """What a statement covering several plans is filed under.

    The employer's name when the header gives one and the statement itself
    carries it too, so a header that names another, remembered employer is
    not believed. Otherwise the plans' names, sorted, as in "ABC 403(b),
    ABC 457(b) and ABC DCP Plans"."""
    if employer and _squeezed(employer) in _squeezed(text):
        return option_label(employer).rstrip(" ,.") + " Plans"
    names = sorted(re.sub(r"\s+Plan$", "", p["label"]) for p in plans)
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return joined + " Plans"


def collect_documents(page, keep=None, config: Optional[dict] = None) -> List[dict]:
    """One row per completed period the site can make a statement for.
    Newest first. Stops walking older periods at the first error page that
    follows a real statement, since a plan has one start date."""
    kind = ((config or {}).get("statement_period") or "quarterly").lower()
    rows: List[dict] = []
    found_any = False
    for p in periods(kind):
        if keep is not None and not keep(p["date"][:4]):
            continue
        res = make_statement(page, p["range"])
        if res.get("isStatement"):
            found_any = True
            rows.append({"title": p["title"], "date": p["date"], "period_start": p["start"],
                         "account": plan_label(res.get("plan") or ""), "category": "Statement",
                         "item_id": p["range"], "client_id": kind, "href": WPS + STATEMENT_PATH})
            log.info("%s %s: statement", p["title"], p["label"])
        else:
            log.info("%s %s: %s", p["title"], p["label"], "none (before the plan)" if found_any else "none")
            if found_any:
                break
    return rows


def download_document(page, title: str, date: str, out_path: Path, occurrence: int = 0,
                      item_hint: str = "", client_hint: str = "", account: str = "",
                      plan: Optional[dict] = None) -> bool:
    """Make the statement for this period again and render it to PDF.

    On a login with several plans, `plan` is the one this statement belongs
    to, and it is opened first unless it is the one already open. A plan
    that will not open saves nothing and answers False."""
    from paperpull_core import receipt_pdf
    if plan and (_plan_in_use.get("plan") or {}).get("digest") != plan.get("digest"):
        reason = open_plan(page, plan)
        if reason != OPENED:
            log.warning("%s %s: the plan %s did not open (%s)", title, date, plan.get("label"), reason)
            return False
    date_range = item_hint
    if not date_range:
        kind = client_hint or "quarterly"
        match = next((p for p in periods(kind) if p["date"] == date and p["title"] == title), None)
        if not match:
            log.warning("no period for %s %s", title, date)
            return False
        date_range = match["range"]
    try:
        res = make_statement(page, date_range, want_html=True)
    except PlanDidNotOpen as e:
        log.warning("%s %s: the plan %s did not open again (%s)", title, date,
                    (plan or {}).get("label"), e.reason)
        return False
    if not res.get("isStatement"):
        log.warning("%s %s: the site made no statement (%s)", title, date, res.get("title"))
        return False
    html = res.get("html") or ""
    # The print button is window.print(). Hide it and the site chrome the
    # way the print stylesheet would, then render.
    html = html.replace("</head>", "<style>@media print { .no-print, nav, header nav { display:none } }</style></head>", 1)
    receipt_pdf.print_html_to_pdf(page, html, out_path)
    return out_path.is_file() and out_path.stat().st_size > 1000


def _page_summary(page) -> dict:
    out = {"url": redact(page.url or ""), "title": ""}
    try:
        out["title"] = redact(page.title() or "")
    except Exception:
        pass
    try:
        heads = page.locator("h1, h2, h3").all_inner_texts()
        out["headings"] = [redact(h.strip())[:80] for h in heads if h.strip()][:30]
    except Exception:
        out["headings"] = []
    controls = []
    for role in ("link", "button", "tab", "menuitem"):
        try:
            loc = page.get_by_role(role)
            for i in range(min(loc.count(), 80)):
                el = loc.nth(i)
                try:
                    text = (el.inner_text(timeout=300) or "").strip()
                except Exception:
                    text = ""
                if not text:
                    try:
                        text = (el.get_attribute("aria-label") or "").strip()
                    except Exception:
                        text = ""
                if not text:
                    continue
                href = ""
                if role == "link":
                    try:
                        href = el.get_attribute("href") or ""
                    except Exception:
                        pass
                controls.append({"role": role, "text": redact(text)[:60],
                                 "href": redact(href)[:120],
                                 "safe": is_safe_control(text),
                                 "survey": bool(SURVEY_LINK_RE.match(text.strip()))})
        except Exception:
            pass
    out["controls"] = controls
    return out


def survey(page, dwell_ms: int = 4000, max_follow: int = 6) -> dict:
    """What the signed-in NetBenefits plan looks like, without downloading anything.

    Records the page, its headings and controls with the guard's verdict on
    each, and every JSON response fidelity.com sends while the page settles. Then
    follows, one at a time and back again, the few links whose text is
    exactly a document word, recording the same for each. Bodies
    are recorded as shape only, never values, and any run of six digits is
    masked. No screenshot, a retirement account page shows balances."""
    seen: list = []

    def on_response(res):
        try:
            url = res.url or ""
            if not is_safe_url(url):
                return
            ct = (res.headers.get("content-type") or "").lower()
            if "json" not in ct and "pdf" not in ct:
                return
            entry = {"url": redact(url)[:200], "status": res.status, "type": ct[:40]}
            if "json" in ct:
                try:
                    body = res.json()
                    entry["shape"] = _shape(body)
                except Exception:
                    entry["shape"] = "unreadable"
            seen.append(entry)
        except Exception:
            pass

    page.on("response", on_response)
    report = {"pages": [], "responses": seen}
    try:
        page.wait_for_timeout(dwell_ms)
        start = _page_summary(page)
        report["pages"].append(start)
        followed = 0
        for c in start["controls"]:
            if followed >= max_follow or c["role"] != "link" or not c["survey"]:
                continue
            if not is_safe_control(c["text"]):
                continue
            try:
                link = page.get_by_role("link", name=re.compile(
                    "^" + escape_for_locator(c["text"].replace("#", "")) + "$", re.I)).first
                if link.count() == 0:
                    continue
                before = page.url
                link.click(timeout=5000)
                page.wait_for_timeout(dwell_ms)
                if not is_safe_url(page.url or ""):
                    page.go_back()
                    continue
                summary = _page_summary(page)
                summary["followed_from"] = c["text"]
                report["pages"].append(summary)
                followed += 1
                if page.url != before:
                    page.go_back(wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(1500)
            except Exception as e:
                report.setdefault("notes", []).append(
                    "could not follow %r: %s" % (c["text"], str(e)[:120]))
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
    return report
