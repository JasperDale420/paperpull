"""The headless fallback prints only from Target's own hosts.

When the ordinary print fails, the receipt's address is printed again in a
headless browser of the core's (receipt_pdf.render_url_headless). It used
to check that first address and nothing after it. The core now asks the
app's guard about every page, every redirect and everything the page
loads, and lets the app name the static hosts its pages are drawn from,
which may serve only scripts, styles, fonts and pictures, and leaves
its browser an address for the hosts the app names and no other. These
hold Target to handing all three over, and its static hosts to exactly two.
"""
import ast
import inspect
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: F401  binds this provider's AppSpec
import target_receipts
import target_site as site


def _fallback_calls():
    tree = ast.parse(textwrap.dedent(inspect.getsource(target_receipts.App._save_receipt)))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "render_url_headless"]


def test_the_fallback_hands_over_targets_guards_and_hosts():
    calls = _fallback_calls()
    assert len(calls) == 1
    given = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert given == {"is_safe_url": "site.is_safe_url",
                     "is_asset_url": "site.is_print_asset_url",
                     "hosts": "site.PRINT_HOSTS"}


def test_only_targets_two_static_hosts_are_asset_hosts():
    for url in ("https://assets.targetimg1.com/webui/app.js",
                "https://target.scene7.com/is/image/x"):
        assert site.is_print_asset_url(url), url
    for url in ("http://assets.targetimg1.com/app.js",
                "https://x.assets.targetimg1.com/app.js",
                "https://targetimg1.com/app.js",
                "https://scene7.com/is/image/x",
                "https://other.scene7.com/is/image/x",
                "https://assets.targetimg1.com.evil.example/app.js",
                "https://www.target.com/app.js",
                "https://client.px-cloud.net/main.js",
                "https://assets.targetimg1.com:8443/app.js",
                "", None):
        assert not site.is_print_asset_url(url), url


def test_no_asset_host_is_one_of_targets_own_pages():
    """The static hosts are never pages, and the guard for pages does not
    take them either, so a redirect to one prints nothing."""
    for host in site.PRINT_ASSET_HOSTS:
        assert not site.is_safe_url("https://%s/" % host), host


def test_the_hosts_the_browser_may_reach_are_the_ones_the_guards_take():
    """Every host either guard accepts is named, so the page is not cut off
    from its own, and nothing else is, so the browser reaches no other."""
    from paperpull_core import receipt_pdf
    receipt_pdf._resolver_rules(site.PRINT_HOSTS)
    for host in site.PRINT_HOSTS:
        probe = "https://%s/x" % host.replace("*.", "www.", 1)
        assert site.is_safe_url(probe) or site.is_print_asset_url(probe), host
    for host in site.ALLOWED_HOSTS:
        assert host in site.PRINT_HOSTS and "*." + host in site.PRINT_HOSTS, host
    for host in site.PRINT_ASSET_HOSTS:
        assert host in site.PRINT_HOSTS, host
    assert len(site.PRINT_HOSTS) == 2 * len(site.ALLOWED_HOSTS) + len(site.PRINT_ASSET_HOSTS)
