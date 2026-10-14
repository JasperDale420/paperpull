# Chase Business statements (business document center)

Downloads the monthly statements of the business accounts on a Chase
business profile from the document center at `secure.chase.com`, as PDFs,
up to the seven years Chase keeps online. This reads from a browser where
you sign in yourself, on the port named in `config.json` (see
`config.example.json`, port 9290). Passwords and verification codes are
never handled by the downloader.

Read-only, delete-safe, part of [PaperPull](../../README.md). For personal
Chase credit cards use the [`chase`](../chase) app instead.

## Setup and use

1. Run `setup.command` (macOS) or `setup.bat` (Windows).
2. Copy `config.example.json` to `config.json` and set your local output folder.
3. Run `login.command` / `login.bat` and sign in with your business login yourself. Leave the window open.
4. Run `paperpull chasebusiness diagnose`, then `paperpull chasebusiness pilot` (one statement).
5. Check the downloaded statement before using `paperpull chasebusiness all`.

Chase signs an idle session out after a few minutes. A run stops on its
own six minutes after its first click, and whenever Chase shows its
"Your session is about to end" dialog or signs you out. Everything saved
is kept, and Resume carries on from there. The six minutes are counted for
the whole run, Discover's clicks included, not for each account, so a
profile with many accounts and years may take a Resume or two to finish.
The app never presses "Stay signed in".

## How it works

The app opens the document center by its address and waits for its
account menu and its Statements section. For each business account in the
menu it chooses the account, opens Statements, and chooses each year the
"View:" picker offers, reading the list Chase's own page asks for and
checking it against the rows the page draws. A statement is saved by
pressing its row's "Opens menu to save document" and then "Save as PDF",
and the browser's download is kept only when it is one PDF from Chase.

Only six kinds of control are ever pressed: an account in the menu, the
Statements section, the year picker, a year in it, a statement's save
menu and its Save as PDF. Payments, transfers, wires, Zelle, ACH,
collections, deposits, approvals, users, paperless, offers, settings and
sign-out are never touched.

Statements are filed by date with the account's class, Business Checking,
Business Card or Business Debit, read from the product word in the menu.
An account's name and number are kept out of every file name, log and
diagnostic file. A menu entry with no such word is passed over only when
it reads as a cardholder, an initial and a surname such as "J. SMITH
(...1234)", and the menu also lists a Business Card. Any other entry
without one, a card whose name has no product word say, stops the list,
and the Diagnose file counts such entries.

Chase gives each account a new id every time you sign in, so a statement
is known by its account's class and last four instead. Two accounts of one
class with the same last four stop the list, since nothing would tell
them apart. The last four are never written to a file name or a log, only
a short digest of them, and that digest is no secret.

A list the app cannot read whole stops rather than being called complete.
The account menu is read twice and both readings must agree, and the
year picker's oldest year must say it ends the menu. A statement is saved
only when exactly one row matches it, and kept only when its PDF prints
the account's last four, since the rows do not name their account. A
statement whose save menu or Save as PDF Chase has turned off is left
for review, and the run goes on to the next one.

## Validation status

Built from the account owner's survey of a signed-in business profile on
2026-10-12 and run live on it on 2026-10-13: Discover found 10 statements,
Pilot and Run All saved them all, and every PDF opened. Not yet seen or
known:

- An empty year read before any year with statements, every account's
  current year in January say, is held until a year with statements in
  the same run shows what Chase answers on success. A run in which every
  year is empty stops rather than calling the list read.
- Seen live: Chase gives every account a new id at each sign-in, and the
  cardholder's entry shows a last four of its own. Records are keyed by
  class and last four for the first, and the cardholder is known by its
  name's shape for the second.
- Two statements of one account on one date stop the list on every run,
  since nothing on a row tells them apart. None has been seen.
- The business card's statements: the card showed no statements in the
  document center when it was surveyed, and whether they appear there is
  still an open question.
- Tax documents, notices, ATM receipts and check images are not collected.

## Help test it, no programming needed

1. Install PaperPull from the [latest release](https://github.com/rheeloaded/paperpull/releases/latest)
   and open the control panel.
2. Click **add a provider** and tick **Chase Business**. Pick it in the App list.
3. Click **Login**. Your own Edge or Chrome opens with a separate profile.
   Sign in with your business login yourself, answer any code it sends, and
   leave the window open.
4. Click **more** under the buttons, then **Diagnose**. It reads the
   document center and writes `Diagnostics\diagnose-documents.json` in the
   Chase Business folder. It downloads nothing, presses nothing, takes no
   screenshot, and keeps account names and numbers out of the file.
5. Open the file in Notepad and look through it. It should hold counts,
   section names and the shape of the page, no values. If anything in it
   looks personal, delete that line.
6. Click **Pilot**. It saves one statement. Say whether a PDF landed in
   `Statements\` and whether it opens.
7. Attach the Diagnose file to a new
   [issue](https://github.com/rheeloaded/paperpull/issues) with a sentence
   about what happened.

**If a run stops early, send the file it wrote.** Every failed run leaves a
`failure-*.json` in the `Diagnostics` folder and prints where it put it. It
says which step broke, as counts and states, with no text from your account
in it.

## Tests

```
python -m pytest tests -q
```

Run from this folder with PaperPull's bundled Python. They use invented
values only and need no browser.
