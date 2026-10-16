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
as LibreOffice can be. A full-width sign counts as the sign it stands for,
＝ as =, which OWASP names for Japanese settings.

A CSV can also be split where its writer did not split it. Excel in a
country whose list separator is ; reads a comma CSV opened from its file
by that separator, so every ; inside a value starts a new cell, and a line
break inside a quoted value starts a new row. A quote at the start of the
whole value does nothing for what follows one of those, so a piece after a
; or a line break that starts like a formula gets a quote of its own.
Neither of these was measured here, since this Excel's separator is a
comma and its language English.

A plain negative number written as text, -12.34 or -1,234.56, is left
alone. Excel reads it as that number and nothing more, the way it reads
12.34 beside it, and a quote would only show in the cell. Anything else
after the minus, -1+1 for one, is quoted.
"""
from __future__ import annotations

import re
import unicodedata

# What a cell may start with that a spreadsheet takes as a formula, from
# OWASP's guidance on CSV injection. Tab, carriage return and line feed are
# there for a spreadsheet that drops them before reading the rest.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "\n")
_SIGNS = ("=", "+", "-", "@")
# A minus and a plain number, the one start among them left alone. ASCII
# digits, commas in threes and a decimal point name no cell and call nothing.
_PLAIN_NEGATIVE = re.compile(r"-(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", re.ASCII)
# Where a spreadsheet that splits a CSV at ; begins a new cell or a new row.
_BREAKS = re.compile(r"(;|\r\n|\r|\n)")


def _as_sign(ch: str) -> str:
    """The character a spreadsheet may read ch as, ＝ as = and ﹢ as +."""
    return unicodedata.normalize("NFKC", ch)[:1]


def starts_a_formula(text: str) -> bool:
    """Whether a spreadsheet could take this text for a formula."""
    if not text:
        return False
    lead = text.lstrip()
    if _as_sign(text[0]) not in FORMULA_STARTS and _as_sign(lead[:1]) not in _SIGNS:
        return False
    return not _PLAIN_NEGATIVE.fullmatch(lead)


def csv_value(value):
    """What a CSV cell is given for value. None is an empty cell and a
    number is written as it is. Text that could start a formula gets a
    single quote in front, and so does each piece of it after a ; or a line
    break that could."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    parts = _BREAKS.split(value)
    for i in range(2, len(parts), 2):
        if starts_a_formula(parts[i]):
            parts[i] = "'" + parts[i]
    text = "".join(parts)
    return "'" + text if starts_a_formula(value) else text


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
