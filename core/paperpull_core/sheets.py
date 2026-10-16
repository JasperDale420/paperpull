"""Text a spreadsheet tool writes stays text, never a formula.

The two spreadsheet tools, tools/export_purchases.py and
tools/export_transactions.py, write out words other people chose. A
marketplace seller names the item on an Amazon receipt and a merchant
names itself on a card statement. A spreadsheet takes a cell that starts
with = as a formula, and Excel takes one starting with +, - or @ the
same way. So an item named =1+1 became a formula in the workbook, and the
CSV kept it for the spreadsheet to work out on opening. A formula can do
more than add. HYPERLINK and WEBSERVICE reach an address, and the cells a
formula names go along with it.

Every value either tool writes goes through here on its way into a cell.

    from paperpull_core.sheets import csv_value, keep_text

    writer.writerow({k: csv_value(v) for k, v in row.items()})
    for row in ws.iter_rows():
        for cell in row:
            keep_text(cell)

Only text is touched. An amount, a quantity or a count is a number before
it gets here and is written as one, so -12.34 in an Amount column stays
the number it was. Text is told apart by its type, not by a list of
columns, so a column added later is covered without anyone naming it.

WHAT EXCEL DOES (measured 2026-10-16, Excel for Microsoft 365 on Windows)

    a CSV       =1+1, -1+1 and @SUM(1,1) opened as formulas.
                -12.34 and -1,234.56 opened as numbers, with no formula.
                =1+1 after a space or a tab opened as text.
                '=1+1 opened as text, with the quote shown in the cell.
    an xlsx     A string cell holding =1+1 opened as text. Edited and
                entered again it became a formula, unless the cell carried
                the quote prefix.

So a CSV value that starts like a formula gets a single quote in front,
OWASP's form for CSV injection. Excel shows that quote, so it goes only
where it is needed. An xlsx text value is always a string cell, which a
spreadsheet does not work out on opening, and one that starts like a
formula also carries the quote prefix, the mark Excel itself leaves when a
person types a leading quote, so editing the cell keeps it text as well.
The xlsx value itself is never changed.

A value with spaces in front is quoted too when the first thing after them
starts a formula, for a spreadsheet set to trim a cell before reading it,
as LibreOffice can be.

A plain negative number written as text, -12.34 or -1,234.56, is left
alone. Excel reads it as that number and nothing more, the way it reads
12.34 beside it, and a quote would only show in the cell. Anything else
after the minus, -1+1 for one, is quoted.
"""
from __future__ import annotations

import re

# What a cell may start with that a spreadsheet takes as a formula, from
# OWASP's guidance on CSV injection. Tab, carriage return and line feed are
# there for a spreadsheet that drops them before reading the rest.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "\n")
_SIGNS = ("=", "+", "-", "@")
# A minus and a plain number, the one start among them left alone. Digits,
# commas in threes and a decimal point name no cell and call nothing.
_PLAIN_NEGATIVE = re.compile(r"-(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def starts_a_formula(text: str) -> bool:
    """Whether a spreadsheet could take this text for a formula."""
    if not text:
        return False
    lead = text.lstrip()
    if text[0] not in FORMULA_STARTS and lead[:1] not in _SIGNS:
        return False
    return not _PLAIN_NEGATIVE.fullmatch(lead)


def csv_value(value):
    """What a CSV cell is given for value. None is an empty cell, a number
    is written as it is, and text that could start a formula gets a single
    quote in front."""
    if value is None:
        return ""
    if isinstance(value, str) and starts_a_formula(value):
        return "'" + value
    return value


def keep_text(cell) -> None:
    """Make an openpyxl cell holding text a string cell. openpyxl stores a
    string starting with = as a formula and one that reads like #N/A as an
    error, so what it guessed is put back to text, and text that could
    start a formula also takes the quote prefix. A number or an empty cell
    is left as it is."""
    if isinstance(cell.value, str):
        cell.data_type = "s"
        if starts_a_formula(cell.value):
            cell.quotePrefix = True
