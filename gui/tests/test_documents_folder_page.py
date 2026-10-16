"""The settings page's Documents folder (#102).

One account's documents_dir, written into that account's config with a copy
of the config kept first. A folder that is not a full path, a browser
profile or inside or around one, one holding a provider's own folder, and
one inside the folder another account keeps its records in are refused,
and on PaperPull Server so is one outside the data folder. The page says
how many documents saved before are still in the account's own folder, and
Move runs the app's own Rename with the core's move mark set.

The endpoints are called directly, the way the rest of this suite does.
Every folder is a throwaway one under tmp_path, and every name is invented.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
app_module = pytest.importorskip("app")
pytest.importorskip("fastapi")
from fastapi import HTTPException            # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
from paperpull_core import renaming  # noqa: E402


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def install(root: Path, name: str, accounts=("primary",)) -> Path:
    """A provider's folder with a config for each account, each with a
    profile and an output folder of its own beside it."""
    d = root / name
    d.mkdir(parents=True)
    for account in accounts:
        cfg_name = "config.json" if account == "primary" else "config.%s.json" % account
        out = "." if account == "primary" else "../%s - %s" % (name, account)
        (d / out).mkdir(parents=True, exist_ok=True)
        (d / cfg_name).write_text(json.dumps({
            "output_dir": out, "profile_dir": "%s-profile" % account}), encoding="utf-8")
    return d


@pytest.fixture
def apps(tmp_path, monkeypatch):
    root = tmp_path / "PaperPull"
    found = {"Chase Statements": install(root, "Chase Statements", ("primary", "spouse")),
             "Target Receipts": install(root, "Target Receipts")}
    metas = {name: {"name": name, "dir": str(d), "accounts": app_module._accounts(d)}
             for name, d in found.items()}
    monkeypatch.setattr(app_module, "discover_apps", lambda: metas)
    monkeypatch.setattr(app_module, "_SAMPLE", None)
    return found


def save(body):
    return asyncio.run(app_module.api_documents_save(_Req(body)))


def config(d: Path, name="config.json") -> dict:
    return json.loads((d / name).read_text(encoding="utf-8"))


def test_an_account_with_no_documents_folder_keeps_everything_together(apps):
    d = app_module.api_documents("Chase Statements")
    assert d["documents_dir"] == "" and d["left"] == 0
    assert Path(d["output_dir"]) == apps["Chase Statements"].resolve()


def test_a_documents_folder_is_written_into_that_accounts_config_alone(apps, tmp_path):
    docs = tmp_path / "Watched"
    d = save({"app": "Chase Statements", "account": "spouse", "folder": str(docs)})
    assert d["written"] and d["documents_dir"] == str(docs)
    chase = apps["Chase Statements"]
    assert config(chase, "config.spouse.json")["documents_dir"] == str(docs)
    assert "documents_dir" not in config(chase)
    kept = list((chase / "Backups").glob("documents-*/config.spouse.json"))
    assert len(kept) == 1 and "documents_dir" not in json.loads(kept[0].read_text("utf-8"))
    assert save({"app": "Chase Statements", "account": "spouse",
                 "folder": str(docs)})["written"] is False, "nothing to change"
    d = save({"app": "Chase Statements", "account": "spouse", "folder": ""})
    assert d["written"] and "documents_dir" not in config(chase, "config.spouse.json")


def test_the_accounts_own_folder_clears_the_setting(apps):
    chase = apps["Chase Statements"]
    save({"app": "Chase Statements", "folder": str(chase.parent / "Elsewhere")})
    save({"app": "Chase Statements", "folder": str(chase)})
    assert "documents_dir" not in config(chase)


@pytest.mark.parametrize("where,says", [
    ("relative", "full path"),
    ("profile", "browser profile"),
    ("inside profile", "browser profile"),
    # The folder of every install holds its profiles too.
    ("holds an install", "browser profile"),
    ("another account", "keeps its records in"),
    ("another provider", "keeps its records in"),
])
def test_a_folder_that_cannot_hold_documents_is_refused(apps, where, says):
    chase = apps["Chase Statements"]
    folder = {"relative": "Watched",
              "profile": str(chase / "primary-profile"),
              "inside profile": str(chase / "spouse-profile" / "Default"),
              "holds an install": str(chase.parent),
              "another account": str(chase.parent / "Chase Statements - spouse" / "Docs"),
              "another provider": str(apps["Target Receipts"] / "Docs")}[where]
    with pytest.raises(HTTPException) as e:
        save({"app": "Chase Statements", "folder": folder})
    assert e.value.status_code == 400 and says in e.value.detail, e.value.detail
    assert "documents_dir" not in config(chase)


def test_a_folder_holding_a_providers_own_folder_is_refused(apps, tmp_path):
    """Its settings and records are there, whatever its profile is. Here it
    is a provider's folder with no config, and so no profile, yet."""
    holder = tmp_path / "Holder"
    (holder / "Target Receipts").mkdir(parents=True)
    app_module.discover_apps()["Target Receipts"]["dir"] = str(holder / "Target Receipts")
    with pytest.raises(HTTPException) as e:
        save({"app": "Chase Statements", "folder": str(holder)})
    assert "Target Receipts's own folder" in e.value.detail, e.value.detail


def test_a_folder_inside_the_accounts_own_folder_may_hold_its_documents(apps):
    chase = apps["Chase Statements"]
    assert save({"app": "Chase Statements", "folder": str(chase / "Documents")})["written"]


def test_on_the_server_a_folder_outside_the_data_folder_is_refused(apps, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.server_mode, "enabled", lambda: True)
    monkeypatch.setattr(app_module.server_mode, "data_root",
                        lambda: apps["Chase Statements"].parent)
    monkeypatch.setenv("PAPERPULL_PROFILES", str(tmp_path / "profiles"))
    with pytest.raises(HTTPException) as e:
        save({"app": "Chase Statements", "folder": str(tmp_path / "elsewhere")})
    assert "data folder" in e.value.detail
    with pytest.raises(HTTPException) as e:
        save({"app": "Chase Statements", "folder": str(tmp_path / "profiles" / "x")})
    inside = apps["Chase Statements"].parent / "Documents"
    assert save({"app": "Chase Statements", "folder": str(inside)})["written"]


def test_the_page_counts_the_documents_still_in_the_accounts_own_folder(apps, tmp_path):
    chase = apps["Chase Statements"]
    statements = chase / "Statements" / "Testco"
    statements.mkdir(parents=True)
    (statements / "a.pdf").write_bytes(b"%PDF- a")
    (chase / "Statements" / "b.pdf").write_bytes(b"%PDF- b")
    (chase / "Manual Review").mkdir()
    (chase / "Manual Review" / "c.pdf").write_bytes(b"%PDF- c")
    records = {"a": {"pdf_path": "Statements/Testco/a.pdf"},
               "a again": {"pdf_path": str(statements / "a.pdf")},
               "b": {"pdf_path": str(chase / "Statements" / "b.pdf")},
               "c": {"pdf_path": "Manual Review/c.pdf"},
               "gone": {"pdf_path": "Statements/gone.pdf"},
               "elsewhere": {"pdf_path": str(tmp_path / "x.pdf")}}
    (chase / "progress.json").write_text(json.dumps(records), encoding="utf-8")
    assert app_module.api_documents("Chase Statements")["left"] == 0, "no folder, none left"
    d = save({"app": "Chase Statements", "folder": str(tmp_path / "Watched")})
    assert d["left"] == 2
    assert app_module.api_documents("Chase Statements")["left"] == 2


def test_an_unknown_account_is_refused(apps):
    with pytest.raises(HTTPException) as e:
        save({"app": "Chase Statements", "account": "nobody", "folder": ""})
    assert e.value.status_code == 404


@pytest.fixture()
def started(tmp_path, monkeypatch):
    """Every command the panel starts with the environment it is given,
    kept rather than run."""
    import io
    monkeypatch.delenv("PAPERPULL_SERVER", raising=False)
    monkeypatch.delenv(renaming.MOVE_DOCUMENTS_ENV, raising=False)
    monkeypatch.setattr(app_module, "_SAMPLE", None)
    venv = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv.parent.mkdir(parents=True)
    venv.write_text("", encoding="utf-8")
    meta = {"name": "Shop Docs", "dir": str(tmp_path), "script": "shop_docs.py",
            "python": "py", "login_flag": "--login", "accounts": ["primary"]}
    monkeypatch.setattr(app_module, "discover_apps", lambda: {"Shop Docs": meta})
    runs = []

    class Process:
        def __init__(self, cmd, **kwargs):
            runs.append((list(cmd), dict(kwargs.get("env") or {})))
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("a line from the app\n")

        def wait(self, **kwargs):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(app_module.subprocess, "Popen", Process)
    return runs


def ran(action):
    async def go():
        response = app_module.api_run(app="Shop Docs", action=action)
        return "".join([part async for part in response.body_iterator])
    return asyncio.run(go())


@pytest.mark.parametrize("action,moves", [("move_documents", True),
                                          ("move_documents_apply", True),
                                          ("rename", False), ("rename_apply", False)])
def test_only_the_move_runs_the_apps_rename_with_the_mark(started, action, moves):
    out = ran(action)
    assert "event: done\ndata: 0" in out, out
    [(cmd, env)] = started
    assert cmd == ["py", "shop_docs.py", *app_module.ACTIONS[action]["flags"]]
    assert (env.get(renaming.MOVE_DOCUMENTS_ENV) == "1") is moves


def test_the_move_is_the_apps_own_rename_with_the_cores_mark():
    for action, flags in (("move_documents", ["--rename"]),
                          ("move_documents_apply", ["--rename", "--apply"])):
        assert app_module.ACTIONS[action]["flags"] == flags
        assert app_module.ACTIONS[action]["env"] == {renaming.MOVE_DOCUMENTS_ENV: "1"}
    assert all("env" not in spec for name, spec in app_module.ACTIONS.items()
               if not name.startswith("move_documents"))
