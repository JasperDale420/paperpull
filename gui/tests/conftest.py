"""Every test here keeps away from the settings of whoever runs it (#153).

_settings_path picks its place by platform, and on macOS reads no variable
at all, so a fixture that moved APPDATA, LOCALAPPDATA and XDG_CONFIG_HOME
moved nothing there: the suite replaced the real settings.json, and the
panel of somebody using PaperPull on that Mac showed no accounts. CI did
not notice, having no real settings to lose.

A test that points _settings_path somewhere of its own still does, since
its patch comes after this one. REAL_SETTINGS is the path the function
gives before any test runs, and the session fails if that file changes,
which catches what a patch here cannot reach, such as a panel started as a
process of its own.
"""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import app as app_module
except ImportError:         # the tests that need it skip themselves
    app_module = None

REAL_SETTINGS = app_module._settings_path() if app_module else None


def _state(path):
    """What is at path: its bytes' digest, or None for nothing there."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def pytest_configure(config):
    config.addinivalue_line("markers", "reads_settings_path: this test is of _settings_path itself, "
                                       "and moves what it reads")


@pytest.fixture(autouse=True)
def own_settings(request, tmp_path, monkeypatch):
    if app_module is not None and not request.node.get_closest_marker("reads_settings_path"):
        monkeypatch.setattr(app_module, "_settings_path",
                            lambda: tmp_path / "own-settings" / "settings.json")


@pytest.fixture(autouse=True, scope="session")
def real_settings_are_left_alone():
    if REAL_SETTINGS is None:
        yield
        return
    before = _state(REAL_SETTINGS)
    yield
    assert _state(REAL_SETTINGS) == before, (
        "A test changed the real settings of whoever ran the suite, at %s. Restore "
        "apps_root there, and find the test that wrote it." % REAL_SETTINGS)
