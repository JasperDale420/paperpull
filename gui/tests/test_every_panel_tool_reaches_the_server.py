"""Every script the panel runs from tools/ is in the PaperPull Server image,
and the server never runs one from the shared folder.

The Status tab, both spreadsheets, the sample and Add a person each load a
script from tools/ by its path. The image carried add_account.py alone, so
on the server the Status tab said status.py was not found, neither
spreadsheet would build and the sample would not open. And with the
panel's own copy missing, the panel went on to look in the apps root and
the folder above it, which on the server is the shared folder anybody who
may write to the share can fill, and it ran what it found there inside the
container that holds every signed-in browser profile.

The scripts come from the panel's own code, every string in gui/*.py that
names a file in tools/, so a script the panel starts to run later is in the
census at once. Each one has to be handed to Docker by server/build.py,
copied by server/Dockerfile into tools/ beside the image's gui folder, and
able to import what it imports there, or be listed in DESKTOP_ONLY with the
reason the server goes without it. The loaders come from the panel's code
too, every function that looks in tools/, and each is called as the
server with a copy of every script planted in the apps root, the folder
above it and a tools folder in each. None of them may run.
"""
import ast
import asyncio
import importlib.util
import inspect
import re
import sys
from pathlib import Path, PurePosixPath

import pytest

GUI = Path(__file__).resolve().parents[1]
REPO = GUI.parent
TOOLS = REPO / "tools"
SERVER = REPO / "server"

sys.path.insert(0, str(GUI))
app_module = pytest.importorskip("app")

# A script the panel runs that PaperPull Server goes without, with the
# reason. Whatever needs it has to be hidden on the server, since a button
# that answers "not found" is the defect this file was written for.
DESKTOP_ONLY: dict = {}

# What each one is for, so the census cannot quietly find nothing.
KNOWN = {"status.py", "export_purchases.py", "export_transactions.py",
         "make_sample.py", "add_account.py"}


def _build():
    spec = importlib.util.spec_from_file_location("paperpull_server_build", SERVER / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _build()


def _trees():
    return {src.name: ast.parse(src.read_text(encoding="utf-8"))
            for src in sorted(GUI.glob("*.py"))}


def panel_tools() -> set:
    """Every script in tools/ that a string in the panel's code names."""
    names = set()
    for tree in _trees().values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                name = node.value.strip()
                if name.startswith("tools/"):
                    name = name[len("tools/"):]
                if re.fullmatch(r"[A-Za-z0-9_]+\.py", name) and (TOOLS / name).is_file():
                    names.add(name)
    return names


def loaders() -> list:
    """Every function of the panel that looks for a script in tools/, by
    the place it names or through _tool_places. A route is left out, since
    calling one here would do what it does, and a route reaches a script
    through one of these."""
    found = []
    for node in _trees()["app.py"].body:
        if (not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                or node.decorator_list or node.name == "_tool_places"):
            continue
        for inner in ast.walk(node):
            if ((isinstance(inner, ast.Constant) and inner.value == "tools")
                    or (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                        and inner.func.id == "_tool_places")):
                found.append(node.name)
                break
    return found


def dockerfile_copies() -> list:
    """(source, where it lands in the image) for everything the Dockerfile
    copies from the build folder. A folder's contents land in the folder
    named, as Docker copies them."""
    text = (SERVER / "Dockerfile").read_text(encoding="utf-8")
    text = re.sub(r"\\\n", " ", text)
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*COPY\s+(?!--from)(.+)$", line)
        if not m:
            continue
        parts = [p for p in m.group(1).split() if not p.startswith("--")]
        for src in parts[:-1]:
            dest = parts[-1]
            if (REPO / src).is_dir():
                out.append((src.rstrip("/"), dest.rstrip("/")))
            elif dest.endswith("/"):
                out.append((src, dest + PurePosixPath(src).name))
            else:
                out.append((src, dest))
    return out


def lands_at(path: str, copies) -> str:
    for src, dest in copies:
        if path == src:
            return dest
        if path.startswith(src + "/"):
            return dest + path[len(src):]
    return ""


def image_root(copies) -> PurePosixPath:
    """The panel's HERE.parent in the image, the folder above where gui/ lands."""
    panel = dict(copies).get("gui")
    assert panel, "the Dockerfile does not copy gui/"
    return PurePosixPath(panel).parent


def puts_a_folder_on_sys_path(tree) -> bool:
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("insert", "append")
                and ast.unparse(node.func.value) == "sys.path"):
            return True
    return False


def importable_in_the_image(tree, copies) -> set:
    """Top-level names a script can import in the image. The panel loads a
    script by its path, which puts no folder on sys.path, so that is the
    standard library, every package server/requirements.in pins and
    PaperPull's core, which the image installs. A script that puts the
    program's folder on sys.path itself, as add_account.py does, may also
    import a module the image keeps there."""
    names = set(sys.stdlib_module_names)
    for line in (SERVER / "requirements.in").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            names.add(re.split(r"[\[=<>~! ]", line)[0].lower().replace("-", "_"))
    if "core" in dict(copies):
        names.add("paperpull_core")
    if puts_a_folder_on_sys_path(tree):
        root = image_root(copies)
        names |= {PurePosixPath(dest).stem for _, dest in copies
                  if PurePosixPath(dest).suffix == ".py" and PurePosixPath(dest).parent == root}
    return names


def imports_of(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module.split(".")[0])
    return out


# -- the census finds what it was written for ----------------------------------------

def test_the_census_finds_every_script_and_loader_it_was_written_for():
    assert KNOWN <= panel_tools(), sorted(panel_tools())
    assert {"_sample_builder", "_status_module", "_export_module", "_transactions_tool",
            "_account_module"} <= set(loaders()), loaders()


def test_desktop_only_names_only_scripts_the_panel_runs():
    assert set(DESKTOP_ONLY) <= panel_tools()
    assert all(isinstance(why, str) and why.strip() for why in DESKTOP_ONLY.values())


# -- every one of them is in the image ---------------------------------------------------

@pytest.mark.parametrize("name", sorted(panel_tools() - set(DESKTOP_ONLY)))
def test_the_image_carries_it_where_the_panel_looks(name):
    path = "tools/" + name
    assert build.selected([path]) == [path], "server/build.py does not hand %s to Docker" % path
    copies = dockerfile_copies()
    want = str(image_root(copies) / "tools" / name)
    assert lands_at(path, copies) == want, \
        "server/Dockerfile does not copy %s to %s, where the panel looks" % (path, want)


@pytest.mark.parametrize("name", sorted(panel_tools() - set(DESKTOP_ONLY)))
def test_what_it_imports_is_in_the_image(name):
    tree = ast.parse((TOOLS / name).read_text(encoding="utf-8"))
    missing = imports_of(tree) - importable_in_the_image(tree, dockerfile_copies())
    assert not missing, "%s imports %s, which the image does not have" % (name, sorted(missing))


# -- the server never runs one from the shared folder ----------------------------------

PLANTED = ("with open(%r, 'a', encoding='utf-8') as f:\n"
           "    f.write(%r + '\\n')\n")


def _plant(tmp_path):
    """A copy of every panel script in the apps root, the folder above it
    and a tools folder in each, each copy noting in ran.txt that it ran."""
    share = tmp_path / "share"
    data = share / "data"
    ran = tmp_path / "ran.txt"
    for folder in (data, share, data / "tools", share / "tools"):
        folder.mkdir(parents=True, exist_ok=True)
        for name in panel_tools():
            (folder / name).write_text(PLANTED % (str(ran), str(folder / name)), encoding="utf-8")
    return data, ran


def _call(name):
    found = getattr(app_module, name)()
    return asyncio.run(found) if inspect.iscoroutine(found) else found


@pytest.fixture()
def fresh(monkeypatch):
    """Nothing the panel found before this test is remembered."""
    monkeypatch.setattr(app_module, "_SAMPLE", None)
    for name in dir(app_module):
        if name.startswith("_") and name.endswith("_MOD"):
            monkeypatch.setattr(app_module, name, None)


def _where(found) -> Path:
    return Path(found if isinstance(found, Path) else found.__file__).resolve()


def test_without_its_own_tools_the_server_runs_none_from_the_shared_folder(tmp_path, monkeypatch, fresh):
    data, ran = _plant(tmp_path)
    gui = tmp_path / "opt" / "paperpull" / "gui"
    (gui.parent / "tools").mkdir(parents=True)
    gui.mkdir()
    monkeypatch.setattr(app_module, "HERE", gui)
    monkeypatch.setenv("APPS_ROOT", str(data))
    monkeypatch.setenv("PAPERPULL_SERVER", "1")

    for name in panel_tools():
        assert app_module._tool_places(name) == (gui.parent / "tools" / name,), name
    for name in loaders():
        assert _call(name) is None, name
    assert not ran.exists(), ran.read_text(encoding="utf-8")


def test_the_server_finds_its_own_copy_of_each(tmp_path, monkeypatch, fresh):
    data, ran = _plant(tmp_path)
    monkeypatch.setenv("APPS_ROOT", str(data))
    monkeypatch.setenv("PAPERPULL_SERVER", "1")
    monkeypatch.setattr(sys, "path", list(sys.path))   # add_account.py puts its folder first

    for name in loaders():
        found = _call(name)
        assert found is not None, name
        assert _where(found).parent == TOOLS.resolve(), (name, _where(found))
    assert not ran.exists(), ran.read_text(encoding="utf-8")


def test_the_desktop_still_finds_one_kept_beside_the_installs(tmp_path, monkeypatch, fresh):
    """Older desktop layouts kept the tools beside the installs, and a
    person's own computer is not a shared folder, so the desktop still
    looks there when the panel's own copy is missing."""
    data, ran = _plant(tmp_path)
    gui = tmp_path / "PaperPull" / "gui"
    gui.mkdir(parents=True)
    monkeypatch.setattr(app_module, "HERE", gui)
    monkeypatch.setenv("APPS_ROOT", str(data))
    monkeypatch.delenv("PAPERPULL_SERVER", raising=False)

    assert _where(app_module._status_module()) == (data / "status.py").resolve()
    assert ran.read_text(encoding="utf-8").splitlines() == [str(data / "status.py")]
