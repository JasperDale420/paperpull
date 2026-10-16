# Security, PaperPull Server

The desktop app listens on nothing your network can reach, see
[SECURITY.md](SECURITY.md). PaperPull Server does, by design, because you
use it from a browser on another computer. This page says what protects it,
and what is left to you.

Everything PaperPull itself does is as read-only as on the desktop. It
never moves money, changes a setting or submits a form beyond the one
exception the desktop page names, and every app keeps its host allowlist.
See "Design safety" in [SECURITY.md](SECURITY.md).

## The password

- **Chosen on the first visit.** Until there is a password, every page leads
  to one that asks for it, and that page wants a setup code the container
  prints in its log. So whoever reaches the page first on your network
  cannot choose it, only someone who can read the container's log, which is
  you. You can set it with `PAPERPULL_PASSWORD` in the environment instead.
- **Kept as a hash.** An scrypt hash in the `settings` volume, readable by
  the container's user only. The password itself is never written down.
  The hash is made at OWASP's scrypt cost of 16 MiB in five lanes
  (n=2^14, r=8, p=5). A password chosen with 0.53.0 or before was hashed
  at a fifth of that work, and is hashed again at this cost the next time
  you sign in, with nobody signed out. Hashing runs apart from the rest of
  the panel, two at a time at most, so the panel keeps answering while a
  password is checked, and a sign-in or setup form longer than 8 KiB is
  refused before more of it is read.
- **Sessions.** Signing in gives your browser a random token in a cookie
  that the page's scripts cannot read and other sites cannot send. A
  session lasts 14 days, and ends sooner when the container restarts, the
  password changes, or you sign out. A sign-in window left open closes
  within seconds of its session ending.
- **Wrong guesses wait.** After five wrong passwords or setup codes from one
  address, each next try waits, twice as long each time, up to a quarter of
  an hour. Each try is counted before its password is checked, so many
  sent at the same moment still meet the wait after the fifth.
- **Requests from other sites are refused,** even from a browser that is
  signed in, by checking that a request came from the panel's own page.
  A link opened from an email, a chat or a PDF can open the panel, never
  start a run. The panel also answers only to its own names, the server's
  address, a home network name such as `nas` or `nas.local` and the names
  you list in `PAPERPULL_HOSTS`, see step 4 in [SERVER.md](SERVER.md), so a
  website cannot reach it through your browser by pointing a name of its
  own at your server. And no other site may show the panel inside a page
  of its own.
- **A forgotten password** is removed with
  `docker exec paperpull python /opt/paperpull/server/reset_password.py`,
  which signs everyone out.

## Treat it like a bank password

Whoever has the password can use every site you are signed in to in the
sign-in window, as you. Choose one you use nowhere else, and keep it in your
password manager.

## Keep it on your home network

- **Never forward its port** on your router, and never put it on the open
  internet.
- **From outside,** reach it through your own VPN, such as WireGuard or
  Tailscale, into your home network.
- **HTTPS.** On your home network the panel speaks plain HTTP, like most
  things on a NAS. If its traffic crosses a network you do not trust, put it
  behind a reverse proxy that adds HTTPS, and list the name the proxy is
  reached by in `PAPERPULL_HOSTS`. When the proxy says the browser came by
  HTTPS, in `X-Forwarded-Proto` or `Forwarded`, the sign-in cookie is one
  of its own, `__Host-paperpull_session`, marked to travel over HTTPS
  only, so a plain HTTP visit to the proxy's name never carries it. Its
  prefix has the browser keep it only that way and only for the very name
  that set it, so no other name under the same domain can set or replace
  it. Caddy and Traefik say so on their own, and nginx
  does with `proxy_set_header X-Forwarded-Proto $scheme;`. Anyone else on
  a network the panel's plain HTTP crosses can read what it carries, the
  password as you sign in included, so a guest network or a device you do
  not trust on the same network is reason enough for HTTPS.
- **Rented servers** are not recommended, see "Where to run it" in
  [SERVER.md](SERVER.md).

## What runs inside the container

- **Not as root.** Everything runs as an ordinary user that takes your NAS
  user's ids, so the files it writes are yours and nothing more.
- **Chrome's sandbox stays on.** Chrome shuts every page it shows in a
  sandbox of its own. That needs the container to allow user namespaces,
  which Docker's default security profile forbids, and the usual workaround
  is to switch the profile off altogether, which leaves no filter at all.
  `server/seccomp-chrome.json` is instead Docker's own default profile with
  exactly two system calls allowed, `clone` with namespace flags and
  `unshare`, and nothing else changed (`server/seccomp.py` makes it).
  Never run the container with `seccomp=unconfined` or `privileged`.
- **Only the panel is on the network.** Each provider's browser debugging
  port and the screen sharing server listen inside the container only. The
  sign-in window reaches you through the panel, behind the password, on the
  panel's one port.
- **Chrome keeps no passwords.** Its password manager and card autofill are
  switched off by policy, since a container's browser profile has no
  operating system keyring to protect them.
- **Its Python packages are the ones it was tested with.**
  `server/requirements.txt` names every package the image installs from
  the Python Package Index, the ones they bring in too, each at one
  version with the hashes of its built files, and the image is built with
  pip refusing any file whose hash differs. PaperPull's own core is built
  from the repository with the setuptools named there.
- **Its base is pinned, and kept current.** The image starts from
  `python:3.12-slim-bookworm` named by its digest, so every build of a
  release starts from the very same files. A pinned base stops getting
  Debian's fixes, so Dependabot proposes the tag's newer digest each week,
  and the Server image workflow builds, starts and scans the image before
  that change can be merged. That workflow also runs each Monday, which
  scans the image again and says when the tag has a newer digest.
- **Its parts are listed and checked.** Every build lists every package in
  the image, Debian's, Python's and PaperPull's own, as an SPDX software
  bill of materials, and Grype checks that list for known
  vulnerabilities. A critical one with a fix stops the build, so no
  release publishes it. From the first release after 0.53.0, each image
  carries that list and a record of the GitHub Actions run that built it,
  both signed through Sigstore. The GitHub CLI checks them once you have
  signed in to it with `gh auth login`, and with `--format json` it shows
  the list itself.
  ```bash
  gh attestation verify oci://ghcr.io/rheeloaded/paperpull-server:<version> --repo rheeloaded/paperpull
  gh attestation verify oci://ghcr.io/rheeloaded/paperpull-server:<version> --repo rheeloaded/paperpull --predicate-type https://spdx.dev/Document/v2.3
  ```

## Where the provider code runs

On PaperPull Server the provider code comes from the image and nothing
else. The entry script, the Python interpreter and every module a run
imports are loaded from the read-only application folder inside the
container. The data folder holds only data, your configuration, your rules
file, the downloaded documents and the records. Editing a provider's code
files under the data folder, or placing a Python virtual environment there,
has no effect, because the server runs the image's copy.

This matters because the data folder is often a shared folder that more
than one person or device can write to. Keeping the code on the image side
means that being able to write to the share does not let anyone change the
code the server runs as you, inside the container that holds your signed-in
browser profiles.

Writable data is not the same as harmless. The files in the data folder are
not executable Python any more, but they still shape what a run does. A
provider's `document_rules.json` decides how a document is named and filed,
and `config.json` holds its settings. Treat the data folder as something
only you, the operator, should be able to write to, and restrict write
access to the share accordingly. A person who can write there can still
change how your documents are classified and named, even though they can no
longer run code.

Some settings in `config.json` decide where a run reaches outside a
provider's folder, so the server does not take them from the file on trust.

- **`profile_dir`** is always the account's folder in the `profiles`
  volume, named for the provider's folder and the account, whatever the
  file says. A run that finds another folder there says so and uses the
  volume's.
- **`cdp_url`** has to name a browser at `127.0.0.1` or `localhost`, the
  sign-in browsers inside the container, written plainly as a scheme, the
  name, a port and a path. A run whose `cdp_url` names any other machine
  stops before it connects to anything, so a changed setting cannot hand
  a run to a browser someone else controls.
- **`output_dir`** has to lead inside the data folder when a run starts,
  judged by where any link on the way leads, so a provider folder replaced
  by a link to somewhere else is refused. A run whose `output_dir` leads
  outside stops before it saves anything, and the panel keeps no run log,
  reads no records and imports or exports no history there.
- **UKG's `base_url`**, the employer's UKG site that its sign-in browser
  opens and every run reaches, has to be an https address on UKG's own
  domains, `ultipro.com` or `saashr.com`, with nothing after the name,
  such as `https://yourcompany.ultipro.com`. UKG's check of each page it
  requests allows whatever site this names, so a run or a sign-in whose
  `base_url` names any other site stops before it opens a page, and a
  changed setting cannot put somebody else's page in the Browser Screen in
  place of UKG's. A UKG site an employer runs under a name of its own, as
  some Workforce Central sites are, is refused on the server too.
  Amazon's `marketplace` already had to be one of Amazon's own stores,
  on the desktop as well.

Each refusal says which setting and why, and the one for `base_url` does
not repeat the address, since it can name your employer. The desktop app
reads all of them as written, since its folder is yours alone.

Some shares also let the people who use them leave a link, a file or folder
name that leads somewhere else, and the container's user owns the server's
settings and every browser profile as well as the data folder. So on the
server a run stops before it writes anything when a folder it writes into
holds a link that leads outside the data folder, Logs, Diagnostics,
Backups, Manual Review, a year folder or the browser's download folder
among them. A folder that is a link to somewhere else inside the data
folder is allowed, and looked through the same way. Each folder is checked
again as it is made, the run's records, index, staged downloads, review
list and backups are never read or written through a link of any kind left
at their names, and the browser's downloads are never pointed at a folder
that leads out. The panel does the same with the files it writes there, and
never reads a file through a link to copy what it read into the share. The
browser's own saves and each app's log file are checked only where the run
starts, so a link left among them while a run goes on is not caught.

This does not make the share safe to open to everyone. A provider's other
settings, such as which documents it fetches and how they are named, are
read as written. So keep write access to the data folder to yourself.

If you previously edited a provider's scripts on the server, or kept a
custom Python environment inside a provider's folder, those changes stop
taking effect once you are on this version. Fold any real code change into
the project and rebuild the image, or raise it upstream, rather than editing
inside the data folder.

## Where your sign-ins live

Each provider account's signed-in browser profile is in the `profiles`
volume, kept out of the data folder, which is often a shared folder open to
other people. A run takes its profile from that volume whatever the
provider's `config.json` says, see "Where the provider code runs" above.
Anyone who can read that volume can act as you on those
sites, so back it up only somewhere as safe as your passwords, or not at
all, since you can always sign in again. Deleting the volume signs you out
everywhere at once.

## Reporting

The same way as for the desktop app, see [SECURITY.md](SECURITY.md).
