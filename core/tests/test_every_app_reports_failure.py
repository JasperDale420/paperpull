"""Every app writes a failure file, and writes at most one.

The reason a new provider takes eight rounds is that the maintainer
cannot run it, and the tester sends back a sentence. This is the thing
that turns that sentence into evidence, so an app that quietly does not
do it is an app back on eight rounds without anybody noticing.
"""
import ast
import io
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def entries():
    out = []
    for app in sorted((REPO / "apps").iterdir()):
        if not app.is_dir():
            continue
        found = list(app.glob("*_docs.py")) + list(app.glob("*_receipts.py"))
        if found:
            out.append(found[0])
    return out


ENTRIES = entries()
IDS = [p.parent.name for p in ENTRIES]

# Apps that keep no census of the calls the provider answered, and why. The
# census reads every response's headers, and each of these reads nothing of
# a request or response but what its own code names. Each is held below to
# having no census anywhere and to handing the failure file requests=None.
NO_CENSUS = {
    "spectrum": "reads nothing of a request or response but the one GraphQL answer "
                "spectrum_site.Watch names; a census would read every response's headers",
}


def census_app(entry):
    """Skip an app on NO_CENSUS, for the tests that ask for the census."""
    if entry.parent.name in NO_CENSUS:
        pytest.skip("keeps no request census: %s" % NO_CENSUS[entry.parent.name])


def source(path):
    return io.open(path, encoding="utf-8", errors="ignore").read()


def test_there_are_apps_to_check():
    assert len(ENTRIES) > 40


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_app_can_write_a_failure_file(entry):
    text = source(entry)
    assert "from paperpull_core import failure" in text
    assert "def write_failure(self" in text


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_app_actually_calls_it_somewhere(entry):
    """A method nobody calls is eight rounds with extra steps."""
    calls = re.findall(r"\bself\.write_failure\(", source(entry))
    assert calls, "nothing in this app ever writes a failure file"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_only_the_first_failure_of_a_run_writes_one(entry):
    """A run where thirty documents fail for one reason does not need
    thirty files, and the thirtieth is taken long after the page has
    moved on from the thing that broke."""
    text = source(entry)
    m = re.search(r"def write_failure\(self.*?(?=\n    def )", text, re.S)
    assert m, "no method to check"
    body = m.group(0)
    assert 'if self.stats.get("failure_files"):' in body
    assert "return" in body


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_it_hands_over_the_apps_own_selectors(entry):
    """The census is the whole point, and it needs the table."""
    text = source(entry)
    m = re.search(r"def write_failure\(self.*?(?=\n    def )", text, re.S)
    assert 'selectors=getattr(site, "FALLBACK", None)' in m.group(0)


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_it_names_its_provider(entry):
    text = source(entry)
    m = re.search(r"def write_failure\(self.*?(?=\n    def )", text, re.S)
    assert re.search(r"provider=['\"][^'\"]+['\"]", m.group(0)), \
        "a file that does not say which provider it came from"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_writing_one_can_never_stop_a_run(entry):
    """It runs when something has already gone wrong. A diagnostic that
    raises in the middle of a failure costs the round it was meant to
    save, so nothing in the method may be left unguarded."""
    text = source(entry)
    m = re.search(r"def write_failure\(self.*?(?=\n    def )", text, re.S)
    body = m.group(0)
    # The one thing that could raise is reading the file back to say
    # what is in it, and it is wrapped.
    assert "try:" in body and "except Exception:" in body


def test_no_app_writes_a_failure_file_from_a_look_only_run():
    """Diagnose and record download nothing, so they have nothing to
    fail at, and a file from one of them would be noise."""
    for entry in ENTRIES:
        text = source(entry)
        for mode in ("cmd_diagnose", "cmd_record"):
            m = re.search(r"def %s\(self.*?(?=\n    def )" % mode, text, re.S)
            if m:
                assert "self.write_failure(" not in m.group(0), \
                    "%s calls it from %s" % (entry.parent.name, mode)


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_app_keeps_a_journal(entry):
    """The census says what the page looked like when a run gave up. The
    journal is the only thing that can speak for a layer it got past."""
    text = source(entry)
    assert "from paperpull_core.journal import Journal" in text
    assert "def journal(self)" in text


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_journal_is_made_only_when_something_writes_to_it(entry):
    """A run that never opens a page has nothing to say, and must not
    fail differently because of this."""
    m = re.search(r"def journal\(self\).*?(?=\n    def )", source(entry), re.S)
    assert m, "no property to check"
    assert "if self._journal is None:" in m.group(0)


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_failure_file_carries_the_journal(entry):
    m = re.search(r"def write_failure\(self.*?(?=\n    def )",
                  source(entry), re.S)
    assert "journal=self._journal" in m.group(0), \
        "a failure file with nothing about how the run got there"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_app_marks_a_document_it_saved(entry):
    """The entry a later failure is read against. Without one, a run
    that saved three documents and then broke looks the same as a run
    that never saved anything."""
    assert re.search(r"self\.journal\.checkpoint\(", source(entry)), \
        "nothing in this app records a document going well"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_app_counts_the_calls_the_provider_answered(entry):
    """Eleven of these drive an API and declare no selectors, so the
    selector census has nothing to say about them. This is their half,
    and it is on every app because a page-driven one calls an API too."""
    census_app(entry)
    text = source(entry)
    assert "from paperpull_core.api_census import Requests" in text
    assert "def requests(self)" in text


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_it_starts_listening_where_the_page_is_made(entry):
    """It only sees what arrives after it starts, so starting it at the
    first failure would be starting it too late."""
    census_app(entry)
    text = source(entry)
    m = re.search(r"\n    def page\(self.*?(?=\n    def )", text, re.S)
    assert m, "no page method"
    assert "self.requests" in m.group(0), \
        "nothing starts the request census when the page is made"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_failure_file_carries_the_calls(entry):
    census_app(entry)
    m = re.search(r"def write_failure\(self.*?(?=\n    def )",
                  source(entry), re.S)
    assert "requests=self._requests" in m.group(0)


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_failure_file_has_a_journal_even_when_nothing_was_saved(entry):
    """The journal was made the first time something wrote to it, and in
    most apps the only thing that wrote was a document being saved. Every
    failure file testers sent in on 2026-09-25, from five providers, came
    back with no journal at all, which is the run that needed one. A
    checkpoint as the run gives up makes it, and records the page's state
    at that moment."""
    m = re.search(r"def write_failure\(self.*?(?=\n    def )", source(entry), re.S)
    assert m and 'self.journal.checkpoint("when the run gave up")' in m.group(0)
    assert m.group(0).index("when the run gave up") < m.group(0).index(
        "failure.write_failure(")


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_journal_says_which_document_the_run_was_on(entry):
    """So a failure file says how far a run got and whether it ever
    reached a second document, which is where the bugs that work once and
    fail twice live."""
    text = source(entry)
    assert '"next_item" if i > 1 else "open_item"' in text


def census_uses(path: Path) -> list:
    """Every place a module reaches for the request census, by line.

    An import of api_census in any form, aliased ones included (`import
    paperpull_core.api_census as c`, `from paperpull_core.api_census import
    Requests as R`), any use of a name such an import bound, `self.requests`
    or `self._requests`, and a `def requests`, the property the census is
    made through."""
    tree = ast.parse(source(path))
    found, bound, package = [], set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if "api_census" in parts:
                    found.append(node.lineno)
                    bound.add(alias.asname or parts[0])
                elif parts[0] == "paperpull_core":
                    # The package itself, through which api_census is one
                    # attribute away: `import paperpull_core as pc`.
                    package.add(alias.asname or parts[0])
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")
            for alias in node.names:
                if "api_census" in module or alias.name == "api_census":
                    found.append(node.lineno)
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "requests":
            found.append(node.lineno)
        elif (isinstance(node, ast.Attribute) and node.attr in ("requests", "_requests")
              and isinstance(node.value, ast.Name) and node.value.id == "self"):
            found.append(node.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in bound:
            found.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "api_census":
            # Any chain reaching api_census, `pc.api_census.Requests` and
            # `paperpull_core.api_census` included, wherever it starts.
            found.append(node.lineno)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in ("getattr", "__import__", "import_module")
              or isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "import_module"):
            if any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                   and "api_census" in a.value for a in node.args):
                found.append(node.lineno)
    return sorted(set(found))


def hands_over_no_requests(path: Path) -> bool:
    """True when the app's write_failure calls failure.write_failure, and
    every such call passes requests=None."""
    tree = ast.parse(source(path))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "write_failure":
            calls += [c for c in ast.walk(node) if isinstance(c, ast.Call)
                      and isinstance(c.func, ast.Attribute) and c.func.attr == "write_failure"]
    def none(call):
        kw = [k for k in call.keywords if k.arg == "requests"]
        return (len(kw) == 1 and isinstance(kw[0].value, ast.Constant)
                and kw[0].value.value is None)
    return bool(calls) and all(none(c) for c in calls)


@pytest.mark.parametrize("name", sorted(NO_CENSUS))
def test_an_app_without_a_census_has_none_anywhere(name):
    """An app let off the census keeps none in any of its modules, and its
    failure file says so by handing over requests=None, or its entry goes."""
    app = REPO / "apps" / name
    entry = [p for p in ENTRIES if p.parent == app]
    assert entry, "%s is not an app here" % name
    modules = [p for p in sorted(app.glob("*.py")) if not p.name.startswith("test_")]
    used = {p.name: census_uses(p) for p in modules}
    assert not any(used.values()), "%s reaches for the census: %s" % (name, used)
    assert hands_over_no_requests(entry[0]), \
        "%s's write_failure does not hand over requests=None" % name


@pytest.mark.parametrize("text", [
    "from paperpull_core.api_census import Requests\n",
    "from paperpull_core.api_census import Requests as R\n",
    "from paperpull_core import api_census\n",
    "import paperpull_core.api_census as c\n",
    "import paperpull_core.api_census\n",
    "class A:\n    def go(self):\n        return self.requests\n",
    "class A:\n    def go(self):\n        self._requests = None\n",
    "class A:\n    @property\n    def requests(self):\n        return 1\n",
    "import paperpull_core as pc\nx = pc.api_census.Requests(page)\n",
    "import paperpull_core\nx = paperpull_core.api_census\n",
    "import paperpull_core as pc\nx = getattr(pc, 'api_census')\n",
    "import importlib\nx = importlib.import_module('paperpull_core.api_census')\n",
], ids=["from-import", "from-import-as", "module-from", "import-as", "import",
        "self-requests", "self-_requests", "property", "package-alias", "package",
        "getattr", "import-module"])
def test_the_census_reader_finds_a_census(tmp_path, text):
    """The reader under the test above, on modules written to fail it."""
    probe = tmp_path / "made_docs.py"
    probe.write_text(text, encoding="utf-8")
    assert census_uses(probe)


def test_the_census_reader_finds_a_use_of_an_aliased_name(tmp_path):
    probe = tmp_path / "made_docs.py"
    probe.write_text("import paperpull_core.api_census as c\n\n\nx = c.Requests(page)\n",
                     encoding="utf-8")
    assert census_uses(probe) == [1, 4]


def test_the_census_reader_lets_a_module_without_one_pass(tmp_path):
    """Nothing here is the census: a library named requests, a word that
    only starts like it, someone else's attribute, and requests=None."""
    probe = tmp_path / "made_docs.py"
    probe.write_text(
        "import requests\n"
        "import paperpull_core as pc\n"
        "from paperpull_core import failure\n"
        "pc.capture.set_download_dir(page, d)\n"
        "class A:\n"
        "    def write_failure(self, step, reason):\n"
        "        requests_seen = other.requests\n"
        "        failure.write_failure('d', step=step, reason=reason, requests=None)\n",
        encoding="utf-8")
    assert census_uses(probe) == []
    assert hands_over_no_requests(probe)
