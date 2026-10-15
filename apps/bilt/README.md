# Bilt Card statements

Downloads the monthly statements of the Bilt Card 2.0, the card Cardless
services, from the Bilt wallet at `www.bilt.com/wallet`, as PDFs. This
reads from a browser where you sign in yourself, on the port named in
`config.json` (see `config.example.json`, port 9297). Passwords and
verification codes are never handled by the downloader.

Read-only, delete-safe, part of [PaperPull](../../README.md).

## Validation status

**Surveyed live, run pending.** Built from a read-only survey of one
signed-in account on 2026-10-15, which showed one Bilt Blue Card with eight
statements, October 06, 2026 back to March 06, 2026, and opened one of
them. Discover, Pilot and Run All have not been run yet. What the survey
saw:

- The wallet shows the card under a "View balance summary" button. That
  opens a sheet with "View statements", which opens a second sheet with one
  button per statement, "Download statement for October 06, 2026".
- Opening the list makes the page ask Bilt's API for the card's
  statements, and the app hears that answer. It never asks the API itself.
- A statement's button makes the page ask for that statement's details and
  then open a new tab at `statements.cardless.com` holding the PDF. No
  browser download begins.

What the first live run has to confirm:

- That the PDF can be read from the tab's own answer, or, if Chrome's PDF
  viewer keeps it, that asking the tab's exact address once more works.
- That the tab's address carries no query and its date is the pressed
  statement's own date.
- Whether the page's details answer names the tab's own address.
- That a statement keeps the same id from one load and sign-in to the
  next, and that the list is asked again on every load of the wallet.
- That the PDF prints its statement date in a way the app reads, and the
  card's last four where it prints a masked card number.
- The address of Bilt's sign-in page, which the survey never saw.
- How long Bilt keeps an idle session.

Statements of the earlier Bilt card issued by Wells Fargo, before February
2026, are not in the Bilt wallet. They are Wells Fargo's, and the
[`wellsfargo`](../wellsfargo) app reads Wells Fargo.

## Help test it, no programming needed

1. Install PaperPull from the [latest release](https://github.com/rheeloaded/paperpull/releases/latest)
   and open the control panel.
2. Click **add a provider** and tick **Bilt**. Pick it in the App list.
3. Click **Login**. Your own Edge or Chrome opens with a separate profile.
   Sign in to your Bilt account yourself, open **Wallet**, make sure the
   wallet shows your Bilt Card, and leave the window open.
4. Click **more** under the buttons, then **Diagnose**. It opens the wallet
   by its address and writes `Diagnostics\diagnose-documents.json` in the
   Bilt folder. It downloads nothing, clicks nothing, takes no screenshot,
   and writes any word that is not on PaperPull's fixed list as its shape.
5. Click **Pilot**. It saves the newest statement and stops. Open the PDF
   and check it is the statement of the date in its name.
6. Open the Diagnostics file in Notepad and look through it. It should hold
   counts and the shape of each address, no values. If anything in it looks
   personal, delete that line.
7. Attach it to a new
   [provider issue](https://github.com/rheeloaded/paperpull/issues/new/choose)
   titled "Bilt Card", with a sentence saying whether the pilot saved the
   right statement.

**If a run stops early, send the file it wrote.** Every failed run leaves a
`failure-*.json` in the `Diagnostics` folder and prints where it put it, with
no text from your account in it. Attach it to the issue the same way.

## Setup and use, for a checkout

1. Run `setup.command` (macOS) or `setup.bat` (Windows).
2. Copy `config.example.json` to `config.json` and set your local output folder.
3. Run `login.command` / `login.bat` and sign in yourself. Open Wallet and
   leave it showing your Bilt Card. Leave the window open.
4. Run `paperpull bilt diagnose`, then `paperpull bilt pilot` (one statement).
5. Check the downloaded statement before using `paperpull bilt all`.

## How it works

The app opens the wallet by its address and presses only three things:
the card's "View balance summary", then "View statements", then one
statement's own button. It never moves the wallet's card carousel, so a
wallet left showing another card, or showing more than one Bilt Card, stops
the list. The card's balance summary button, its card label and its
"Recent activity" link have to belong to the one card.

The list of statements is the answer the page itself gets from Bilt when
"View statements" opens, heard as it arrives. That answer has to be one
answer, for the card the wallet shows, with its count equal to its list and
no statement or date twice, and the sheet has to show exactly its dates,
one button each, read twice alike. Anything else stops the list rather
than being called whole.

Before each statement the wallet is loaded again and the list read again,
whole, and exactly one statement there must carry the recorded id and date.
Only then is that statement's button pressed. The statement is kept only
when exactly one tab opened, on `statements.cardless.com` at the PDF
address of that statement's date, the page's own details answer named that
statement, no browser download began, and no other tab opened. The tab is
closed before anything is saved. The PDF has to print the statement's date,
and every masked card number it prints has to end in the card's last four.
A statement whose button is turned off on the page is left for the next
run and the run goes on. Anything else unexpected stops the run.

If Bilt answers as signed out, the run stops and keeps what it saved. Sign
in again and run Resume.

Files are named `YYYY-MM-DD Bilt Card Statement.pdf`. Only when that name
is already taken does the file add six letters that tell it apart. No card
number or statement id is ever in a file name.

Never touched are Pay card, Pay with points, Make payment, Transfer a
balance, Lock card, Manage card, Authorized users, Add, Refer a friend,
Rewards, Neighborhood, the concierge chat, your profile and the wallet's
Previous and Next card. Your card id, statement ids, the card's last four,
your name, your balances and your recent activity are kept out of every
log, the index and every diagnostic file. The card id and statement ids
are kept only in the app's own discovery and progress records, to find the
statement again.

## Tests

```
python -m pytest tests -q
```

Run from this folder with PaperPull's bundled Python. They use invented
values only and need no browser.
