"""Every app on PaperPull Server stops before it writes through a link out
of the data folder.

A provider's folder lives in /data, often a share other people can write,
and a folder left there as a link to /config or /profiles would have the
run's own writes, its log first of all, land there as the container's user.
The core holds this in two places, the walk of the output folder when the
config is read (storage.refuse_links_out) and each folder as it is made
(storage.Paths, through shared_folder.hold). An app that made its folders
or read its config another way would pass both by.

So every app that writes under its output folder through the core, found
by what its code does, the config read with load_config and the folders
made with Paths and ensure, is built here as its own main builds it, on a
made-up server data folder whose Logs folder is a link out, once as it is
and once with the walk taken away, as if the link were left a moment after
it ran. Either way the app has to stop with nothing written outside. A
junction stands in for the link on Windows, since it needs no special
right. Every name and file is invented.
"""
import ast
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from paperpull_core import shared_folder
from paperpull_core import storage as core_storage

REPO = Path(__file__).resolve().parents[2]


def entry_of(app: Path):
    for pattern in ("*_receipts.py", "*_docs.py"):
        found = sorted(app.glob(pattern))
        if found:
            return found[0]
    return None


def writes_through_the_core(app: Path) -> bool:
    """An app whose run reads its config with the core's load_config and
    makes its folders with the core's Paths and ensure, found by those
    calls in its own code and never by its name."""
    tree = ast.parse(entry_of(app).read_text(encoding="utf-8-sig"))
    calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    return ("load_config" in calls and "Paths" in calls
            and any(call.endswith(".ensure") for call in calls))


APPS = sorted(d for d in (REPO / "apps").iterdir()
              if d.is_dir() and entry_of(d) and writes_through_the_core(d))
IDS = [d.name for d in APPS]


def test_the_census_finds_every_app():
    """No app went missing from the census, which it would by building its
    folders or reading its config some other way."""
    every = [d for d in (REPO / "apps").iterdir() if d.is_dir() and entry_of(d)]
    assert len(APPS) == len(every) >= 70, sorted(set(every) - set(APPS))


def link_folder(target: Path, link: Path) -> None:
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def load(app: Path):
    for name in [m for m in list(sys.modules)
                 if m.endswith(("_docs", "_receipts", "_site")) or m == "storage"]:
        del sys.modules[name]
    sys.path.insert(0, str(app))
    try:
        return importlib.import_module(entry_of(app).stem)
    finally:
        sys.path.pop(0)


class NoBrowser:
    """sync_playwright() for a machine with no browser. Building an app
    never needs one, so any attach or launch is refused."""

    def __init__(self):
        def refuse(*_a, **_kw):
            raise Exception("connect ECONNREFUSED 127.0.0.1:9")
        self.chromium = SimpleNamespace(connect_over_cdp=refuse,
                                        launch_persistent_context=refuse, launch=refuse)

    def __call__(self):
        return self

    def start(self):
        return self

    def stop(self):
        return None


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """A made-up server, its data folder holding one install whose Logs
    folder is a link to an empty folder outside, standing for /profiles."""
    for name in ("_SPEC", "_FILENAME_PATTERN", "_PATTERN_OWNER", "_FILENAME_OWNER"):
        monkeypatch.setattr(core_storage, name, getattr(core_storage, name))
    data = tmp_path / "data"
    install = data / "Invented Provider"
    install.mkdir(parents=True)
    outside = tmp_path / "profiles"
    outside.mkdir()
    link_folder(outside, install / "Logs")
    monkeypatch.setenv("PAPERPULL_SERVER", "1")
    monkeypatch.setenv("APPS_ROOT", str(data))
    monkeypatch.setenv("PAPERPULL_PROFILES", str(outside))
    monkeypatch.delenv("PAPERPULL_DATA_DIR", raising=False)
    monkeypatch.chdir(install)
    import playwright.sync_api as sync_api
    monkeypatch.setattr(sync_api, "sync_playwright", NoBrowser())
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: (_ for _ in ()).throw(EOFError()))
    return SimpleNamespace(install=install, outside=outside)


def build(app: Path, install: Path):
    """The app as its own main builds it, from its own config example with
    the output folder its own."""
    mod = load(app)
    example = app / "config.example.json"
    config = json.loads(example.read_text(encoding="utf-8")) if example.exists() else {}
    config.update({"owner": "Dana Example", "output_dir": ".",
                   "delay_min_seconds": 0, "delay_max_seconds": 0})
    for key in ("filename_pattern", "filename_pattern_receipts", "filename_pattern_statements"):
        config.pop(key, None)
    cfg = install / "config.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")
    return mod.App(mod.build_parser().parse_args(["--config", str(cfg)]))


@pytest.mark.parametrize("app", APPS, ids=IDS)
def test_a_link_out_found_when_the_run_starts_stops_it(server, app):
    with pytest.raises(SystemExit) as stopped:
        build(app, server.install)
    assert "is a link that leads to" in str(stopped.value), stopped.value
    assert list(server.outside.iterdir()) == []


@pytest.mark.parametrize("app", APPS, ids=IDS)
def test_a_link_out_left_after_the_walk_stops_it_where_the_folder_is_made(
        server, app, monkeypatch):
    monkeypatch.setattr(core_storage, "refuse_links_out", lambda *_a, **_kw: None)
    with pytest.raises(shared_folder.LeadsOut):
        build(app, server.install)
    assert list(server.outside.iterdir()) == []
