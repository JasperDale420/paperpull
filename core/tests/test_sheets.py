"""paperpull_core.sheets, the rule that keeps text a spreadsheet tool writes
from becoming a formula, and the census that every tool writing a
spreadsheet for a person goes through it. The write paths of the two tools
are tested in test_export_purchases.py and test_export_transactions.py.

The same rule for the apps' own CSV files, which are read back as well, so
every value has to read back as it was written, and the census that every
reader and writer of a CSV in the repository goes through it. CsvFile is
tested in test_csv.py and every app's own write, read and Rename in
test_every_app_keeps_its_csv_text_as_text.py. Every value here is made up."""
import ast
import csv
import importlib.util
import itertools
import os
import random
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from paperpull_core.sheets import (csv_value, keep_text, read_stored, read_stored_row,
                                   starts_a_formula, store_value)

TOOLS = Path(__file__).resolve().parents[2] / "tools"

FORMULAS = [
    "=1+1", "+1+1", "-1+1", "@SUM(1,1)",
    '=HYPERLINK("https://example.invalid/?"&A1,"open")',
    "\t=1+1", "\r=1+1", "\n=1+1", "\tplain words after a tab",
    " =1+1", "   @SUM(1,1)", " =1+1",
    "-", "+", "=", "@", "-.5", "-1e3", "-1,2", "-12.34x", "--12", "+12.34",
    # A money sign that is letters can start a cell name, -kr12 is cell KR12,
    # and Excel opened -kr12.34 and -zł12.34 as formulas. So did -$A$1.
    "-$A$1", "-$B2", "-kr12.34", "-zł12.34", "-$12.34x", "-$ 12.34", "-$.5", "-$-1",
    # Full-width and small signs, which OWASP names for Japanese settings,
    # and a minus before digits that are not ASCII ones.
    "＝1+1", "＋1+1", "－1+1", "＠SUM(1,1)", "﹦1+1", "﹢1+1",
    "－12", "-١٢٣",
]
TEXT = ["", "Kettle", "a=b", "1-1", "12.34", "1,234.56", "'=1+1", "(12.34)",
        "PAYMENT - THANK YOU", "AplPay EXAMPLE STORE", "Size M; 20% off", "a;b",
        "ＡＢＣ"]
PLAIN_NEGATIVE = ["-12.34", "-1,234.56", "-42", "-0.5", "-1,000,000", " -12.34",
                  # A refund as the apps write one. Excel opened each as a
                  # number, -£12.34 as text, none as a formula.
                  "-$12.34", "-$1,234.56", "-$0.99", "-$12", "-€12.34", "-£12.34"]
# A value with a ; or a line break inside, and the CSV cell it becomes.
PIECES = [
    ("Widget;=1+1", "Widget;'=1+1"),
    ("Widget; @SUM(1,1)", "Widget;' @SUM(1,1)"),
    ("Widget\n=1+1", "Widget\n'=1+1"),
    ("Widget\r\n+1+1", "Widget\r\n'+1+1"),
    ("Widget\r-1+1", "Widget\r'-1+1"),
    ("=1+1;=2+2", "'=1+1;'=2+2"),
    ("\n=1+1", "'\n'=1+1"),
    ("\r=1+1", "'\r'=1+1"),
    ("Refund; -12.34", "Refund; -12.34"),
    ("Size M; 20% off; final sale", "Size M; 20% off; final sale"),
    ("a;;b;", "a;;b;"),
]


@pytest.mark.parametrize("text", FORMULAS)
def test_text_that_could_start_a_formula_is_caught(text):
    assert starts_a_formula(text)
    if any(b in text for b in ";\r\n"):
        assert csv_value(text).startswith("'")       # and more, in PIECES
    else:
        assert csv_value(text) == "'" + text


@pytest.mark.parametrize("text", TEXT + PLAIN_NEGATIVE)
def test_other_text_and_a_plain_negative_number_are_left_alone(text):
    assert not starts_a_formula(text)
    assert csv_value(text) == text


@pytest.mark.parametrize("text, cell", PIECES)
def test_a_piece_after_a_semicolon_or_a_line_break_gets_its_own_quote(text, cell):
    """Excel in a country whose list separator is ; starts a new cell at
    each ; and a new row at a line break inside a quoted value."""
    assert csv_value(text) == cell


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


def _imported_from_the_rule(tree) -> set:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "paperpull_core.sheets":
            names |= {a.name for a in node.names}
    return names


def _called(node) -> set:
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _writing_functions(tree) -> list:
    """Each function that makes a CSV writer or an openpyxl Workbook, the
    functions it holds included, with what it writes."""
    return [(node, _kinds(node)) for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _kinds(node)]


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
    names = {name: sorted(f.name for f, _ in _writing_functions(tree))
             for name, tree in writers().items()}
    assert names["export_purchases.py"] == ["write_csv", "write_xlsx"]
    assert names["export_transactions.py"] == ["_write_csv", "_write_xlsx"]


@pytest.mark.parametrize("name", sorted(set(writers()) - set(NOT_FOR_PEOPLE)))
def test_every_function_writing_a_spreadsheet_for_a_person_goes_through_the_rule(name):
    tree = writers()[name]
    rule = _imported_from_the_rule(tree)
    functions = _writing_functions(tree)
    assert functions, "%s writes a spreadsheet outside any function" % name
    for func, kinds in functions:
        missing = {NEEDS[k] for k in kinds} - (rule & _called(func))
        assert not missing, "%s's %s writes a spreadsheet without paperpull_core.sheets' %s" % (
            name, func.name, ", ".join(sorted(missing)))


@pytest.mark.parametrize("tool", ["export_purchases.py", "export_transactions.py"])
def test_each_tool_takes_the_core_beside_it_with_no_core_installed(tool, tmp_path):
    """-S leaves out every installed package, so the only core either tool
    can import is the one in core/ beside tools/, which is where a checkout,
    the packaged builds and the server image all keep it. The tool runs as
    a script, as the transactions export runs, and is loaded by its path,
    as the panel loads the purchases export, which puts no folder on
    sys.path."""
    path = TOOLS / tool
    ran = subprocess.run([sys.executable, "-I", "-S", str(path), "--help"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=120)
    assert ran.returncode == 0 and "usage" in ran.stdout, ran.stderr
    load = ("import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('tool', sys.argv[1])\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "print(sys.modules['paperpull_core.sheets'].__file__)\n")
    loaded = subprocess.run([sys.executable, "-I", "-S", "-c", load, str(path)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=120)
    assert loaded.returncode == 0, loaded.stderr
    assert Path(loaded.stdout.strip()).resolve() == \
        (TOOLS.parent / "core" / "paperpull_core" / "sheets.py").resolve()


def test_a_tool_set_aside_still_writes_one_and_says_why():
    assert set(NOT_FOR_PEOPLE) <= set(writers())
    assert all(why.strip() for why in NOT_FOR_PEOPLE.values())


# -- the apps' own CSV files ---------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
# A minus, at most one of the money signs that are not letters, and a plain
# number, which Excel opened as a number or as text and never as a formula.
# Written out again here, so the model below is not the rule checking itself.
_EXCEL_PLAIN = re.compile(r"-[$£€]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?")
_BREAK = re.compile(r";|\r\n|\r|\n")


def _live(piece: str) -> bool:
    """Whether a spreadsheet would work a cell out, seen from its side. It
    may drop what a cell starts with that is blank, and then a sign starts
    a formula unless the rest is a plain negative number."""
    lead = piece.lstrip()
    return (bool(lead) and unicodedata.normalize("NFKC", lead[0])[:1] in "=+-@"
            and not _EXCEL_PLAIN.fullmatch(lead))


def live_anywhere(text: str) -> bool:
    """The whole value, or any piece of it a spreadsheet splitting at ; or a
    line break would make a cell or a row of its own."""
    return _live(text) or any(_live(p) for p in _BREAK.split(text)[1:])


# What can sit in a value, each kind of thing the rule looks at.
ALPHABET = ["'", "=", "+", "-", "@", " ", "\t", "\r", "\n", ";", "1", "a", "＝", "$"]


def every_short_value():
    for n in range(5):
        for chars in itertools.product(ALPHABET, repeat=n):
            yield "".join(chars)


def longer_values(count=20000):
    pieces = ALPHABET + ["\r\n", "''", "-$12.34", "-12.34", "=HYPERLINK(\"x\")", ",", '"',
                         "é", "£", "kr", "SUM(1,1)", "Statement"]
    rnd = random.Random(20261016)
    for _ in range(count):
        yield "".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 16)))


def test_every_value_reads_back_exactly_as_it_was_written():
    checked = 0
    for value in itertools.chain(every_short_value(), longer_values()):
        assert read_stored(store_value(value)) == value, repr(value)
        checked += 1
    assert checked > 50000


def test_nothing_written_is_ever_worked_out_by_a_spreadsheet():
    for value in itertools.chain(every_short_value(), longer_values()):
        assert not live_anywhere(store_value(value)), repr(value)


def test_a_value_with_no_quote_in_front_is_written_as_the_exporters_write_it():
    """One rule. A value only differs from the exporters' CSV where it
    starts with a quote, whole or after a ; or a line break, which the
    exporters need not tell apart since nothing reads their file back."""
    for value in itertools.chain(every_short_value(), longer_values()):
        if not any(p.startswith("'") for p in [value] + _BREAK.split(value)[1:]):
            assert store_value(value) == csv_value(value), repr(value)


# A value, and what the file holds for it.
STORED = [
    ("=1+1", "'=1+1"),
    ('=HYPERLINK("https://example.invalid/?"&A1,"open")',
     '\'=HYPERLINK("https://example.invalid/?"&A1,"open")'),
    ("@SUM(1,1)", "'@SUM(1,1)"),
    ("-1+1", "'-1+1"),
    ("=3+3; @SUM(1,1) Statement.pdf", "'=3+3;' @SUM(1,1) Statement.pdf"),
    # A real name that starts with a quote and then = is told apart from one
    # given its quote here by one more quote.
    ("'=1+1", "''=1+1"),
    ("''=1+1", "'''=1+1"),
    ("' =1+1", "'' =1+1"),
    ("Widget;'=1+1", "Widget;''=1+1"),
    ("\n=1+1", "'\n'=1+1"),
    (" =1+1", "' =1+1"),
    ("＝1+1", "'＝1+1"),
    ("-$A$1", "'-$A$1"),
    ("-kr12.34", "'-kr12.34"),
    # Written as they are.
    ("'Tis the season ornament", "'Tis the season ornament"),
    ("O'Brien", "O'Brien"),
    ("'", "'"),
    ("''", "''"),
    ("Rock 'n' roll; 'live'", "Rock 'n' roll; 'live'"),
    ("-$12.34", "-$12.34"),
    ("-$1,234.56", "-$1,234.56"),
    ("-€12.34", "-€12.34"),
    ("-12.34", "-12.34"),
    ("Refund; -$12.34", "Refund; -$12.34"),
    ("C:\\Users\\Dana\\Shop;=1+1\\a.pdf", "C:\\Users\\Dana\\Shop;'=1+1\\a.pdf"),
    ("", ""),
]


@pytest.mark.parametrize("value, held", STORED)
def test_what_the_file_holds_and_what_reads_back(value, held):
    assert store_value(value) == held
    assert read_stored(held) == value
    assert not live_anywhere(held)


# What a file written before the rule could hold, which reads as it did.
OLD = ["=1+1", "@SUM(1,1)", "-1+1", "+1+1", " =1+1", "Widget;=1+1", "\n=1+1",
       "O'Brien", "'Tis the season ornament", "Rock 'n' roll; 'live'", "'", "''",
       "'plain words", "a;'b", "-$12.34", "-12.34", "'-12.34", "'-$12.34",
       "2026-09-12", "https://example.invalid/r?id=1", "C:\\Users\\Dana\\a.pdf"]


@pytest.mark.parametrize("value", OLD)
def test_a_file_written_before_the_rule_reads_as_it_did(value):
    assert read_stored(value) == value


def test_the_one_old_shape_that_reads_differently_is_a_quote_before_a_formula():
    """A value an older version wrote starting with a quote and then text
    like a formula reads without that quote, since that is what a quote
    put in by the rule looks like. The real installs held no such value on
    2026-10-16, and no value starting with a quote at all."""
    assert read_stored("'=1+1") == "=1+1"
    assert read_stored("Widget;'=1+1") == "Widget;=1+1"
    for value in itertools.chain(every_short_value(), longer_values()):
        pieces = [value] + _BREAK.split(value)[1:]
        if not any(p.startswith("'") and starts_a_formula(p.lstrip("'")) for p in pieces):
            assert read_stored(value) == value, repr(value)


def test_none_is_an_empty_cell_and_a_number_is_written_as_its_text():
    assert store_value(None) == ""
    assert (store_value(-5), store_value(12.5), store_value(0), store_value(True)) == \
        ("-5", "12.5", "0", "True")
    # Text a number gives that a spreadsheet could take for a formula gets
    # its quote too, and reads back as that text.
    assert store_value(-1e-05) == "'-1e-05" and read_stored("'-1e-05") == "-1e-05"


def test_what_is_not_text_reads_back_as_it_is():
    """csv.DictReader hands None for a short row's missing values and a
    list under None for a long row's extra ones."""
    assert read_stored(None) is None
    row = read_stored_row({"Item Name": "'=1+1", "Notes": None, None: ["'=1+1"]})
    assert row == {"Item Name": "=1+1", "Notes": None, None: ["'=1+1"]}


# -- the census of every CSV reader and writer ------------------------------------------

ROOTS = ("apps", "core", "gui", "server", "tools")
MADE_FOLDERS = {"build", "dist", "site-packages", "node_modules"}


def sources():
    """Every Python file of the repository that is not a test, a copy a
    build made or a linked environment."""
    out = []
    for root in ROOTS:
        for folder, dirs, files in os.walk(REPO / root):
            dirs[:] = sorted(d for d in dirs if d != "tests" and d not in MADE_FOLDERS
                             and not d.startswith((".", "__")) and not d.endswith(".egg-info"))
            out.extend(Path(folder) / f for f in files
                       if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py")
    return sorted(out)


def _makes(node) -> set:
    """Whether a function makes a csv reader or a csv writer."""
    kinds = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "csv":
            if n.attr in ("reader", "DictReader"):
                kinds.add("reads")
            elif n.attr in ("writer", "DictWriter"):
                kinds.add("writes")
    return kinds


def _calls(node) -> set:
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            names.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute)
                      else "")
    return names


def csv_functions() -> dict:
    """'<path>::<function>' to what it does with a csv, for every function
    in the repository that makes a csv reader or writer."""
    out = {}
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _makes(node):
                name = "%s::%s" % (path.relative_to(REPO).as_posix(), node.name)
                out[name] = (_makes(node), _calls(node))
    return out


# How each kind of use has to go through the rule.
THROUGH = {"reads": {"read_stored", "read_stored_row"},
           "writes": {"store_value", "csv_value"}}
# A function that makes a csv reader or writer for something other than an
# app's own record, with the reason. Anything else has to go through the rule.
NOT_A_RECORD = {
    "core/paperpull_core/storage.py::_ensure_header":
        "writes the header row alone, the app's own column names",
    "tools/make_sample.py::statements":
        "writes the sample archive's invented records, each value checked below "
        "to be held as an app would hold it",
    "tools/make_sample.py::receipts":
        "writes the sample archive's invented records, each value checked below "
        "to be held as an app would hold it",
    "tools/snapshot_traffic.py::merge":
        "reads and writes the repository's daily view and clone counts, dates and numbers",
}
# What the census has to find, so a change that hides one fails here.
MUST_FIND = {
    "core/paperpull_core/storage.py::append_rows": {"writes"},
    "core/paperpull_core/storage.py::read_all": {"reads"},
    "core/paperpull_core/storage.py::rewrite": {"writes"},
    "tools/export_purchases.py::load_purchases": {"reads"},
    "tools/export_transactions.py::_index_rows": {"reads"},
}


def test_the_census_finds_every_reader_and_writer_of_an_apps_csv():
    found = csv_functions()
    for name, kinds in MUST_FIND.items():
        assert name in found and found[name][0] == kinds, name
    assert set(NOT_A_RECORD) <= set(found), sorted(set(NOT_A_RECORD) - set(found))
    assert all(why.strip() for why in NOT_A_RECORD.values())


@pytest.mark.parametrize("name", sorted(set(csv_functions()) - set(NOT_A_RECORD)))
def test_every_csv_reader_and_writer_goes_through_the_rule(name):
    kinds, calls = csv_functions()[name]
    for kind in kinds:
        assert THROUGH[kind] & calls, "%s %s a CSV without paperpull_core.sheets' %s" % (
            name, kind, " or ".join(sorted(THROUGH[kind])))


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / ("%s.py" % name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_sample_archive_holds_each_value_as_an_app_would(tmp_path):
    """The sample writes its records with csv itself. Each value it writes
    has to be one the rule leaves as it is, so every reader reads the
    sample as written."""
    _load_tool("make_sample").build(tmp_path)
    files = [p for p in tmp_path.rglob("*.csv")
             if p.name.endswith((" Order History.csv", " Receipt Index.csv", " Document Index.csv"))]
    assert len(files) >= 3
    values = 0
    for path in files:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                for value in row.values():
                    assert store_value(value) == value, (path.name, value)
                    values += 1
    assert values > 100
