# Backlog

Things we've agreed to build or want to consider, across the backend, app,
admin dashboard and website. Keep entries short; move done items out (git
history is the record).

## Next up: leader feedback and volunteer standing

Fremhev (leaders flag people during the event) is done. The rest of the
design, as agreed:

- **Post-event rating.** Per person, per oppgave, per leader, so someone who
  worked two oppgaver can be rated by both leaders. Scale: Utmerket / God /
  Ok / Bekymring. "Bekymring" requires a note. Entered by leaders or admins.
  Who can read ratings is an event setting; default is admins see all and a
  leader sees only their own.
- **Standing (internal only).** Derived from ratings, no-show/attendance
  data we already track, and optionally the number of fremhevinger. Roughly:
  good standing = no "Bekymring", no-show rate under a threshold. Admins can
  override. Never shown to the volunteer.
- **Filtered e-mail to past volunteers**, asking them to sign up again.
  Filter by standing, years taken part, attended, X1 eligibility, oppgave
  skills. Show recipient count and a preview before sending; keep a send log;
  opt-out link in every mail. A person sends it; standing only filters.
- **Retention setting + delete-everything-about-a-person admin action.**
  Default is to keep indefinitely (the organisation's decision), but a
  volunteer's request to see or erase their data must be easy to honour.
  Mention this data in the signup privacy text.

## Sign in with other identity providers

Let people sign up and log in with **Google, Microsoft, Apple and Meta**, in
addition to e-mail + password. Open points to settle when picking this up:

- Where: website signup, the mobile app, the admin dashboard, or all three.
- Account linking: match on a *verified* e-mail so someone who already
  registered passwordless (the normal website path) lands on their existing
  account instead of a duplicate. Never auto-link on an unverified e-mail.
- Apple: if the iOS app offers any third-party login, App Store review
  expects Sign in with Apple (or an equivalent privacy-preserving option)
  alongside it, so Apple is effectively required for the app.
- Meta/Facebook needs an app review and business verification, and doesn't
  always return an e-mail; it's the heaviest of the four. Consider doing
  Google, Microsoft and Apple first.
- Backend: a library such as django-allauth or python-social-auth that ends
  in our existing SimpleJWT tokens; the app would use expo-auth-session.
- Admin/staff roles must stay tied to our own memberships, not to whatever
  identity provider someone used.

## CRM for donors (companies and individuals who give gifts)

A system to track who gives gifts and how that turned out against what was
promised. Per donor (company or individual): contact details and history
across years, the **expected gift and expected amount**, and **what was
actually received**, so expected vs received is visible per donor and per
year. Open points to settle first:

- "Amount": money (NOK), a quantity of items, or both? Gifts in Lager are
  items in categories, so received gifts should probably be able to link to
  or create the matching Lager entries rather than being typed twice.
- Who owns the relationship with each donor, and follow-up reminders
  (e.g. contact again before next Christmas).
- Individuals are personal data: same care as volunteer data (who can see
  it, retention, and a way to export or erase one person's record).
- Whether a donor can also be a volunteer, and whether they share a record.

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
(sender name and timestamp). A message stays until newer messages push it
out of the lane. (Our reading of "pushed out by others"; confirm.)

**Who sends and who sees.**
- Leaders and volunteers can send a message to a station.
- Senders do **not** see each other's messages. A sender only sees what they
  wrote themselves (their own sent list). The big screen shows everything.

**Admin moderation.** An admin sets a status on each message, e.g.
`waiting` / `active` / `cleared`. Our reading: `waiting` = sent but not shown
yet (held for approval or queued), `active` = on the screen, `cleared` =
taken down. Confirm whether new messages start as `waiting` (admin approves
each one) or go straight to `active` (admin only clears), since that decides
how much work the admin has during the event.

Open points:
- A "station" tied to oppgaver, and which station(s) a sender may post to
  (only their own, or any).
- The screen is a web page opened full-screen on a TV or tablet, with a
  secret link rather than a login, updated in real time (websocket/SSE) or by
  short polling.
- How many messages a lane keeps visible before the oldest is pushed out, and
  whether a very old one fades regardless.
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
