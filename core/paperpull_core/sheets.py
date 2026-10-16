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

Every value either tool writes goes through here on its way into a cell,
and so does every value the apps write to their own CSV files and read
back from them (THE APPS' OWN CSV FILES, below).

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
                -$12.34, -$1,234.56 and -€12.34 opened as numbers and
                -£12.34 as text, none of them as a formula. -$A$1, -$B2,
                -kr12.34 and -zł12.34 opened as formulas.
                =1+1 after a space or a tab opened as text.
                '=1+1 and ''=1+1 opened as text, with the quotes shown in
                the cell.
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
＝ as =, which OWASP names for Japanese settings. A NUL character counts
for nothing, since Excel drops it from a CSV it opens, and =1+1 behind one
opened as a working formula.

A CSV can also be split where its writer did not split it. Excel in a
country whose list separator is ; reads a comma CSV opened from its file
by that separator, so every ; inside a value starts a new cell, and a line
break inside a quoted value starts a new row. A quote at the start of the
whole value does nothing for what follows one of those, so a piece after a
; or a line break that starts like a formula gets a quote of its own.
A double click in a ; country was not measured here, since this Excel's
separator is a comma. Excel's own import set to split at ; did split that
way, and it read ""=1+1 at the start of a piece, which is how the csv
module writes "=1+1 inside a quoted value, as an empty quoted part with
=1+1 after it, a working formula. So a piece is also quoted when it starts
with a sign once blank space and quotes of either kind in front of it are
set aside.

A plain negative number written as text, -12.34 or -1,234.56, is left
alone, and so is one with a $, £ or € between the minus and the digits,
the way the apps write a refund, -$12.34. Excel reads it as that number,
or as plain text, and nothing more, the way it reads 12.34 beside it, and
a quote would only show in the cell. Anything else after the minus, -1+1,
-$A$1 or -kr12.34 for three, is quoted.

A piece after a ; or a line break that is a plain negative number is
quoted all the same. Where the list separator is ; the decimal sign is a
comma, and the last piece of a value the csv module leaves unquoted runs
on through the comma that ends it into the next value, so -1 beside 5+1
is read as -1,5+1, a working formula there. That was found by modelling
such a reader, not measured, and no real value had such a piece.

THE APPS' OWN CSV FILES

Every app keeps its record as CSV files beside the PDFs, the order
history, the receipt index and the document index, written and read back
through core storage's CsvFile. They hold the same words other people
chose, are written for Excel to open on a double click, and are read back
by the app itself, by Rename and by both tools here. So the quote goes in
by the same rule on the way in and comes off on every way out, and it
must come off only where it was put, since a name can really start with a
quote.

    store_value(v)    for every value written to one of these files
    read_stored(t)    for every value read from one, read_stored_row(r)
                      for a whole row from csv.DictReader

store_value quotes the whole value and each piece after a ; or a line
break by the test a piece gets above, which sets aside the quotes already
in front. read_stored takes one quote off a piece, and then off the whole
value, only where that test passes, and a quote in front never changes
its answer. So '=1+1 as a real name is written ''=1+1 and read
back as '=1+1, =1+1 is written '=1+1 and read back as =1+1, and every
value reads back exactly as it was written.

That test is part of the files' format. read_stored takes a quote off only
where today's test would put one, so a test that quotes less than an
earlier one leaves the earlier version's quotes showing for good, and a
file name carrying one no longer matches its file. So the test may grow
and never shrink. Growing it changes how an older file reads only where a
value starts with a quote and then what the test newly covers. The STORED
examples in core/tests/test_sheets.py hold the format.

A file written before this rule reads as it did, value for value, unless
a value in it starts with a quote and then text like a formula, or holds
such a piece after a ; or a line break. On 2026-10-16 the real installs
had none of those among 180,418 values in 55 files, and no value starting
with a quote at all.
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
# digits, commas in threes and a decimal point name no cell and call nothing,
# and neither does one of the three money signs that are not letters, which
# no cell name or function name can hold.
_PLAIN_NEGATIVE = re.compile(r"-[$£€]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", re.ASCII)
# Where a spreadsheet that splits a CSV at ; begins a new cell or a new row.
_BREAKS = re.compile(r"(;|\r\n|\r|\n)")


def _as_sign(ch: str) -> str:
    """The character a spreadsheet may read ch as, ＝ as = and ﹢ as +."""
    return unicodedata.normalize("NFKC", ch)[:1]


def starts_a_formula(text: str) -> bool:
    """Whether a spreadsheet could take this text for a formula. Excel drops
    a NUL character from a CSV it opens, so one in front hides nothing."""
    text = text.replace("\x00", "") if text else text
    if not text:
        return False
    lead = text.lstrip()
    if _as_sign(text[0]) not in FORMULA_STARTS and _as_sign(lead[:1]) not in _SIGNS:
        return False
    return not _PLAIN_NEGATIVE.fullmatch(lead)


# What a spreadsheet splitting a CSV at ; may pass over at the start of a
# piece before the text it reads, blank space and quotes of either kind.
_PASSED_OVER = re.compile(r"[\s'\"]*")


def _gets_a_quote(text: str) -> bool:
    """Whether a piece of a CSV value after a ; or a line break gets a quote
    in front, and in the apps' own files the whole value too. That is when
    it starts like a formula once any single quotes in front are set aside,
    or starts with a sign once blank space and quotes of either kind in
    front are. The csv module writes a " in a value doubled, so a piece
    starting "=1+1 is held as ""=1+1, which a spreadsheet splitting at ;
    reads as an empty quoted part with =1+1 after it. A quote in front
    changes neither answer, which is how read_stored tells the quote put in
    here from one the text came with."""
    bare = text.replace("\x00", "")
    return (starts_a_formula(bare.lstrip("'"))
            or starts_a_formula(bare[_PASSED_OVER.match(bare).end():]))


def _piece_gets_a_quote(piece: str) -> bool:
    """Whether a piece after a ; or a line break gets a quote in front. As
    _gets_a_quote, and a plain negative number gets one too. Where the list
    separator is ; the decimal sign is a comma, and the last piece of a value
    the csv module leaves unquoted runs on through the comma that ends it
    into the next value, so -1 there and 5+1 beside it are read as -1,5+1, a
    working formula."""
    bare = piece.replace("\x00", "")
    lead = bare[_PASSED_OVER.match(bare).end():]
    return _gets_a_quote(piece) or _as_sign(lead[:1]) in _SIGNS


def csv_value(value):
    """What a CSV cell is given for value. None is an empty cell and a
    number is written as it is. Text that could start a formula gets a
    single quote in front, and so does each piece of it after a ; or a line
    break that could, quotes and blank space in front of it set aside."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    parts = _BREAKS.split(value)
    for i in range(2, len(parts), 2):
        if _piece_gets_a_quote(parts[i]):
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


# The apps' own CSV files. Unlike the two tools' output, these are read back,
# so every quote put in on the way in comes off again on the way out.

def store_value(value) -> str:
    """What one of the apps' own CSV files is given for value. None is an
    empty cell, and anything else is written as its text. Text that starts
    like a formula, once any quotes in front of it are set aside, gets one
    more quote in front, and so does each piece after a ; or a line break
    that does (_gets_a_quote). read_stored gives back exactly what came in."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    parts = _BREAKS.split(text)
    for i in range(2, len(parts), 2):
        if _piece_gets_a_quote(parts[i]):
            parts[i] = "'" + parts[i]
    out = "".join(parts)
    return "'" + out if _gets_a_quote(text) else out


def read_stored(text):
    """A value as store_value was given it, from what the file holds. The
    pieces after a ; or a line break first, then the whole value, each
    loses the one quote store_value put in front, and only where it did. A
    value that is not text is given back as it is."""
    if not isinstance(text, str) or "'" not in text:
        return text
    parts = _BREAKS.split(text)
    for i in range(2, len(parts), 2):
        if parts[i].startswith("'") and _piece_gets_a_quote(parts[i]):
            parts[i] = parts[i][1:]
    out = "".join(parts)
    return out[1:] if out.startswith("'") and _gets_a_quote(out) else out


def read_stored_row(row: dict) -> dict:
    """A row csv.DictReader read from one of the apps' own CSV files, each
    value as it was given to store_value."""
    return {k: read_stored(v) for k, v in row.items()}
