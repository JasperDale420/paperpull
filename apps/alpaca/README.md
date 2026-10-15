# Alpaca statements and trade confirmations (Live account)

Downloads the documents of an Alpaca brokerage account from its documents
page at `app.alpaca.markets/account/documents`, as PDFs: monthly account
statements, crypto account statements and trade confirmations. This reads
from a browser where you sign in yourself, on the port named in
`config.json` (see `config.example.json`, port 9292). Passwords and
verification codes are never handled by the downloader.

Read-only, delete-safe, part of [PaperPull](../../README.md).

## Live account only

One Alpaca login holds a Paper account and a Live account, and the
dashboard opens on whichever you chose last. Before you run this app,
choose your **Live** account in Alpaca's account switcher yourself. The app
never opens the switcher. If the switcher shows a Paper account, cannot be
read, or the page shows the Paper trading banner or leaves the documents
page (Alpaca sends a Paper account to its dashboard), the run stops before
pressing anything and says so.

## Setup and use

1. Run `setup.command` (macOS) or `setup.bat` (Windows).
2. Copy `config.example.json` to `config.json` and set your local output folder.
3. Run `login.command` / `login.bat`, sign in yourself, and choose your Live
   account. Leave the window open.
4. Run `paperpull alpaca diagnose`, then `paperpull alpaca pilot` (one
   brokerage statement).
5. Check the downloaded statement before using `paperpull alpaca all`.

A run stops on its own twenty minutes after it first opens the documents
page, and whenever Alpaca signs you out. Everything saved is kept, and
Resume carries on from there.

## How it works

The app opens the documents page by its address and listens for the list
Alpaca's own page asks for, the whole list in one answer. It checks that
list against the rows the page draws, ten to a page, pressing the page's
own Next until every row has been seen. A document is saved by opening the
page again, finding its one row by date and type, and pressing that row's
Download once. The page asks Alpaca for the document's link, and the
browser's download is kept only when it is one PDF that came from that
link, in Alpaca's document bucket.

Only two controls are ever pressed: a row's "Download" and the list's
"Next". Buy, sell, orders, transfers, deposits, withdrawals, funds, API
keys, Alpaca Connect, plans, the account switcher, profile, settings and
sign-out are never touched.

Files are named by date and kind: Brokerage Statement, Crypto Statement or
Trade Confirmation. Your account's name and number, and every document's
id, are kept out of every file name, log and diagnostic file. The account's
details, owners and billing answers that the page also loads are never read.

Skipped and counted: the JSON copy Alpaca keeps of each trade confirmation,
and the account application. A list that cannot be read whole stops rather
than being called complete.

## Validation status

Built from the account owner's survey of a signed-in Live account on
2026-10-12, then run on that account the same day: Discover listed 42
documents and kept 22, Pilot saved one brokerage statement, Run All saved
the other 21, and nothing was left in the download folder. Not yet seen or
known:

- Tax forms (1099s): none had been issued when the account was surveyed,
  so they are counted and skipped, never pressed.
- Two documents of one kind on one date, two trade confirmations on one
  day, say: a row shows only its date and kind, so those two are counted
  and left unsaved rather than guess which row is which, and the rest are
  saved. The run summary and the panel say how many were left.
- An account older than the list's window. The page asks for about three
  years of documents. When the list holds no account application, Discover
  stops unless `--start-date` or `default_start_date` is on or after the
  day the list starts, so older documents are never missed unnoticed. An
  application only counts when nothing listed is dated before it, and a
  list whose window starts after it ends, or ended before yesterday, is
  never called whole.
  Whether the page's Filters reach further back has not been seen.
- The exact markup of the table beyond what the survey saw.

## Tests

```
python -m pytest tests -q
```

Run from this folder with PaperPull's bundled Python. They use invented
values only and need no browser.
