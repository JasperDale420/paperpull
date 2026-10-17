"""Invest529: the guard, the host allowlist, the date and title shapes, the
row building for both the statement and 1099-Q APIs, and the in-page fetch's
host check. Invented values only - never a real account's."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # binds this provider's AppSpec
import invest529_site as site
from paperpull_core import doc_types


def test_the_folders_and_categories_are_what_the_index_expects():
    assert storage.SPEC.provider == "Invest529"
    assert [f.name for f in storage.SPEC.folders
            if not f.name.startswith(("Backups", "Logs", "Diagnostics", "Manual"))] == \
        ["Statements", "Tax Documents", "Other Documents"]
    assert storage.SPEC.routes[storage.STATEMENT] == "statements"
    assert storage.SPEC.routes[storage.TAX] == "tax_documents"
    assert storage.SPEC.config_defaults["document_types"] == ["Statement", "Tax Document"]


@pytest.mark.parametrize("title,category,summary", [
    ("Quarterly Statement", "Statement", "Quarterly Statement"),
    ("Annual Statement", "Statement", "Annual Statement"),
    ("Monthly Statement", "Statement", "Monthly Statement"),
    ("1099-Q", "Tax Document", "1099-Q Tax Form"),
])
def test_known_invest529_titles_classify(title, category, summary):
    rules = doc_types.load_rules()
    got = doc_types.classify_document(title, rules)
    assert (got[0], got[1]) == (category, summary)


def test_dates_are_read_as_iso_and_a_non_date_is_rejected():
    assert site.parse_date("2026-09-30") == "2026-09-30"
    assert site.parse_date("2026-09-30T00:00:00") == "2026-09-30"
    assert site.parse_date("2026-02-30") == ""      # a day that cannot exist
    assert site.parse_date("yesterday") == ""


def test_moving_money_and_changing_the_account_are_never_safe():
    for name in ["Contribute", "Withdraw Funds", "Request a distribution",
                 "Transfer", "Move Funds", "Change Investments or Move Funds",
                 "Change beneficiary", "Roll Over Funds to Roth IRA",
                 "Cancel an Account", "Open an Account", "Update Bank Information",
                 "Log out", "Exit", "Change password", "Go paperless",
                 "Gift", "Manage Other Individuals"]:
        assert not site.is_safe_control(name), name


def test_reading_a_document_is_safe():
    for name in ["Download", "View", "View Statement", "Account Statements",
                 "Tax Information", "1099-Q", "Download PDF", "Quarterly Statement"]:
        assert site.is_safe_control(name), name


def test_a_verb_stem_inside_another_word_is_not_a_refusal():
    assert site.is_safe_control("Credit statement")      # "edit" inside Credit
    assert not site.is_safe_control("Exchange")           # a move-money word on its own


def test_only_invest529_com_and_its_subdomains_are_allowed():
    assert site.is_safe_url("https://myaccount.invest529.com/pls/prod/hwtkstmt.p_renderpage_account_statement")
    assert site.is_safe_url("https://factory.invest529.com/accounts/statements")
    assert site.is_safe_url("https://login.invest529.com/oauth2/default/v1/authorize")
    assert not site.is_safe_url("http://myaccount.invest529.com/")      # not https
    assert not site.is_safe_url("https://invest529.com.example/")
    assert not site.is_safe_url("https://notinvest529.com/")
    assert not site.is_safe_url("https://user:pw@factory.invest529.com/")


def test_the_documents_page_is_a_signed_in_banner_page_not_the_login():
    class SimpleLocator:
        def count(self): return 0

    class P:
        url = "https://myaccount.invest529.com/pls/prod/hwtkstmt.p_renderpage_account_statement#/"
        def locator(self, *_): return SimpleLocator()
    assert site.on_documents_page(P())
    P.url = "https://myaccount.invest529.com/pls/prod/hwtk1099.P_RenderPage1099q#/"
    assert site.on_documents_page(P())
    P.url = "https://login.invest529.com/oauth2/default/v1/authorize"
    assert not site.on_documents_page(P())      # a sign-in page
    P.url = "https://www.invest529.com/"
    assert not site.on_documents_page(P())      # the marketing site


# -- row building -------------------------------------------------------------

OWNERS = [{
    "ownerPidm": "1000001", "ownerName": "Pat Owner", "isMyAccount": True,
    "beneficiaries": [{
        "beneficiaryPidm": "2000002", "beneficiaryName": "Sam Student",
        "accounts": [{
            "accountType": "VS", "accountDescription": "Invest529 Account",
            "minSelectDate": "2023-12-28",
            "officialStatements": [
                {"seqNo": "9000001", "startDate": "2026-07-01", "endDate": "2026-09-30"},
                {"seqNo": "9000002", "startDate": "2026-04-01", "endDate": "2026-06-30"},
            ]}]}]}]

TAX_ROW = {"taxYear": "2025", "accountNumber": "20250000000000",
           "correctionIndicator": "", "identifierText": "Original"}


def test_a_statement_row_carries_beneficiary_period_and_what_the_download_needs():
    rows = site._statement_rows(OWNERS)
    q3 = next(r for r in rows if r["date"] == "2026-09-30")
    assert q3["title"] == "Quarterly Statement"           # ~91 days
    assert q3["account"] == "Sam Student" and q3["category"] == "Statement"
    assert q3["seqNo"] == "9000001" and q3["endDate"] == "2026-09-30"
    assert q3["beneficiaryPidm"] == "2000002" and q3["ownerPidm"] == "1000001"
    assert q3["item_id"] == "9000001" and q3["client_id"] == "statement|2000002|1000001"
    assert q3["href"] == site.API["statement"] and q3["period_start"] == "2026-07-01"


def test_statement_rows_sort_stably_so_occurrence_numbers_hold():
    rows = site._statement_rows(OWNERS)
    assert [r["date"] for r in rows] == ["2026-06-30", "2026-09-30"]


def test_a_1099_row_files_on_year_end_and_carries_the_account():
    r = site._tax_row(TAX_ROW)
    assert r["kind"] == "tax" and r["title"] == "1099-Q" and r["category"] == "Tax Document"
    assert r["date"] == "2025-12-31" and r["taxYear"] == "2025"
    assert r["accountNumber"] == "20250000000000" and r["correctionIndicator"] == "null"
    assert r["item_id"] == "20250000000000" and r["client_id"] == "tax|null"
    assert r["href"].endswith("/customers/1099/2025/20250000000000/null")


def test_collect_honours_wanted_kinds_and_the_year_scope(monkeypatch):
    monkeypatch.setattr(site, "list_statements", lambda page: OWNERS)
    monkeypatch.setattr(site, "list_1099", lambda page: [TAX_ROW])
    # both kinds, 2026 only -> two statements, the 2025 tax form dropped
    rows = site.collect_documents(None, keep=lambda y: y == "2026",
                                  config={"document_types": ["Statement", "Tax Document"]})
    assert sorted(r["category"] for r in rows) == ["Statement", "Statement"]
    # both kinds, no scope -> two statements + the tax form
    rows = site.collect_documents(None, config={"document_types": ["Statement", "Tax Document"]})
    assert [r["category"] for r in rows].count("Tax Document") == 1
    # statements only -> the tax form is never asked for
    rows = site.collect_documents(None, config={"document_types": ["Statement"]})
    assert all(r["category"] == "Statement" for r in rows)


# -- download -----------------------------------------------------------------

def test_fetch_statement_posts_the_official_body(monkeypatch):
    seen = {}

    def fake_call(page, url, method="GET", body=None, want="json"):
        seen.update(url=url, method=method, body=body, want=want)
        return b"%PDF-1.4 ok"
    monkeypatch.setattr(site, "_call", fake_call)
    out = site.fetch_statement_pdf(None, "9000001", "2026-09-30", "2000002", "1000001")
    assert out == b"%PDF-1.4 ok"
    assert seen["url"] == site.API["statement"] and seen["method"] == "POST" and seen["want"] == "bytes"
    assert seen["body"] == {"seqNo": "9000001", "requestType": "official",
                            "endDate": "2026-09-30", "beneficiaryPidm": "2000002",
                            "ownerPidm": "1000001"}


def test_fetch_1099_gets_the_year_account_correction_path(monkeypatch):
    seen = {}

    def fake_call(page, url, method="GET", body=None, want="json"):
        seen.update(url=url, method=method, want=want)
        return b"%PDF-1.4 ok"
    monkeypatch.setattr(site, "_call", fake_call)
    out = site.fetch_1099_pdf(None, "2025", "20250000000000", "null")
    assert out == b"%PDF-1.4 ok"
    assert seen["url"].endswith("/customers/1099/2025/20250000000000/null")
    assert seen["method"] == "GET" and seen["want"] == "bytes"


def test_download_resolves_fresh_and_refuses_a_non_pdf(monkeypatch, tmp_path):
    fresh = {"kind": "statement", "seqNo": "fresh", "endDate": "2026-09-30",
             "beneficiaryPidm": "2000002", "ownerPidm": "1000001"}
    monkeypatch.setattr(site, "resolve_item", lambda *a, **k: fresh)
    seen = {}

    def fake_fetch_row(page, row):
        seen["row"] = row
        return b"%PDF-1.4 ok"
    monkeypatch.setattr(site, "_fetch_row", fake_fetch_row)
    out = tmp_path / "x.pdf"
    assert site.download_document(None, "Quarterly Statement", "2026-09-30", out,
                                  item_hint="stale", client_hint="statement|a|b",
                                  account="Sam Student")
    assert seen["row"]["seqNo"] == "fresh" and out.read_bytes().startswith(b"%PDF")

    monkeypatch.setattr(site, "_fetch_row", lambda page, row: b"<html>Forbidden</html>")
    bad = tmp_path / "y.pdf"
    assert not site.download_document(None, "Quarterly Statement", "2026-09-30", bad,
                                      account="Sam Student")
    assert not bad.exists()


def test_the_in_page_fetch_only_ever_goes_to_an_invest529_host():
    assert "host.endsWith('.invest529.com')" in site._FETCH_JS
    with pytest.raises(ValueError):
        site._call(None, "https://evil.example/x")


@pytest.mark.parametrize("status", [401, 403])
def test_a_sign_in_or_forbidden_answer_stops_the_run(status):
    class P:
        def evaluate(self, js, arg): return {"status": status, "json": None}
    with pytest.raises(site.SessionExpired):
        site._call(P(), site.API["statements"])


def test_the_survey_follows_only_exact_document_words():
    assert site.SURVEY_LINK_RE.match("Account Statements")
    assert site.SURVEY_LINK_RE.match("Statements")
    assert site.SURVEY_LINK_RE.match("Tax Information")
    assert not site.SURVEY_LINK_RE.match("Contribute")
    assert not site.SURVEY_LINK_RE.match("Transfer money")


def test_account_numbers_are_masked_in_the_survey():
    assert site.redact("acct 123456789 view") == "acct ######### view"


def test_json_bodies_are_recorded_as_shape_not_values():
    shape = site._shape({"accounts": [{"number": "123456789", "balance": 12.5}]})
    assert shape == {"accounts": ["1 item(s)", {"number": "string", "balance": "number"}]}
