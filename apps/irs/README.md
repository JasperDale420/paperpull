# IRS transcripts and notices (IRS Online Account)

Downloads your tax transcripts (account, return, wage and income, and
record of account) from
your IRS Individual Online Account, as PDFs. This reads from a browser
where you sign in yourself, through ID.me with its one-time code, on the
port named in `config.json` (see `config.example.json`, port 9293).
Passwords, codes and ID.me are never handled by the downloader.

Read-only, delete-safe, part of [PaperPull](../../README.md).

## Validation status

**UNTESTED by anyone but its builder. Built from a read-only survey of one
signed-in account on 2026-10-13, then run in full on it the same day: 22
transcripts saved, every one a valid PDF, none for manual review.** That
run confirmed the transcript list, its four transcript types (account,
return, wage and income, record of account), that each tax period is a
month, and that each transcript's own address answers its PDF. Not yet
seen: any notice (the surveyed account had none). That is listed as
"SURVEY NEEDED" in `irs_site.py`, and a notices page that lists one stops
the run, rather than guess, with what it had saved kept.

Files are named `<date> IRS <Type> Transcript.pdf`, dated the end of the
transcript's tax period and filed under `Tax Documents`, for example
`2024-12-31 IRS Account Transcript.pdf`. When the IRS lists two
transcripts of one type for one period (an account transcript for each
of two account types), each name adds the form the list gives it, or its
MFT number when the forms are the same: `2025-12-31 IRS Account
Transcript MFT 29.pdf`.

Not yet read:

- Notices and letters. A notices page that says there are none is read as
  none. One that lists a notice stops the run until a notice has been seen
  and the app taught to read it. If yours does, please send a Diagnose file.
- W-2 or 1099 copies under information returns.
- A fresher copy of a transcript already saved. The IRS updates an account
  transcript as the account changes, and the app takes each one once; use
  `--redownload` for a fresh copy.

Never touched are payments, payment plans, bank accounts, IP PINs,
transcript requests by mail, authorizations for a representative, your
address, language or notice preferences, and sign-out. Your name and SSN
or ITIN (and its last four digits) are kept out of every log, record,
diagnostic file and file name.

## Help test it, no programming needed

1. Install PaperPull from the [latest release](https://github.com/rheeloaded/paperpull/releases/latest)
   and open the control panel.
2. Click **add a provider** and tick **IRS**. Pick it in the App list.
3. Click **Login**. Your own Edge or Chrome opens with a separate profile.
   Sign in to your IRS Online Account yourself, through ID.me, answer the
   code it sends, and leave the window open.
4. Click **more** under the buttons, then **Diagnose**. It opens the
   transcript and notices pages by their addresses and writes
   `Diagnostics\diagnose-documents.json` in the IRS folder. It downloads
   nothing, clicks nothing, takes no screenshot, and writes any word that
   is not on PaperPull's fixed list as its shape.
5. Click **Record**, in the same **more** menu. Go back to the browser window
   and click your way to one transcript the way you normally would, then
   come back here and click **Stop recording**. It writes
   `Diagnostics\recording.json`. It records nothing you type and reads no
   cookies, and it refuses to start before you are signed in. The whole
   walkthrough is [Testing a provider](../../docs/testing-a-provider.md).
6. Open each file in Notepad and look through it. It should hold page
   headings, the names of buttons and links, and the shape of each
   address, no values. If anything in it looks personal, above all
   anything like your SSN or name, delete that line.
7. Attach both files to a new
   [provider issue](https://github.com/rheeloaded/paperpull/issues/new/choose)
   titled "IRS Online Account", with a sentence about which transcript
   sections you see and whether your notices page lists any notices.

**If a run stops early, send the file it wrote.** Every failed run leaves a
`failure-*.json` in the `Diagnostics` folder and prints where it put it, with
no text from your account in it. Attach it to the issue the same way.

## Setup and use, for a checkout

1. Run `setup.command` (macOS) or `setup.bat` (Windows).
2. Copy `config.example.json` to `config.json` and set your local output folder.
3. Run `login.command` / `login.bat` and sign in yourself. Leave the window open.
4. Run `paperpull irs diagnose`, then `paperpull irs pilot` (one document).
5. Check the downloaded document before using `paperpull irs all`.

## How it works

Nothing is ever clicked. The app opens the transcript page by its address
and reads the same list of transcripts the page itself asks the IRS for,
each with its type, tax period and form. Each transcript is then fetched
by its own address, built from those fields, with the signed-in session,
no redirect followed, and kept only when it is a PDF. Before each one the
list is read again, and the transcript is fetched only when exactly one
item still matches. The page's own "EN" link, which opens the PDF in a new
tab, is never pressed. The notices page is opened and read as described
above. A list the app cannot read whole stops rather than being called
complete. If the account answers as signed out, or the tab is on ID.me,
the run stops and keeps what it saved. Sign in again and run Resume.

## Tests

```
python -m pytest tests -q
```

Run from this folder with PaperPull's bundled Python. They use invented
values only and need no browser.
