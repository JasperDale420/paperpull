# Fidelity NetBenefits statement downloader

Makes and saves your own **quarterly statements** (or monthly ones) from
Fidelity NetBenefits, the workplace-plan site for a 401(k) and the like,
as PDFs, for your records. Read-only, delete-safe, and part of
[PaperPull](../../README.md).

Mapped and run against a real account on 2026-09-19. Thirty-nine quarterly
statements back to the plan's first quarter in 2016, every one valid, and
a second run downloaded nothing.

**Several employers and plans.** One owner's sign-in with two employers and
four plans was surveyed read-only on 2026-10-14, and the app was changed to
handle it (below). A live run on that sign-in is still pending, so treat the
several-plans path as untested against the real site until it is reported.

## How it reads the site

NetBenefits keeps no archive of statements. Its Statements page is a
generator, you pick a month, a quarter, year to date or a custom range and
it builds the statement on the spot as a web page, and its "Download or
Print This Statement" button is the browser's print. So there is no list to
read, and nothing on the page is clicked. This app makes the statement for
each completed period itself, through the same form request the page sends
(the site's transaction token is fetched and used inside the page, and
never enters this program), and renders the result to PDF the way the print
button would, with the site's own stylesheet and logo.

Discovery walks completed periods newest first, back to the oldest year the
page's own picker offers, and stops at the first period the site refuses
after a real statement, since a plan has one start date. A run scoped with
`--year` or `--start-date` makes only the years it wants. Each statement is
identified by its kind and period end date. The plan's name goes in the
filename, the plan number never does.

**A sign-in with several plans.** With more than one plan, and none chosen
yet, the Statements address shows a "Select Account" page listing the
plans by name, each with its own Statements address. The app reads that
list, never presses the page's Go button and never uses the employer
chooser, whose Continue changes a saved preference. The tab never goes to
a plan's own address. For each plan the app asks for that address from
inside the page without following a redirect, which chooses the plan on
the site, then reloads the Statements page and goes on only when it shows
that plan's Statements page. Then it makes that plan's statements as above
and files them under the plan's name, as in
`2026-06-30 NetBenefits Quarterly Statement - ABC 457(b) Plan.pdf`. A plan
whose address sends you elsewhere (on the surveyed sign-in, one plan's went
to the Fidelity retail sign-in) is left for you to open yourself, counted
as needing review, and never followed. If the session is gone when a later
plan is asked for, the run stops and keeps what it saved. Records are kept
by a digest of the plan, never its numbers, and the plan list is kept in
`plans.json` beside the progress file so a later run finds the plans again
once the site has gone back to remembering one plan. If it cannot, it says
so and asks you to sign out and in, then Discover, then Resume. A sign-in
with one plan works exactly as it did, with the same file names and
records.

`statement_period` in `config.json` is `quarterly` by default, the cadence
a plan issues on paper. Set it to `monthly` for one per month instead.

**One quirk, handled.** NetBenefits times a session out on page activity,
not on requests, so a run that only fetches gets signed out after a few
minutes, and the site also moves its own tab around on occasion. The app
reloads the Statements page every minute during a run, and if the tab is
moved mid-call it goes back and asks once more.

## Sign-in

Sign-in uses your own installed Edge or Chrome in a separate profile, on
`nb.fidelity.com`. You sign in yourself, answer your own two-factor prompt,
and leave the window open. The app never sees your password. If you also
use the `fidelity` app, that is a separate sign-in, since the two sites are
separate.

## Safety

This is a retirement account. The site can change contributions, move
money between investments, take a loan or a withdrawal, and change
beneficiaries. This app never activates a control that does any of those.
The only request it sends is the statement request the page itself sends,
and it refuses to send it from any page that is not on
`workplaceservices.fidelity.com`. An expired session stops the run rather
than reporting an empty success.

## Commands

```
paperpull netbenefits setup
paperpull netbenefits login        sign in yourself, leave the window open
paperpull netbenefits discover     find the periods the site can make, save nothing
paperpull netbenefits pilot        the newest five
paperpull netbenefits all          everything, delete-safe on a rerun
paperpull netbenefits verify       re-check every saved PDF
```

## Tests

```
python -m pytest tests -q
```
