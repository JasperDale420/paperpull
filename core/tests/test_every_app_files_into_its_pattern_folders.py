"""Every app files a download in the folders its naming pattern names, and
its Rename moves files out of them and back in, the ledgers following
(#101).

A / in a naming pattern names folders inside the folder each kind of
document is filed in. build_pdf_filename hands the folders over with the
name (storage.FileName), and an app's own download hands that name to
unique_path, which files it there. Rename asks where a file belongs the
same way, so a file a download filed in a pattern's folders is named right
already, and a pattern changed later moves each file to where a download
would now put it, out of the folders or into them.

Each app here is built as it is at home from its own code, with a made-up
config and output folder and no browser (the rename census harness,
test_every_app_renames_each_file_for_its_own_record). Two documents of one
date for two accounts are saved where the app's own download_one saves
them, or two purchases of one day where the app's own folders route them,
and each is written down by the app's own code. Then the app's own Rename
runs, as the panel's File names page runs it. That a receipt app's own
download hands its name to unique_path whole is held by the census in
test_every_app_hands_its_file_name_on_whole.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from paperpull_core import storage as core_storage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_naming_folders import limit_for  # noqa: E402


def _rename_census():
    path = Path(__file__).with_name("test_every_app_renames_each_file_for_its_own_record.py")
    spec = importlib.util.spec_from_file_location("rename_census_for_folders", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


census = _rename_census()
APPS, IDS = census.APPS, census.IDS
ACCOUNTS = census.ACCOUNTS
# The year as a folder, and the account as one inside it where a record has
# an account, which a purchase does not. The name carries the account and
# the order number, so each file says whose it is wherever it is.
FOLDERED = "{year}/[{account}/]{date:yyyy-mm-dd} {provider}[ {account}][ {number}] {summary}"


@pytest.fixture
def home(request, tmp_path, monkeypatch, capsys):
    """The app at home, with a path limit that leaves its longest folder
    room for the names here however deep pytest's temporary folder is
    (test_naming_folders.limit_for)."""
    home = census.Home(request.param, tmp_path, monkeypatch, capsys)
    longest = max(len(f.name) for f in core_storage.spec().folders)
    home.config["max_path_length"] = limit_for(home.out / ("x" * longest))
    home._write_config()
    return home


def saved_two(home, app):
    """Two documents or two purchases saved where the app's own code saves
    them under the pattern it was built with, and the record key and the
    values that tell each one's file."""
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
    return paths, key, values


def filed_in(app, path: Path) -> list:
    """The folders between the folder a file is filed in and the file."""
    for folder in app.paths.filing_folders():
        try:
            return list(path.relative_to(folder).parts[:-1])
        except ValueError:
            continue
    raise AssertionError("%s is in none of the app's folders" % path)


def ledgers_follow(home, key: str, values) -> dict:
    """Each file, found by its bytes, with the folders it is in, after the
    index and the run state are shown to name it there."""
    app = home.build()
    rows = app.index_csv.read_all()
    files = home.files()
    assert sorted(files) == sorted(census.body_of(a) for a in ACCOUNTS), sorted(files)
    out = {}
    for account, value in zip(ACCOUNTS, values):
        path = files[census.body_of(account)]
        records = [r for r in app.progress.data.values()
                   if isinstance(r, dict) and r.get(key) == value]
        assert len(records) == 1 and Path(records[0]["pdf_path"]) == path, (
            "%s's run state does not name %s" % (home.app.name, path))
        assert [r for r in rows if Path(r["PDF Full Path"]) == path], (
            "%s's index does not name %s" % (home.app.name, path))
        out[value] = filed_in(app, path)
    return out


@pytest.mark.parametrize("home", APPS, ids=IDS, indirect=True)
def test_a_download_files_in_the_folders_and_rename_moves_files_out_and_back(home):
    home.set_pattern(FOLDERED)
    app = home.build()
    paths, key, values = saved_two(home, app)
    for path in paths:
        assert filed_in(app, path)[:1] == ["2026"], (
            "%s's download saved %s outside the pattern's folders" % (home.app.name, path))
    said = home.rename()
    assert census.NAMED_ALREADY in said, (
        "%s's Rename would move what its download filed\n%s" % (home.app.name, said[-1500:]))

    home.set_pattern("")
    said = home.rename("--apply")
    assert census.RENAMED_BOTH in said, said[-1500:]
    assert all(not folders for folders in ledgers_follow(home, key, values).values()), said
    assert not [p for p in home.out.rglob("2026") if p.is_dir()], (
        "%s left the pattern's folders behind" % home.app.name)

    home.set_pattern(FOLDERED)
    said = home.rename("--apply")
    assert census.RENAMED_BOTH in said, said[-1500:]
    census.each_file_is_its_own(home, said, key, values)
    for value, folders in ledgers_follow(home, key, values).items():
        assert folders[:1] == ["2026"], (home.app.name, value, folders)
    said = home.rename()
    assert census.NAMED_ALREADY in said, said[-1500:]
    assert home.browser.asked == 0, "Rename reached for a browser"
