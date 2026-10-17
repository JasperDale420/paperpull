"""Every app keeps text like a formula as text in its own CSV files, and
reads back each value as it wrote it.

An app writes its order history, receipt index and document index beside
the PDFs, for a person to open in Excel. They carry words other people
chose, an item's name from a marketplace seller, a store's or a merchant's
own name, a document's title, and the file names made from them. A value
that starts with =, +, - or @ was a working formula when the file was
opened. Core storage's CsvFile now holds such a value with a quote in front
(paperpull_core.sheets.store_value), and every read takes it off again
(read_stored). A quote that did not come off would break whatever the app
matches on, a file name, an order number, a path, so Rename, Verify and a
second run would no longer find what the index names.

Each app here is built as it is at home, from its own code, the way
test_every_app_renames_each_file_for_its_own_record.py builds it. Two
documents or two purchases are written down by the app's own code, with a
summary that starts like a formula and has a formula after a semicolon, so
under the pattern set here each file's own name starts like one too, and a
title or an order number shaped the same way. Then

  - no cell of any of the app's CSV files is one a spreadsheet would work
    out, read as Excel reads a file,
  - the app's own CsvFile reads back each value as it was given,
  - the app's own Rename, through its own main, moves both files to a
    pattern that starts with the date and back again, which it can only do
    when it reads each file's name and path from the index as they are,
  - a preview after that says every file is already named as the app
    names it, and the files hold every value once quoted, never twice,
  - and a receipt app's own check whether its history already has an order
    (not_shown.written_down) finds each order by its number.
"""
import csv
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from paperpull_core import not_shown
from paperpull_core.sheets import store_value

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_every_app_renames_each_file_for_its_own_record as census  # noqa: E402
from test_sheets import live_anywhere  # noqa: E402

APPS, IDS = census.APPS, census.IDS

# Harmless arithmetic, shaped like a formula, after a semicolon too.
SUMMARY = "=1+1; @SUM(1,1) Statement"
# One title shaped like a formula behind a NUL, which Excel drops, with a
# piece starting with " after a semicolon, and one that really starts with
# a quote.
TITLES = ('\x00+2+2 Statement; "=4+4 - September 12, 2026',
          "'=3+3 Statement - September 12, 2026")
# Order numbers, each starting like a formula.
NUMBERS = ("\x00-1+1 A1111", '@SUM B2222; "+5')
FORMULA_FIRST = "{summary} {date:yyyy-mm-dd}[ {account}][ {number}]"
DATE_FIRST = "{date:yyyy-mm-dd} {summary}[ {account}][ {number}]"


@pytest.fixture
def home(request, tmp_path, monkeypatch, capsys):
    return census.Home(request.param, tmp_path, monkeypatch, capsys)


def written_down(home) -> dict:
    """Two documents or two purchases written down by the app's own code,
    saved under FORMULA_FIRST, and the name each file has, by its bytes."""
    home.set_pattern(FORMULA_FIRST)
    app = home.build()
    if hasattr(home.mod, "Document"):
        docs = census.two_documents(home, titles=TITLES, summary=SUMMARY)
        assert docs, "%s holds only one of two documents" % home.app.name
        for doc in docs:
            census.downloaded(app, doc)
    else:
        for number in NUMBERS:
            census.purchased(home, app, number, body=census.body_of(number),
                             summary=SUMMARY)
    census.saved(app)
    names = {body: path.name for body, path in home.files().items()}
    assert len(names) == 2, names
    assert all(name.startswith(SUMMARY) for name in names.values()), names
    return names


def csv_files(home) -> list:
    app = home.build()
    return [c for c in (getattr(app, "index_csv", None), getattr(app, "order_csv", None)) if c]


def as_excel_reads(path: Path):
    """The file split into cells as a spreadsheet splits it, before any
    reading back."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def held_as_written(home):
    """No cell is one a spreadsheet would work out, and each holds what
    store_value gives for what the app's own CsvFile reads back from it,
    so nothing carries a quote twice."""
    for ledger in csv_files(home):
        header, rows = as_excel_reads(Path(ledger.path))
        back = ledger.read_all()
        assert len(back) == len(rows) >= 2, (home.app.name, ledger.path)
        for raw, row in zip(rows, back):
            for column, cell in zip(header, raw):
                assert not live_anywhere(cell), (home.app.name, column, cell)
                assert cell == store_value(row[column]), (home.app.name, column, cell)


def renamed(home, pattern: str) -> str:
    home.set_pattern(pattern)
    return home.rename("--apply")


def where_the_index_says(home, said: str, starts: str) -> None:
    files = home.files()
    assert len(files) == 2, said[-1500:]
    rows = home.build().index_csv.read_all()
    for path in files.values():
        assert path.name.startswith(starts), (home.app.name, path.name, said[-1500:])
        mine = [r for r in rows if Path(r["PDF Full Path"]) == path]
        assert mine and all(r["PDF Filename"] == path.name for r in mine), (
            "%s's index does not name %s\n%s" % (home.app.name, path.name, said[-1500:]))


def test_there_are_apps_to_check():
    assert len(APPS) >= 75, IDS


@pytest.mark.parametrize("home", APPS, ids=IDS, indirect=True)
def test_every_app_keeps_text_like_a_formula_as_text_and_reads_it_back(home):
    names = written_down(home)
    held_as_written(home)
    app = home.build()
    rows = app.index_csv.read_all()
    assert sorted(r["PDF Filename"] for r in rows if r.get("PDF Filename")) == sorted(names.values())
    if hasattr(home.mod, "Document"):
        assert {r["Document Summary"] for r in rows} == {SUMMARY}
        assert {r["Document Title"] for r in rows} == set(TITLES)
    else:
        assert {r["Purchase Summary"] for r in rows} == {SUMMARY}
        assert {r["Order or Receipt Number"] for r in rows} == set(NUMBERS)
        history = app.order_csv.read_all()
        assert {r["Order or Receipt Number"] for r in history} == set(NUMBERS)
        assert {r["Purchase Summary"] for r in history} == {SUMMARY}
        for number in NUMBERS:
            purchase = SimpleNamespace(order_number=number,
                                       purchase_type=history[0]["Purchase Type"])
            assert not_shown.written_down(app, purchase), (home.app.name, number)


@pytest.mark.parametrize("home", APPS, ids=IDS, indirect=True)
def test_every_app_renames_files_named_like_a_formula_both_ways(home):
    written_down(home)
    said = renamed(home, DATE_FIRST)
    assert census.RENAMED_BOTH in said, said[-1500:]
    where_the_index_says(home, said, census.DATE)
    said = renamed(home, FORMULA_FIRST)
    assert census.RENAMED_BOTH in said, said[-1500:]
    where_the_index_says(home, said, SUMMARY)
    said = home.rename()
    assert census.NAMED_ALREADY in said, said[-1500:]
    held_as_written(home)
    assert home.browser.asked == 0, "Rename reached for a browser"
