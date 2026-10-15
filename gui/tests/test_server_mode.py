"""The panel as PaperPull Server runs it, on the network behind a password.

The real panel is served on a local port by uvicorn, as the server image
serves it, and asked for things the way a browser on the home network
would ask. PAPERPULL_SERVER=1 switches the server's behavior on for a
test, and without it the panel is the desktop panel it always was.

Everything the panel reads or writes is under a throwaway folder, the
apps root, the settings, the server's password file and noVNC's files.
"""
import asyncio
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
app_module = pytest.importorskip("app")
uvicorn = pytest.importorskip("uvicorn")
import server_mode  # noqa: E402

PASSWORD = "an invented passphrase"


@pytest.fixture(scope="module")
def base():
    """The panel, served on a free local port for the whole module."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app_module.app, host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield "http://127.0.0.1:%d" % port
    server.should_exit = True
    thread.join(10)


@pytest.fixture(autouse=True)
def own_folders(tmp_path, monkeypatch):
    """Every place the panel might read or write is under tmp_path, and
    nothing of the server's is left from another test."""
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "settings"))
    (tmp_path / "apps").mkdir()
    monkeypatch.setenv("APPS_ROOT", str(tmp_path / "apps"))
    monkeypatch.setenv("PAPERPULL_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("PAPERPULL_PASSWORD", raising=False)
    monkeypatch.delenv("PAPERPULL_SERVER", raising=False)
    monkeypatch.delenv("PAPERPULL_HOSTS", raising=False)
    server_mode._SESSIONS.clear()
    server_mode._FAILS.clear()
    server_mode._SETUP["code"] = None
    yield


@pytest.fixture()
def on_server(monkeypatch):
    monkeypatch.setenv("PAPERPULL_SERVER", "1")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def ask(url, method="GET", form=None, headers=None, session=None, body=None):
    """(status, headers, body) for one request, redirects not followed. form
    is sent as a form, body as JSON."""
    data, kind = None, None
    if form is not None:
        data, kind = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    if body is not None:
        data, kind = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if kind:
        req.add_header("Content-Type", kind)
    if session:
        req.add_header("Cookie", "%s=%s" % (server_mode.SESSION_COOKIE, session))
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as r:
            return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


def session_from(headers) -> str:
    for value in headers.get_all("Set-Cookie") or []:
        name, _, rest = value.partition("=")
        if name == server_mode.SESSION_COOKIE:
            return rest.split(";")[0]
    return ""


def signed_in(base) -> str:
    server_mode.set_password(PASSWORD)
    status, headers, _ = ask(base + "/login", "POST", {"password": PASSWORD})
    assert status == 303, status
    return session_from(headers)


# -- the desktop panel is untouched ------------------------------------------------

def test_off_the_server_nothing_changes(base):
    status, _, page = ask(base + "/")
    assert status == 200 and 'class="serverbar"' not in page and "__SERVER_BAR__" not in page
    assert 'action="/logout"' not in page
    assert ask(base + "/login")[0] == 404
    assert ask(base + "/setup")[0] == 404
    assert ask(base + "/screen")[0] == 404
    assert ask(base + "/api/apps")[0] == 200


# -- the first visit -----------------------------------------------------------------

def test_with_no_password_everything_leads_to_choosing_one(base, on_server):
    status, headers, _ = ask(base + "/")
    assert status == 303 and headers["Location"] == "/setup"
    assert ask(base + "/api/apps")[0] == 401
    assert ask(base + "/favicon.ico")[0] == 200
    status, _, page = ask(base + "/setup")
    assert status == 200 and "Setup code" in page


def test_choosing_it_takes_the_code_from_the_log(base, on_server):
    ask(base + "/setup")
    code = server_mode._SETUP["code"]
    assert code

    wrong = ask(base + "/setup", "POST", {"code": "AAAA-BBBB-CCCC", "password": PASSWORD,
                                          "again": PASSWORD})
    assert wrong[0] == 401 and not server_mode.password_set()
    short = ask(base + "/setup", "POST", {"code": code, "password": "short", "again": "short"})
    assert short[0] == 400 and not server_mode.password_set()
    apart = ask(base + "/setup", "POST", {"code": code, "password": PASSWORD,
                                          "again": PASSWORD + "!"})
    assert apart[0] == 400 and not server_mode.password_set()

    status, headers, _ = ask(base + "/setup", "POST",
                             {"code": code.lower(), "password": PASSWORD, "again": PASSWORD})
    assert status == 303 and headers["Location"] == "/"
    cookie = [v for v in headers.get_all("Set-Cookie") if v.startswith("paperpull_session=")][0]
    assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    assert ask(base + "/", session=session_from(headers))[0] == 200
    # Once chosen, the setup page is gone and the code with it.
    assert ask(base + "/setup")[1]["Location"] == "/login"
    assert server_mode._SETUP["code"] is None


def test_the_password_is_kept_only_as_a_hash(base, on_server, tmp_path):
    server_mode.set_password(PASSWORD)
    raw = (tmp_path / "config" / "server.json").read_text(encoding="utf-8")
    assert PASSWORD not in raw
    kept = json.loads(raw)["password"]
    assert set(kept) == {"scrypt", "salt", "hash"} and len(kept["hash"]) == 64
    if os.name != "nt":
        assert (tmp_path / "config" / "server.json").stat().st_mode & 0o077 == 0


def test_a_password_from_the_environment_is_taken_at_start(on_server, monkeypatch):
    monkeypatch.setenv("PAPERPULL_PASSWORD", PASSWORD)
    server_mode.on_start()
    assert server_mode.password_set() and server_mode.check_password(PASSWORD)
    assert server_mode._SETUP["code"] is None


def test_a_short_one_from_the_environment_is_not(on_server, monkeypatch, capsys):
    monkeypatch.setenv("PAPERPULL_PASSWORD", "short")
    server_mode.on_start()
    assert not server_mode.password_set()
    assert server_mode._SETUP["code"] and server_mode._SETUP["code"] in capsys.readouterr().out


# -- signing in ----------------------------------------------------------------------

def test_signing_in_and_out(base, on_server):
    server_mode.set_password(PASSWORD)
    status, headers, _ = ask(base + "/")
    assert status == 303 and headers["Location"] == "/login"
    assert ask(base + "/login", "POST", {"password": "not it at all"})[0] == 401

    session = signed_in(base)
    status, _, page = ask(base + "/", session=session)
    assert status == 200 and 'href="/screen"' in page and 'action="/logout"' in page
    assert ask(base + "/api/apps", session=session)[0] == 200

    status, headers, _ = ask(base + "/logout", "POST", session=session)
    assert status == 303 and headers["Location"] == "/login"
    assert ask(base + "/", session=session)[0] == 303


def session_cookies(headers) -> dict:
    """{name: the whole Set-Cookie line} for each session cookie an answer sets."""
    out = {}
    for value in headers.get_all("Set-Cookie") or []:
        name = value.partition("=")[0]
        if name in server_mode.SESSION_COOKIES:
            out[name] = value
    return out


def is_secure(cookie: str) -> bool:
    return "secure" in [part.strip().lower() for part in cookie.split(";")]


def test_the_session_cookie_travels_only_over_https_when_it_came_that_way(base, on_server):
    """On the home network the panel speaks plain HTTP, and a browser keeps
    no Secure cookie sent that way, so there the cookie is not marked.
    Behind a reverse proxy that adds HTTPS it is, under a name of its own,
    so a later plain HTTP visit to the proxy's name never carries it in the
    clear, and a plain HTTP sign-in at that name is never refused its cookie
    because the browser holds a Secure one of the same name."""
    server_mode.set_password(PASSWORD)
    plain = session_cookies(ask(base + "/login", "POST", {"password": PASSWORD})[1])
    assert list(plain) == [server_mode.SESSION_COOKIE]
    assert not is_secure(plain[server_mode.SESSION_COOKIE])
    for proxy in ({"X-Forwarded-Proto": "https"},
                  {"X-Forwarded-Proto": "https, http"},
                  {"Forwarded": 'for=192.0.2.1;proto="https", for=10.0.0.2;proto=http'}):
        status, headers, _ = ask(base + "/login", "POST", {"password": PASSWORD}, headers=proxy)
        cookies = session_cookies(headers)
        assert status == 303 and list(cookies) == [server_mode.SECURE_SESSION_COOKIE], proxy
        assert is_secure(cookies[server_mode.SECURE_SESSION_COOKIE]), proxy
    for proxy in ({"X-Forwarded-Proto": "http"}, {"X-Forwarded-Proto": "http, https"},
                  {"Forwarded": "for=192.0.2.1;proto=http"}):
        status, headers, _ = ask(base + "/login", "POST", {"password": PASSWORD}, headers=proxy)
        assert status == 303 and list(session_cookies(headers)) == [server_mode.SESSION_COOKIE]


def test_a_session_from_either_cookie_is_signed_in_and_signing_out_ends_both(base, on_server):
    plain = signed_in(base)
    over_https = {"X-Forwarded-Proto": "https"}
    secure = session_cookies(ask(base + "/login", "POST", {"password": PASSWORD},
                                 headers=over_https)[1])[server_mode.SECURE_SESSION_COOKIE]
    secure = secure.split(";")[0].partition("=")[2]
    both = {"Cookie": "%s=%s; %s=%s" % (server_mode.SECURE_SESSION_COOKIE, secure,
                                        server_mode.SESSION_COOKIE, plain)}
    as_secure = {"Cookie": "%s=%s" % (server_mode.SECURE_SESSION_COOKIE, secure)}
    assert ask(base + "/", headers=as_secure)[0] == 200
    assert ask(base + "/", session=plain)[0] == 200
    # A plain cookie whose session ended does not hide a live Secure one.
    stale = {"Cookie": "%s=%s; %s=%s" % (server_mode.SESSION_COOKIE, "ended",
                                         server_mode.SECURE_SESSION_COOKIE, secure)}
    assert ask(base + "/", headers=stale)[0] == 200

    status, headers, _ = ask(base + "/logout", "POST", headers=dict(both, **over_https))
    assert status == 303
    cleared = session_cookies(headers)
    assert sorted(cleared) == sorted(server_mode.SESSION_COOKIES)
    assert is_secure(cleared[server_mode.SECURE_SESSION_COOKIE])
    assert ask(base + "/", headers=as_secure)[0] == 303
    assert ask(base + "/", session=plain)[0] == 303


def test_signing_out_by_plain_http_leaves_the_secure_cookie_alone(base, on_server):
    """A browser would refuse to clear it from a plain HTTP page anyway."""
    session = signed_in(base)
    status, headers, _ = ask(base + "/logout", "POST", session=session)
    assert status == 303 and list(session_cookies(headers)) == [server_mode.SESSION_COOKIE]


def test_choosing_the_password_through_an_https_proxy_marks_the_cookie(base, on_server):
    ask(base + "/setup")
    status, headers, _ = ask(base + "/setup", "POST",
                             {"code": server_mode._SETUP["code"], "password": PASSWORD,
                              "again": PASSWORD}, headers={"X-Forwarded-Proto": "https"})
    cookies = session_cookies(headers)
    assert status == 303 and list(cookies) == [server_mode.SECURE_SESSION_COOKIE]
    assert is_secure(cookies[server_mode.SECURE_SESSION_COOKIE])


def scope_with(scheme: str, *headers) -> dict:
    return {"scheme": scheme, "headers": [(k.encode(), v.encode()) for k, v in headers]}


def test_what_a_proxy_says_is_read_from_its_first_line_and_entry():
    """Read straight from the scope, since the test server's uvicorn trusts
    proxy headers from 127.0.0.1 and turns some into the scheme itself."""
    https = server_mode.over_https
    assert https(scope_with("https")) and https(scope_with("wss"))
    assert not https(scope_with("http")) and not https(scope_with("ws"))
    assert https(scope_with("http", ("x-forwarded-proto", "https")))
    assert https(scope_with("http", ("X-Forwarded-Proto", " HTTPS , http")))
    assert not https(scope_with("http", ("x-forwarded-proto", "http, https")))
    assert https(scope_with("http", ("x-forwarded-proto", "https"), ("x-forwarded-proto", "http")))
    assert not https(scope_with("http", ("x-forwarded-proto", "http"), ("x-forwarded-proto", "https")))
    assert https(scope_with("http", ("forwarded", 'for=192.0.2.1;Proto="https";host=nas')))
    assert not https(scope_with("http", ("forwarded", "for=192.0.2.1;proto=http, proto=https")))
    assert not https(scope_with("http", ("forwarded", "for=192.0.2.1")))


def test_the_session_is_found_in_either_cookie(on_server):
    server_mode.set_password(PASSWORD)
    live = server_mode.new_session()
    secure, plain = server_mode.SECURE_SESSION_COOKIE, server_mode.SESSION_COOKIE
    assert server_mode.session_token({"cookie": "%s=%s" % (secure, live)}) == live
    assert server_mode.session_token({"cookie": "%s=%s" % (plain, live)}) == live
    assert server_mode.session_token({"cookie": "%s=old; %s=%s" % (secure, plain, live)}) == live
    assert server_mode.session_token({"cookie": "%s=old; %s=%s" % (plain, secure, live)}) == live
    assert server_mode.session_token({"cookie": "other=%s" % live}) == ""
    assert server_mode.session_token({}) == ""


def test_wrong_passwords_make_an_address_wait(base, on_server):
    server_mode.set_password(PASSWORD)
    for _ in range(server_mode.FREE_TRIES):
        assert ask(base + "/login", "POST", {"password": "a wrong guess"})[0] == 401
    status, _, page = ask(base + "/login", "POST", {"password": PASSWORD})
    assert status == 429 and "Try again in" in page


def test_a_new_password_signs_every_session_out(base, on_server):
    session = signed_in(base)
    server_mode.set_password(PASSWORD + " again")
    assert ask(base + "/", session=session)[0] == 303


def test_a_password_forgotten_by_another_process_signs_everyone_out(base, on_server):
    """server/reset_password.py runs in a process of its own, beside the
    panel, and the panel's sessions still end."""
    session = signed_in(base)
    data = server_mode._read()
    del data["password"]
    server_mode._write(data)

    status, headers, _ = ask(base + "/", session=session)
    assert status == 303 and headers["Location"] == "/setup"


def test_the_reset_tool_forgets_the_password_and_nothing_else(on_server, tmp_path, capsys):
    import importlib.util
    server_mode.set_password(PASSWORD)
    data = server_mode._read()
    data["kept"] = "another setting"
    server_mode._write(data)
    tool = Path(__file__).resolve().parents[2] / "server" / "reset_password.py"
    spec = importlib.util.spec_from_file_location("reset_password", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main() == 0

    assert not server_mode.password_set()
    assert server_mode._read() == {"kept": "another setting"}
    assert "signed out" in capsys.readouterr().out


# -- requests from somewhere else ---------------------------------------------------

def test_another_sites_request_is_refused_even_signed_in(base, on_server):
    session = signed_in(base)
    host = base.split("//")[1]
    assert ask(base + "/api/apps", session=session,
               headers={"Origin": "http://elsewhere.example"})[0] == 403
    assert ask(base + "/api/apps", session=session,
               headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert ask(base + "/api/apps", session=session,
               headers={"Origin": "http://" + host, "Sec-Fetch-Site": "same-origin"})[0] == 200
    assert ask(base + "/login", "POST", {"password": PASSWORD},
               headers={"Origin": "http://elsewhere.example"})[0] == 403


def test_signing_out_takes_a_request_from_the_panels_own_page(base, on_server):
    """Ending a session is a change like any other, so a request that names
    no page of the panel's, Origin null among them, leaves it signed in."""
    session = signed_in(base)
    status, _, _ = ask(base + "/logout", "POST", {}, session=session, headers={"Origin": "null"})
    assert status == 403 and ask(base + "/", session=session)[0] == 200
    status, _, _ = ask(base + "/logout", "POST", {}, session=session, headers={"Origin": base})
    assert status == 303 and ask(base + "/", session=session)[0] == 303


# -- the names it answers to ---------------------------------------------------------

def test_the_server_answers_to_its_addresses_and_to_home_network_names():
    for name in ("192.168.50.20", "10.0.0.5", "172.17.0.2", "fd00::5", "::1", "127.0.0.1",
                 "localhost", "nas", "nas.local", "nas.lan", "nas.home", "nas.home.arpa",
                 "nas.internal", "nas.localdomain"):
        assert server_mode.host_allowed(name), name


def test_a_name_a_website_could_give_is_refused_until_it_is_listed(monkeypatch):
    """A website can point a name of its own at the server, a browser on
    the home network then takes that site's page for the panel's own, and
    the sign-in page would answer it. A VPN's or a reverse proxy's name is
    listed in PAPERPULL_HOSTS, written any way a person might paste it. So
    is a router's name under a real top-level domain, such as fritz.box,
    since .box can be registered."""
    for name in ("rebind.example", "paperpull.example.com", "nas.tail1234.ts.net",
                 "nas.local.example.com", "nas.fritz.box", ""):
        assert not server_mode.host_allowed(name), name
    monkeypatch.setenv("PAPERPULL_HOSTS", "paperpull.example.com, https://NAS.tail1234.ts.net:443/")
    assert server_mode.host_allowed("paperpull.example.com")
    assert server_mode.host_allowed("nas.tail1234.ts.net")
    assert not server_mode.host_allowed("rebind.example")


def test_a_page_that_points_its_own_name_at_the_server_is_refused(base, on_server):
    session = signed_in(base)
    port = base.rsplit(":", 1)[1]
    elsewhere = {"Host": "rebind.example:" + port}
    status, _, said = ask(base + "/api/apps", headers=elsewhere, session=session)
    assert status == 400 and "rebind.example" in said and "PAPERPULL_HOSTS" in said
    status, _, _ = ask(base + "/login", "POST", {"password": PASSWORD}, headers=elsewhere)
    assert status == 400 and not server_mode._FAILS, "no password was even tried"
    assert ask(base + "/api/apps", session=session)[0] == 200
    assert ask(base + "/api/apps", session=session, headers={"Host": "nas.local:" + port})[0] == 200


def test_a_name_listed_in_paperpull_hosts_is_answered(base, on_server, monkeypatch):
    session = signed_in(base)
    by_proxy = {"Host": "paperpull.example.com:" + base.rsplit(":", 1)[1]}
    assert ask(base + "/api/apps", session=session, headers=by_proxy)[0] == 400
    monkeypatch.setenv("PAPERPULL_HOSTS", "paperpull.example.com")
    assert ask(base + "/api/apps", session=session, headers=by_proxy)[0] == 200


def test_off_the_server_the_panel_answers_only_at_its_own_address(base):
    port = base.rsplit(":", 1)[1]
    status, _, said = ask(base + "/", headers={"Host": "rebind.example:" + port})
    assert status == 400 and "http://127.0.0.1:%s" % port in said
    assert ask(base + "/api/apps", headers={"Host": "192.168.1.20:" + port})[0] == 400
    assert ask(base + "/", headers={"Host": "localhost:" + port})[0] == 200


def test_no_other_site_may_show_the_panel_in_a_frame(base, on_server, tmp_path, monkeypatch):
    """Every answer says so, a refusal included. The one page that may be
    framed, by the panel's own Browser Screen alone, is noVNC's."""
    (tmp_path / "novnc").mkdir()
    (tmp_path / "novnc" / "vnc.html").write_text("<title>noVNC</title>", encoding="utf-8")
    monkeypatch.setenv("PAPERPULL_NOVNC", str(tmp_path / "novnc"))
    session = signed_in(base)
    for path, headers in (("/", {}), ("/screen", {}), ("/api/apps", {}), ("/login", {}),
                          ("/api/apps", {"Host": "rebind.example"})):
        _, answer, _ = ask(base + path, session=session, headers=headers)
        assert answer["X-Frame-Options"] == "DENY", path
        assert answer["Content-Security-Policy"] == "frame-ancestors 'none'", path
    _, answer, _ = ask(base + "/screen/novnc/vnc.html", session=session)
    assert answer["X-Frame-Options"] == "SAMEORIGIN"
    assert answer["Content-Security-Policy"] == "frame-ancestors 'self'"


def test_what_only_means_something_on_a_desktop_is_not_offered(base, on_server):
    session = signed_in(base)
    for path in ("/api/root", "/api/export/reveal", "/api/failure/reveal"):
        assert ask(base + path, "POST", {}, session=session)[0] == 404, path


# -- the browser screen ------------------------------------------------------------------

def test_the_screen_is_behind_the_password(base, on_server, tmp_path, monkeypatch):
    (tmp_path / "novnc").mkdir()
    (tmp_path / "novnc" / "vnc.html").write_text("<title>noVNC</title>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not noVNC's", encoding="utf-8")
    monkeypatch.setenv("PAPERPULL_NOVNC", str(tmp_path / "novnc"))
    server_mode.set_password(PASSWORD)

    assert ask(base + "/screen")[0] == 303
    assert ask(base + "/screen/novnc/vnc.html")[0] == 303

    session = signed_in(base)
    status, _, page = ask(base + "/screen", session=session)
    assert status == 200 and "path=screen/websockify" in page and "/favicon.ico" in page
    assert "<title>PaperPull Browser Screen</title>" in page
    assert ask(base + "/screen/novnc/vnc.html", session=session)[2] == "<title>noVNC</title>"
    assert ask(base + "/screen/novnc/%2e%2e/secret.txt", session=session)[0] == 404


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_a_screen_connection_needs_a_session_from_the_panels_own_page(on_server):
    server_mode.set_password(PASSWORD)
    token = server_mode.new_session()
    passed = []

    async def inner(scope, receive, send):
        passed.append(scope["path"])

    async def attempt(headers):
        sent = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "websocket", "path": "/screen/websockify",
                 "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
        await server_mode.SignInGate(inner)(scope, receive, send)
        return sent

    host = {"host": "nas.local:8765"}
    assert run(attempt(host)) == [{"type": "websocket.close", "code": 1008}]
    cookie = {"cookie": "%s=%s" % (server_mode.SESSION_COOKIE, token)}
    other = dict(host, origin="http://elsewhere.example", **cookie)
    assert run(attempt(other)) == [{"type": "websocket.close", "code": 1008}]
    assert not passed
    own = dict(host, origin="http://nas.local:8765", **cookie)
    assert run(attempt(own)) == [] and passed == ["/screen/websockify"]


def test_a_closed_screen_tab_ends_its_connection_quietly(on_server, monkeypatch):
    """Closing the Browser Screen's tab drops its connection, and the panel
    closing it afterwards found it gone. Every tab closed on the NAS logged
    a whole traceback for it. It ends quietly now."""
    from fastapi import FastAPI

    async def scenario():
        async def screen_sharing(reader, writer):
            await reader.read()
            writer.close()

        sharing = await asyncio.start_server(screen_sharing, "127.0.0.1", 0)
        monkeypatch.setenv("PAPERPULL_VNC_PORT", str(sharing.sockets[0].getsockname()[1]))
        app = FastAPI()
        server_mode.install(app)
        cookie = "%s=%s" % (server_mode.SESSION_COOKIE, server_mode.new_session())
        from_page = [{"type": "websocket.connect"}, {"type": "websocket.disconnect", "code": 1006}]

        async def receive():
            return from_page.pop(0)

        async def send(message):
            # What uvicorn raises for a page that is already gone.
            if message["type"] == "websocket.close":
                raise OSError("the page is gone")

        scope = {"type": "websocket", "path": "/screen/websockify", "query_string": b"",
                 "headers": [(b"host", b"nas.local:8765"), (b"origin", b"http://nas.local:8765"),
                             (b"cookie", cookie.encode())]}
        try:
            await asyncio.wait_for(app(scope, receive, send), 10)
        finally:
            sharing.close()

    server_mode.set_password(PASSWORD)
    run(scenario())


@pytest.mark.parametrize("cookie", server_mode.SESSION_COOKIES)
@pytest.mark.parametrize("end", ["signing out", "a new password", "the reset tool"])
def test_an_open_browser_screen_closes_once_its_session_ends(on_server, monkeypatch, end, cookie):
    """The gate looks at the session when the Browser Screen connects, and
    the connection then carried keyboard and mouse until one side closed.
    Signing out, a new password and reset_password.py all left a screen
    already open working, on every provider signed in there. It now closes
    within seconds of any of them, and stays open while the session holds."""
    from fastapi import FastAPI
    monkeypatch.setattr(server_mode, "SCREEN_CHECK_SECONDS", 0.05)
    server_mode.set_password(PASSWORD)

    async def scenario():
        connected = asyncio.Event()

        async def screen_sharing(reader, writer):
            connected.set()
            await reader.read()
            writer.close()

        sharing = await asyncio.start_server(screen_sharing, "127.0.0.1", 0)
        monkeypatch.setenv("PAPERPULL_VNC_PORT", str(sharing.sockets[0].getsockname()[1]))
        app = FastAPI()
        server_mode.install(app)
        token = server_mode.new_session()
        from_page = [{"type": "websocket.connect"}]
        sent = []

        async def receive():
            if from_page:
                return from_page.pop()
            await asyncio.Event().wait()     # the page says nothing more

        async def send(message):
            sent.append(message)

        scope = {"type": "websocket", "path": "/screen/websockify", "query_string": b"",
                 "headers": [(b"host", b"nas.local:8765"), (b"origin", b"http://nas.local:8765"),
                             (b"cookie", ("%s=%s" % (cookie, token)).encode())]}
        screen = asyncio.ensure_future(app(scope, receive, send))
        try:
            await asyncio.wait_for(connected.wait(), 10)
            await asyncio.sleep(0.3)
            assert not screen.done(), "the screen stays open while its session holds"
            if end == "signing out":
                server_mode.end_session(token)
            elif end == "a new password":
                server_mode.set_password(PASSWORD + " changed")
            else:
                settings = server_mode._read()
                del settings["password"]
                server_mode._write(settings)
            await asyncio.wait_for(screen, 5)
        finally:
            screen.cancel()
            sharing.close()
        return sent

    sent = run(scenario())
    assert sent[-1] == {"type": "websocket.close", "code": 1008, "reason": ""}


def test_wrong_passwords_sent_at_once_still_wait(base, on_server):
    """The wait after wrong passwords was looked at before a login's body
    came in, so logins whose headers all arrived first passed it together
    and guesses were limited only by how fast scrypt runs. Each is judged
    once its body is in now, after the one before it was counted."""
    server_mode.set_password(PASSWORD)
    port = int(base.rsplit(":", 1)[1])
    body = b"password=not+the+password"
    head = ("POST /login HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: %s\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\nContent-Length: %d\r\n"
            "Connection: close\r\n\r\n" % (port, base, len(body))).encode()
    tries = 12
    socks = [socket.create_connection(("127.0.0.1", port), timeout=60) for _ in range(tries)]
    try:
        for s in socks:
            s.sendall(head)
        time.sleep(0.5)    # every login's headers are in before any body
        for s in socks:
            s.sendall(body)
        statuses = []
        for s in socks:
            answer = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                answer += chunk
            statuses.append(int(answer.split(b" ", 2)[1]))
    finally:
        for s in socks:
            s.close()
    assert statuses.count(401) <= server_mode.FREE_TRIES, statuses
    assert statuses.count(429) == tries - statuses.count(401), statuses


def test_the_screen_bridge_carries_bytes_both_ways():
    """The page's messages reach the screen sharing server as they are, and
    what the server sends comes back as binary messages, until the page
    goes."""
    async def scenario():
        got_by_server = []

        async def fake_server(reader, writer):
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            got_by_server.append(await reader.read(100))
            writer.close()

        server = await asyncio.start_server(fake_server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        to_page = []
        messages = asyncio.Queue()
        await messages.put({"type": "websocket.receive", "bytes": b"RFB 003.008\n"})

        async def receive():
            return await messages.get()

        async def send_bytes(data):
            to_page.append(data)

        await asyncio.wait_for(server_mode.bridge(receive, send_bytes, reader, writer), 10)
        server.close()
        return got_by_server, to_page

    got_by_server, to_page = run(scenario())
    assert got_by_server == [b"RFB 003.008\n"]
    assert b"".join(to_page) == b"RFB 003.008\n"


# -- setting providers up on the server ---------------------------------------------------

def test_the_page_knows_it_is_on_the_server(base, on_server, monkeypatch):
    session = signed_in(base)
    assert "const SERVER = true;" in ask(base + "/", session=session)[2]
    monkeypatch.delenv("PAPERPULL_SERVER")
    assert "const SERVER = false;" in ask(base + "/")[2]


def test_the_welcome_page_says_where_the_data_folder_is_on_the_server(base, on_server):
    """/data is only the container's name for the folder. A person looking
    for it on the NAS finds a folder named data beside compose.yaml, so the
    page says so, on the server and nowhere else."""
    page = ask(base + "/", session=signed_in(base))[2]
    start = page.index('<p class="hint" id="serverroot" hidden>')
    note = page[start:page.index("</p>", start)]
    assert "<b>data</b>" in note and "compose.yaml" in note
    welcome = page.split("async function openWelcome(add) {", 1)[1].split("\n}\n", 1)[0]
    shown = welcome[welcome.index("if (SERVER) {"):]
    assert "$('serverroot').hidden = false;" in shown and "$('newroot').readOnly = true;" in shown
    assert "the data folder beside compose.yaml" in page.lower()


def test_the_sign_in_window_is_a_button_at_the_top_and_docks_on_sign_in(base, on_server):
    """Every sign-in on the server happens on the server's own screen, so it
    is a button at the top, not a small link. Open sign-in window docks it
    beside the page, and the step that offers it says where it opens."""
    page = ask(base + "/", session=signed_in(base))[2]
    bar = page[page.index('<div class="serverbar">'):]
    bar = bar[:bar.index("</form></div>") + len("</form></div>")]
    assert '<a id="screenbtn" class="btn btn-s screenbtn" href="/screen"' in bar
    assert "Sign-in window</a>" in bar and "sign-in pages open" in bar
    assert 'action="/logout"' in bar and "Sign out</button>" in bar
    assert "if (SERVER && action === 'login') openDock(r.provider);" in page
    login = " ".join(page.split("const loginSay = SERVER", 1)[1].split(";\n", 1)[0].split())
    assert "in the sign-in window, docked beside this page" in login
    assert "frame.src = '/screen/novnc/vnc.html?" in page


def test_both_editions_say_what_discover_is_for(base, on_server, monkeypatch):
    """Get new documents is the one main button. Pilot and Discover are links
    beside it, named for what they do, and Discover says it downloaded
    nothing when it ends."""
    server_page = ask(base + "/", session=signed_in(base))[2]
    monkeypatch.delenv("PAPERPULL_SERVER")
    desktop_page = ask(base + "/")[2]
    for page in (server_page, desktop_page):
        account = page[page.index("function renderAccount()"):]
        account = account[:account.index("\nfunction ")]
        main = account.index('data-action="all"')
        assert main < account.index('data-action="pilot"') < account.index('data-action="discover"')
        assert "Try the newest few first</button>" in account
        assert "Only check what\\'s there</button>" in account
        assert "discover: ['Checked ' + who" in page and "Nothing was downloaded." in page


def _footer(page):
    return " ".join(page[page.index("<footer"):page.index("</footer>")].split())


def test_each_edition_says_only_what_is_true_of_it_at_the_bottom(base, on_server, monkeypatch):
    """The desktop footer says the panel runs locally and offers the paid
    Store edition. The server is free and runs on a box of the person's
    own, so it says that and keeps only the donation link."""
    server = _footer(ask(base + "/", session=signed_in(base))[2])
    monkeypatch.delenv("PAPERPULL_SERVER")
    desktop = _footer(ask(base + "/")[2])
    assert "PaperPull Server v%s &middot;" % app_module.VERSION in server
    assert "beta" not in server.lower()
    assert "runs on your own server" in server and "ko-fi.com/rheeloaded" in server
    assert "runs locally" not in server and "Store edition" not in server
    assert "paperpull#support" not in server
    assert "PaperPull v%s" % app_module.VERSION in desktop and "PaperPull Server" not in desktop
    assert "runs locally" in desktop and "buy the Store edition</a>" in desktop
    assert "ko-fi.com/rheeloaded" in desktop
    for page in (server, desktop):
        assert "__FOOTER__" not in page and "__VERSION__" not in page


def test_the_shared_folder_is_the_only_one_offered(base, on_server, tmp_path):
    session = signed_in(base)
    status, _, page = ask(base + "/api/providers", session=session)
    data = json.loads(page)
    assert status == 200 and data["server"] is True
    assert data["suggested_root"] == str(tmp_path / "apps")


def test_a_provider_set_up_on_the_server_goes_into_the_shared_folder(
        base, on_server, tmp_path, monkeypatch):
    """Whatever folder the page sends, and with its sign-in profile in the
    profiles volume, out of the shared folder."""
    monkeypatch.setenv("PAPERPULL_PROFILES", str(tmp_path / "profiles"))
    session = signed_in(base)

    status, _, page = ask(base + "/api/create", "POST", session=session,
                          body={"root": str(tmp_path / "elsewhere"), "providers": ["walmart"],
                                "owner": ""})

    assert status == 200, page
    folder = tmp_path / "apps" / "Walmart Receipts"
    config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    assert config["profile_dir"] == str(tmp_path / "profiles" / "Walmart Receipts")
    assert not (tmp_path / "elsewhere").exists()
    assert not list((tmp_path / "settings").rglob("settings.json")), "nothing remembered"


def test_a_second_account_signs_in_from_the_profiles_volume_too(
        base, on_server, tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERPULL_PROFILES", str(tmp_path / "profiles"))
    session = signed_in(base)
    ask(base + "/api/create", "POST", session=session,
        body={"root": "", "providers": ["walmart"], "owner": ""})

    status, _, page = ask(base + "/api/account", "POST", session=session,
                          body={"app": "Walmart Receipts", "label": "spouse", "owner": ""})

    assert status == 200, page
    config = json.loads((tmp_path / "apps" / "Walmart Receipts" / "config.spouse.json")
                        .read_text(encoding="utf-8"))
    assert config["profile_dir"] == str(tmp_path / "profiles" / "Walmart Receipts - spouse")
    assert "Walmart Receipts - spouse" in config["output_dir"]


def test_off_the_server_a_set_folder_still_cannot_be_overridden(base):
    status, _, _ = ask(base + "/api/create", "POST",
                       body={"root": "C:/anything", "providers": ["walmart"], "owner": ""})
    assert status == 409


def test_what_the_desktop_shows_in_a_folder_the_server_downloads(
        base, on_server, tmp_path, monkeypatch):
    session = signed_in(base)
    shop = tmp_path / "apps" / "Shop Receipts"
    (shop / "Diagnostics").mkdir(parents=True)
    (shop / "shop_receipts.py").write_text("", encoding="utf-8")
    (shop / "Diagnostics" / "failure-run-1.json").write_text('{"step": "a step"}',
                                                             encoding="utf-8")
    sheet = tmp_path / "PaperPull purchases.xlsx"
    sheet.write_bytes(b"a spreadsheet")
    monkeypatch.setattr(app_module, "_LAST_EXPORT", sheet)

    status, headers, page = ask(base + "/api/failure/download?app=Shop%20Receipts",
                                session=session)
    assert status == 200 and page == '{"step": "a step"}'
    assert "failure-run-1.json" in headers.get("Content-Disposition", "")
    assert ask(base + "/api/export/download", session=session)[2] == "a spreadsheet"
    assert ask(base + "/api/failure/download?app=Nobody", session=session)[0] == 404

    monkeypatch.delenv("PAPERPULL_SERVER")
    assert ask(base + "/api/export/download")[0] == 404
    assert ask(base + "/api/failure/download?app=Shop%20Receipts")[0] == 404


# -- what a run hands the plug-ins ----------------------------------------------------------

SHOP = '''
import sys
from pathlib import Path
# Where its output goes. Real apps file under the config's output_dir, which
# is "." for a fresh install and so resolves to the working directory the
# panel runs them in, the install folder in the /data share. On the server
# the code itself comes from the image, so a fake that wrote next to its own
# file would write into the read-only image, which no real app does.
here = Path.cwd()
if "--pilot" in sys.argv:
    saved = here / "In-Store" / "2026-01-02 Shop Receipt.pdf"
    saved.parent.mkdir(exist_ok=True)
    saved.write_bytes(b"%PDF-1.7 a receipt")
    (here / "new-this-run.txt").write_text(
        "# 1 file(s) downloaded on this run\\n" + str(saved) + "\\n", encoding="utf-8")
    print("Saved: " + saved.name)
print('PAPERPULL_RUN_RESULT {"new_files": 1, "failed": 0}')
'''

ECHO = '''
import json, sys
from pathlib import Path
event = json.load(sys.stdin)
names = [Path(p).name for p in event["new_files"]]
print("%s %s heard %d: %s" % (event["provider"], event["action"], len(names), ", ".join(names)))
'''


@pytest.fixture()
def shop(tmp_path, monkeypatch):
    """A provider's folder in the shared folder, with an app that saves one
    receipt, and a plug-in that says what it was told.

    The server runs the provider's code from the image, so the same app
    also ships as a template under an image-like tree, and the panel is
    pointed at it. The folder in the shared folder holds the data, the
    config among it, exactly as a real install does.
    """
    image = tmp_path / "image"
    (image / "gui").mkdir(parents=True)
    template = image / "templates" / "apps" / "shop"
    template.mkdir(parents=True)
    (template / "shop_receipts.py").write_text(SHOP, encoding="utf-8")

    folder = tmp_path / "apps" / "Shop Receipts"
    folder.mkdir(parents=True)
    (folder / "shop_receipts.py").write_text(SHOP, encoding="utf-8")
    (folder / "config.json").write_text('{"output_dir": "."}', encoding="utf-8")
    echo = tmp_path / "plugins" / "echo"
    echo.mkdir(parents=True)
    (echo / "plugin.json").write_text(json.dumps(
        {"name": "Echo", "events": ["run-finished"], "run": ["python", "echo.py"]}),
        encoding="utf-8")
    (echo / "echo.py").write_text(ECHO, encoding="utf-8")
    monkeypatch.setenv("PAPERPULL_PLUGINS", str(tmp_path / "plugins"))
    # Point the panel at the image-like tree, so it is packaged (templates
    # and no checkout apps beside it) and the server runs each app with its
    # one Python from the image, the way the real build does.
    monkeypatch.setattr(app_module, "HERE", image / "gui")
    return folder


def test_a_run_hands_its_new_documents_to_the_plugins(base, on_server, shop):
    session = signed_in(base)
    status, _, stream = ask(base + "/api/run?app=Shop%20Receipts&action=pilot", session=session)
    assert status == 200
    told = "data: [Echo] Shop Receipts pilot heard 1: 2026-01-02 Shop Receipt.pdf"
    assert told in stream, stream
    assert stream.index("[Echo]") < stream.index("event: done")


def test_off_the_server_no_plugin_runs(base, shop):
    status, _, stream = ask(base + "/api/run?app=Shop%20Receipts&action=pilot")
    assert status == 200 and "[Echo]" not in stream and "event: done" in stream


def test_a_list_from_an_earlier_run_is_not_handed_on(on_server, shop):
    (shop / "new-this-run.txt").write_text(
        "# 1 file(s)\n" + str(shop / "old.pdf") + "\n", encoding="utf-8")
    (shop / "old.pdf").write_bytes(b"%PDF old")
    old = time.time() - 600
    os.utime(shop / "new-this-run.txt", (old, old))

    assert server_mode.new_files(shop, "primary", time.time() - 5) == []
    assert server_mode.new_files(shop, "primary", old - 5) == [str((shop / "old.pdf").resolve())]


def test_only_files_in_the_shared_folder_are_handed_on(on_server, shop, tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF somewhere else")
    inside = shop / "In-Store" / "kept.pdf"
    inside.parent.mkdir()
    inside.write_bytes(b"%PDF kept")
    (shop / "new-this-run.txt").write_text("\n".join(
        ["# 3 file(s)", str(outside), "In-Store/kept.pdf", str(shop / "gone.pdf")]) + "\n",
        encoding="utf-8")

    assert server_mode.new_files(shop, "primary", 0) == [str(inside.resolve())]


def test_a_plugin_that_fails_is_reported_and_the_run_still_ends(base, on_server, shop, tmp_path):
    bad = tmp_path / "plugins" / "bad"
    bad.mkdir()
    (bad / "plugin.json").write_text(json.dumps(
        {"name": "Bad", "events": ["run-finished"], "run": ["python", "bad.py"]}),
        encoding="utf-8")
    (bad / "bad.py").write_text("print('something went wrong')\nraise SystemExit(3)\n",
                                encoding="utf-8")
    session = signed_in(base)

    stream = ask(base + "/api/run?app=Shop%20Receipts&action=pilot", session=session)[2]

    assert "data: [Bad] something went wrong" in stream
    assert "data: [Bad] ended with code 3." in stream
    assert "[Echo] Shop Receipts pilot heard 1" in stream and "event: done" in stream
