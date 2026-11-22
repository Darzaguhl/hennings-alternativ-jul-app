# Backlog

Things we've agreed to build or want to consider, across the backend, app,
admin dashboard and website. Keep entries short; move done items out (git
history is the record).

## Next up: filtered e-mail to past volunteers, and retention

Done: fremhev, post-event ratings, internal standing (with overrides),
per-day attendance and excusing absences. What is left of that design:

- **Filtered e-mail to past volunteers**, asking them to sign up again.
  Filter by standing, years taken part, attended, X1 eligibility, oppgave
  skills. Show recipient count and a preview before sending; keep a send log;
  opt-out link in every mail. A person sends it; standing only filters.
- **Retention setting + delete-everything-about-a-person admin action.**
  Default is to keep indefinitely (the organisation's decision), but a
  volunteer's request to see or erase their data must be easy to honour.
  Mention this data (ratings, standing, attendance) in the signup privacy text.

## Sign in with other identity providers (remaining)

**Done:** Google and Microsoft sign-in on the mobile app, the admin dashboard
and the website signup form (`/api/auth/social/`, see `api/social.py` and
`api/social_views.py`). A first sign-in with an unknown verified e-mail
creates a volunteer account (the dashboard never creates one). A verified
e-mail only auto-links to an account that has *no password*; accounts with a
password sign in with it once and connect a provider on the dashboard's
"Innlogging" page. Roles still come only from our own memberships.

Still open:

- **Apple.** If the iOS app offers any third-party login, App Store review
  expects Sign in with Apple alongside it, so this is effectively required
  before the iOS app ships with Google/Microsoft buttons. Needs an Apple
  Developer account (99 USD/year); same verification approach, plus Apple
  only reports the e-mail on the very first sign-in.
- **Meta/Facebook.** Needs an app review and business verification, and
  doesn't always return an e-mail. Heaviest of the four.
- **Connect/disconnect in the mobile app.** Only the dashboard has the
  "Innlogging" page. A volunteer who set a password via the e-mailed link
  and later taps Google in the app is told to use their password; give the
  app the same connect screen if that comes up.
- Microsoft work/school accounts only sign in when the tenant asserts domain
  ownership (`xms_edov`); personal Microsoft accounts and Google always work.
  Revisit if staff with work accounts are refused.

## CRM for donors: what is left

**Done (first version):** donors and their contacts (Givere), the yearly food
plan per item with who provides it, what was asked for / confirmed / received,
and how it went (Matplan), guest counts per day, suggested quantities for next
year with reasons, "plan next year from last year", and an admin-only Excel
import of the food workbook. Suggestions are rule-based on purpose: last
year's amount, adjusted for whether it ran out (early) or was left over, and
scaled by expected guests when counts exist.

Still open:

- **Kjøreliste (pickups and deliveries).** The workbook's driving list: per
  supplier a date, time window, address, phone, vehicle and done/not done, plus
  "not agreed yet". Belongs next to the plan lines.
- **Follow-up reminders.** `next_contact_on` exists and "Må følges opp" flags
  donors; nothing nudges anyone yet (e-mail or a digest).
- **Better predictions** as data accrues: per-guest rates once several years of
  guest counts exist, a trend over more than two years, and supplier
  reliability (confirmed vs delivered) feeding a risk flag on unconfirmed lines.
  The outcome (ran out / leftover) should be entered by hand after each event;
  the imported ones are only guesses from old comments.
- **Link received gifts to Lager** instead of typing them twice, and
  expected-vs-received per delivery with a "kastet" status.
- **Money gifts and individual donors.** Lines are food with quantities and unit
  prices; cash gifts and per-person giving history aren't modelled.
- **Personal data:** contacts are business contacts' details. A per-person
  export/erase action and a retention rule are still to do (same as for
  volunteers).
- Kitchen sheets in the workbook (menu, heating instructions, prep list) are
  operations, not CRM, and are not imported.

## Equipment inventory (setup gear), with minimum-level alerts

Inventory of the gear and supplies used to set up and run the event. This is
separate from Lager, which tracks donated gifts going in and out; this is
reusable equipment (and consumables) the organisation owns. Track what they
have, what was taken out for the event and **what came back**, so losses and
breakage are visible. 

- **Minimum level per item.** Set a minimum quantity; when stock falls below
  it, show an alert in a log (a "low stock" list an admin can read) instead
  of relying on someone noticing. Start with the log; e-mail or push later.
- Open points: storage location, check-out/in per team or station, and a
  condition/lost/broken status on return. Consumables vs reusable items may
  need slightly different handling.

## Live event dashboard and station screens

A live view used *during* the event: people and leaders send short messages
that show up on a big screen.

**The screen.** One screen shows all stations at once, each station (an
oppgave) as its own horizontal lane. A new message enters its station's lane
from the right side of the screen. Each message shows **who sent it and when**
(sender name and timestamp).

**Message age.** A configurable timer (default about 15 minutes) decides when
a message becomes "old". Fresh messages sit on the right; once a message is
old it moves to the left side of its lane and stacks there with the other
old ones.

**Who sends and who sees.**
- Leaders and volunteers can send a message to a station.
- Senders do **not** see each other's messages. A sender only sees what they
  wrote themselves (their own sent list). The big screen shows everything.

**Admin moderation.** An admin sets a status on each message, e.g.
`waiting` / `active` / `cleared`: `waiting` = sent but not shown yet,
`active` = on the screen, `cleared` = taken down. Approval is a setting:
either an admin moves each message from `waiting` to `active` by hand, or the
admin switches it to **auto**, so new messages go straight to `active` and the
admin only clears the bad ones.

Open points:
- A "station" tied to oppgaver, and which station(s) a sender may post to
  (only their own, or any).
- The screen is a web page opened full-screen on a TV or tablet, with a
  secret link rather than a login, updated in real time (websocket/SSE) or by
  short polling.
- The left-hand stack of old messages keeps **10** per lane; when an 11th
  arrives, the oldest is cleared. Make the 10 configurable too.
- The message-age timer, the approval mode (manual/auto) and the stack size
  are **event-level settings**, with sensible defaults (15 min, manual, 10).
  They are stored as settings, not hardcoded, so they can be changed per
  event (and later per station if that turns out to be needed).
- Overlaps with the push-notification idea below: decide whether station
  messages and phone notifications share one message model.

## Ideas from the meeting notes (unprioritised)

- Per-oppgave roles (editable, addable) with a time of day (e.g. 21:30
  nissetog), visible to the signed-up volunteers; a program for the day built
  from them. Team members visible to the signed-up.
- Oppgaveleder sees only their own signups per vakt, with feedback per group.
- Push notifications (new oppgave, check-in on oppgave).
- Avmelding på vakt in the app/website (the API's `withdraw` exists).
- Lager: expected vs actually-received per delivery, a "kastet" status,
  and a checklist for the people picking things up.
- Health, safety and age as staffing priorities (e.g. one person per
  emergency exit), and an AI recommendation of what suits a person. Sensitive
  data: settle who can see it, the legal basis and storage first.
- Hotel need at signup; commute/address analysis.
- Calendar invites (.ics) for a vakt that update on change.
- Event feedback form, summarised with AI.
- Twin vakt that swap on a schedule; early-departure note on a signup;
  hand-over for consecutive vakter (e.g. vakt 4 to 5).
