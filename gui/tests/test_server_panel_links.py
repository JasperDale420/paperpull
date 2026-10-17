"""On PaperPull Server the panel writes nothing through a link left in
the shared folder, and copies nothing it read through one into it.

The panel writes into each provider's folder in /data, a config's profile
and owner, settings a template gained, a naming change, the code a release
refreshes, the Stop button's file for a recording and a history import.
The data folder is often a share other people can write, and a link left
there leads wherever its maker chose, /config with the server's password
among the places, while the panel runs as the user who owns it. Each
place is checked here with a canary outside the data folder that must
come through untouched and must not turn up inside the share. A hard link
stands in for a link at a file's name and a junction for a folder that is
a link, since Windows gives an ordinary user neither symbolic links nor
anything else that needs no special right. Off the server each write is
the one it always was. Every name and value is invented.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
app = pytest.importorskip("app")
import server_mode  # noqa: E402

SECRET = {"password": {"salt": "an invented salt", "hash": "an invented hash"}}
ENTRY = "testco_docs.py"


def link_folder(target: Path, link: Path) -> None:
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.fixture()
def place(tmp_path, monkeypatch):
    """An image-like tree with one template, a data folder with an install
    of it, and outside the data folder a canary settings file and an empty
    folder, as /config and /profiles would be."""
    opt = tmp_path / "opt" / "paperpull"
    (opt / "gui").mkdir(parents=True)
    tmpl = opt / "templates" / "apps" / "testco"
    tmpl.mkdir(parents=True)
    (tmpl / ENTRY).write_text("# the shipped app\n", encoding="utf-8")
    (tmpl / "config.example.json").write_text(json.dumps(
        {"owner": "", "output_dir": ".", "cdp_url": "http://127.0.0.1:9299",
         "a_new_setting": True}), encoding="utf-8")
    data = tmp_path / "data"
    install = data / "Testco Statements"
    install.mkdir(parents=True)
    (install / ENTRY).write_text("# an older copy\n", encoding="utf-8")
    (install / "config.example.json").write_text(
        (tmpl / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    outside = tmp_path / "config"
    outside.mkdir()
    canary = outside / "server.json"
    canary.write_text(json.dumps(SECRET), encoding="utf-8")
    elsewhere = tmp_path / "profiles"
    elsewhere.mkdir()
    # The history tool beside this checkout's panel, found before the panel
    # is pointed at the image-like tree, and put back as it was after.
    monkeypatch.setattr(app, "_MIGRATE_MOD", app._migrate_module() or None)
    monkeypatch.setattr(app, "_ACCOUNT_MOD", app._account_module() or None)
    monkeypatch.setattr(app, "HERE", opt / "gui")
    monkeypatch.setenv("APPS_ROOT", str(data))
    monkeypatch.setenv("PAPERPULL_PROFILES", str(elsewhere))
    monkeypatch.setenv("PAPERPULL_SERVER", "1")
    return SimpleNamespace(data=data, install=install, tmpl=tmpl, canary=canary,
                           elsewhere=elsewhere)


def untouched(place) -> bool:
    """The canary is as it was, nothing was written beside it, and nothing
    in the share carries what it holds."""
    if json.loads(place.canary.read_text(encoding="utf-8")) != SECRET:
        return False
    if sorted(p.name for p in place.canary.parent.iterdir()) != ["server.json"]:
        return False
    if list(place.elsewhere.iterdir()):
        return False
    for path in place.data.rglob("*"):
        if path.is_file() and path.stat().st_nlink == 1 and \
                "an invented hash" in path.read_text(encoding="utf-8", errors="replace"):
            return False
    return True


def link_config(place, name="config.json") -> Path:
    config = place.install / name
    os.link(place.canary, config)
    return config


# -- a config left as a link -------------------------------------------------------------

def test_the_profile_is_never_written_into_a_linked_config(place):
    config = link_config(place)
    app._set_profile(config, server_mode.profile_for(place.install.name))
    assert untouched(place)


def test_the_owner_is_never_written_into_a_linked_config(place):
    config = link_config(place)
    app._set_owner(config, "Dana Example")
    assert untouched(place)


def test_settings_a_template_gained_are_never_merged_into_a_linked_config(place):
    link_config(place)
    assert app.ensure_settings(place.install, "2026-01-02-030405") == []
    assert untouched(place)


def test_a_naming_change_is_never_written_into_a_linked_config(place):
    config = link_config(place)
    assert app._write_config(config, "filename_pattern", "{date} {provider}", "20260102") is False
    assert untouched(place)


def test_a_history_import_never_writes_settings_into_a_linked_config(place):
    config = link_config(place)
    mod = app._migrate_or_fail()
    app._apply_settings(mod, config, place.install, {"owner": "Dana Example"})
    assert untouched(place)


def test_a_second_person_is_never_copied_from_a_linked_config(place):
    link_config(place)
    with pytest.raises(app.HTTPException) as refused:
        app._make_account(place.install, "spouse", "Jamie Example")
    assert refused.value.status_code == 409 and "is a link" in refused.value.detail
    assert not (place.install / "config.spouse.json").exists()
    assert untouched(place)


def test_a_second_persons_folder_is_never_made_outside(place):
    """The first person's config names a folder outside the data folder,
    which the new person's folder would be made beside."""
    (place.install / "config.json").write_text(json.dumps(
        {"output_dir": str(place.elsewhere / "Testco Statements"),
         "cdp_url": "http://127.0.0.1:9299"}), encoding="utf-8")
    with pytest.raises(app.HTTPException) as refused:
        app._make_account(place.install, "spouse", "Jamie Example")
    assert refused.value.status_code == 409
    assert not (place.install / "config.spouse.json").exists()
    assert untouched(place)


def test_a_second_person_is_added_as_before_when_nothing_is_a_link(place):
    (place.install / "config.json").write_text(json.dumps(
        {"output_dir": ".", "cdp_url": "http://127.0.0.1:9299", "owner": "Dana Example"}),
        encoding="utf-8")
    dest = app._make_account(place.install, "spouse", "Jamie Example")
    made = json.loads(dest.read_text(encoding="utf-8"))
    assert made["owner"] == "Jamie Example"
    assert made["profile_dir"] == server_mode.profile_for(place.install.name, "spouse")
    assert (place.data / "Testco Statements - spouse").is_dir()
    assert untouched(place)


def test_a_plain_config_is_written_on_the_server_as_on_the_desktop(place, tmp_path, monkeypatch):
    config = place.install / "config.json"
    original = json.dumps({"owner": "", "output_dir": "."}, indent=2)
    config.write_text(original, encoding="utf-8")
    app._set_owner(config, "Dana Example")
    app._set_profile(config, server_mode.profile_for(place.install.name))
    on_server = config.read_bytes()

    twin = tmp_path / "desktop" / "config.json"
    twin.parent.mkdir()
    twin.write_text(original, encoding="utf-8")
    monkeypatch.delenv("PAPERPULL_SERVER")
    app._set_owner(twin, "Dana Example")
    app._set_profile(twin, server_mode.profile_for(place.install.name))
    assert on_server == twin.read_bytes()
    assert sorted(p.name for p in place.install.iterdir()) == sorted(
        [ENTRY, "config.example.json", "config.json"])


def test_off_the_server_the_panel_writes_a_config_as_it_always_did(place, monkeypatch):
    """The desktop's folders are its owner's alone, and its writes are the
    ones they always were, through a link too."""
    monkeypatch.delenv("PAPERPULL_SERVER")
    config = link_config(place)
    app._set_owner(config, "Dana Example")
    assert json.loads(place.canary.read_text(encoding="utf-8"))["owner"] == "Dana Example"


# -- the code a release refreshes ----------------------------------------------------------

def test_a_code_file_left_as_a_link_is_replaced_and_never_read(place):
    target = place.install / ENTRY
    target.unlink()
    os.link(place.canary, target)
    replaced = app.refresh_install_code(place.install, place.tmpl)
    assert ENTRY in replaced
    assert target.read_text(encoding="utf-8") == "# the shipped app\n"
    assert not (place.install / "Backups").exists(), "nothing read through the link is backed up"
    assert untouched(place)


def test_a_backups_folder_left_as_a_link_stops_the_refresh(place):
    link_folder(place.elsewhere, place.install / "Backups")
    with pytest.raises(OSError):
        app.refresh_install_code(place.install, place.tmpl)
    assert untouched(place)
    assert (place.install / ENTRY).read_text(encoding="utf-8") == "# an older copy\n"


def test_a_refresh_with_nothing_linked_backs_up_and_replaces_as_before(place):
    replaced = app.refresh_install_code(place.install, place.tmpl)
    assert ENTRY in replaced
    assert (place.install / ENTRY).read_text(encoding="utf-8") == "# the shipped app\n"
    backed = list((place.install / "Backups").rglob(ENTRY))
    assert [b.read_text(encoding="utf-8") for b in backed] == ["# an older copy\n"]


def test_the_server_never_copies_a_core_into_an_installs_venv(place):
    """The server runs the image's core and never an install's venv, so a
    .venv left in the shared folder is never written into, here holding a
    link where the core's first file goes."""
    core = app.HERE.parent / "core" / "paperpull_core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("# the image's core\n", encoding="utf-8")
    pkg = place.install / ".venv" / "Lib" / "site-packages" / "paperpull_core"
    pkg.mkdir(parents=True)
    os.link(place.canary, pkg / "__init__.py")
    assert app.ensure_core(place.install, "2026-01-02-030405") == []
    assert untouched(place)


# -- the Stop button and a history import ---------------------------------------------------

class Req:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def test_the_stop_button_never_writes_through_a_link(place):
    (place.install / "config.json").write_text(json.dumps({"output_dir": "."}), encoding="utf-8")
    diagnostics = place.install / "Diagnostics"
    diagnostics.mkdir()
    os.link(place.canary, diagnostics / ".stop-recording")
    answer = asyncio.run(app.api_record_stop(Req({"app": "Testco Statements"})))
    assert answer["stopping"] is True
    assert (diagnostics / ".stop-recording").read_text(encoding="utf-8") == "stop\n"
    assert untouched(place)


def test_the_stop_button_never_writes_into_a_folder_linked_out(place):
    (place.install / "config.json").write_text(json.dumps({"output_dir": "."}), encoding="utf-8")
    link_folder(place.elsewhere, place.install / "Diagnostics")
    with pytest.raises(app.HTTPException) as refused:
        asyncio.run(app.api_record_stop(Req({"app": "Testco Statements"})))
    assert refused.value.status_code == 500
    assert untouched(place)


def test_removing_a_provider_never_moves_it_through_a_link(place):
    """Removed left as a link to another disk would have the move copy the
    whole install, its records and sign-in state, to wherever it leads."""
    (place.install / "config.json").write_text(json.dumps({"output_dir": "."}), encoding="utf-8")
    link_folder(place.elsewhere, place.data / "Removed")
    with pytest.raises(app.HTTPException) as refused:
        asyncio.run(app.api_remove(Req({"app": "Testco Statements"})))
    assert refused.value.status_code == 500
    assert (place.install / ENTRY).is_file() and untouched(place)


def test_removing_a_provider_moves_it_into_removed_as_before(place):
    (place.install / "config.json").write_text(json.dumps({"output_dir": "."}), encoding="utf-8")
    answer = asyncio.run(app.api_remove(Req({"app": "Testco Statements"})))
    assert Path(answer["moved_to"]) == place.data / "Removed" / "Testco Statements"
    assert (place.data / "Removed" / "Testco Statements" / ENTRY).is_file()


def test_a_history_never_reads_or_writes_a_linked_progress_file(place):
    keep = app._history_keep()
    assert keep(place.install)
    os.link(place.canary, place.install / "progress.json")
    assert not keep(place.install)


def test_a_history_is_written_on_the_server_without_a_fixed_temporary_name(place):
    mod = app._migrate_or_fail()
    (place.install / "progress.json").write_text("{}", encoding="utf-8")
    os.link(place.canary, place.install / "progress.json.tmp")
    records = {"id:1": {"state": "Completed", "downloaded_ok": True}}
    mod.write_history(place.install, records, "20260102-030405", write=server_mode.write_in_data)
    assert json.loads((place.install / "progress.json").read_text(encoding="utf-8")) == records
    assert (place.install / "Backups" / "progress.20260102-030405.before-import.bak"
            ).read_text(encoding="utf-8") == "{}"
    assert untouched(place)
