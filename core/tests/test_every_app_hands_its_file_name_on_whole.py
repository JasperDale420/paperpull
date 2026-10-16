"""Every app hands the name build_pdf_filename gives it on to unique_path
whole, so the folders a naming pattern names are never lost (#101).

A pattern with a / names folders, and build_pdf_filename hands them over
with the name, as a FileName, which is the name itself and carries its
folders. unique_path files the name in them. A name an app changed on the
way, cut, joined to a folder or given an ending, is plain text and is
filed in the folder its document is routed to with no folders, which is
safe and is also a pattern quietly not followed. And a name handed to
unique_path with the folder of a file already saved, out_path.parent, would
make the pattern's folders inside the ones that file is in. Ally, Newrez,
NetBenefits and Robinhood each named a saved file again that way, from
what its PDF said, and now file it from the folder its kind is routed to
(storage.refile).

So this reads every app's own code. Each name a build_pdf_filename call
gives is followed through the names it is given and into each method of
the app it is handed to, and every place it reaches has to be one of
these. The name argument of unique_path, fitted_name or refile, with a
folder that is not a saved file's parent. A comparison, which reads the
name alone, as the seventy-odd comparisons of out_path.name with it do.
Text shown to a person, in an f-string, print or a log. Anything else is
named here. The canary at the end holds the census to the shapes it has
to see.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Each function that files a name, by the place of its name argument and of
# its folder argument among the positional ones.
FILERS = {"unique_path": (1, 0), "fitted_name": (1, 0), "refile": (2, 1)}
SHOWN = {"print", "log.info", "log.debug", "log.warning", "log.error", "say"}


def sources():
    return sorted(p for p in (REPO / "apps").glob("*/*.py")
                  if not p.name.startswith("test_") and p.name != "storage.py")


def _parents(tree) -> dict:
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _enclosing(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def census(source: str, where: str = "") -> tuple:
    """(what is wrong, as "line: why", the names followed, the filings
    found) for one module's source."""
    tree = ast.parse(source)
    parents = _parents(tree)
    methods = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.setdefault(node.name, []).append(node)
    wrong, followed, filed = [], 0, 0
    todo = [("call", node) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "build_pdf_filename"]
    seen = set()
    while todo:
        kind, item = todo.pop()
        if kind == "call":
            followed += 1
            uses, fn = [item], _enclosing(item, parents)
        else:
            fn, name = item
            if (id(fn), name) in seen:
                continue
            seen.add((id(fn), name))
            uses = [n for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == name
                    and isinstance(n.ctx, ast.Load)]
        for use in uses:
            parent = parents[use]
            line = "%s%d" % (where, use.lineno)
            if (isinstance(parent, ast.Assign) and len(parent.targets) == 1
                    and isinstance(parent.targets[0], ast.Name)):
                todo.append(("name", (fn, parent.targets[0].id)))
                continue
            if isinstance(parent, (ast.FormattedValue, ast.Compare)):
                continue
            call, at = None, None
            if isinstance(parent, ast.keyword) and isinstance(parents[parent], ast.Call):
                call, at = parents[parent], parent.arg
            elif isinstance(parent, ast.Call) and use in parent.args:
                call, at = parent, parent.args.index(use)
            if call is None:
                wrong.append("%s: the name is changed or kept by %s"
                             % (line, ast.unparse(parent)[:80]))
                continue
            func = ast.unparse(call.func)
            short = func.split(".")[-1]
            if short in FILERS:
                name_at, folder_at = FILERS[short]
                if at not in (name_at, "filename"):
                    wrong.append("%s: the name is handed to %s as something else" % (line, short))
                    continue
                folder = call.args[folder_at] if len(call.args) > folder_at else None
                if folder is None or ".parent" in ast.unparse(folder):
                    wrong.append("%s: %s files the name from %s, a saved file's own folder"
                                 % (line, short, ast.unparse(folder) if folder else "nothing"))
                    continue
                filed += 1
                continue
            if func in SHOWN:
                continue
            if func.startswith("self.") and short in methods and func.count(".") == 1:
                for target in methods[short]:
                    params = [a.arg for a in target.args.args]
                    if isinstance(at, int) and at + 1 < len(params):
                        todo.append(("name", (target, params[at + 1])))
                    elif isinstance(at, str) and at in params:
                        todo.append(("name", (target, at)))
                    else:
                        wrong.append("%s: %s takes the name where it cannot be followed"
                                     % (line, func))
                continue
            wrong.append("%s: the name is handed to %s" % (line, func))
    return wrong, followed, filed


def test_every_app_hands_its_file_name_on_whole():
    wrong, followed, filed = [], 0, 0
    for path in sources():
        found, n, f = census(path.read_text(encoding="utf-8-sig"),
                             "%s:" % path.relative_to(REPO).as_posix())
        wrong += found
        followed += n
        filed += f
    assert not wrong, "\n".join(wrong)
    # Not a census that passes because it found nobody.
    assert followed >= 95 and filed >= 80, (followed, filed)


CANARY = '''
class App:
    def process(self, doc):
        filename = build_pdf_filename(doc.date, doc.summary, "", record=doc)
        print(f"  DRY RUN - would save: {filename}")
        self.download_one(None, doc, filename)

    def download_one(self, page, doc, filename):
        out_path = unique_path(self.paths.folder_for(doc.category), filename, 240)
        if out_path.name != filename:
            pass

    def joined(self, doc):
        filename = build_pdf_filename(doc.date, doc.summary, "", record=doc)
        return self.paths.statements / filename

    def cut(self, doc):
        name = build_pdf_filename(doc.date, doc.summary, "", record=doc)
        return unique_path(self.folder, name.replace(".pdf", " x.pdf"), 240)

    def from_its_parent(self, doc, out_path):
        name = build_pdf_filename(doc.date, doc.summary, "", record=doc)
        return unique_path(out_path.parent, name, 240)

    def handed_away(self, doc):
        site.save(build_pdf_filename(doc.date, doc.summary, "", record=doc))

    def refiled(self, doc, out_path):
        name = build_pdf_filename(doc.date, doc.summary, "", record=doc)
        return refile(out_path, self.paths.folder_for(doc.category), name, 240)
'''


def test_the_census_tells_each_shape_apart():
    """A name joined to a folder, cut, filed from a saved file's own folder
    or handed to something else is named, and a name followed into a
    method, compared, shown, filed or refiled from a routed folder is not."""
    wrong, followed, filed = census(CANARY)
    expected = ["changed or kept by self.paths.statements / filename",
                "changed or kept by name.replace",
                "unique_path files the name from out_path.parent",
                "handed to site.save"]
    assert len(wrong) == len(expected), wrong
    for said in expected:
        assert any(said in w for w in wrong), (said, wrong)
    assert followed == 6 and filed == 2, (followed, filed)
