# Spectrum Internet statement downloader

Spectrum Internet statements, about a year of them, from Billing → Statements at www.spectrum.net:
the current account's, and the older account's after an account transfer. Mobile statements are not
included: Spectrum's site has no PDF for them.

Sign in yourself with `login.bat` / `login.command` (the browser opens on port 9291 with its own
profile), answer any one-time passcode yourself, and keep the window open. Then Discover, Pilot
(one statement) and Run All.

How it works: the app opens Statements by its address and reads the list the page's own request
gets back. The newest statement's page is opened by its address. Every older statement is chosen
the way a person would, by pressing its own row on Statements, because a page opened by address for
an older statement prints the newest one instead. The app checks the page it moved to is that very
statement's, then presses "View Printable Statement" once. The page builds the PDF in the browser;
the app keeps it only when the PDF the page received was asked for that statement, the PDF it built
holds the same bytes, its text prints the statement's date, it is not the same file as a statement
already saved, and no tab opened and no download began. Anything else stops the run. Two statements
of one date (one for each account, after a transfer) are left for you to save by hand, since nothing
on the page tells their rows apart before a press.

Read-only: a statement's own row and "View Printable Statement" are the only controls the app will
press, each checked by name first (a row is refused when its words mention paying, Auto Pay,
paperless, equipment, settings, signing out and the like). It never pays, never
touches Auto Pay, paperless, equipment, service or settings, and never types anything. If Spectrum
signs you out or asks for a one-time passcode, the run stops, keeps what it saved, and asks you to
sign in (or answer the passcode) and press Resume.

Privacy: account numbers, the service address and Spectrum's statement ids (which contain the
account number) are kept out of every file, log and diagnostic. Files are named by date, for
example `2026-09-26 Spectrum Internet Statement.pdf`; a prior account's statement adds
"Prior Account", so two statements of one day get two names.

Status: built from the account owner's survey; live run pending.

Tests: `python -m pytest -q tests` with PaperPull's bundled Python.
