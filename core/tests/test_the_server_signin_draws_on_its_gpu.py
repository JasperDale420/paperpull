"""PaperPull Server's sign-in window draws on the machine's GPU when it has one.

On the server the window draws on a virtual screen with no graphics of its
own, so Chrome drew in software and turned WebGL off, and Best Buy's bot
check refused every sign-in there while the desktop signed in at once. With
the GPU passed to the container, the window is told to draw through Vulkan
on it, shown only the real chip's driver, never Mesa's software one. Off the
server, with no GPU, with no driver for it, or with PAPERPULL_GPU=off, it
starts as it always did.
"""
from paperpull_core import browser


def _machine(tmp_path, monkeypatch, nodes=("renderD128",), drivers=("intel_icd.x86_64.json",
                                                                    "lvp_icd.x86_64.json")):
    dri, icd = tmp_path / "dri", tmp_path / "icd.d"
    dri.mkdir(parents=True)
    icd.mkdir(parents=True)
    for n in nodes:
        (dri / n).write_text("")
    for d in drivers:
        (icd / d).write_text("{}")
    monkeypatch.setattr(browser, "_RENDER_NODES", dri)
    monkeypatch.setattr(browser, "_VULKAN_DRIVERS", icd)
    monkeypatch.setenv("PAPERPULL_SERVER", "1")
    monkeypatch.delenv("PAPERPULL_GPU", raising=False)
    return icd


def test_a_server_with_a_gpu_draws_through_vulkan_on_the_real_chip(tmp_path, monkeypatch):
    icd = _machine(tmp_path, monkeypatch)
    switches, env = browser.server_gpu()
    assert "--use-angle=vulkan" in switches
    assert env["VK_ICD_FILENAMES"] == str(icd / "intel_icd.x86_64.json")
    assert "lvp" not in env["VK_DRIVER_FILES"]
    assert not [s for s in switches if browser.turns_sandbox_off(s)]


def test_the_window_starts_as_always_off_the_server_without_a_gpu_or_when_turned_off(
        tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch, nodes=())
    assert browser.server_gpu() == ((), None)
    _machine(tmp_path / "b", monkeypatch, drivers=("lvp_icd.x86_64.json",))
    assert browser.server_gpu() == ((), None)
    _machine(tmp_path / "c", monkeypatch)
    monkeypatch.setenv("PAPERPULL_GPU", "off")
    assert browser.server_gpu() == ((), None)
    monkeypatch.delenv("PAPERPULL_SERVER")
    monkeypatch.delenv("PAPERPULL_GPU")
    assert browser.server_gpu() == ((), None)


def test_the_launch_hands_the_switches_and_drivers_to_the_browser(tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch)
    started = []

    def popen(argv, **kwargs):
        started.append((list(argv), kwargs.get("env")))
        raise OSError("not started in a test")

    monkeypatch.setattr(browser.subprocess, "Popen", popen)
    browser._launch("/x/chrome", browser.CHROME, tmp_path / "p", "9333",
                    "https://example.invalid/", explain_failure=False)
    argv, env = started[0]
    assert "--use-angle=vulkan" in argv and argv[-1] == "https://example.invalid/"
    assert "VK_ICD_FILENAMES" in env


def test_a_sign_in_on_a_server_without_graphics_says_so_and_where_to_look(tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch, nodes=())
    note = browser.profile_note("Chrome")
    assert "no graphics card" in note and "Graphics in SERVER.md" in note
    _machine(tmp_path / "b", monkeypatch)
    assert "no graphics card" not in browser.profile_note("Chrome")
    monkeypatch.delenv("PAPERPULL_SERVER")
    assert "no graphics card" not in browser.profile_note("Chrome")


def test_a_failure_file_says_how_the_server_draws_and_the_desktop_says_nothing(
        tmp_path, monkeypatch):
    import json
    from paperpull_core import failure
    for nodes, said in (((), "software"), (("renderD128",), "gpu")):
        _machine(tmp_path / said, monkeypatch, nodes=nodes)
        path = failure.write_failure(tmp_path / said / "out", command="pilot",
                                     step="sign in", reason="", provider="Best Buy")
        assert json.loads(open(path, encoding="utf-8").read())["graphics"] == said
    monkeypatch.delenv("PAPERPULL_SERVER")
    path = failure.write_failure(tmp_path / "desk", command="pilot", step="sign in",
                                 reason="", provider="Best Buy")
    assert "graphics" not in json.loads(open(path, encoding="utf-8").read())
