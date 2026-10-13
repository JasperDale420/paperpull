"""A history moved with --minimal still moves a key cut before 0.34.0.

Until 0.34.0 a document's key held its account cut to forty characters, and
until 0.17.1 Chase's left out the last four of an account of any length.
Every app whose key carries the account moves a record still under such a
key to the whole one as it loads its progress, through
paperpull_core.keys.migrate_account_keys or Chase's own copy. It rebuilds
that key from the record's own fields, its category or type, date, title,
account and, in Capital One and Schwab, its place among same-day twins, and
compares those and the document id with a record already under the new key.
tools/migrate.py --minimal left all of those out, so on the new computer
such a record never moved, and the statement, listed under its whole key,
was downloaded again. It reaches an install no version since opened on the
old computer.

The apps are found by what they do, a function their App runs over each
store's records as it loads, and every app that calls the core's move must
be among them. What each one's key reads is read from its Document, and the
keys a record is moved from are found by running the app's own move. A
record holding all of it goes through a real export and import and the
app's own move and skip decision, into a fresh install, one that listed the
statement already, and one an older --minimal moved it to. Every name and
number is invented.
"""
import argparse
import ast
import copy
import importlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from paperpull_core import keys
from paperpull_core import storage as core_storage
from paperpull_core.keys import account_component
from paperpull_core.models import State

REPO = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO / "tools"))
migrate = pytest.importorskip("migrate")


def entry_of(app: Path):
    for pattern in ("*_docs.py", "*_receipts.py"):
        found = sorted(app.glob(pattern))
        if found:
            return found[0]
    return None


APPS = sorted(d for d in (REPO / "apps").iterdir() if d.is_dir() and entry_of(d))

# Long enough that the last four falls past the cut, the second with
# characters a filename cannot hold, which the key leaves out. Every app's
# key changed for these.
LONG = ("Invented Cash Rewards World Elite Mastercard (...1234)",
        'Invented Rewards: Visa Signature/Card "Household" (...5678)')
# Short enough to fit, which only Chase's key changed for, since it keeps
# the last four of any account.
ACCOUNTS = LONG + ("Invented Checking (...4321)",)

# An invented value for every field a key reads but the account, none of
# them empty, so a field the tool leaves out builds another key.
VALUES = {"category": "Statement", "doc_type": "Statement", "date": "2031-03-31",
          "title": "Invented Monthly Statement", "occurrence": 1,
          "document_id": "DOC0001"}
ACCOUNT_FIELDS = {"account", "listed_account"}


class _Asked(dict):
    """A record that notes every field asked of it."""

    def __init__(self):
        super().__init__()
        self.asked = set()

    def get(self, field, default=None):
        self.asked.add(field)
        return default


def identity_fields() -> set:
    """What paperpull_core.keys compares before moving a record over one
    already under the new key, as Chase's own copy does."""
    record = _Asked()
    keys._identity(record)
    return record.asked


# -- finding them -------------------------------------------------------------

def _app_init(app: Path):
    tree = ast.parse(entry_of(app).read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "App":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "__init__":
                    return sub
    return None


def load_time_moves(app: Path) -> list:
    """The module functions the app's App runs over a store's records as it
    loads, like migrate_legacy_keys(store.data), in the order it runs them."""
    init = _app_init(app)
    if init is None:
        return []
    calls = sorted((node.lineno, node.func.id) for node in ast.walk(init)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and any(isinstance(a, ast.Attribute) and a.attr == "data"
                           for a in node.args))
    return list(dict.fromkeys(name for _line, name in calls))


def key_reads(app: Path) -> set:
    """Every field of itself the app's Document.key reads, through any
    method of its own it calls."""
    tree = ast.parse(entry_of(app).read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Document":
            methods = {f.name: f for f in node.body if isinstance(f, ast.FunctionDef)}
            todo, seen, fields = ["key"], set(), set()
            while todo:
                name = todo.pop()
                if name in seen or name not in methods:
                    continue
                seen.add(name)
                for n in ast.walk(methods[name]):
                    if not (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                            and n.value.id == "self"):
                        continue
                    if n.attr in methods:
                        todo.append(n.attr)
                    else:
                        fields.add(n.attr)
            return fields
    return set()


def calls_the_core_move(app: Path) -> bool:
    """Whether any of the app's own modules calls the core's move."""
    for path in sorted(app.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        names = {"migrate_account_keys"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "paperpull_core.keys":
                names |= {a.asname or a.name for a in node.names
                          if a.name == "migrate_account_keys"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = ast.unparse(node.func)
                if func in names or func.endswith(".migrate_account_keys"):
                    return True
    return False


# Functions an app runs over its records as it loads that move no cut key,
# each with why --minimal does not need to carry what it reads. One no
# longer run fails, so the list cannot outlive its reason.
OTHER_MOVES = {
    ("aafmaa", "forget_postback_names"):
        "takes an id out of a record, and --minimal never carries that id",
    ("optum", "adopt_product_keys"):
        "Optum first shipped in 0.46.0 with this move, so no released version "
        "wrote a key it moves",
    ("robinhood", "migrate_html_titles"):
        "moves a record whose title still carries the control's words Download HTML "
        "under the cleaned title's key, read from category, date, title and account, "
        "which --minimal carries, so it moves there as from a full history. It moves "
        "no cut key",
}

LOADS = {app: load_time_moves(app) for app in APPS}
MOVERS = [(app, move) for app, moves in LOADS.items() for move in moves
          if (app.name, move) not in OTHER_MOVES]


def test_every_app_that_moves_a_cut_key_is_found():
    """Not a census of nothing, and none that call the core's move from
    somewhere else is missed."""
    found = {app.name for app, _move in MOVERS}
    calling = {app.name for app in APPS if calls_the_core_move(app)}
    assert calling <= found, sorted(calling - found)
    assert len(calling) >= 44, sorted(calling)
    assert len(found) >= 45, sorted(found)


@pytest.mark.parametrize("app", sorted({a for a, _m in MOVERS}), ids=lambda a: a.name)
def test_what_a_key_is_built_from_goes_with_minimal_for_a_cut_key(app):
    """Read from the app's own Document, so a key that reads a field the
    tool leaves out fails here, and the run below fills every one."""
    reads = key_reads(app)
    assert reads & ACCOUNT_FIELDS, (app.name, "its key reads no account", reads)
    needed = reads | identity_fields()
    assert needed <= set(migrate.KEY_FIELDS), (
        "%s's key or the move's comparison reads %s, which --minimal leaves out "
        "for a cut key" % (app.name, sorted(needed - set(migrate.KEY_FIELDS))))
    assert reads <= set(VALUES) | ACCOUNT_FIELDS, (
        app.name, "give the run below an invented value for", reads - set(VALUES))


def test_every_other_move_is_still_run():
    run = {(app.name, move) for app, moves in LOADS.items() for move in moves}
    assert set(OTHER_MOVES) <= run, sorted(set(OTHER_MOVES) - run)


# -- running them -------------------------------------------------------------

def load(app: Path):
    for name in [m for m in list(sys.modules)
                 if m.endswith(("_docs", "_receipts", "_site")) or m == "storage"]:
        del sys.modules[name]
    sys.path.insert(0, str(app))
    try:
        return importlib.import_module(entry_of(app).stem)
    finally:
        sys.path.pop(0)


@pytest.fixture
def loading(monkeypatch):
    """What loading an app sets in the core, put back after."""
    for name in ("_SPEC", "_FILENAME_PATTERN", "_PATTERN_OWNER", "_FILENAME_OWNER"):
        monkeypatch.setattr(core_storage, name, getattr(core_storage, name))
    return load


def app_over(mod, folder: Path, records: dict):
    """The app's own downloader over these records, without its __init__,
    which would want a config file and a browser."""
    storage = sys.modules["storage"]            # the app's own, loaded with it
    inst = object.__new__(mod.App)
    inst.config = {"max_path_length": 240, "min_pdf_bytes": 1000, "owner": ""}
    inst.args = argparse.Namespace(redownload=False)
    inst.paths = storage.Paths(folder)
    inst.paths.ensure()
    inst.progress = storage.JsonStore(inst.paths.progress_json)
    inst.progress.data = records
    inst.discovery = storage.JsonStore(inst.paths.discovery_json)
    return inst


def document(mod, app: Path, account: str):
    """One of the app's documents with every field its key reads filled in
    as its records hold them, and a category. A document id only where the
    key does not read it, as Capital One and Schwab keep one beside the key,
    since elsewhere it would make the key the id alone."""
    reads = key_reads(app)
    fields = {f: VALUES[f] for f in reads & set(VALUES) - {"document_id"}}
    fields.update({f: account for f in reads & ACCOUNT_FIELDS}, account=account)
    fields.setdefault("category", VALUES["category"])
    if "document_id" not in reads:
        fields["document_id"] = VALUES["document_id"]
    return mod.Document(**fields)


def old_keys(rebuild, doc, held: dict) -> list:
    """Every key the app's own move takes this record from to the key it
    has now, found by running the move on the key with each last four it
    may have added taken back out. So nothing here builds an old key the
    way the tool decides one."""
    found = []
    for added in re.finditer(r"-\d{4}", doc.key):
        old = doc.key[:added.start()] + doc.key[added.end():]
        trial = {old: copy.deepcopy(held)}
        if rebuild(trial) == 1 and doc.key in trial:
            found.append(old)
    return found


def moved(tmp_path: Path, app: Path, records: dict, minimal: bool, new=None) -> dict:
    """The records exported from one computer's install and imported into
    one on another, fresh or holding NEW."""
    for side, held in (("old", records), ("new", new or {})):
        out = tmp_path / side / app.name
        out.mkdir(parents=True)
        shutil.copy(app / "storage.py", out / "storage.py")
        (out / "progress.json").write_text(json.dumps(held), encoding="utf-8")
    archive = tmp_path / "history.ppz"
    assert migrate.export(tmp_path / "old", archive, minimal=minimal) == 0
    assert migrate.do_import(archive, tmp_path / "new", assume_yes=True) == 0
    path = tmp_path / "new" / app.name / "progress.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("app,move", MOVERS, ids=["%s.%s" % (a.name, m) for a, m in MOVERS])
def test_a_cut_key_moved_with_minimal_is_moved_and_skipped(app, move, loading, tmp_path):
    mod = loading(app)
    rebuild = getattr(mod, move)
    moves = 0
    for n, account in enumerate(ACCOUNTS):
        doc = document(mod, app, account)
        held = dict(doc.to_dict(), state=State.COMPLETED.value, downloaded_ok=True,
                    pdf_path=str(tmp_path / "old" / app.name / "Statements" / "x.pdf"))
        # What the old computer would have done with it.
        found = old_keys(rebuild, doc, held)
        assert found or account not in LONG, (
            "%s runs %s over its records as it loads and it moves no cut key. "
            "Read what it reads, and add it to OTHER_MOVES with why --minimal "
            "need not carry that, or carry it in tools/migrate.py" % (app.name, move))
        moves += len(found)

        # The new computer may have listed the statement already, under its
        # whole key, and the move takes the saved record over it only when
        # the two are the same document. Or an older --minimal may have
        # brought the record there without what its key is built from.
        listed = dict(document(mod, app, account).to_dict(),
                      state=State.DISCOVERED.value, downloaded_ok=False)
        for m, old_key in enumerate(found):
            stripped = dict(migrate._skip_fields(held), imported_at="2031-04-01T10:00:00")
            for v, (minimal, new) in enumerate(((False, None), (True, None),
                                                (True, {doc.key: listed}),
                                                (True, {old_key: stripped}))):
                here = tmp_path / ("%d-%d-%d" % (n, m, v))
                records = moved(here, app, {old_key: held}, minimal, new)
                for loaded in LOADS[app]:
                    getattr(mod, loaded)(records)
                assert old_key not in records and doc.key in records, (
                    "%s left a record moved %s under a key without the account's "
                    "last four, so the statement listed under its whole key is "
                    "downloaded again" % (app.name, "with --minimal" if minimal else "whole"),
                    account, new and sorted(new))
                assert app_over(mod, here / "out", records)._already_done(doc), \
                    (app.name, account, minimal, new and sorted(new))
    assert moves, app.name


# -- what --minimal carries for it --------------------------------------------

def _install(root: Path, provider: str, records: dict) -> Path:
    folder = root / ("%s Statements" % provider)
    folder.mkdir(parents=True)
    (folder / "storage.py").write_text(
        'SPEC = AppSpec(provider="%s", kind=DOCUMENT)\n' % provider, encoding="utf-8")
    (folder / "progress.json").write_text(json.dumps(records), encoding="utf-8")
    return folder


def _minimal_blob(tmp_path: Path, records: dict, provider: str = "Bank") -> dict:
    _install(tmp_path / "old", provider, records)
    archive = tmp_path / "h.ppz"
    assert migrate.export(tmp_path / "old", archive, minimal=True) == 0
    with zipfile.ZipFile(archive) as z:
        blob = z.read("apps/%s Statements/progress.json" % provider)
    return json.loads(blob.decode("utf-8"))


HISTORY = {"total": "12.34", "items": "Garden Hose", "summary": "Invented Statement",
           "order_number": "INV0001", "store_info": "Main St", "pdf_size": "4096",
           "state": State.COMPLETED.value, "downloaded_ok": True, "pdf_path": ""}


def test_a_cut_key_carries_only_what_its_key_is_rebuilt_from(tmp_path):
    account = ACCOUNTS[0]
    rec = dict(HISTORY, category="Statement", doc_type="Statement", date="2031-03-31",
               title="Invented Monthly Statement", account=account, occurrence=1,
               document_id="DOC0001")
    whole = "Statement:2031-03-31:Invented Monthly Statement:" + account_component(account)
    cut = "Statement:2031-03-31:Invented Monthly Statement:" + account[:40]
    got = _minimal_blob(tmp_path, {cut: rec, whole: dict(rec)})
    carried = (set(migrate.SKIP_FIELDS) | set(migrate.KEY_FIELDS)) & set(rec)
    assert set(got[cut]) == carried, got[cut]
    assert got[cut]["account"] == account
    # The same record under its whole key moves nowhere and carries none of it.
    assert not set(migrate.KEY_FIELDS) & set(got[whole]), got[whole]


SHORT = "Statement:2031-03-31:Invented Monthly Statement:Invented Checking (...1234)"


def test_a_key_that_never_cut_its_account_carries_none_of_it(tmp_path):
    """A short account, a long one with no last four to keep, and a key
    that is a document id, none of them is ever moved."""
    short = dict(HISTORY, category="Statement", date="2031-03-31",
                 title="Invented Monthly Statement", account="Invented Checking (...1234)")
    no_four = dict(short, account="An invented account name with no digits at its end")
    by_id = dict(short, account=ACCOUNTS[0], document_id="DOC0001")
    got = _minimal_blob(tmp_path, {
        SHORT: short,
        "Statement:2031-03-31:Invented Monthly Statement:" + no_four["account"][:40]: no_four,
        "id:DOC0001": by_id})
    for key, rec in got.items():
        assert not set(migrate.KEY_FIELDS) & set(rec), (key, rec)


def test_a_chase_key_without_a_short_accounts_last_four_carries_it(tmp_path):
    """Chase keeps the last four of an account of any length, so a short
    account's record under the key Chase wrote before 0.17.1 is moved too,
    and the same record under the key it writes now is not."""
    short = dict(HISTORY, category="Statement", date="2031-03-31",
                 title="Invented Monthly Statement", account="Invented Checking (...1234)",
                 document_id="")
    got = _minimal_blob(tmp_path, {SHORT: short, SHORT + "-1234": dict(short)}, "Chase")
    assert got[SHORT]["account"] == short["account"], got[SHORT]
    assert not set(migrate.KEY_FIELDS) & set(got[SHORT + "-1234"]), got[SHORT + "-1234"]


def test_an_import_adds_nothing_to_a_record_the_app_wrote(tmp_path, capsys):
    """Only a record that holds none of what its key is built from takes it,
    so a field a newer version added is not counted as a change."""
    rec = dict(HISTORY, category="Statement", date="2031-03-31",
               title="Invented Monthly Statement", account="Invented Checking (...1234)")
    _install(tmp_path / "old", "Bank", {SHORT: dict(rec, listed_account=rec["account"])})
    archive = tmp_path / "h.ppz"
    assert migrate.export(tmp_path / "old", archive) == 0
    folder = _install(tmp_path / "new", "Bank", {SHORT: rec})
    capsys.readouterr()
    assert migrate.do_import(archive, tmp_path / "new", assume_yes=True) == 0
    assert "0 new,    0 marked done" in capsys.readouterr().out
    got = json.loads((folder / "progress.json").read_text(encoding="utf-8"))
    assert "listed_account" not in got[SHORT], got[SHORT]
