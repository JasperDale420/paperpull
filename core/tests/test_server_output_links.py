"""On PaperPull Server a run writes nothing through a link out of /data.

A provider's folder lives in the data folder, often a share other people
can write, and some shares let them leave a link there. The container's
user also owns /config and /profiles, so a write through such a link could
land on the server's password or a signed-in profile. These tests leave
links where a run writes, in a throwaway data folder, and a canary file and
folder outside it, and check that on the server nothing reaches the canary
and the run stops with a sentence, while off the server everything is
written exactly as before.

Windows gives an ordinary user no symbolic links, so a hard link stands in
for a link at a file's name and a junction for a folder that is a link.
Both are made with no special right, so these run on Windows CI. A real
symbolic link is tried too where the system allows it.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import capture, delivery, renaming, shared_folder, storage

CANARY = "the server's own settings, which no run may touch\n"


def link_folder(target: Path, link: Path) -> None:
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def can_symlink(tmp_path) -> bool:
    try:
        (tmp_path / "probe-link").symlink_to(tmp_path / "probe-target")
        (tmp_path / "probe-link").unlink()
        return True
    except (OSError, NotImplementedError):
        return False


@pytest.fixture()
def place(tmp_path, monkeypatch):
    """A data folder with one install, the run's working folder, and
    outside it a canary file and an empty folder, as /config would be."""
    data = tmp_path / "data"
    install = data / "Testco Statements"
    install.mkdir(parents=True)
    outside = tmp_path / "config"
    outside.mkdir()
    canary = outside / "server.json"
    canary.write_text(CANARY, encoding="utf-8")
    elsewhere = tmp_path / "profiles"
    elsewhere.mkdir()
    monkeypatch.setenv("APPS_ROOT", str(data))
    monkeypatch.setenv("PAPERPULL_PROFILES", str(elsewhere))
    monkeypatch.chdir(install)
    return SimpleNamespace(data=data, install=install, canary=canary, elsewhere=elsewhere)


@pytest.fixture()
def server(place, monkeypatch):
    monkeypatch.setenv("PAPERPULL_SERVER", "1")
    return place


@pytest.fixture()
def desktop(place, monkeypatch):
    monkeypatch.delenv("PAPERPULL_SERVER", raising=False)
    return place


def untouched(place) -> bool:
    return (place.canary.read_text(encoding="utf-8") == CANARY
            and sorted(p.name for p in place.elsewhere.iterdir()) == [])


# -- where a path leads ---------------------------------------------------------------------

def test_a_folder_linked_out_of_the_data_folder_leads_out(server):
    link_folder(server.elsewhere, server.install / "Logs")
    assert shared_folder.leads_out(server.install / "Logs")
    assert shared_folder.leads_out(server.install / "Logs" / "run.log")
    assert shared_folder.leads_out(Path("Logs") / "deeper" / "run.log")
    assert not shared_folder.leads_out(server.install / "Statements")
    assert not shared_folder.leads_out(server.data)
    # A path named outside the data folder is not this module's to judge,
    # a browser profile or a temporary folder for instance.
    assert not shared_folder.leads_out(server.elsewhere / "Testco Statements")


def test_a_folder_linked_inside_the_data_folder_does_not(server):
    other = server.data / "Testco Archive"
    other.mkdir()
    link_folder(other, server.install / "Statements")
    assert not shared_folder.leads_out(server.install / "Statements" / "x.pdf")


def test_hold_stops_the_run_on_the_server_only(place, monkeypatch):
    link_folder(place.elsewhere, place.install / "Logs")
    monkeypatch.delenv("PAPERPULL_SERVER", raising=False)
    shared_folder.hold(place.install / "Logs")
    monkeypatch.setenv("PAPERPULL_SERVER", "1")
    with pytest.raises(SystemExit) as stopped:
        shared_folder.hold(place.install / "Logs")
    assert isinstance(stopped.value, shared_folder.LeadsOut)
    said = str(stopped.value)
    assert "Logs" in said and "link" in said and "this run stopped" in said


# -- a link at a file's own name ----------------------------------------------------------------

@pytest.mark.parametrize("mode", ["w", "a", "wb", "ab"])
def test_a_link_at_the_name_is_never_written_through_on_the_server(server, mode):
    name = server.install / "Testco Document Index.csv"
    os.link(server.canary, name)
    with pytest.raises(shared_folder.LeadsOut):
        with shared_folder.open_to_write(name, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
            f.write(b"overwritten" if "b" in mode else "overwritten")
    assert untouched(server)


def test_a_real_symbolic_link_at_the_name_is_refused_too(server, tmp_path):
    if not can_symlink(tmp_path):
        pytest.skip("this system makes no symbolic links for an ordinary user")
    name = server.install / "Testco Document Index.csv"
    name.symlink_to(server.canary)
    with pytest.raises(shared_folder.LeadsOut):
        with shared_folder.open_to_write(name, "a", encoding="utf-8") as f:
            f.write("overwritten")
    with pytest.raises(shared_folder.LeadsOut):
        shared_folder.write_bytes(name, b"overwritten")
    assert untouched(server)


def test_a_plain_file_is_written_and_appended_as_before(server):
    name = server.install / "index.csv"
    with shared_folder.open_to_write(name, "w", encoding="utf-8", newline="") as f:
        f.write("a,b\r\n")
    with shared_folder.open_to_write(name, "a", encoding="utf-8", newline="") as f:
        f.write("1,2\r\n")
    with shared_folder.open_to_write(name, "a", encoding="utf-8", newline="") as f:
        f.write("3,4\r\n")
    assert name.read_bytes() == b"a,b\r\n1,2\r\n3,4\r\n"
    with shared_folder.open_to_write(name, "w", encoding="utf-8", newline="") as f:
        f.write("new\r\n")
    assert name.read_bytes() == b"new\r\n"
    shared_folder.write_bytes(name, b"%PDF-1.7 bytes")
    assert name.read_bytes() == b"%PDF-1.7 bytes"


def test_off_the_server_a_write_goes_where_it_always_did(desktop):
    """The desktop's own folder is its owner's alone, and nothing about how
    it writes changed. A link there is followed, as it always was."""
    name = desktop.install / "index.csv"
    os.link(desktop.canary, name)
    with shared_folder.open_to_write(name, "a", encoding="utf-8") as f:
        f.write("appended")
    assert desktop.canary.read_text(encoding="utf-8") == CANARY + "appended"


# -- the core's own writers --------------------------------------------------------------------

def test_the_csv_index_is_never_appended_through_a_link(server):
    path = server.install / "Testco Document Index.csv"
    os.link(server.canary, path)
    index = storage.CsvFile(path, ["Account Holder", "Notes"])
    with pytest.raises(shared_folder.LeadsOut):
        index.append_rows([{"Account Holder": "Dana Example", "Notes": "x"}])
    assert untouched(server)


def test_the_csv_index_is_written_on_the_server_as_on_the_desktop(server, desktop_twin):
    rows = [{"Account Holder": "Dana Example", "Notes": "first"},
            {"Account Holder": "Dana Example", "Notes": "second"}]
    here = storage.CsvFile(server.install / "index.csv", ["Account Holder", "Notes"])
    here.append_rows(rows[:1])
    here.append_rows(rows[1:])
    assert (server.install / "index.csv").read_bytes() == desktop_twin(rows)


@pytest.fixture()
def desktop_twin(tmp_path, monkeypatch):
    def write(rows):
        with monkeypatch.context() as m:
            m.delenv("PAPERPULL_SERVER", raising=False)
            twin = storage.CsvFile(tmp_path / "twin.csv", ["Account Holder", "Notes"])
            twin.append_rows(rows[:1])
            twin.append_rows(rows[1:])
        return (tmp_path / "twin.csv").read_bytes()
    return write


def test_a_routed_folder_left_as_a_link_stops_the_run(server):
    paths = storage.Paths(".")
    link_folder(server.elsewhere, paths.root / "Other Documents")
    with pytest.raises(shared_folder.LeadsOut):
        paths.folder_for("Something no route names")
    assert untouched(server)


def test_a_records_folder_left_as_a_link_stops_the_run_before_it_is_made(server):
    paths = storage.Paths(".")
    link_folder(server.elsewhere, server.install / "Logs")
    with pytest.raises(shared_folder.LeadsOut):
        paths.ensure()
    assert untouched(server)


def test_the_folders_are_made_as_before_when_nothing_is_a_link(server):
    paths = storage.Paths(".")
    paths.ensure()
    assert all(d.is_dir() for d in paths.all_dirs())
    assert [p.name for p in server.install.iterdir() if p.name.startswith(".write-probe")] == []


def test_the_write_probe_is_never_written_through_a_link_at_its_old_name(server):
    """The probe was .write-probe-<pid>.tmp, a name anyone could guess."""
    os.link(server.canary, server.install / (".write-probe-%d.tmp" % os.getpid()))
    storage.Paths(".").ensure()
    assert untouched(server)


def test_a_document_the_core_asks_for_again_is_never_written_through_a_link(server):
    import base64
    out = server.install / "x.pdf.delivering"
    os.link(server.canary, out)
    page = SimpleNamespace(url="https://provider.example/documents",
                           evaluate=lambda *_a: base64.b64encode(b"%PDF-1.7 a statement").decode())
    with pytest.raises(shared_folder.LeadsOut):
        capture.ask_again(page, [("GET", "https://provider.example/doc.pdf")], out,
                          lambda _url: True)
    assert untouched(server)


def test_a_save_into_a_folder_left_as_a_link_stops_the_run(server):
    link_folder(server.elsewhere, server.install / "Diagnostics")
    with pytest.raises(shared_folder.LeadsOut):
        storage.atomic_write_text(server.install / "Diagnostics" / "failure.json", "{}")
    assert untouched(server)


def test_a_save_replaces_a_link_at_its_own_name_rather_than_follow_it(server):
    os.link(server.canary, server.install / "progress.json")
    storage.atomic_write_json(server.install / "progress.json", {"k": {"state": "Completed"}})
    assert untouched(server)
    assert '"Completed"' in (server.install / "progress.json").read_text(encoding="utf-8")


def test_a_backup_never_lands_through_a_link(server, monkeypatch):
    class Now:
        @staticmethod
        def now():
            from datetime import datetime
            return datetime(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr(storage, "datetime", Now)
    (server.install / "progress.json").write_text("{}", encoding="utf-8")
    backups = server.install / "Backups"
    backups.mkdir()
    os.link(server.canary, backups / "progress.20260102-030405.json.bak")
    made = storage.backup_file(server.install / "progress.json", backups)
    assert made.name == "progress.20260102-030405-2.json.bak"
    assert made.read_text(encoding="utf-8") == "{}"
    assert untouched(server)

    gone = server.install / "Old Backups"
    link_folder(server.elsewhere, gone)
    with pytest.raises(shared_folder.LeadsOut):
        storage.backup_file(server.install / "progress.json", gone)
    # Nor is a folder made through one, a level below it.
    with pytest.raises(shared_folder.LeadsOut):
        storage.backup_file(server.install / "progress.json", gone / "deeper")
    assert untouched(server)


def test_a_backup_never_copies_a_file_read_through_a_link(server):
    """progress.json swapped for a link while a run goes on, and the next
    save backs it up first, which would copy what it leads to into the
    share."""
    import json
    server.canary.write_text(json.dumps({"password": "kept"}), encoding="utf-8")
    os.link(server.canary, server.install / "progress.json")
    backups = server.install / "Backups"
    with pytest.raises(shared_folder.LeadsOut):
        storage.backup_file(server.install / "progress.json", backups)
    assert not backups.exists() or not any(backups.iterdir())


def test_the_records_are_never_read_through_a_link(server):
    import json
    server.canary.write_text(json.dumps({"password": {"hash": "kept"}}), encoding="utf-8")
    os.link(server.canary, server.install / "progress.json")
    with pytest.raises(shared_folder.LeadsOut):
        storage.JsonStore(server.install / "progress.json", server.install / "Backups").load()


def test_a_backup_left_as_a_link_is_never_taken_for_the_records(server):
    import json
    server.canary.write_text(json.dumps({"password": {"hash": "kept"}}), encoding="utf-8")
    (server.install / "progress.json").write_text("{ not json", encoding="utf-8")
    backups = server.install / "Backups"
    backups.mkdir()
    os.link(server.canary, backups / "progress.29990101-000000.json.bak")
    store = storage.JsonStore(server.install / "progress.json", backups)
    assert "password" not in store.load()


def test_the_index_is_never_read_through_a_link(server):
    os.link(server.canary, server.install / "Testco Document Index.csv")
    with pytest.raises(shared_folder.LeadsOut):
        storage.CsvFile(server.install / "Testco Document Index.csv", ["Notes"]).read_all()


def test_off_the_server_the_records_and_index_are_read_as_before(desktop):
    import json
    desktop.canary.write_text(json.dumps({"k": {"state": "Completed"}}), encoding="utf-8")
    os.link(desktop.canary, desktop.install / "progress.json")
    assert storage.JsonStore(desktop.install / "progress.json").load() == {"k": {"state": "Completed"}}


def test_a_dangling_link_at_the_backup_name_is_never_followed(server, tmp_path, monkeypatch):
    """A link to a file that is not there yet would have the copy make it,
    outside, as the container's user."""
    if not can_symlink(tmp_path):
        pytest.skip("this system makes no symbolic links for an ordinary user")

    class Now:
        @staticmethod
        def now():
            from datetime import datetime
            return datetime(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr(storage, "datetime", Now)
    (server.install / "progress.json").write_text("{}", encoding="utf-8")
    backups = server.install / "Backups"
    backups.mkdir()
    planted = server.canary.parent / "made-by-a-link.json"
    (backups / "progress.20260102-030405.json.bak").symlink_to(planted)
    made = storage.backup_file(server.install / "progress.json", backups)
    assert made.name == "progress.20260102-030405-2.json.bak"
    assert not planted.exists() and untouched(server)


def test_the_owner_asked_for_at_a_console_is_kept_without_following_a_link(server, monkeypatch):
    config = server.install / "config.json"
    import json
    server.canary.write_text(json.dumps({"password": "kept"}), encoding="utf-8")
    os.link(server.canary, config)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda *_a: "Dana Example")
    assert storage.ensure_owner({}, config)["owner"] == "Dana Example"
    # Nothing read through the link is written back into the share.
    assert json.loads(server.canary.read_text(encoding="utf-8")) == {"password": "kept"}
    assert json.loads(config.read_text(encoding="utf-8")) == {"password": "kept"}


def test_the_owner_asked_for_at_a_console_is_kept_in_a_plain_config(server, monkeypatch):
    import json
    config = server.install / "config.json"
    config.write_text(json.dumps({"output_dir": "."}), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda *_a: "Dana Example")
    storage.ensure_owner({}, config)
    assert json.loads(config.read_text(encoding="utf-8")) == {"output_dir": ".",
                                                               "owner": "Dana Example"}


def test_a_staged_document_is_never_made_through_a_link(server):
    folder = server.install / "Statements"
    link_folder(server.elsewhere, folder)
    with pytest.raises(shared_folder.LeadsOut):
        delivery._stage(folder / "2026-01-02 Testco Statement.pdf")
    assert untouched(server)


def test_a_review_list_is_never_written_through_a_link_at_its_part(server):
    where = server.install / "review-names.json"
    os.link(server.canary, where.with_name(where.name + ".part"))
    with pytest.raises(shared_folder.LeadsOut):
        renaming._write_review_list(where, SimpleNamespace(progress={}), [])
    assert untouched(server)


class Cdp:
    def __init__(self):
        self.sent = []

    def send(self, method, params):
        self.sent.append((method, params))


def a_page():
    cdp = Cdp()
    return SimpleNamespace(context=SimpleNamespace(new_cdp_session=lambda _page: cdp)), cdp


def test_the_browser_is_never_pointed_at_a_download_folder_linked_out(server, capsys):
    folder = server.install / ".testco-downloads"
    link_folder(server.elsewhere, folder)
    page, cdp = a_page()
    assert capture.set_download_dir(page, folder) is False
    assert cdp.sent == [] and untouched(server)
    assert "leads outside the data folder" in capsys.readouterr().out

    page, cdp = a_page()
    assert capture.set_download_dir(page, server.install / ".other-downloads") is True
    assert cdp.sent[0][1]["downloadPath"] == str((server.install / ".other-downloads").resolve())


def test_a_download_written_by_the_core_is_never_written_through_a_link(server):
    staged = server.install / "x.pdf.delivering"
    os.link(server.canary, staged)
    with pytest.raises(shared_folder.LeadsOut):
        shared_folder.write_bytes(staged, b"%PDF-1.7 a statement")
    assert untouched(server)


# -- the run's start --------------------------------------------------------------------------

def a_config(install: Path) -> Path:
    import json
    path = install / "config.json"
    path.write_text(json.dumps({"output_dir": ".", "cdp_url": "http://127.0.0.1:9299"}),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("where", ["Logs", "Diagnostics", "Backups", "Manual Review",
                                   ".testco-downloads", "Statements/2026"])
def test_a_link_out_anywhere_in_the_output_folder_stops_the_run_at_its_start(server, where):
    target = server.install / where
    target.parent.mkdir(parents=True, exist_ok=True)
    link_folder(server.elsewhere, target)
    with pytest.raises(SystemExit) as stopped:
        storage.load_config(a_config(server.install))
    said = str(stopped.value)
    assert "is a link that leads to" in said and "this run stopped" in said
    assert target.name in said
    assert untouched(server)


def test_a_folder_linked_elsewhere_in_the_data_folder_is_looked_through(server):
    """Logs leads to another folder in the data folder, which is allowed,
    and that folder holds a link out, which every log line would go
    through. Found by an independent review of the first version, whose
    walk stopped at the first link."""
    other = server.data / "Somewhere Else"
    other.mkdir()
    link_folder(server.elsewhere, other / "deeper")
    link_folder(other, server.install / "Logs")
    with pytest.raises(SystemExit) as stopped:
        storage.load_config(a_config(server.install))
    assert "deeper" in str(stopped.value) and untouched(server)


def test_a_folder_linked_in_a_circle_is_walked_once(server):
    other = server.data / "Somewhere Else"
    other.mkdir()
    link_folder(server.install, other / "back")
    link_folder(other, server.install / "Logs")
    assert storage.load_config(a_config(server.install))["output_dir"] == "."


def test_links_no_run_writes_through_stop_nothing(server):
    """A virtual environment holds links to the system's Python, and a
    browser profile copied from a desktop holds links of its own. A folder
    the app does not file into is not walked either."""
    for folder in (".venv/bin", "browser-profile/Default", "Statements/.venv/lib",
                   "Something Else"):
        (server.install / folder).mkdir(parents=True)
    link_folder(server.elsewhere, server.install / ".venv" / "bin" / "python3")
    link_folder(server.elsewhere, server.install / "browser-profile" / "Default" / "Singleton")
    link_folder(server.elsewhere, server.install / "Statements" / ".venv" / "lib" / "x")
    link_folder(server.elsewhere, server.install / "Something Else" / "x")
    assert storage.load_config(a_config(server.install))["output_dir"] == "."


def test_links_that_stay_in_the_data_folder_are_left_alone(server):
    archive = server.data / "Testco Archive"
    archive.mkdir()
    link_folder(archive, server.install / "Statements")
    assert storage.load_config(a_config(server.install))["output_dir"] == "."


def test_a_file_linked_out_stops_the_run_too(server, tmp_path):
    if not can_symlink(tmp_path):
        pytest.skip("this system makes no symbolic links for an ordinary user")
    (server.install / "progress.json").symlink_to(server.canary)
    with pytest.raises(SystemExit) as stopped:
        storage.load_config(a_config(server.install))
    assert "progress.json" in str(stopped.value)


def test_off_the_server_no_folder_is_walked(desktop):
    link_folder(desktop.elsewhere, desktop.install / "Logs")
    assert storage.load_config(a_config(desktop.install))["output_dir"] == "."


def test_a_long_output_folder_is_walked_quickly(server):
    """A run starts with this walk, so it has to be cheap on a folder of
    years of documents."""
    import time
    for year in range(2000, 2026):
        folder = server.install / "Statements" / str(year)
        folder.mkdir(parents=True)
        for month in range(1, 13):
            (folder / ("%d-%02d-01 Testco Statement.pdf" % (year, month))).write_bytes(b"%PDF")
    started = time.perf_counter()
    assert shared_folder.first_link_out(server.install) is None
    assert time.perf_counter() - started < 5
    link_folder(server.elsewhere, server.install / "Statements" / "2025" / "late")
    assert shared_folder.first_link_out(server.install).endswith("late")

