"""The Mac package is built for Apple Silicon and for Intel, and a release
carries both (#88).

The Mac build was arm64 only, so an Intel Mac had to run PaperPull from a
checkout. packaging/build_macos.py now takes --arch, the macOS workflow
builds each architecture on a runner of its own, and tools/release.sh and
tools/release_assets.py pick up and label every dmg it makes. A new
architecture added to one of them and not the others would build a dmg no
release ever carries, so they are checked against each other.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import release_assets  # noqa: E402


@pytest.fixture()
def bm(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "bm_arches", REPO / "packaging" / "build_macos.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "say", lambda *a, **k: None)
    return mod


def _workflow_arches():
    text = (REPO / ".github" / "workflows" / "build-macos.yml").read_text(encoding="utf-8")
    return dict(re.findall(r"- arch: (\S+)\s+runner: (\S+)", text))


def test_each_arch_fetches_a_python_of_its_own_architecture(bm, monkeypatch):
    monkeypatch.setattr(bm, "ARCH", "arm64")
    assert "-aarch64-apple-darwin-" in bm.pbs_asset()
    monkeypatch.setattr(bm, "ARCH", "x86_64")
    assert "-x86_64-apple-darwin-" in bm.pbs_asset()
    assert bm.pbs_url().endswith("/" + bm.pbs_asset())


def test_the_launcher_plist_and_dmg_follow_the_arch_being_built(bm):
    """A launcher compiled for arm64 inside an Intel bundle would not open
    at all on the Mac it was made for."""
    source = (REPO / "packaging" / "build_macos.py").read_text(encoding="utf-8")
    assert '"clang", "-arch", ARCH' in source
    assert '"LSArchitecturePriority": [ARCH]' in source
    assert '"PaperPull-%s-%s.dmg" % (version(), ARCH)' in source


def test_an_intel_mac_will_not_try_to_build_for_apple_silicon(bm, monkeypatch):
    monkeypatch.setattr(bm.sys, "platform", "darwin")
    monkeypatch.setattr(bm, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(bm, "stage_python", lambda: pytest.fail("built anyway"))
    assert bm.main(["--arch", "arm64"]) == 1


def test_the_workflow_builds_every_arch_on_a_runner_of_its_own(bm):
    arches = _workflow_arches()
    assert set(arches) == set(bm.ARCHES)
    assert arches["arm64"] != arches["x86_64"], "each is smoke-tested on its own hardware"
    assert "intel" in arches["x86_64"]


def test_a_release_carries_and_labels_every_dmg_the_workflow_builds():
    script = (REPO / "tools" / "release.sh").read_text(encoding="utf-8")
    notes = release_assets.downloads_section("1.2.3")
    for arch in _workflow_arches():
        name = "PaperPull-1.2.3-%s.dmg" % arch
        assert 'PaperPull-"$VERSION"-%s.dmg' % arch in script, arch
        assert release_assets.label(name, "1.2.3") != name, "%s has no label" % arch
        assert "`%s`" % name in notes, arch
