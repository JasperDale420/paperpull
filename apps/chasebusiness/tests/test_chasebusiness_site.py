"""The checks that have to pass before this app touches a real account.
No browser, no network, invented values only: a made-up document center
drawn the way the owner's survey found Chase's, driven through the site
layer's own code with the core's press replaced by the fake's."""
import ast
import io
import json
import logging
import sys
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from urllib.parse import urlencode

import pytest

HERE = Path(__file__).resolve().parent.parent
# This checkout's own core, ahead of any installed copy, so its capture is
# the one tested.
sys.path.insert(0, str(HERE.parents[1] / "core"))
sys.path.insert(0, str(HERE))
import chasebusiness_site as site  # noqa: E402
import chasebusiness_docs as docs  # noqa: E402

BASE = "https://secure.chase.com"
DOCREF = BASE + site.DOCREF_PATH
PDF_URL = BASE + "/svc/rr/documents/secure/idal/v5/pdfdoc/star/list?docKey=KEYZZ&csrftoken=TOKZZ"
DASHBOARD = BASE + "/web/auth/dashboard#/dashboard/index/index"
CUR = 2026
EIGHT_YEARS = tuple(range(CUR, CUR - 8, -1))

# Invented accounts, which must never leave through a log, the console, a
# trace or a file name.
CHK = {"id": "bottom-7700112233", "label": "BUS COMPLETE CHK (...4321)"}
CARD = {"id": "bottom-7700445566", "label": "BUSINESS CARD (...8765)"}
DEBIT = {"id": "bottom-7700778899", "label": "BUS TOTAL DEBIT (...5555)"}
HOLDER = {"id": "bottom-7700990011", "label": "ZEPHYRINA QUILLFEATHER (...8765)"}
SECRETS = ["BUS COMPLETE CHK", "BUSINESS CARD (...8765)", "BUS TOTAL DEBIT", "ZEPHYRINA",
           "QUILLFEATHER", "4321", "8765", "5555", "7700112233", "7700445566",
           "7700778899", "7700990011", "DOCQX", "KEYZZ", "TOKZZ"]


def _pdf(tail: str = "") -> bytes:
    """A statement PDF whose text prints an account number ending in
    `tail`, the way Chase prints it in full."""
    from paperpull_core import testkit
    return testkit.text_pdf(["Business Complete Checking", "Account Number: 000000%s" % tail])


PDF = _pdf("4321")


def item(doc_id, iso, pages=2, kind="Statement"):
    return {"documentId": doc_id, "documentDate": iso.replace("-", ""), "pageCount": pages,
            "documentTypeDesc": kind, "inserts": [], "adaVersionAvailable": True}


def label_date(iso):
    return site._row_label_date(iso)


# ---------------------------------------------------------------------------
# The made-up document center
# ---------------------------------------------------------------------------

class Request:
    def __init__(self, body, method="POST"):
        self.post_data, self.method = body, method


class Response:
    def __init__(self, account_id, filt, data, status=200, url=DOCREF, method="POST", raw=None):
        self.url, self.status = url, status
        self.request = Request(urlencode({"accountFilter": account_id,
                                          "dateFilter.idalDateFilterType": filt}), method)
        self._data, self._raw = data, raw

    def json(self):
        if self._raw is not None:
            return json.loads(self._raw)
        return self._data


class Download:
    def __init__(self, url, body):
        self.url, self.body = url, body
        self.suggested_filename = "20260930-STATEMENTS-4321-.pdf"
        self.canceled = False

    def save_as(self, path):
        Path(path).write_bytes(self.body)

    def cancel(self):
        self.canceled = True


class Info:
    value = None


class Locator:
    def __init__(self, page, css):
        self.page, self.css = page, css

    def _labels(self):
        return self.page.labels_for(self.css)

    def count(self):
        if self.css == "input[type='password']":
            return 1 if self.page.password else 0
        if self.css == site.FALLBACK["doc_row"]:
            return len(self.page.state()["rows"])
        return 1 if self._labels() is not None else 0

    def is_visible(self):
        return self._labels() is not None

    def evaluate(self, js):
        assert js == site._LABELS_JS
        return list(self._labels() or [])

    def inner_text(self, timeout=None):
        return ""


class Center:
    """Accounts: dicts of id, label, years ({year: [items]}) or None for an
    account with no Statements accordion."""

    def __init__(self, accounts, selected=0, years=EIGHT_YEARS):
        self.accounts = accounts
        self.years = list(years)
        self.initial = selected
        self.url = DASHBOARD
        self.listeners = {"response": [], "download": []}
        self.emitted, self.downloads, self.presses = [], [], []
        self.dialog = self.password = False
        self.duplicate_answers = False
        self.answer_override = {}     # (account_id, filter) -> Response factory
        self.rows_override = None
        # None: a statement printing the current account's own last four.
        self.download_body = None
        self.download_url = PDF_URL
        self.downloads_per_save = 1
        self.on_goto = None
        self.on_wait = None
        self.option_place = "row"     # "row", "menu", "detached", "other_row"
        self.late_download = False
        self.folder_copy = None       # a folder to drop the browser's own file in
        self.state_hook = None
        self.closed = False
        self.reset()

    # -- the page's own behavior -------------------------------------------
    def reset(self):
        self.selected = self.initial
        self.expanded = True
        self.year = CUR
        self.picker_open = False
        self.menu_open = None

    def acct(self):
        return self.accounts[self.selected]

    def account_id(self, a=None):
        return (a or self.acct())["id"][len("bottom-"):]

    def items(self):
        years = self.acct().get("years")
        return list((years or {}).get(self.year, []))

    def answer(self):
        aid = self.account_id()
        filt = site.filter_for(CUR, self.year)
        if (aid, filt) in self.answer_override:
            return self.answer_override[(aid, filt)]()
        data = {"code": "SUCCESS", "payeeId": "x", "paperless": True}
        its = self.items()
        if its:
            data["idaldocRefs"] = its
        return Response(aid, filt, data)

    def emit_list(self):
        if self.acct().get("years") is None or not self.expanded:
            return
        self._emit(self.answer())
        if self.duplicate_answers:
            self._emit(self.answer())

    def _download(self):
        body = self.download_body
        if body is None:
            body = _pdf(site.label_tail(self.acct()["label"]))
        dl = Download(self.download_url, body)
        if self.folder_copy is not None:
            (Path(self.folder_copy) / dl.suggested_filename).write_bytes(body)
        self.downloads.append(dl)
        for f in list(self.listeners["download"]):
            f(dl)

    def _emit(self, resp):
        self.emitted.append(resp)
        for f in list(self.listeners["response"]):
            f(resp)

    def goto(self, url, **kw):
        self.url = url
        if self.late_download and self.downloads:
            self.late_download = False
            self._download()
        self.reset()
        if self.on_goto:
            self.on_goto(self)
        self.emit_list()

    def click(self, css):
        self.presses.append(css)
        if css.startswith("#bottom-"):
            self.selected = [a["id"] for a in self.accounts].index(css[1:])
            self.year, self.picker_open, self.menu_open = CUR, False, None
            self.emit_list()
        elif css == "#" + site.STATEMENTS_ACCORDION_ID:
            self.expanded = True
            self.emit_list()
        elif css == "#" + site.YEAR_PICKER_ID:
            self.picker_open = True
        elif css.startswith("#container-"):
            self.year = self.years[int(css.split("-")[1])]
            self.picker_open = False
            self.emit_list()
        elif "downloadDocumentDropdown" in css:
            self.menu_open = int(css.rsplit("-row", 1)[1].split("-")[0])
        elif css.endswith("-downloadPDFOption"):
            for _ in range(self.downloads_per_save):
                self._download()
        else:
            raise AssertionError("pressed something else: " + css)

    # -- what the site layer reads ------------------------------------------
    def rows(self):
        if self.rows_override is not None:
            return self.rows_override
        if self.acct().get("years") is None or not self.expanded:
            return []
        out = []
        for n, it in enumerate(self.items()):
            iso = site.answer_date(it["documentDate"])
            out.append({"id": "accountsTable-STATEMENTS-row%d" % n, "shown": True,
                        "cells": [label_date(iso), it["documentTypeDesc"],
                                  "%d pages" % it["pageCount"],
                                  "%s Opens menu to save document" % label_date(iso)]})
        return out

    def state(self):
        menu = [{"id": a["id"], "labels": [a["label"]], "shown": True,
                 "all": a["label"] + (", current selection" if i == self.selected else "")}
                for i, a in enumerate(self.accounts)]
        acc = [{"id": "button-accountsAccordian-NOTICES", "expanded": "false",
                "labels": ["Notices and letters"], "shown": True}]
        if self.acct().get("years") is not None:
            acc.insert(0, {"id": site.STATEMENTS_ACCORDION_ID,
                           "expanded": "true" if self.expanded else "false",
                           "labels": ["Statements"], "shown": True})
        options = []
        if self.picker_open:
            for i, y in enumerate(self.years):
                text = str(y)
                if y == self.year:
                    text += ", current selection"
                if i == len(self.years) - 1:
                    text += ", you've reached the end of the list"
                options.append({"id": "container-%d-filterstyledselect-0" % i,
                                "labels": [text], "shown": True})
        out = {"menu": menu, "accordions": acc,
                "picker": {"labels": ["View:"], "value": str(self.year), "shown": True},
                "options": options, "rows": self.rows(), "dialog": self.dialog,
                "password": self.password}
        if self.state_hook:
            self.state_hook(out)
        return out

    def labels_for(self, css):
        st = self.state()
        if css.startswith("#bottom-"):
            hits = [a for a in self.accounts if a["id"] == css[1:]]
            return [hits[0]["label"]] if hits else None
        if css == "#" + site.STATEMENTS_ACCORDION_ID:
            return ["Statements"] if any(a["id"] == css[1:] for a in st["accordions"]) else None
        if css == "#" + site.YEAR_PICKER_ID:
            return ["View:"]
        if css.startswith("#container-"):
            hits = [o for o in st["options"] if "#" + o["id"] == css]
            return hits[0]["labels"] if hits else None
        if "downloadDocumentDropdown" in css:
            n = int(css.rsplit("-row", 1)[1].split("-")[0])
            hits = [r for r in st["rows"] if r["id"] == "accountsTable-STATEMENTS-row%d" % n]
            return [hits[0]["cells"][3]] if hits else None
        if css.endswith("-downloadPDFOption"):
            n = int(css.rsplit("item-0-", 1)[1].split("-")[0])
            return ["Save as PDF"] if self.menu_open == n else None
        return None

    # -- the Playwright surface ----------------------------------------------
    def evaluate(self, js, arg=None):
        if js == site._DIALOG_OPEN_JS:
            return bool(self.dialog)
        if js == site._MENU_JS:
            n = int(arg["row"].rsplit("row", 1)[1])
            has_row = any(r["id"] == arg["row"] for r in self.rows())
            shown = self.menu_open == n
            menu = self.option_place in ("menu", "shared_two_expanded", "menu_not_expanded")
            return {"controls": 1 if has_row else 0, "option": shown,
                    "shownOptions": (1 if shown else 0) + (1 if self.option_place == "other_row" else 0),
                    "inRow": self.option_place in ("row", "other_row", "wrong_trigger", "row_collapsed"),
                    "menuId": "menu-shared" if menu else "",
                    "triggerId": ("header-accountsTable-STATEMENTS-row%d-cell3-downloadDocumentDropdown"
                                  % (n + 1 if self.option_place == "wrong_trigger" else n)),
                    "expandedSelf": shown and self.option_place not in ("menu_not_expanded", "row_collapsed"),
                    "expandedOthers": 1 if self.option_place == "shared_two_expanded" else 0}
        assert js == site._STATE_JS
        return self.state()

    def locator(self, css):
        return Locator(self, css)

    def on(self, event, f):
        self.listeners[event].append(f)

    def remove_listener(self, event, f):
        self.listeners[event].remove(f)

    def wait_for_timeout(self, ms):
        if self.on_wait:
            self.on_wait(self, ms)

    def title(self):
        return ""

    def is_closed(self):
        return self.closed

    @contextmanager
    def expect_response(self, pred, timeout=None):
        start = len(self.emitted)
        yield
        if not any(pred(r) for r in self.emitted[start:]):
            raise TimeoutError("no response")

    @contextmanager
    def expect_download(self, timeout=None):
        info, start = Info(), len(self.downloads)
        yield info
        if len(self.downloads) == start:
            raise TimeoutError("no download")
        info.value = self.downloads[start]


@pytest.fixture(autouse=True)
def fake_press(monkeypatch):
    """The core's press, replaced by the made-up center's. The press is the
    core's own to test; this only says which control was pressed."""
    def click(page, locator, *, css, what, words, step="press a control", **kw):
        assert locator.css == css
        page.click(css)
    monkeypatch.setattr(site.pressing, "click", click)
    site.set_private_words([])
    yield


def chk_years():
    return {2026: [item("DOCQX0926", "2026-09-30"), item("DOCQX0826", "2026-08-31", 3)],
            2025: [item("DOCQX1225", "2025-12-31")],
            2024: []}


def center(**kw):
    return Center([dict(CHK, years=chk_years()),
                   dict(CARD, years={2026: [item("DOCQXC0926", "2026-09-28", 4)]}),
                   dict(DEBIT, years=None), dict(HOLDER, years=None)],
                  years=[2026, 2025, 2024], **kw)


def run():
    return site.Run(today=lambda: site._date(CUR, 10, 12))


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

BUSINESS_WORDS = [
    "Add employee card", "Send money with Zelle", "Wires & global transfers", "ACH Payment Services",
    "ACH Collections", "Deposit Checks", "Merchant Services", "Accept Card Payments", "QuickAccept",
    "Catalog", "Pending approvals", "Payment center", "Access & Security Manager",
    "Show or hide accounts", "Stay signed in", "Chase Offers", "Paperless", "Pay card",
    "Open an account", "Profile & settings", "Continue", "Sign out", "Pay bills", "Transfer money",
    "Collect & deposit center", "Save as accessible PDF", "Sep 30, 2026 Opens document menu",
    "Tax documents", "Notices and letters", "ATM and branch receipts", "",
]


@pytest.mark.parametrize("label", BUSINESS_WORDS)
def test_business_words_are_refused_by_every_guard(label):
    assert not site.is_safe_control(label)
    for guard in site.PRESS_GUARDS:
        assert not guard(label), (guard.__name__, label)


def test_the_six_shapes_are_taken_each_by_its_own_predicate():
    assert site.is_account_entry("BUS COMPLETE CHK (...4321)")
    assert site.is_account_entry("BUSINESS CARD (...8765), current selection")
    assert site.is_statements_accordion("Statements")
    assert site.is_year_picker("View:") and site.is_year_picker("2026") and site.is_year_picker("View: 2026")
    assert site.is_year_option("2025") and site.is_year_option("2019, you've reached the end of the list")
    assert site.is_save_menu("Sep 30, 2026 Opens menu to save document", "Sep 30, 2026")
    assert site.is_save_as_pdf("Save as PDF")
    for good in ("Statements", "Save as PDF", "2025", "Sep 30, 2026 Opens menu to save document",
                 "Bank Statement", "View Bank Statement", "Download statement"):
        assert site.is_safe_control(good), good


def test_each_predicate_takes_only_its_own_shape():
    assert not site.is_save_menu("Sep 30, 2026 Opens menu to save document", "Aug 31, 2026")
    assert not site.is_year_option("2026, current selection")
    assert not site.is_account_entry("Statements")
    assert not site.is_account_entry("PAY CARD (...4321)")
    assert not site.is_account_entry("BUS COMPLETE CHK (...4321) Transfer")
    assert not site.is_statements_accordion("Statements and Tax documents")
    assert not site.is_save_as_pdf("Save as PDF document")
    assert site.is_save_as_pdf("Save as PDF 1 of 2 items"), "live, the menu names the item's place"
    assert not site.is_save_as_pdf("Save as PDF 1 of 2 items Sign out")
    assert not site.is_save_as_pdf("Print 2 of 2 items")
    assert site.is_account_entry("INK BUSINESS REWARDS CARD (...4321)"), "a product word is no verb"


@pytest.mark.parametrize("bad", ["Sign out", "Stay signed in", "Pay card", "Wires & global transfers"])
@pytest.mark.parametrize("good", ["Save as PDF", "Statements", "2025",
                                  "Sep 30, 2026 Opens menu to save document"])
def test_mixed_labels_are_refused(good, bad):
    for joined in ("%s | %s" % (good, bad), "%s | %s" % (bad, good), "%s %s" % (good, bad)):
        assert not site.is_safe_control(joined)
        assert not any(g(joined) for g in site.PRESS_GUARDS)


def test_a_control_with_one_refused_label_is_not_pressed():
    page = center()
    page.labels_for = lambda css: ["Save as PDF", "Sign out"]
    with pytest.raises(site.ListStopped):
        site._press(page, page.locator("#item-0-0-downloadPDFOption"), css="#item-0-0-downloadPDFOption",
                    what="x", guard=site.is_save_as_pdf, step="save the statement", run=run())
    assert page.presses == []


def test_a_press_with_a_guard_not_of_the_six_is_refused():
    page = center()
    with pytest.raises(site.ListStopped):
        site._press(page, page.locator("#" + site.YEAR_PICKER_ID), css="#" + site.YEAR_PICKER_ID,
                    what="x", guard=site.is_safe_control, step="open the year picker", run=run())
    assert page.presses == []


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

def test_url_guards():
    assert site.is_safe_url(BASE + "/web/auth/dashboard")
    for bad in ("http://secure.chase.com/x", "https://www.chase.com/", "https://chase.com/",
                "https://analytics.chase.com/x", "https://secure.chase.com.evil.test/",
                "https://reco.chase.com/x", "https://sites.chase.com/x", ""):
        assert not site.is_safe_url(bad), bad
    assert site.is_docref_url(DOCREF)
    assert not site.is_docref_url(BASE + "/svc/rr/documents/secure/tax/v2/docref/list")
    assert not site.is_docref_url(BASE + "/svc/rr/documents/secure/idal/v4/docref/list")
    assert site.is_download_url(PDF_URL)
    assert not site.is_download_url(BASE + "/svc/rr/documents/secure/idal/v2/dockey/list")
    assert not site.is_download_url("https://www.chase.com/svc/rr/documents/secure/idal/v5/pdfdoc/star/list")
    assert site.is_documents_url(site.DOCUMENTS_URL)
    assert site.is_documents_url(site.DOCUMENTS_URL + ";accountId=1;mode=accounts")
    assert not site.is_documents_url(DASHBOARD)


# ---------------------------------------------------------------------------
# The account menu
# ---------------------------------------------------------------------------

def test_label_classification():
    assert site.account_class("BUS COMPLETE CHK (...4321)") == "Business Checking"
    assert site.account_class("PREMIER CHECKING (...4321), current selection") == "Business Checking"
    assert site.account_class("BUSINESS CARD (...8765)") == "Business Card"
    assert site.account_class("BUS TOTAL DEBIT (...5555)") == "Business Debit"
    assert site.account_class("ZEPHYRINA QUILLFEATHER (...8765)") == ""
    assert site.account_class("ZEPHYRINA CARDONA (...8765)") == "", "a surname is no product noun"
    assert site.account_class("BUS DEBIT CARD (...5555)") == "", "two nouns are not read"


def test_cardholder_entries_are_skipped_and_shared_tails_kept_apart():
    """The cardholder is listed under the card's own last four, as the
    survey found it, so it is passed over."""
    st = center().state()
    accts = site.accounts_of(st)
    assert [a.klass for a in accts] == ["Business Checking", "Business Card", "Business Debit"]
    assert [a.tail for a in accts] == ["4321", "8765", "5555"]
    assert len({a.ref for a in accts}) == 3


@pytest.mark.parametrize("label", ["INK BUSINESS CASH (...9876)", "ZEPHYRINA QUILLFEATHER (...9876)",
                                   "BUS DEBIT CARD (...1357)", "ZEPHYRINA QUILLFEATHER"])
def test_an_entry_that_names_no_class_and_is_no_cardholder_stops_the_list(label):
    """A card whose name carries no product word, an entry under no
    account's last four, one naming two classes, or one with no last four
    is not passed over, since the list would then be called whole
    without it."""
    page = center()
    page.accounts.append({"id": "bottom-7700123123", "label": label, "years": None})
    with pytest.raises(site.ListStopped):
        site.collect_download_docs(page, run())
    assert page.presses == []


def test_a_menu_that_reads_differently_twice_over_stops_the_list():
    """The menu is read again once the page settles, and an account drawn
    late is never left out of a list called whole."""
    page = center()
    reads = []

    def late_entry(st):
        reads.append(1)
        if len(reads) >= 2:
            st["menu"].append({"id": "bottom-7700123123", "labels": ["BUS SAVINGS CHK (...1212)"],
                               "all": "BUS SAVINGS CHK (...1212)", "shown": True})
    page.state_hook = late_entry
    with pytest.raises(site.ListStopped) as stop:
        site.collect_download_docs(page, run())
    assert "differently" in str(stop.value)
    assert page.presses == []


def test_a_menu_that_changes_between_accounts_stops_the_list():
    page = center()

    def drop_debit(p):
        if p.presses:
            p.accounts = [a for a in p.accounts if a["id"] != DEBIT["id"]]
    page.on_goto = drop_debit
    with pytest.raises(site.ListStopped) as stop:
        site.collect_download_docs(page, run())
    assert "changed during the listing" in str(stop.value)


def test_duplicate_labels_are_two_accounts_and_one_id_twice_stops():
    twin = {"menu": [{"id": "bottom-1001", "labels": [CHK["label"]], "shown": True},
                     {"id": "bottom-1002", "labels": [CHK["label"]], "shown": True}]}
    assert [a.ref for a in site.accounts_of(twin)] == ["bottom-1001", "bottom-1002"]
    with pytest.raises(site.ListStopped):
        site.accounts_of({"menu": [{"id": "bottom-1001", "labels": [CHK["label"]], "shown": True},
                                   {"id": "bottom-1001", "labels": [CARD["label"]], "shown": True}]})
    with pytest.raises(site.ListStopped):
        site.accounts_of({"menu": [{"id": "bottom-10 01", "labels": [CHK["label"]], "shown": True}]})


def test_every_menu_label_and_id_is_private_once_read():
    site.guard_menu(center().state())
    words = " ".join(site.private_words())
    for secret in ("ZEPHYRINA", "QUILLFEATHER", "7700112233", "bottom-7700990011", "4321"):
        assert secret in words
    assert "4321" not in site.redact("BUS COMPLETE CHK (...4321)")


# ---------------------------------------------------------------------------
# The page's own answers
# ---------------------------------------------------------------------------

def test_the_response_predicate():
    good = Response("7700112233", "CURRENT_YEAR", {})
    assert site.answers(good, "7700112233", "CURRENT_YEAR")
    assert not site.answers(good, "7700445566", "CURRENT_YEAR"), "another account"
    assert not site.answers(good, "770011", "CURRENT_YEAR"), "a prefix of the account"
    assert not site.answers(good, "7700112233", "CURRENT_YEAR_MINUS_1"), "another year"
    other = Response("7700112233", "CURRENT_YEAR", {}, url=BASE + "/svc/rr/documents/secure/idal/v4/docref/list")
    assert not site.answers(other, "7700112233", "CURRENT_YEAR"), "another path"
    got = Response("7700112233", "CURRENT_YEAR", {}, method="GET")
    assert not site.answers(got, "7700112233", "CURRENT_YEAR"), "not a POST"
    twice = Response("7700112233", "CURRENT_YEAR", {})
    twice.request.post_data += "&accountFilter=7700445566"
    assert not site.answers(twice, "7700112233", "CURRENT_YEAR"), "two accounts named"
    assert site.filter_for(2026, 2026) == "CURRENT_YEAR"
    assert site.filter_for(2026, 2019) == "CURRENT_YEAR_MINUS_7"


def test_two_answers_for_one_transition_stop_the_list():
    page = center()
    page.duplicate_answers = True
    with pytest.raises(site.ListStopped) as stop:
        site.collect_download_docs(page, run())
    assert "2 list answers" in str(stop.value)


def test_an_answer_for_another_account_is_not_taken():
    page = center()
    page.answer_override[("7700445566", "CURRENT_YEAR")] = \
        lambda: Response("7700112233", "CURRENT_YEAR", {"code": "SUCCESS",
                                                        "idaldocRefs": [item("DOCQXZ", "2026-01-31")]})
    with pytest.raises(site.ListStopped) as stop:
        site.collect_download_docs(page, run())
    assert "0 list answers" in str(stop.value)


def test_the_empty_year_baseline():
    r = run()
    empty = Response("1", "CURRENT_YEAR_MINUS_7", {"code": "SUCCESS"})
    # Held, not stopped, before any list has shown what success looks like.
    assert site.read_answer(empty, r, 2019) == []
    assert r.unproven()
    full = Response("1", "CURRENT_YEAR", {"code": "SUCCESS", "idaldocRefs": [item("DOCQX1", "2026-01-31")]})
    assert [i.date for i in site.read_answer(full, r, 2026)] == ["2026-01-31"]
    assert not r.unproven()
    assert site.read_answer(empty, r, 2019) == []
    with pytest.raises(site.ListStopped):
        site.read_answer(Response("1", "X", {"code": "ERROR"}), r, 2019)
    with pytest.raises(site.ListStopped):
        site.read_answer(Response("1", "X", {"message": "no code"}), r, 2019)
    with pytest.raises(site.ListStopped):
        site.read_answer(Response("1", "X", None, raw="<html>not json"), r, 2019)
    with pytest.raises(site.ListStopped):
        site.read_answer(Response("1", "X", {"code": "SUCCESS"}, status=500), r, 2019)
    with pytest.raises(site.ListStopped):
        site.read_answer(Response("1", "X", {"code": "SUCCESS", "idaldocRefs": []}), r, 2019)
    with pytest.raises(site.SessionExpired):
        site.read_answer(Response("1", "X", {"code": "SUCCESS"}, status=401), r, 2019)


def test_an_answer_is_validated_whole():
    r = run()
    for bad in ([item("DOCQX1", "2025-12-31")],            # outside the year asked for
                [item("DOCQX1", "2026-02-30")],            # no such day
                [item("DOCQX1", "2026-01-31", pages=0)],
                [item("DOCQX1", "2026-01-31", kind="Notice")],
                [item("DOCQX1", "2026-01-31"), item("DOCQX1", "2026-02-28")],
                [dict(item("DOCQX1", "2026-01-31"), documentId="")]):
        with pytest.raises(site.ListStopped):
            site.read_answer(Response("1", "CURRENT_YEAR", {"code": "SUCCESS", "idaldocRefs": bad}), r, 2026)


def test_an_empty_current_year_is_held_until_a_list_shows_success():
    """In January every account's current year is empty, and it is the first
    list read. It is held until the next year's statements show the code."""
    page = Center([dict(CHK, years={2026: [], 2025: [item("DOCQX1225", "2025-12-31")]}),
                   dict(CARD, years={2026: [], 2025: []})],
                  years=[2026, 2025])
    docs_ = site.collect_download_docs(page, run())
    assert [(d.account, d.date_text) for d in docs_] == [("Business Checking", "2025-12-31")]


def test_a_held_empty_year_with_another_code_stops_the_list():
    r = run()
    assert site.read_answer(Response("1", "CURRENT_YEAR", {"code": "PARTIAL"}), r, 2026) == []
    full = Response("1", "CURRENT_YEAR_MINUS_1",
                    {"code": "SUCCESS", "idaldocRefs": [item("DOCQX1", "2025-01-31")]})
    with pytest.raises(site.ListStopped) as stop:
        site.read_answer(full, r, 2025)
    assert "another code" in str(stop.value)


def test_a_listing_where_every_year_is_empty_stops():
    page = Center([dict(CHK, years={2026: [], 2025: []})], years=[2026, 2025])
    with pytest.raises(site.ListStopped) as stop:
        site.collect_download_docs(page, run())
    assert "every year read was empty" in str(stop.value)


# ---------------------------------------------------------------------------
# The rows, checked against the answer
# ---------------------------------------------------------------------------

def test_the_multiset_cross_check():
    items = [site.Item("A", "2026-09-30", "Statement", 2), site.Item("B", "2026-09-30", "Statement", 2)]
    row = {"cells": ["Sep 30, 2026", "Statement", "2 pages"]}
    assert site.rows_match(items, [row, row])
    assert not site.rows_match(items, [row])
    assert not site.rows_match(items[:1], [row, row])
    assert not site.rows_match(items[:1], [{"cells": ["Sep 30, 2026", "Statement", "3 pages"]}])
    assert not site.rows_match(items[:1], [{"cells": ["Sep 31, 2026", "Statement", "2 pages"]}])
    assert not site.rows_match(items[:1], [{"cells": ["Sept 30 2026", "Statement", "2 pages"]}])
    assert site.rows_match([], [])
    assert site.row_shape(["Feb 29, 2024", "Statement", "1 page"]) == ("2024-02-29", "Statement", 1)
    assert site.row_shape(["Feb 29, 2025", "Statement", "1 page"]) is None


def test_rows_that_differ_from_the_answer_stop_the_list():
    page = center()
    page.rows_override = [{"id": "accountsTable-STATEMENTS-row0", "shown": True,
                           "cells": ["Sep 30, 2026", "Statement", "2 pages"]}]
    with pytest.raises(site.ListStopped) as stop:
        site.collect_download_docs(page, run())
    assert "rows" in str(stop.value)


# ---------------------------------------------------------------------------
# Listing, whole
# ---------------------------------------------------------------------------

def test_discovery_lists_every_business_account_and_year():
    page = center()
    docs_ = site.collect_download_docs(page, run())
    got = sorted((d.account, d.date_text, d.page_count) for d in docs_)
    assert got == [("Business Card", "2026-09-28", 4),
                   ("Business Checking", "2025-12-31", 2),
                   ("Business Checking", "2026-08-31", 3),
                   ("Business Checking", "2026-09-30", 2)]
    # Chase mints a statement's documentId anew each session, so none is kept.
    assert all(d.document_id == "" for d in docs_)
    assert {d.title for d in docs_} == {"Business Checking Statement", "Business Card Statement"}
    assert all(d.href == "" for d in docs_)
    # The current year is never chosen again, the cardholder's entry never
    # pressed, and nothing but the six controls ever pressed.
    assert "#" + HOLDER["id"] not in page.presses
    assert not any(p == "#container-0-filterstyledselect-0" for p in page.presses)
    assert {p.split("-")[0] for p in page.presses} <= {"#bottom", "#header", "#container"}


def test_a_closed_accordion_is_opened_and_its_answer_taken():
    page = center()
    original = page.reset

    def closed():
        original()
        page.expanded = False
    page.reset = closed
    page.reset()
    docs_ = site.collect_download_docs(page, run())
    assert len(docs_) == 4
    assert page.presses.count("#" + site.STATEMENTS_ACCORDION_ID) >= 2


def test_a_document_center_that_never_draws_stops():
    page = center()
    page.on_goto = lambda p: setattr(p, "accounts", [])
    with pytest.raises(site.ListStopped):
        site.collect_download_docs(page, run())


def test_a_picker_year_that_is_not_this_year_stops():
    r = site.Run(today=lambda: site._date(2027, 1, 2))
    with pytest.raises(site.ListStopped):
        site.collect_download_docs(center(), r)


# ---------------------------------------------------------------------------
# Signed out
# ---------------------------------------------------------------------------

def test_the_timeout_dialog_is_session_expired_and_never_pressed():
    page = center()
    page.dialog = True
    with pytest.raises(site.SessionExpired):
        site.collect_download_docs(page, run())
    assert page.presses == []


def test_the_sign_in_page_is_session_expired():
    page = center()

    def to_logon(p):
        p.url = BASE + "/web/auth/#/logon/logon/chaseOnline"
    page.goto = lambda url, **kw: to_logon(page)
    with pytest.raises(site.SessionExpired):
        site.collect_download_docs(page, run())


def test_a_401_on_the_list_call_is_session_expired():
    page = center()
    page.answer_override[("7700445566", "CURRENT_YEAR")] = \
        lambda: Response("7700445566", "CURRENT_YEAR", {}, status=401)
    with pytest.raises(site.SessionExpired):
        site.collect_download_docs(page, run())


def test_a_dialog_that_opens_during_the_listing_is_session_expired():
    page = center()
    original = page.click

    def click(css):
        original(css)
        if css.startswith("#container-"):
            page.dialog = True
    page.click = click
    with pytest.raises(site.SessionExpired):
        site.collect_download_docs(page, run())


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def take(page, tmp_path, doc_id="DOCQX1225", iso="2025-12-31", pages=2, ref=CHK["id"], trace=None,
         r=None, name="out.pdf"):
    out = tmp_path / name
    dl = tmp_path / "dl"
    dl.mkdir(exist_ok=True)
    got = site.download_statement(page, r or run(), dl, out, account_ref=ref, document_id=doc_id,
                                  iso_date=iso, doc_type="Statement", page_count=pages,
                                  trace=trace if trace is not None else [])
    return got, out


def test_a_statement_is_taken_through_its_own_row(tmp_path):
    page = center()
    got, out = take(page, tmp_path)
    assert got and out.read_bytes()[:5] == b"%PDF-"
    assert page.presses[-2:] == ['#accountsTable-STATEMENTS-row0 a#header-accountsTable-STATEMENTS-row0-cell3-downloadDocumentDropdown',
                                 "#accountsTable-STATEMENTS-row0 #item-0-0-downloadPDFOption"]
    assert "#container-1-filterstyledselect-0" in page.presses


def test_binding_refuses_duplicates_and_missing_rows():
    items = [site.Item("A", "2026-09-30", "Statement", 2), site.Item("B", "2026-09-30", "Statement", 2)]
    row = {"id": "accountsTable-STATEMENTS-row0", "shown": True, "cells": ["Sep 30, 2026", "Statement", "2 pages"]}
    row1 = dict(row, id="accountsTable-STATEMENTS-row1")
    assert site.bind(items, [row, row1], "A", "2026-09-30", "Statement", 2) == -1, "two answered"
    assert site.bind(items[:1], [row, row1], "A", "2026-09-30", "Statement", 2) == -1, "two rows"
    assert site.bind(items[:1], [], "A", "2026-09-30", "Statement", 2) == -1, "no row"
    assert site.bind(items[:1], [dict(row, shown=False)], "A", "2026-09-30", "Statement", 2) == -1
    # The id is not compared: Chase mints it anew each session, so a
    # record's id from an earlier run names nothing now.
    assert site.bind(items[:1], [row], "Z", "2026-09-30", "Statement", 2) == 0, "another session's id"
    assert site.bind(items[:1], [row1], "A", "2026-09-30", "Statement", 2) == 1


def test_no_row_press_without_one_bound_row(tmp_path):
    years = chk_years()
    years[2026] = [item("DOCQX0926", "2026-09-30"), item("DOCQX0927", "2026-09-30")]
    page = Center([dict(CHK, years=years)], years=[2026, 2025])
    got, out = take(page, tmp_path, doc_id="DOCQX0926", iso="2026-09-30")
    assert not got and not out.exists()
    assert not any("download" in p for p in page.presses)


def test_an_account_gone_from_the_menu_presses_nothing(tmp_path):
    page = center()
    got, _out = take(page, tmp_path, ref="bottom-1234567")
    assert not got and page.presses == []


@pytest.mark.parametrize("change, reason", [
    ({"downloads_per_save": 2}, "more than one download came"),
    ({"download_url": "https://www.chase.com/x.pdf"}, "the download came from elsewhere"),
    ({"download_body": b"<html>signed out</html>"}, "the download was not a pdf"),
    ({"downloads_per_save": 0}, "no download came"),
])
def test_a_download_that_is_not_one_pdf_from_chase_stops_the_run(tmp_path, change, reason):
    page = center()
    for k, v in change.items():
        setattr(page, k, v)
    with pytest.raises(site.pressing.Stop) as stop:
        take(page, tmp_path)
    assert stop.value.reason == reason
    assert not (tmp_path / "out.pdf").exists()


def test_a_download_pending_before_the_press_stops_the_run(tmp_path):
    page = center()
    (tmp_path / "dl").mkdir()
    (tmp_path / "dl" / "x.pdf.crdownload").write_bytes(b"")
    with pytest.raises(site.pressing.Stop) as stop:
        take(page, tmp_path)
    assert stop.value.reason == "a download was pending"
    assert not any("download" in p for p in page.presses)


# ---------------------------------------------------------------------------
# The run's deadline
# ---------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_the_deadline_counts_from_the_first_press():
    clock = Clock()
    r = site.Run(clock=clock, today=lambda: site._date(CUR, 10, 12))
    assert not r.past_deadline()
    clock.t += 10 * 60
    assert not r.past_deadline(), "nothing pressed yet"
    r.pressed()
    clock.t += site.DEADLINE_S - 1
    r.pressed()
    assert not r.past_deadline()
    clock.t += 1
    assert r.past_deadline()


# ---------------------------------------------------------------------------
# The orchestrator
# ---------------------------------------------------------------------------

def make_app(tmp, *args):
    config = json.loads((HERE / "config.example.json").read_text())
    config.update({"owner": "Tester Example", "output_dir": str(Path(tmp) / "out"),
                   "min_pdf_bytes": 100, "delay_min_seconds": 0, "delay_max_seconds": 0})
    cfg = Path(tmp) / "config.json"
    cfg.write_text(json.dumps(config))
    app = docs.App(docs.build_parser().parse_args([*args, "--config", str(cfg)]))
    app.run = run()
    app._dl_dir = Path(tmp) / "out" / ".chasebusiness-downloads"
    app._dl_dir.mkdir(parents=True, exist_ok=True)
    return app


def attach(app, page):
    page.url = site.DOCUMENTS_URL
    app.page = lambda: page
    app._delay = lambda *a, **k: None


def test_records_carry_the_class_and_never_a_query(tmp_path):
    app = make_app(tmp_path, "--discover")
    attach(app, center())
    with redirect_stdout(io.StringIO()):
        app.cmd_discover()
    recs = list(app.discovery.data.values())
    assert len(recs) == 4
    for rec in recs:
        assert rec["account"] in ("Business Checking", "Business Card")
        assert rec["summary"] == "Bank Statement " + rec["account"]
        assert rec["href"] == "" and "?" not in rec["source_url"]
    told = docs.told_apart_by(recs[0])
    assert len(told) == 6 and told not in recs[0]["account_ref"]
    assert all(rec["document_id"] == "" for rec in recs)


def test_an_expired_session_stops_the_run_and_records_nothing(tmp_path):
    app = make_app(tmp_path, "--all")
    attach(app, center())
    with redirect_stdout(io.StringIO()):
        app.cmd_discover()
    page = center()
    page.dialog = True
    attach(app, page)
    app.check_session = lambda p: None
    with redirect_stdout(io.StringIO()) as said, pytest.raises(SystemExit):
        app.process(app._select())
    assert "signed out" in said.getvalue()
    assert app.progress.data == {}
    assert app.stats["manual_review"] + app.stats["failed"] == 0
    assert not list(app.paths.diagnostics.glob("*failure*"))


def test_an_expired_session_during_discovery_stops_the_run(tmp_path):
    app = make_app(tmp_path, "--discover")
    page = center()
    page.dialog = True
    attach(app, page)
    with redirect_stdout(io.StringIO()) as said, pytest.raises(SystemExit):
        app.cmd_discover()
    assert app.discovery.data == {}
    assert "signed out" in said.getvalue() and "Press Enter" not in said.getvalue()
    assert not list(app.paths.diagnostics.glob("*failure*"))


def test_the_deadline_stops_the_run_before_the_next_document(tmp_path):
    app = make_app(tmp_path, "--all")
    attach(app, center())
    with redirect_stdout(io.StringIO()):
        app.cmd_discover()
    clock = Clock()
    app.run = site.Run(clock=clock, today=lambda: site._date(CUR, 10, 12))
    app.run.pressed()
    clock.t += site.DEADLINE_S
    taken = []
    original = site.download_statement
    site.download_statement = lambda *a, **k: taken.append(1)
    try:
        with redirect_stdout(io.StringIO()) as said, pytest.raises(SystemExit):
            app.process(app._select())
    finally:
        site.download_statement = original
    assert taken == [] and app.progress.data == {}
    assert "minutes" in said.getvalue()


def test_pilot_gate():
    from paperpull_core.models import State

    class D:
        def __init__(self, k):
            self.key = k
    done = {"a": {"state": State.COMPLETED.value}}
    assert not docs.pilot_failed(done, [D("a")])
    assert docs.pilot_failed({"a": {"state": State.FAILED.value}}, [D("a")])
    assert docs.pilot_failed({}, [D("a")])
    assert docs.pilot_failed(done, [])


# ---------------------------------------------------------------------------
# Privacy, on the way that works and the ways that do not
# ---------------------------------------------------------------------------

@contextmanager
def everything_said():
    seen = io.StringIO()
    handler = logging.StreamHandler(seen)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    old = root.level
    root.setLevel(logging.DEBUG)
    try:
        with redirect_stdout(seen):
            yield seen
    finally:
        root.removeHandler(handler)
        root.setLevel(old)


def written(app) -> str:
    """Every file the run wrote but its two record stores, and every name."""
    out = []
    for p in app.paths.root.rglob("*"):
        out.append(p.name)
        if p.is_file() and p.suffix.lower() in (".log", ".json", ".csv", ".txt") \
                and p.name not in ("discovery.json", "progress.json") and "backup" not in str(p).lower():
            out.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(out)


def assert_private(text):
    for secret in SECRETS:
        assert secret.lower() not in text.lower(), secret


def test_nothing_private_leaves_on_a_run_that_works(tmp_path):
    app = make_app(tmp_path, "--all")
    with everything_said() as seen:
        attach(app, center())
        app.cmd_discover()
        app.check_session = lambda p: None
        app.process(app._select())
    assert app.stats["statements"] == 4, seen.getvalue()
    names = [p.name for p in app.paths.root.rglob("*.pdf")]
    assert len(names) == 4 and any("Business Checking" in n for n in names)
    assert_private(seen.getvalue() + written(app))


def test_nothing_private_leaves_on_runs_that_stop(tmp_path):
    app = make_app(tmp_path, "--all")
    traces = []
    with everything_said() as seen:
        attach(app, center())
        app.cmd_discover()
        app.check_session = lambda p: None
        # A list that stops on the way to the statement.
        bad = center()
        bad.rows_override = []
        attach(app, bad)
        app.process(app._select(limit=1))
        # A download that is not a PDF stops the run.
        worse = center()
        worse.download_body = b"<html>BUS COMPLETE CHK (...4321)</html>"
        attach(app, worse)
        try:
            app.process(app._select(limit=2))
        except site.pressing.Stop as stop:
            traces.append(" ".join(stop.lines) + str(stop.facts))
            try:
                site.pressing.stop_run(app, stop)
            except SystemExit:
                pass
        # A discovery that stops partway.
        third = center()
        third.answer_override[("7700445566", "CURRENT_YEAR")] = \
            lambda: Response("7700445566", "CURRENT_YEAR", None, raw="{not json")
        attach(app, third)
        try:
            app.cmd_discover()
        except SystemExit:
            pass
        trace = []
        take(center(), tmp_path, doc_id="DOCQXNOPE", trace=trace)
        traces.append(json.dumps(trace))
    assert app.stats["manual_review"] >= 1
    assert_private(seen.getvalue() + written(app) + " ".join(traces))


# ---------------------------------------------------------------------------
# What the site layer presses, read from its source
# ---------------------------------------------------------------------------

SITE = ast.parse((HERE / "chasebusiness_site.py").read_text(encoding="utf-8"))
SIX = {"is_account_entry", "is_statements_accordion", "is_year_picker", "is_year_option",
       "is_save_menu", "is_save_as_pdf"}


def test_the_site_layer_presses_nothing_itself():
    called = {n.func.attr for n in ast.walk(SITE)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for verb in ("click", "dblclick", "tap", "fill", "type", "press", "select_option", "check",
                 "uncheck", "set_input_files", "dispatch_event", "press_sequentially", "hover",
                 "focus", "set_checked", "drag_to", "press_once"):
        if verb == "click":
            continue
        assert verb not in called, verb
    attrs = {n.attr for n in ast.walk(SITE) if isinstance(n, ast.Attribute)}
    assert not attrs & {"mouse", "keyboard", "touchscreen"}
    assert not [k for k in ast.walk(SITE) if isinstance(k, ast.keyword) and k.arg == "force"]
    assert "dismiss_timeout" not in {f.name for f in ast.walk(SITE) if isinstance(f, ast.FunctionDef)}


def test_the_one_press_is_the_cores_inside_press_and_every_press_names_one_of_six():
    clicks = []
    for fn in [n for n in ast.walk(SITE) if isinstance(n, ast.FunctionDef)]:
        for c in ast.walk(fn):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "click":
                clicks.append((fn.name, ast.unparse(c.func.value)))
    assert clicks == [("_press", "pressing"), ("_press_row", "pressing")]
    guards = {"_press": [], "_press_row": []}
    for c in ast.walk(SITE):
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in guards:
            g = [k.value for k in c.keywords if k.arg == "guard"]
            assert len(g) == 1 and isinstance(g[0], ast.Name), ast.unparse(c)
            guards[c.func.id].append(g[0].id)
    # The four that reach a list stop the run when turned off, and the two a
    # statement's own row carries leave that statement.
    assert sorted(guards["_press"]) == sorted(SIX - {"is_save_menu", "is_save_as_pdf"})
    assert sorted(guards["_press_row"]) == ["is_save_as_pdf", "is_save_menu"]
    assert {g.__name__ for g in site.PRESS_GUARDS} == SIX
    # The JavaScript handed to the page reads; it presses nothing.
    for js in (site._STATE_JS, site._LABELS_JS):
        assert ".click" not in js and "dispatchEvent" not in js and ".submit" not in js


# ---------------------------------------------------------------------------
# After review: the menu's own option, late downloads, hidden nodes, waits
# ---------------------------------------------------------------------------

def test_the_option_inside_the_menu_the_row_owns_is_taken(tmp_path):
    page = center()
    page.option_place = "menu"
    got, _out = take(page, tmp_path)
    assert got and page.presses[-1] == '[id="menu-shared"] #item-0-0-downloadPDFOption'


@pytest.mark.parametrize("place", ["detached", "other_row", "shared_two_expanded",
                                   "menu_not_expanded", "wrong_trigger", "row_collapsed"])
def test_a_save_as_pdf_not_the_bound_menus_own_is_never_pressed(tmp_path, place):
    page = center()
    page.option_place = place
    with pytest.raises(site.pressing.Stop) as stop:
        take(page, tmp_path)
    assert stop.value.reason == "the save menu showed no save as pdf of its own"
    assert not any(p.endswith("downloadPDFOption") for p in page.presses)
    assert page.downloads == []


def test_a_second_download_that_comes_late_stops_the_next_statement(tmp_path):
    page = center()
    page.late_download = True
    r = run()
    got, _out = take(page, tmp_path, r=r)
    assert got and r.untied() == 0
    before = list(page.presses)
    with pytest.raises(site.pressing.Stop) as stop:
        take(page, tmp_path, doc_id="DOCQX0926", iso="2026-09-30", r=r, name="next.pdf")
    assert stop.value.reason == "a download came that no press took"
    assert not any("download" in p for p in page.presses[len(before):])
    assert not (tmp_path / "next.pdf").exists()


def test_a_failed_capture_says_no_file_name(tmp_path, monkeypatch, caplog):
    """The core's capture names the exception's kind only: a filesystem
    error carries the browser's suggested name, with the account's tail."""
    import paperpull_core.capture as capture

    def no_save(self, path):
        raise OSError("cannot write %s" % self.suggested_filename)

    def no_move(src, dst):
        raise OSError("cannot move %s" % src)
    monkeypatch.setattr(Download, "save_as", no_save)
    monkeypatch.setattr(capture.shutil, "move", no_move)
    page = center()
    page.folder_copy = tmp_path / "dl"
    (tmp_path / "dl").mkdir()
    caplog.set_level(logging.DEBUG)
    with pytest.raises(site.pressing.Stop) as stop:
        take(page, tmp_path)
    assert stop.value.reason == "the download was not a pdf"
    assert "OSError" in caplog.text
    assert "4321" not in caplog.text and "STATEMENTS-4321" not in caplog.text


def _hide(kind):
    def hook(st):
        if kind == "hidden statements":
            for a in st["accordions"]:
                if a["id"] == site.STATEMENTS_ACCORDION_ID:
                    a["shown"] = False
        elif kind == "two statements":
            st["accordions"].append(dict(st["accordions"][0]))
        elif kind == "hidden entry":
            for e in st["menu"]:
                e["shown"] = False
        elif kind == "two entries":
            st["menu"].append(dict(st["menu"][0]))
    return hook


@pytest.mark.parametrize("kind", ["hidden statements", "two statements", "hidden entry", "two entries"])
def test_hidden_or_doubled_nodes_never_stand_for_the_page(kind):
    page = center()
    page.state_hook = _hide(kind)
    with pytest.raises(site.ListStopped):
        site.collect_download_docs(page, run())
    assert page.presses == []


def test_a_hidden_entry_is_never_the_current_account():
    page = center()
    st = page.state()
    st["menu"][0]["shown"] = False
    assert site._entry(st, CHK["id"]) is None
    with pytest.raises(site.ListStopped):
        site.accounts_of(st)


def test_the_timeout_dialog_during_the_download_settle_is_session_expired(tmp_path):
    page = center()

    def dialog_on_settle(p, ms):
        if ms == site.SECOND_DOWNLOAD_WAIT_MS and p.downloads:
            p.dialog = True
    page.on_wait = dialog_on_settle
    with pytest.raises(site.SessionExpired):
        take(page, tmp_path)
    assert not (tmp_path / "out.pdf").exists()


def test_the_timeout_dialog_during_the_load_settle_is_session_expired():
    page = center()

    def dialog_on_settle(p, ms):
        if ms == site.SETTLE_MS:
            p.dialog = True
    page.on_wait = dialog_on_settle
    with pytest.raises(site.SessionExpired):
        site.collect_download_docs(page, run())
    assert page.presses == []


def test_a_record_key_never_holds_the_statement_id(tmp_path):
    app = make_app(tmp_path, "--discover")
    attach(app, center())
    with redirect_stdout(io.StringIO()):
        app.cmd_discover()
    for key, rec in app.discovery.data.items():
        assert rec["document_id"] == "" and rec["account_ref"] not in key
        assert "DOCQX" not in key and "DOCQX" not in json.dumps(rec)
        assert key.startswith("id:" + rec["account"] + ":")


def _reminted(years):
    """The same statements with the documentIds another session mints."""
    return {y: [dict(i, documentId="NEW" + i["documentId"]) for i in items]
            for y, items in years.items()} if years else years


def test_a_second_discover_with_new_statement_ids_keeps_one_record_each(tmp_path):
    """Live, Chase gave every statement a new documentId on the next
    session, and keying records by it doubled every one. The same
    statements under new ids are the same records."""
    app = make_app(tmp_path, "--discover")
    attach(app, center())
    with redirect_stdout(io.StringIO()):
        app.cmd_discover()
    first = dict(app.discovery.data)
    assert len(first) == 4
    again = Center([dict(CHK, years=_reminted(chk_years())),
                    dict(CARD, years=_reminted({2026: [item("DOCQXC0926", "2026-09-28", 4)]})),
                    dict(DEBIT, years=None), dict(HOLDER, years=None)],
                   years=[2026, 2025, 2024])
    attach(app, again)
    with redirect_stdout(io.StringIO()):
        app.cmd_discover()
    assert sorted(app.discovery.data) == sorted(first)


# ---------------------------------------------------------------------------
# The timeout dialog as Chase draws it: always in the page, open only when
# shown. Read in a real browser, since what decides is the page's own box.
# ---------------------------------------------------------------------------

# A web component like Chase's mds-dialog: its host draws no box of its own
# (display: contents) and its panel, in an open shadow root, is fixed on
# screen while the host carries open. Every word here is made up.
MDS_DIALOG = ("<script>customElements.define('mds-dialog', class extends HTMLElement {"
              " constructor() { super(); this.attachShadow({mode: 'open'}).innerHTML ="
              " '<style>:host { display: contents } #panel { display: none; position: fixed;"
              " top: 10px; left: 10px; width: 300px; height: 200px }"
              " :host([open]) #panel, :host([data-drawn]) #panel { display: block }"
              " :host([data-see-through]) #panel { opacity: 0 }"
              " :host([data-hidden]) #panel { visibility: hidden }</style>"
              "<div id=\\'panel\\'>Your session is about to end</div>'; } });</script>")


def _dialog(attrs: str) -> str:
    return "<mds-dialog id='octagonSessionTimeoutDialogue' %s></mds-dialog>" % attrs


DIALOG_PAGES = {
    "closed": (_dialog(""), False),
    "closed though its panel is drawn": (_dialog("data-drawn"), False),
    "open is false": (_dialog("open='false' data-drawn"), False),
    "open with a see-through panel": (_dialog("open data-see-through"), False),
    "open with a hidden panel": (_dialog("open data-hidden"), False),
    "open with its panel drawn in the shadow root": (_dialog("open"), True),
}


@pytest.mark.parametrize("shape", sorted(DIALOG_PAGES))
def test_only_a_timeout_dialog_drawn_open_is_session_expired(shape):
    sync_api = pytest.importorskip("playwright.sync_api")
    html, expired = DIALOG_PAGES[shape]
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content("<!doctype html><meta charset='utf-8'><title>Statements and Documents</title>"
                                 "<a id='bottom-1001' href='javascript:void(0)'>BUS COMPLETE CHK (...4321)</a>"
                                 + MDS_DIALOG + html)
                assert page.evaluate("() => !!document.getElementById("
                                     "'octagonSessionTimeoutDialogue').shadowRoot")
                assert site._timeout_dialog_open(page) is expired
                assert bool(page.evaluate(site._STATE_JS)["dialog"]) is expired
                if expired:
                    with pytest.raises(site.SessionExpired):
                        site.check_signed_in(page)
                else:
                    site.check_signed_in(page)
            finally:
                browser.close()
    except sync_api.Error as e:
        pytest.skip("no browser here (%s)" % type(e).__name__)


def test_the_ready_check_takes_the_live_page_shape():
    """The document center as the live probe found it: four menu entries,
    one of them current, and the Statements accordion shown."""
    page = center()
    page.goto(site.DOCUMENTS_URL)
    assert site._ready(page.state())
    assert len(page.state()["menu"]) == 4


def test_the_year_picker_is_known_by_its_live_label():
    """Live, the picker's aria-label reads " View:: 2026", two colons."""
    assert site.is_year_picker(" View:: 2026")
    assert site.is_year_picker("View: 2026") and site.is_year_picker("2026")
    assert not site.is_year_picker("View:: Pay card")


def test_the_pickers_inner_parts_are_not_read_as_years():
    """Live, every year option has primary and secondary inner nodes with
    ids of their own; the secondary one has no words. Only the option's own
    node counts."""
    state = {"options": [
        {"id": "container-0-filterstyledselect-0", "shown": True, "labels": ["2026, current selection"]},
        {"id": "container-primary-0-filterstyledselect-0", "shown": True, "labels": ["2026"]},
        {"id": "container-secondary-0-filterstyledselect-0", "shown": True, "labels": []},
        {"id": "container-1-filterstyledselect-0", "shown": True, "labels": ["2025 , you've reached the end of the menu"]},
        {"id": "container-primary-1-filterstyledselect-0", "shown": True, "labels": ["2025"]},
        {"id": "container-secondary-1-filterstyledselect-0", "shown": True, "labels": []},
    ]}
    got = site._options(state)
    assert [(y, cur) for y, _css, cur in got] == [(2026, True), (2025, False)]


def test_the_last_year_in_the_menu_reads_with_its_end_of_menu_words():
    assert site.is_year_option("2019 , you've reached the end of the menu")
    assert site.is_year_option("2019, you\u2019ve reached the end of the list")
    assert site.is_year_option("2025")
    assert not site.is_year_option("2025, current selection")
    state = {"options": [{"id": "container-7-filterstyledselect-0", "shown": True,
                          "labels": ["2019 , you've reached the end of the menu"]}]}
    assert site._options(state) == [(2019, "#container-7-filterstyledselect-0", False)]


def test_the_current_year_reads_with_a_space_before_its_comma_too():
    state = {"options": [{"id": "container-0-filterstyledselect-0", "shown": True, "labels": ["2026 , current selection"]},
                         {"id": "container-1-filterstyledselect-0", "shown": True, "labels": ["2025, you've reached the end of the list"]}]}
    assert site._options(state) == [(2026, "#container-0-filterstyledselect-0", True), (2025, "#container-1-filterstyledselect-0", False)]


def test_the_center_is_ready_without_a_statements_accordion_when_another_shows():
    state = {"menu": [{"id": "bottom-1", "shown": True, "labels": ["BUS TOTAL DEBIT (...1234)"]}],
             "accordions": [{"id": "ATM_RECEIPTS", "shown": True, "expanded": "false"}]}
    assert site._ready(state)
    assert not site._ready({"menu": state["menu"], "accordions": []})
    assert not site._ready({"menu": [], "accordions": state["accordions"]})


def test_a_closed_menus_option_with_a_box_but_no_words_is_not_shown():
    """Live, every row's Save as PDF exists and has a box even while its menu
    is closed; only the open menu's option carries words."""
    assert "innerText" in site._MENU_JS and "getBoundingClientRect" in site._MENU_JS


def test_the_words_span_inside_a_save_as_pdf_is_not_counted_as_a_second_option():
    """Live, the option anchor item-0-N-downloadPDFOption holds a span
    itemLink-0-N-downloadPDFOption; both end alike, one option is shown."""
    assert """a[id^="item-"][id$="-downloadPDFOption"]""" in site._MENU_JS


def test_the_save_menu_is_named_by_its_exact_id_before_any_press():
    """A lookalike anchor, header-legacy-…-row0-cell3-downloadDocumentDropdown
    with the same words, must never be pressed: the selector names the one
    exact id up front, not a suffix."""
    import inspect
    src = inspect.getsource(site._catch_statement)
    assert 'a#header-accountsTable-STATEMENTS-row%d-cell3-downloadDocumentDropdown' in src
    assert 'a[id^="header-"][id$="-row%d-cell3-downloadDocumentDropdown"]' not in src


# ---------------------------------------------------------------------------
# After the maintainer's review: turned off row controls, the PDF's last
# four, the picker's end, the account digest
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["downloadDocumentDropdown", "downloadPDFOption"])
def test_a_turned_off_row_control_leaves_its_statement_and_the_run_goes_on(tmp_path, monkeypatch,
                                                                          which):
    page = center()

    def click(pg, locator, *, css, what, words, step="press a control", **kw):
        if which in css:
            raise site.pressing.TurnedOff(step, "the control is turned off", ["turned off"])
        pg.click(css)
    monkeypatch.setattr(site.pressing, "click", click)
    trace: list = []
    got, out = take(page, tmp_path, trace=trace)
    assert got is False and not out.exists()
    assert page.downloads == []
    assert any("turned off" in str(t.get("note")) for t in trace)


def test_a_turned_off_list_control_stops_the_run(monkeypatch):
    page = center()

    def click(pg, locator, *, css, what, words, step="press a control", **kw):
        if css.startswith("#container-"):
            raise site.pressing.TurnedOff(step, "the control is turned off", ["turned off"])
        pg.click(css)
    monkeypatch.setattr(site.pressing, "click", click)
    with pytest.raises(site.pressing.TurnedOff):
        site.collect_download_docs(page, run())


def test_a_pdf_that_does_not_print_the_accounts_last_four_is_not_kept(tmp_path):
    page = center()
    page.download_body = _pdf("9999")
    trace: list = []
    got, out = take(page, tmp_path, trace=trace)
    assert got is False and not out.exists()
    assert {"note": "the pdf does not print the account's last four"} in trace
    page = center()
    page.download_body = _pdf("4321")
    got, out = take(page, tmp_path, name="kept.pdf")
    assert got is True and out.exists()


def test_the_last_four_must_end_an_account_number():
    assert site.names_tail("Account Number: 000000004321", "4321")
    assert site.names_tail("Account Number:\n \n 000000004321\nPAGE", "4321")
    assert site.names_tail("Card ending XXXX XXXX XXXX 4321.", "4321")
    assert site.names_tail("Account ****-****-4321", "4321")
    assert not site.names_tail("Account Number: 000043210", "4321")
    # The same four digits ending something that is not an account number.
    for other in ("Call 1-800-555-4321", "Check 4321 paid", "Ref 1234321", "BUS COMPLETE CHK (...4321)",
                  "Total 12,344,321.00", "ID A1234321"):
        assert not site.names_tail(other, "4321"), other
    assert not site.names_tail("", "4321")
    assert not site.names_tail("Account Number: 4321", "")


def _options_state(labels):
    return {"options": [{"id": "container-%d-filterstyledselect-0" % i, "labels": [t], "shown": True}
                        for i, t in enumerate(labels)]}


def test_the_oldest_year_must_carry_the_end_of_menu_words():
    end = ", you've reached the end of the menu"
    good = site._options(_options_state(["2026 , current selection", "2025", "2024" + end]))
    assert [y for y, _c, _cur in good] == [2026, 2025, 2024]
    assert site._options(_options_state(["2026", "2025 , current selection" + end]))[1][2] is True
    for labels in (["2026 , current selection", "2025", "2024"],            # no end words
                   ["2026 , current selection", "2025" + end, "2024"],      # on another year
                   ["2026" + end, "2025", "2024" + end]):                   # on two
        with pytest.raises(site.ListStopped):
            site._options(_options_state(labels))


def test_the_account_digest_is_its_own_field_and_survives_without_the_ref():
    d = docs.Document(title="Business Checking Statement", account="Business Checking",
                      date="2026-09-30", account_ref=CHK["id"], doc_type="Statement")
    assert d.account_digest == docs.account_digest_of(CHK["id"]) and len(d.account_digest) == 12
    assert d.key.endswith(":" + d.account_digest)
    rec = d.to_dict()
    moved = {k: v for k, v in rec.items() if k not in ("account_ref", "document_id")}
    assert docs.Document.from_dict(moved).key == d.key
    assert docs.told_apart_by(moved) == docs.told_apart_by(rec) == d.account_digest[:6]
