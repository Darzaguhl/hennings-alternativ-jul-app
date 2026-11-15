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
