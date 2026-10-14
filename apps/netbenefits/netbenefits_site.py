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
  employers and four plans. With no plan chosen, the Statements address
  shows a "Select Account" page instead ("The page you've chosen requires
  you to select an account."), a select named accountSelect whose options
  are the plan names, each valued with that plan's own Statements address
  (OnlineStatement?&client=<digits>&plan=<digits>), and a Go button that
  only navigates to the chosen value. The app reads the options and never
  presses Go. The tab never navigates to a plan's address. The address is
  asked for from inside the page without following a redirect, which is
  what chooses the plan on the site, and then the Statements address is
  reloaded and must show that plan's Statements page by its form and its
  heading. One plan on the surveyed login sent its address to the Fidelity
  retail sign-in on digital.fidelity.com, so a plan whose address
  redirects is left for the owner and never followed. The site remembers
  the plan last chosen, and the statement request makes that plan's
  statement. The employer chooser and its Continue, which change a saved
  preference, are never used.

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

# The only address a plan's option may carry. Relative, so it stays on
# this host, and nothing after the two ids.
PLAN_PATH_RE = re.compile(
    r"^/mybenefits/savings2/navigation/dc/OnlineStatement\?&?client=(\d{1,15})&plan=(\d{1,15})$")

OPENED = "open"

# Why a plan was left for the owner. Fixed words, never an address or an
# error's own text, which could carry the plan's ids.
LEAVES_THE_SITE = "its address leaves the NetBenefits statements page"
REDIRECTS = "its statements address sends you elsewhere"
NOT_ITS_PAGE = "its address did not answer with its own Statements page"
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

_READ_CHOICES_JS = r"""() => {
  const sel = document.querySelector("select[name='accountSelect']");
  const text = (document.body && document.body.innerText) || '';
  if (!sel || !/requires you to select an account/i.test(text)) return null;
  return Array.from(sel.options).map(o => ({
    text: (o.textContent || '').replace(/\s+/g, ' ').trim(),
    value: o.getAttribute('value') || '', disabled: !!o.disabled}));
}"""

# Asks for a plan's address from inside the page, with the session's
# cookies, as the Go button's navigation would, and that request is what
# chooses the plan on the site. It refuses to follow a redirect, so an
# address that sends the browser to another site is seen without going
# there. Says only what kind of answer came back.
_PROBE_JS = r"""async ({path, heading}) => {
  const here = location;
  if (here.protocol !== 'https:' || here.hostname !== 'workplaceservices.fidelity.com') {
    throw new Error('Refusing to look from an off-host page');
  }
  let r;
  try {
    r = await fetch(path, {credentials: 'include', redirect: 'manual'});
  } catch (e) { return {kind: 'unreachable'}; }
  if (r.type === 'opaqueredirect' || (r.status >= 300 && r.status < 400)) return {kind: 'redirect'};
  const html = await r.text();
  const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  return {kind: 'page', status: r.status,
          form: /name=["']?frmRequest\b/i.test(html),
          password: /type=["']?password\b/i.test(html),
          named: new RegExp(heading, 'i').test(text)};
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


def plan_digest(client: str, plan: str) -> str:
    """What a plan is known by in this app's records. The whole SHA-256 of
    its two ids, so two plans never share one and neither id is written."""
    return hashlib.sha256(("%s:%s" % (client, plan)).encode("ascii")).hexdigest()


def _keep_private(ids) -> None:
    words = set(private_words())
    new = {i for i in ids if i} - words
    if new:
        set_private_words(sorted(words | new))


def plan_from_option(text: str, value: str) -> Optional[dict]:
    """One plan from one option of the Select Account page.

    None for a placeholder, an option with no name or no value. A value that
    is not this site's own Statements address for a client and a plan makes
    a plan with no address, which is left for the owner and never opened."""
    label = option_label(text)
    value = (value or "").strip()
    if not label or not value:
        return None
    m = PLAN_PATH_RE.match(value)
    if not m:
        digest = hashlib.sha256(("label:" + label).encode("utf-8")).hexdigest()
        return {"label": label, "digest": digest, "path": "", "heading": text, "review": LEAVES_THE_SITE}
    _keep_private(m.groups())
    # A name that carries one of the plan's own numbers loses it, so it
    # never reaches a filename or a log.
    for number in sorted(set(m.groups()), key=len, reverse=True):
        label = re.sub(r"\s+", " ", label.replace(number, "")).strip(" -")
    return {"label": label or "Plan", "digest": plan_digest(*m.groups()), "path": value, "heading": text}


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
    m = PLAN_PATH_RE.match(path)
    if not m or plan_digest(*m.groups()) != rec["digest"]:
        return None
    _keep_private(m.groups())
    return plan


def _settle(page) -> None:
    for _ in range(20):
        if on_documents_page(page):
            break
        page.wait_for_timeout(750)


def read_plan_choices(page) -> Optional[List[dict]]:
    """The plans this login holds, from the Select Account page, or None
    when the Statements address shows no such page.

    The bare Statements address is always opened afresh, so a page left on
    some plan from before is not taken for the whole list. Every option is
    read, nothing is pressed. A placeholder option is skipped and two
    options for one plan are one plan."""
    # Whatever plan was open may not be once this page has loaded.
    _plan_in_use["plan"] = None
    page.goto(URLS["documents"], wait_until="domcontentloaded", timeout=30000)
    _settle(page)
    if looks_signed_out(page):
        raise SessionExpired("NetBenefits showed its sign-in page, sign in again")
    raw = page.evaluate(_READ_CHOICES_JS)
    if not isinstance(raw, list):
        # The script answers a list only on the Select Account page.
        return None
    plans, seen = [], set()
    for o in raw:
        if o.get("disabled"):
            continue
        plan = plan_from_option(o.get("text") or "", o.get("value") or "")
        if plan is None or plan["digest"] in seen:
            continue
        seen.add(plan["digest"])
        plans.append(plan)
    if not plans:
        raise PlanListUnavailable("the Select Account page listed no plan")
    names = [p["heading"].upper() for p in plans]
    for p in plans:
        p["shared_name"] = names.count(p["heading"].upper()) > 1
    log.info("NetBenefits lists %d plan(s), %d left for the owner",
             len(plans), sum(1 for p in plans if not p["path"]))
    return plans


def _heading_pattern(plan: dict) -> str:
    """The plan's heading on its Statements page, its name and then its
    number in brackets, as in 'ABC 457(B) PLAN (12345)'. When two plans on
    the login share a name, the number must be this plan's own, from its
    address, since the name alone would take one for the other."""
    name = re.sub(r"\(\s*\d+\s*\)", "", plan.get("heading") or plan["label"])
    name = re.sub(r"\s+", " ", name).strip()
    number = r"\d+"
    m = PLAN_PATH_RE.match(plan.get("path") or "")
    if plan.get("shared_name") and m:
        number = re.escape(m.group(2))
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

    The tab never navigates to the plan's own address. That address is
    asked for from inside the page, with the session's cookies and without
    following a redirect, which is the request that chooses the plan on the
    site. An address that redirects, or that answers with anything but this
    plan's own Statements page, chooses nothing that is used. If the
    session itself is gone by then, that is SessionExpired, not a plan to
    review. Then the tab reloads the Statements address, which stays on
    this host, and it must show this plan's Statements page by its form
    and its heading, or the plan is left for the owner. Nothing is pressed.
    The reason is one of the fixed words above, never an address or an
    error's text."""
    if not plan.get("path"):
        return plan.get("review") or LEAVES_THE_SITE
    if not PLAN_PATH_RE.match(plan["path"]):
        return LEAVES_THE_SITE
    _plan_in_use["plan"] = None
    if not on_documents_page(page):
        _settle(page)
        if not on_documents_page(page):
            raise SessionExpired("not on a signed-in workplaceservices page")
    try:
        probe = page.evaluate(_PROBE_JS, {"path": plan["path"], "heading": _heading_pattern(plan)})
    except Exception as e:
        log.info("plan %s: the look ahead failed (%s)", plan["label"], type(e).__name__)
        probe = {"kind": "unreachable"}
    if probe.get("kind") == "page" and probe.get("password"):
        raise SessionExpired("NetBenefits answered a plan with its sign-in page")
    if not (probe.get("kind") == "page" and probe.get("status") == 200
            and probe.get("form") and probe.get("named")):
        if not _signed_in(page):
            raise SessionExpired("NetBenefits signed this session out")
        return REDIRECTS if probe.get("kind") == "redirect" else NOT_ITS_PAGE
    # The Statements address is asked for the same way before the tab
    # reloads it, so a day it redirects is seen without following it.
    try:
        bare = page.evaluate(_PROBE_JS, {"path": urlsplit(URLS["documents"]).path, "heading": "$^"})
    except Exception as e:
        log.info("the look ahead at the Statements page failed (%s)", type(e).__name__)
        bare = {"kind": "unreachable"}
    if bare.get("kind") != "page" or bare.get("status") != 200 or bare.get("password"):
        raise SessionExpired("the Statements address did not answer on its own host, sign in again")
    try:
        page.goto(URLS["documents"], wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        log.info("plan %s: reloading the Statements page failed (%s)", plan["label"], type(e).__name__)
    _settle(page)
    if looks_signed_out(page) or not on_documents_page(page):
        raise SessionExpired("NetBenefits did not come back on its Statements page, sign in again")
    if not _shows_plan(page, plan):
        return DID_NOT_OPEN
    _plan_in_use["plan"] = plan
    _last_navigation["at"] = time.monotonic()
    return OPENED


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
