"""Every receipt app's Review Names files a receipt in the folders its
naming pattern names, and puts it back when a ledger cannot be written
(#101).

Review Names renames a receipt to the summary a person types, by the same
naming pattern a download names it by. A pattern with a / names folders
inside the folder a receipt is filed in, Online or In-Store, and the review
asked for a free name in the receipt's own folder, which would have made
the pattern's folders inside the ones it was already in. Now the receipt
goes to the folders its new name gives inside the folder of its kind, its
index rows, its order history rows and its record follow it, and a folder
it leaves empty is taken away. When a write fails after the receipt moved,
it goes back where it was and a folder made for it goes too.

These run each receipt app's own review over an output folder on disk,
with the typing stood in for and each receipt written down by the app's
own code (test_every_app_reviews_a_name_once). Every name, number and
answer is invented.
"""
import json
import sys
from pathlib import Path

import pytest

from paperpull_core import storage as core_storage
from paperpull_core.models import ONLINE

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_every_app_reviews_a_name_once as census  # noqa: E402
import test_review_names_with_a_ledger_held_open as held  # noqa: E402

APPS = census.APPS
IDS = [a.name for a in APPS]
PATTERN = "{provider}/{year}/{date:yyyy-mm-dd} {summary}"
ANSWERS = {"ORDER-0001": "Garden Hose", "ORDER-0002": "Lamp"}


@pytest.fixture(autouse=True)
def default_names():
    core_storage.set_filename_owner("")
    core_storage.set_filename_patterns({})
    yield
    core_storage.set_filename_patterns({})


def under(folder: Path) -> dict:
    """Every file below `folder` with its bytes, by its path inside it, and
    every folder as None."""
    return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in folder.rglob("*")}


def ledgers(inst) -> dict:
    progress = inst.paths.progress_json
    return {"index": inst.index_csv.read_all(), "orders": inst.order_csv.read_all(),
            "progress": (json.loads(progress.read_text(encoding="utf-8"))
                         if progress.exists() else {})}


def with_folders(app: Path, tmp_path):
    """Two receipts the app was unsure of, saved in the folder of their kind
    with no folders, and then a naming pattern with folders chosen."""
    mod, inst, filed = held.two_unsure(app, tmp_path)
    core_storage.set_filename_patterns({"filename_pattern": PATTERN})
    return mod, inst, filed


def folders_for(summary: str) -> str:
    """The folders a receipt of that summary goes in, under the pattern."""
    return core_storage.folder_of(core_storage.build_pdf_filename(
        "2026-05-14", summary, "Receipt", record={"purchase_date": "2026-05-14"}))


def test_the_pattern_names_folders_for_every_app():
    assert len(APPS) >= 14, IDS
    core_storage.set_filename_patterns({"filename_pattern": PATTERN})
    assert folders_for("Lamp") == "Testco/2026"


@pytest.mark.parametrize("app", APPS, ids=IDS)
def test_a_new_name_files_the_receipt_in_its_folders(app, tmp_path, monkeypatch, capsys):
    mod, inst, filed = with_folders(app, tmp_path)
    asked, stop, said = held.reviewed(mod, inst, monkeypatch, capsys,
                                      answers=tuple(ANSWERS.values()))
    assert stop is None, said[-1500:]
    assert "CSV files and progress.json updated." in said, said[-1500:]
    files = {data: path for path, data in under(filed).items() if data is not None}
    assert len(files) == 2, files
    led = ledgers(inst)
    for number, summary in ANSWERS.items():
        folder = folders_for(summary)
        assert folder.endswith("/2026") and len(folder.split("/")) == 2, folder
        where = files[held.body_of(number)]
        assert where.startswith(folder + "/"), (where, said[-1500:])
        path = filed / where
        [row] = [r for r in led["index"] if r["Order or Receipt Number"] == number]
        assert Path(row["PDF Full Path"]) == path and row["PDF Filename"] == path.name
        assert [r["PDF Filename"] for r in led["orders"]
                if r["Order or Receipt Number"] == number] == [path.name]
        [rec] = [r for r in led["progress"].values() if r.get("order_number") == number]
        assert Path(rec["pdf_path"]) == path and rec["pdf_filename"] == path.name
    assert not [p for p in filed.iterdir() if p.is_file()], "a receipt was left behind"


@pytest.mark.parametrize("app", APPS, ids=IDS)
def test_a_receipt_already_in_its_folders_stays_in_them(app, tmp_path, monkeypatch, capsys):
    """A receipt a download filed in the pattern's folders takes its new
    name there. Asked for a name in its own folder, the review would have
    made the pattern's folders again inside them."""
    mod = census.load(app)
    inst = census.runnable(mod, tmp_path / "out")
    core_storage.set_filename_patterns({"filename_pattern": PATTERN})
    filed = inst.paths.folder_for(ONLINE)
    folder = filed.joinpath(*folders_for("Unsure").split("/"))
    folder.mkdir(parents=True, exist_ok=True)
    for number, name in (("ORDER-0001", held.FIRST), ("ORDER-0002", held.SECOND)):
        path = folder / name
        path.write_bytes(held.body_of(number))
        census.as_a_run_left_it(inst, number, path, saved=True, confidence="Low")
    asked, stop, said = held.reviewed(mod, inst, monkeypatch, capsys,
                                      answers=tuple(ANSWERS.values()))
    assert stop is None, said[-1500:]
    files = {data: path for path, data in under(filed).items() if data is not None}
    inside = folder.relative_to(filed).as_posix()
    for number, summary in ANSWERS.items():
        where = files[held.body_of(number)]
        assert where.rsplit("/", 1)[0] == inside, (where, said[-1500:])
        assert summary in where
    parts = inside.split("/")
    folders = {p for p, data in under(filed).items() if data is None}
    assert folders == {"/".join(parts[:i + 1]) for i in range(len(parts))}, (
        "a folder was made inside them", folders)


@pytest.mark.parametrize("app", APPS, ids=IDS)
def test_a_ledger_that_will_not_write_puts_the_receipt_back_and_its_folder_goes(
        app, tmp_path, monkeypatch, capsys):
    mod, inst, filed = with_folders(app, tmp_path)
    before_files, before = under(filed), ledgers(inst)
    real = core_storage.atomic_write_text
    index = Path(inst.index_csv.path).name

    def refuse(path, text, encoding="utf-8", **how):
        if Path(path).name == index:
            raise PermissionError("%s stayed locked." % path)
        return real(path, text, encoding, **how)

    monkeypatch.setattr(core_storage, "atomic_write_text", refuse)
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)
    asked, stop, said = held.reviewed(mod, inst, monkeypatch, capsys,
                                      answers=tuple(ANSWERS.values()))
    assert held.exit_code(stop) == 1, said[-1500:]
    assert len(asked) == 1
    assert "so the receipt was put back under the name it had" in said, said[-1500:]
    assert under(filed) == before_files, "a folder made for the receipt was left behind"
    monkeypatch.setattr(core_storage, "atomic_write_text", real)
    inst.progress.save()
    assert ledgers(inst) == before
