"""Every app checks its documents in its own folder, knows the account's
documents folder, and moves its checked documents there (#102).

A config's documents_dir reaches an app through the Paths it builds from
its config's output_dir (core storage.Paths, documents_dir_for), which
load_config reads first. So every app has to build its Paths that very
way, after load_config, and the census here reads each app's own code for
it. Then each app is built as it is at home from its own code, with a
made-up config and output folder and no browser (the rename census
harness, test_every_app_renames_each_file_for_its_own_record), and asked
where each of its folders is, with no documents_dir and with one. Every
folder stays in the output folder, so a download is checked there, and a
run writes nothing in the documents folder. Two of its documents saved
before the folder was set stay where they are, are named right, verified
and counted as done where they are, and the panel's Move, the app's own
Rename with MOVE_DOCUMENTS_ENV set, takes them to the documents folder
with the index and the run state following.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

from paperpull_core import renaming
from paperpull_core import storage as core_storage


def _rename_census():
    path = Path(__file__).with_name("test_every_app_renames_each_file_for_its_own_record.py")
    spec = importlib.util.spec_from_file_location("rename_census_for_documents", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


census = _rename_census()
REPO = census.REPO
APPS, IDS = census.APPS, census.IDS
ACCOUNTS = census.ACCOUNTS
# The folders every app keeps beside its records (storage._KEPT_WITH_RECORDS).
KEPT = {"manual_review", "logs", "diagnostics", "backups"}


def entries():
    return sorted(p for d in (REPO / "apps").iterdir() if d.is_dir()
                  for p in [*d.glob("*_docs.py"), *d.glob("*_receipts.py")])


BUILT = "Paths(Path(self.config['output_dir']))"


def builds_its_paths_from_its_config(source: str) -> list:
    """What is wrong with how a module's App builds its Paths. Its __init__
    has to set self.config from load_config and then build self.paths as
    Paths(Path(self.config["output_dir"])), and nothing else may build a
    Paths."""
    tree = ast.parse(source)
    wrong = []
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ast.unparse(n.func) in ("Paths", "storage.Paths")]
    if not calls:
        return ["it builds no Paths"]
    for call in calls:
        if ast.unparse(call) != BUILT:
            wrong.append("line %d builds %s" % (call.lineno, ast.unparse(call)))
    init = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and n.name == "__init__" and any(ast.unparse(c) == BUILT
                                                  for c in ast.walk(n)
                                                  if isinstance(c, ast.Call))), None)
    if init is None:
        return wrong + ["no __init__ builds its Paths"]
    loaded = [n.lineno for n in ast.walk(init) if isinstance(n, ast.Assign)
              and ast.unparse(n.targets[0]) == "self.config"
              and isinstance(n.value, ast.Call) and ast.unparse(n.value.func) == "load_config"]
    built = [c.lineno for c in ast.walk(init) if isinstance(c, ast.Call)
             and ast.unparse(c) == BUILT]
    if not loaded or min(loaded) > min(built):
        wrong.append("its Paths is built before self.config comes from load_config")
    return wrong


CANARY = '''
class App:
    def __init__(self, args):
        self.paths = Paths(Path(self.config["output_dir"]))
        self.config = load_config(args.config)

    def elsewhere(self):
        return Paths(self.config["output_dir"])
'''


def test_the_census_tells_a_paths_built_some_other_way():
    wrong = builds_its_paths_from_its_config(CANARY)
    assert any("before self.config" in w for w in wrong), wrong
    assert any("builds Paths(self.config['output_dir'])" in w for w in wrong), wrong


def test_every_app_builds_its_paths_from_the_config_it_loaded():
    found = entries()
    assert len(found) >= 70, len(found)
    wrong = ["%s: %s" % (p.relative_to(REPO).as_posix(), w) for p in found
             for w in builds_its_paths_from_its_config(p.read_text(encoding="utf-8-sig"))]
    assert not wrong, "\n".join(wrong)


# -- every app, built at home ------------------------------------------------------------

@pytest.fixture
def home(request, tmp_path, monkeypatch, capsys):
    return census.Home(request.param, tmp_path, monkeypatch, capsys)


def everywhere(home) -> dict:
    """Every PDF in the output folder and the documents folder beside it,
    by the bytes in it."""
    return {p.read_bytes(): p for p in home.out.parent.rglob("*.pdf")}


def run(home, *flags) -> str:
    """The app's own main with these flags, as the panel runs it, and what
    it said."""
    home.capsys.readouterr()
    try:
        home.mod.main(["--config", str(home.cfg), *flags])
    except SystemExit:
        pass
    return " ".join(home.capsys.readouterr().out.split())


def still_found(home, saved: int) -> None:
    """With the documents folder set, the app's own Verify finds every file
    its index names, and its own skip decision still counts each document
    saved as done, so none is downloaded again (the delete-safe skip). The
    invented files are not whole PDFs, so Verify reads each and says so,
    and what is held here is that it finds every one where it is."""
    said = run(home, "--verify")
    assert "Verified %d index rows" % saved in said, said[-1500:]
    assert "File does not exist" not in said, said[-1500:]
    app = home.build()
    kind = getattr(home.mod, "Document", None) or home.mod.Purchase
    done = [rec for rec in app.progress.data.values()
            if isinstance(rec, dict) and app._already_done(kind.from_dict(rec))]
    assert len(done) == saved, "%s would download again what it saved" % home.app.name


def set_documents(home, docs: Path):
    home.config["documents_dir"] = str(docs)
    home._write_config()


@pytest.mark.parametrize("home", APPS, ids=IDS, indirect=True)
def test_every_app_writes_and_checks_in_its_own_folder_and_knows_its_documents_folder(home):
    """A download is written and checked where it always was, with or
    without a documents folder, and only the Move ever writes there."""
    out, docs = home.out, home.out.parent / "Watched"
    app = home.build()
    folders = [f for f in core_storage.spec().folders]
    for f in folders:
        assert getattr(app.paths, f.attr) == out / f.name, f.attr
    assert app.paths.documents == out and app.paths.documents_moves() == {}
    set_documents(home, docs)
    app = home.build()
    for f in folders:
        assert getattr(app.paths, f.attr) == out / f.name, f.attr
    assert app.paths.documents == docs
    assert app.paths.documents_moves() == {out / f.name: docs / f.name for f in folders
                                           if f.attr not in KEPT}
    for record in (app.index_csv.path, app.progress.path, app.discovery.path):
        assert Path(record).parent == out, record
    app.paths.ensure()
    assert not docs.exists(), "a run wrote into the documents folder"


@pytest.mark.parametrize("home", APPS, ids=IDS, indirect=True)
def test_every_app_finds_what_it_saved_before_and_moves_it(home, monkeypatch):
    app = home.build()
    if hasattr(home.mod, "Document"):
        pair = census.two_documents(home)
        if pair is None:
            pytest.skip("its key is the date and the title alone, so it never "
                        "holds two documents of one date and title")
        paths = [census.downloaded(app, doc) for doc in pair]
        key, values = "account", ACCOUNTS
    else:
        values = tuple("ORD" + a for a in ACCOUNTS)
        paths = [census.purchased(home, app, number) for number in values]
        key = "order_number"
    census.saved(app)
    docs = home.out.parent / "Watched"
    set_documents(home, docs)
    said = home.rename()
    assert census.NAMED_ALREADY in said, (
        "%s did not find what it saved before\n%s" % (home.app.name, said[-1500:]))
    assert sorted(everywhere(home).values()) == sorted(paths), "setting the folder moved a file"
    still_found(home, len(paths))

    monkeypatch.setenv(renaming.MOVE_DOCUMENTS_ENV, "1")
    said = home.rename("--apply")
    assert "Moved 2 file(s)" in said, said[-1500:]
    app = home.build()
    rows = app.index_csv.read_all()
    files = everywhere(home)
    assert sorted(files) == sorted(census.body_of(a) for a in ACCOUNTS)
    for account, value, before in zip(ACCOUNTS, values, paths):
        path = files[census.body_of(account)]
        assert path.name == before.name, "the move renamed %s" % before.name
        assert docs in path.parents and path.parent.name == before.parent.name, path
        records = [r for r in app.progress.data.values()
                   if isinstance(r, dict) and r.get(key) == value]
        assert len(records) == 1 and Path(records[0]["pdf_path"]) == path
        assert [r for r in rows if Path(r["PDF Full Path"]) == path]
    said = home.rename()
    assert "Every document is in the documents folder already." in said, said[-1500:]
    monkeypatch.delenv(renaming.MOVE_DOCUMENTS_ENV)
    said = home.rename()
    assert census.NAMED_ALREADY in said, said[-1500:]
    assert home.browser.asked == 0, "Rename reached for a browser"
