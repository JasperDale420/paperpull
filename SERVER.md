# PaperPull Server

PaperPull for an always-on machine at home, such as a NAS
or a home server, used from a browser on any computer on your home network.
It is meant to sit next to [Paperless-ngx](https://docs.paperless-ngx.com/),
so the documents PaperPull collects can go straight into it.

It is the same PaperPull with the same limits. It does not sign in for you.
You sign in to each provider yourself, on a browser screen the server shows
in your browser, and a bank that signs you out after a few minutes needs
you there again next time, as it would on the desktop. It does not run on a
schedule yet. You start each run from the panel.

It has privacy and security pages of its own, [PRIVACY-SERVER.md](PRIVACY-SERVER.md)
and [SECURITY-SERVER.md](SECURITY-SERVER.md), because unlike the desktop app
it listens on your network.

## Where to run it

On your home network, on a machine with Docker and an Intel or AMD
processor. Google publishes no Chrome for ARM Linux, so ARM machines such as
a Raspberry Pi cannot run it.

- **Memory.** About 120 MB with no sign-in window open. Each provider's
  window you leave open adds a few hundred MB, and Chrome grows the longer
  they stay open. In testing on a NAS, three providers' windows took about
  1.3 GB after a start and about 3 GB a day later. Plan on 4 GB free if you
  keep several open.
- **Disk.** About 1.3 GB for the image and 450 MB for Chrome, twice that
  once Chrome has updated, since the version before is kept. Plus your
  documents.

> **Not on a rented server.** A VPS or any server in a data center can run
> PaperPull Server, but providers treat data-center addresses as likely
> bots. Expect far more "Robot or human?" checks than at home, and some
> sites refusing to let you in at all. It is made for a machine at home.

## Get the image

`compose.yaml` names the image a PaperPull release publishes, by that
release's version, such as `ghcr.io/rheeloaded/paperpull-server:0.53.0`,
and Docker downloads it from GitHub the first time the project starts.
Before a release publishes it, the image is built from that release's
code, started the way this page describes and checked, and only that very
image goes out. Each image comes with a signed list of everything inside
it, see "Its parts are listed and checked" in
[SECURITY-SERVER.md](SECURITY-SERVER.md).

If Docker cannot download it, no release has published that version yet.
A release's own `PaperPull-Server-X.Y.Z.zip` always names an image that
exists, while the copy in this repository names the next version for a
short while before its release goes out. Write the newest version on the
releases page in the `image:` line then, or build the image yourself, from
a checkout of this repository, on any computer with Docker and Python.

```bash
python server/build.py --save paperpull-server.tar
```

That builds `paperpull-server:dev` from the files git tracks, nothing else
from the folder, and writes it to a file you can import on the NAS. Write
`paperpull-server:dev` as the `image:` in `compose.yaml` to use it.
Building it downloads its parts from Debian, the Python Package Index and
Google.

## Set it up

1. **Make a folder for it** on the server, and put `compose.yaml` and
   `seccomp-chrome.json` in it, from `PaperPull-Server-X.Y.Z.zip` on the
   [newest release](https://github.com/rheeloaded/paperpull/releases/latest),
   or from `server/` in this repository. The second
   file is the container's security profile, see "Chrome's sandbox" in
   [SECURITY-SERVER.md](SECURITY-SERVER.md).
2. **Fill in `compose.yaml`.** At least these.
   - `PUID` and `PGID`, the ids of the NAS user your documents should
     belong to. Your NAS shows them, or run `id` over SSH.
   - `TZ`, your time zone.
   - The data folder, `./data` unless you point it at a shared folder.
   - If you use Paperless, its consume folder, see [Paperless](#paperless).
3. **Start it.**
   ```bash
   docker compose up -d
   ```
4. **Choose a password.** Open `http://<the server's address>:8765` in a
   browser on your home network. The first visit asks you to choose a
   password, with a setup code the container prints in its log.
   ```bash
   docker logs paperpull
   ```
   The panel answers to the server's address and to a name that exists
   only on a home network, one without a dot such as `nas`, or one ending
   in `.local`, `.lan`, `.home`, `.home.arpa`, `.internal` or
   `.localdomain`. To reach it
   by any other name, a VPN's or a reverse proxy's, list that name in
   `PAPERPULL_HOSTS` in `compose.yaml`, more than one separated by commas.
5. **Add your providers** from the panel. Each one gets a folder in the data
   folder. Coming from the desktop app, bring them instead, see
   [Coming from the desktop app](#coming-from-the-desktop-app).
6. **Sign in.** Open a provider and press **Open sign-in window**. Its
   sign-in page opens in the sign-in window, the browser on the server's
   screen, docked beside the panel. The **Sign-in window** button at the
   top opens it at any time, and Full screen gives it a tab of its own.
   Sign in there as you would on a new computer, press **Done** to put the
   window aside, then **Try the newest few first** and **Get new
   documents**.

### Coming from the desktop app

Bring your providers and the record of what they already downloaded, so
the server does not download those documents again, or hand them to
Paperless a second time.

1. On the desktop, open the panel's **Settings** and press **Export
   history**. Your browser saves a file named like
   `PaperPull history 2026-10-11.ppz`.
2. On the server's welcome page, choose **Moving from another computer?
   Bring its providers and history**, then that file. The panel shows what
   it will do, and **Set up and import** does it. Once providers are set
   up, the same is under **Import a history file** in Settings.

Each provider and person in the file is set up with their settings, the
account holder's name, file name patterns, the start date and the like,
and every document they already downloaded is marked as downloaded.
Folders, browser profiles and debugging ports are the server's own. A
provider or person already set up keeps its settings and gains only the
history. No sign-in comes with the file, so sign in to each provider in the
sign-in window, as in step 6.

The file holds no password, sign-in or PDF. It does list document titles,
order numbers, totals and the account holders' names, so keep it as
private as your documents. The PDFs can stay where they are, since the
record is all the server needs to skip them.

### On a UGREEN NAS

The labels in the UGOS Docker app may differ a little from these.

1. Put `compose.yaml` and `seccomp-chrome.json` in a shared folder of their
   own, and fill in `compose.yaml` as above. UGOS shows your user's
   `PUID` and `PGID` above the compose editor.
2. Open **Project**, create one with that folder as its path, and paste or
   import `compose.yaml`. Leave **Run immediately after creation** ticked.
   The Docker app downloads the image itself.
3. The setup code is in the container's log. Open the `paperpull` container
   in the Docker app and look at its log.

With an image you built yourself, import `paperpull-server.tar` first. In
the Docker app, open **Image** and choose to add or import a local image.

### On Unraid

A tester ran it on Unraid 7.3.3 with the Compose Manager plugin, from
Community Applications.

1. Put `compose.yaml` and `seccomp-chrome.json` together in a folder of
   their own, such as `/mnt/user/appdata/paperpull`, and fill in
   `compose.yaml` as above.
2. Write the two paths in `compose.yaml` that start with `./` as absolute
   paths to that folder, `seccomp=/mnt/user/appdata/paperpull/seccomp-chrome.json`
   and `/mnt/user/appdata/paperpull/data:/data`.
3. In Compose Manager, add a new stack, paste `compose.yaml` into it and
   start it with Compose Up. The setup code is in the container's log.

Synology should work the same way and has not been tried yet.

## Paperless

Point a volume at Paperless's consume folder, and after every run the
documents that run saved are copied into it.

```yaml
    volumes:
      - /path/to/paperless/consume:/paperless
```

- **Copied, never moved.** PaperPull keeps its own files and its record of
  what it downloaded. Deleting them later never makes it download a
  document again. The desktop panel's Restore deleted files is not
  offered here, since every document it fetched would reach Paperless a
  second time.
- **Only what that run saved.** A run that saved nothing copies nothing,
  and a run that stopped early does not hand over the last run's documents
  a second time.
- **Never half a file.** Each copy is written under a hidden name and then
  renamed, and Paperless skips hidden names.
- **Renaming afterward stays in PaperPull.** Rename existing files and
  Review names rename PaperPull's own copies. A document already copied to
  Paperless keeps the title it has there, and is not copied again.
- **A folder per provider, if you like.** With `PAPERLESS_SUBFOLDERS: "1"`
  in the environment, each provider's documents go in a folder of that
  provider's name inside the consume folder. Paperless turns those folders
  into tags when `PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS` is on in Paperless.

What happened is in each run's output, a line such as "Copied 3 new
documents to Paperless".

## Signing in on the server

- **Pasting a password.** Open noVNC's side panel, the small tab on the left
  edge of the sign-in window, paste into its clipboard box, then press
  Ctrl+V in the page. Chrome on the server does not save passwords, so keep
  them in your own password manager.
- **"Robot or human?"** Some providers check, especially Walmart. Press and
  hold the button in the sign-in window yourself. PaperPull never answers
  one for you.
- **How long you stay signed in** depends on the provider. In testing, Best
  Buy stayed signed in for more than a day and through a restart, Verizon
  for more than a day but not through a restart, and T-Mobile and Navy
  Federal for less than half an hour. A restart of the server signs you out
  of any site that keeps its session only while the browser runs.

## Updating, backing up and removing

- **Updating.** Write the newer version in the `image:` line of
  `compose.yaml`, the number on the
  [releases page](https://github.com/rheeloaded/paperpull/releases), then
  run `docker compose pull` and `docker compose up -d` in the project's
  folder, or do the same from your NAS's Docker app. Everything you set up
  is in the volumes and stays. To go back, write the version you had and
  run the same two commands. Chrome brings itself up to date at every
  start. If you would rather take every release without editing the file,
  write `latest` in place of the version, which always names the newest
  release, and then the two commands alone update it.
- **Backing up.** Your documents are in the data folder. The `profiles`
  volume holds your live sign-ins, so a backup of it is as sensitive as your
  passwords, and you can always sign in again instead. The `settings` volume
  holds the password's hash and the panel's own settings.
- **A forgotten password.** This signs everyone out, and the next visit asks
  for a new password with a new setup code.
  ```bash
  docker exec paperpull python /opt/paperpull/server/reset_password.py
  ```
- **Removing it.** Delete the project, then its volumes, the `profiles`
  volume above all, since it holds live sign-ins. The data folder is yours
  and stays where it is.

## When something is wrong

Run the self-test. It checks Chrome, its sandbox, the screen, printing, the
password gate and what is reachable from the network, and says what failed.

```bash
docker exec paperpull python /opt/paperpull/server/selftest.py
```

- **Chrome does not start.** The security profile is probably not applied.
  Check that `seccomp-chrome.json` sits next to `compose.yaml` and that
  `security_opt` names it. Docker's default profile stops Chrome's sandbox,
  see [SECURITY-SERVER.md](SECURITY-SERVER.md).
- **The first start ends at once.** It needs to reach `dl.google.com` once
  to download Chrome. After that it starts without a connection and uses
  the Chrome it has.
- **The panel does not load.** Check that nothing else uses port 8765 on the
  server, or publish the panel on another port in `compose.yaml`.
- **The panel says it was asked for by a name that is not one of its
  names.** It answers only to the names in step 4 of the setup, so that a
  website cannot reach it through your browser by pointing a name of its
  own at your server. Add the name you use to `PAPERPULL_HOSTS` in
  `compose.yaml`, then recreate the container, with `docker compose up -d`
  or from your NAS's Docker app, as when updating.
- **A file to attach to an issue.** After a run that stops early, a support
  report or a recording, the run's page offers its file to download, where
  the desktop opens the folder. Each is built from PaperPull's fixed word
  list and holds no text from your account. A screenshot Diagnose takes
  stays on the server.
- **Documents set aside.** A run that would not file a document under a
  name it could be wrong about puts it in the account's Manual Review
  folder. The run's page offers what is there as one zip.

## Where the provider code runs

On PaperPull Server the provider code comes from the image. When you add a
provider, its folder in the data folder holds your configuration, your
rules file, the documents it downloads and its records, but not the code
that runs. The code, the Python interpreter and the modules a run imports
all come from the read-only application folder inside the container. So
editing a provider's script under the data folder, or putting a Python
virtual environment in its folder, changes nothing on the server, since the
server runs the image's copy. This keeps the code the server runs as you
out of reach of anyone who can write to a shared data folder. See
[SECURITY-SERVER.md](SECURITY-SERVER.md) for the trust boundary and why
write access to the data share should be yours alone.

## What is in the image

Debian's slim Python image, the virtual screen (Xvfb), a window manager
(Fluxbox), the screen sharing server (x11vnc), noVNC's web page for it, and
PaperPull. Their licenses are in the image under `/usr/share/doc`.
PaperPull is AGPL-3.0, and the container's security profile is Apache-2.0,
see [NOTICE.md](NOTICE.md). Chrome is not in the image. The container
downloads it from Google, under Google's own terms, the first time it
starts.
