"""Every config setting that holds an address is held on PaperPull Server.

On the server a provider's config.json sits in a shared folder that other
people may write, so a setting that names where a run connects or what
its signed-in browser opens cannot be taken as written. The census below
reads every app's config.example.json, the config_defaults and
url_settings of its AppSpec, and every setting its code reads, and finds
each one that holds an address by its value or its name. Each has to be
held one of three ways.

  * cdp_url, the browser a run drives, which the core holds to 127.0.0.1
    or localhost for every app (hold_to_the_server).
  * A setting the app declares in AppSpec.url_settings, which the core
    holds to https on the provider's own domains. UKG's base_url is one.
  * A setting the app's own code checks against a fixed list on every
    platform, listed in HELD_BY_THE_APP with a test of that check below.

So a provider added next year with a tenant address of its own fails
here until it says which domains that address may be on.
"""
import ast
import importlib
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
APPS = sorted(d for d in (REPO / "apps").iterdir()
              if d.is_dir() and (d / "storage.py").exists())

# A value that is an address, by its shape.
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
HOST = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,63}/?$", re.I)
# A setting that is an address, by its name.
NAMED = re.compile(r"(?:^|_)(?:url|uri|host|hostname|domain|site|address|origin|endpoint|"
                   r"tenant|server|marketplace|portal|base)(?:_|$)", re.I)
# A setting an app's code reads.
READ = re.compile(r"""\b\w*(?:config|cfg)\w*(?:\.get\(|\[|\.setdefault\()\s*["'](\w+)["']""")

HELD_BY_THE_APP = {
    # amazon_site.set_marketplace takes only a store in MARKETPLACES, since
    # it also sets the host allowlist, on the desktop and the server alike.
    ("amazon", "marketplace"),
}


def spec_call(app: Path):
    """The AppSpec(...) call in an app's storage.py."""
    tree = ast.parse((app / "storage.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "AppSpec":
            return node
    raise AssertionError("%s/storage.py has no AppSpec(...)" % app.name)


def keyword(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def literal_items(node) -> dict:
    """A dict literal's items, a value that is not a literal read as None."""
    if node is None:
        return {}
    out = {}
    for k, v in zip(node.keys, node.values):
        try:
            out[ast.literal_eval(k)] = ast.literal_eval(v)
        except ValueError:
            out[ast.literal_eval(k)] = None
    return out


def settings_of(app: Path) -> dict:
    """Every setting the app knows, each with a value where one is given."""
    found = {}
    example = app / "config.example.json"
    if example.exists():
        for key, value in json.loads(example.read_text(encoding="utf-8-sig")).items():
            if not key.startswith(("//", "_")):
                found[key] = value
    for key, value in literal_items(keyword(spec_call(app), "config_defaults")).items():
        found.setdefault(key, value)
    for py in app.glob("*.py"):
        for key in READ.findall(py.read_text(encoding="utf-8", errors="replace")):
            found.setdefault(key, None)
    return found


def declared(app: Path) -> dict:
    return literal_items(keyword(spec_call(app), "url_settings"))


def is_address(key: str, value) -> bool:
    if NAMED.search(key):
        return True
    return isinstance(value, str) and bool(SCHEME.match(value) or HOST.match(value.strip()))


def census():
    """(app, setting) for every setting that holds an address."""
    return sorted((app.name, key) for app in APPS
                  for key, value in settings_of(app).items() if is_address(key, value))


def test_the_census_finds_the_address_settings_it_is_meant_to():
    """So a reading that quietly found nothing cannot pass the next test."""
    found = census()
    assert ("ukg", "base_url") in found and ("amazon", "marketplace") in found
    assert ("target", "cdp_url") in found
    assert len(APPS) >= 70


def test_every_address_setting_is_held_on_the_server():
    loose = [(app, key) for app, key in census()
             if key != "cdp_url" and (app, key) not in HELD_BY_THE_APP
             and key not in declared(REPO / "apps" / app)]
    assert loose == [], (
        "These settings hold an address and nothing holds them on PaperPull Server, "
        "where config.json sits in a shared folder. Declare each in the app's "
        "AppSpec.url_settings with the domains it may be on: %s" % loose)


@pytest.mark.parametrize("app", [a for a in APPS if declared(a)], ids=lambda a: a.name)
def test_every_declared_address_setting_is_one_the_app_has(app):
    """A declaration left behind after its setting is gone holds nothing."""
    assert set(declared(app)) <= set(settings_of(app))


def test_the_ukg_address_is_declared_with_ukgs_domains():
    assert declared(REPO / "apps" / "ukg") == {"base_url": ("ultipro.com", "saashr.com")}


@pytest.mark.parametrize("given", [
    "evil.example", "https://evil.example", "amazon.com.evil.example",
    "evil.example/amazon.com", "amazon.com@evil.example"])
def test_amazons_marketplace_is_held_to_its_list_everywhere(given):
    sys.path.insert(0, str(REPO / "apps" / "amazon"))
    try:
        site = importlib.import_module("amazon_site")
    finally:
        sys.path.pop(0)
    before = (site.MARKETPLACE, site.BASE, set(site.ALLOWED_HOSTS))
    with pytest.raises(ValueError):
        site.set_marketplace(given)
    assert (site.MARKETPLACE, site.BASE, set(site.ALLOWED_HOSTS)) == before
