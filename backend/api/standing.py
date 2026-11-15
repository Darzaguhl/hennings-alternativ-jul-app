"""Internal volunteer "standing": a derived, never-volunteer-facing summary
of how someone has been to work with, used by admins to filter who gets
asked back (and, later, who gets the sign-up e-mail).

Nothing here is stored except an admin's manual override
(StandingOverride): the automatic part is recomputed from the underlying
ratings, check-ins and flags every time, so it can never go stale or drift
from the data it summarises.

Automatic rule (deliberately simple and explainable -- every "concern" comes
with the reasons that caused it):
  * concern -- at least one "Bekymring" rating, OR a no-show rate above
    NO_SHOW_THRESHOLD;
  * new     -- nothing to judge by yet (no ratings, no past attendance data);
  * good    -- everything else.

A no-show is a *signup* for a past day with no check-in from that person
that day (Assignments are only created at check-in, so signups are the
record of who said they would come). It only counts on days where *anyone*
was checked in at all -- a day where check-in simply wasn't used would
otherwise make every volunteer a no-show, so such days are reported as
"untracked" (and flagged in the attendance view) instead of guessed at.
Avmelding removes the signup, so a volunteer who withdrew in time is not a
no-show.
"""

from collections import defaultdict

from django.utils import timezone

from .models import Assignment, Commendation, EventCheckIn, Rating, ShiftSignup, StandingOverride

NO_SHOW_THRESHOLD = 0.25

STANDING_GOOD = "good"
STANDING_CONCERN = "concern"
STANDING_NEW = "new"

REASON_BEKYMRING = "bekymring"
REASON_NO_SHOWS = "no_shows"


STATUS_ATTENDED = "attended"
STATUS_NO_SHOW = "no_show"
STATUS_UPCOMING = "upcoming"
STATUS_UNTRACKED = "untracked"
STATUS_WALK_IN = "walk_in"


def attendance_records(event_ids, today=None):
    """One record per (event, date, person) who signed up for, was assigned
    to, or physically arrived on that day, with a status:

      attended  -- signed up (or assigned) and checked in
      walk_in   -- checked in without having signed up
      no_show   -- signed up, the day is past, others checked in but they didn't
      untracked -- signed up, day is past, but nobody was checked in at all
      upcoming  -- signed up for today or later and not (yet) checked in

    Shared by standing and the per-day attendance view so the two can never
    disagree about who was absent."""

    today = today or timezone.localdate()
    event_ids = list(event_ids)

    arrivals = defaultdict(set)  # (event, date) -> user ids checked in
    for event_id, user_id, date in EventCheckIn.objects.filter(event_id__in=event_ids).values_list(
        "event_id", "user_id", "date"
    ):
        arrivals[(event_id, date)].add(user_id)

    people = {}  # (event, date, user) -> {"oppgaver": set, "assigned": name|None}

    def entry(event_id, date, user_id):
        return people.setdefault((event_id, date, user_id), {"oppgaver": set(), "assigned": None})

    for event_id, user_id, date, skill in ShiftSignup.objects.filter(event_id__in=event_ids).values_list(
        "event_id", "user_id", "date", "oppgave_slot__skill__name"
    ):
        entry(event_id, date, user_id)["oppgaver"].add(skill)
    for event_id, user_id, date, skill in Assignment.objects.filter(event_id__in=event_ids).values_list(
        "event_id", "user_id", "date", "oppgave_slot__skill__name"
    ):
        entry(event_id, date, user_id)["assigned"] = skill
    for (event_id, date), user_ids in arrivals.items():
        for user_id in user_ids:
            entry(event_id, date, user_id)

    records = []
    for (event_id, date, user_id), info in people.items():
        signed_up = bool(info["oppgaver"] or info["assigned"])
        checked_in = user_id in arrivals.get((event_id, date), ())
        if checked_in:
            status = STATUS_ATTENDED if signed_up else STATUS_WALK_IN
        elif date >= today:
            status = STATUS_UPCOMING
        elif arrivals.get((event_id, date)):
            status = STATUS_NO_SHOW
        else:
            status = STATUS_UNTRACKED
        records.append(
            {
                "event": event_id,
                "date": date,
                "user": user_id,
                "status": status,
                "signed_up": signed_up,
                "checked_in": checked_in,
                "oppgaver": sorted(o for o in info["oppgaver"] if o),
                "assigned": info["assigned"],
            }
        )
    return records


def compute_standings(event_ids, today=None):
    """Standing rows for every user with a signup, Assignment, check-in or
    Rating in `event_ids`, keyed by user id. Only data from those events is
    counted, so an admin's view never reflects events they don't
    administer."""

    event_ids = list(event_ids)
    rows = {}

    def row(user_id):
        if user_id not in rows:
            rows[user_id] = {
                "user": user_id,
                "ratings": {level: 0 for level, _ in Rating.LEVEL_CHOICES},
                "commendations": 0,
                "attended": 0,
                "walk_ins": 0,
                "no_shows": 0,
                "upcoming": 0,
                "events": set(),
            }
        return rows[user_id]

    for rec in attendance_records(event_ids, today):
        r = row(rec["user"])
        r["events"].add(rec["event"])
        if rec["status"] == STATUS_ATTENDED:
            r["attended"] += 1
        elif rec["status"] == STATUS_WALK_IN:
            r["walk_ins"] += 1
        elif rec["status"] == STATUS_NO_SHOW:
            r["no_shows"] += 1
        elif rec["status"] == STATUS_UPCOMING:
            r["upcoming"] += 1

    for event_id, user_id, level in Rating.objects.filter(event_id__in=event_ids).values_list(
        "event_id", "user_id", "level"
    ):
        r = row(user_id)
        r["events"].add(event_id)
        r["ratings"][level] += 1

    for user_id in Commendation.objects.filter(event_id__in=event_ids).values_list("user_id", flat=True):
        row(user_id)["commendations"] += 1

    overrides = {o.user_id: o for o in StandingOverride.objects.filter(user_id__in=list(rows))}

    for user_id, r in rows.items():
        judged = r["attended"] + r["no_shows"]
        r["no_show_rate"] = round(r["no_shows"] / judged, 3) if judged else None
        r["events"] = len(r["events"])

        reasons = []
        if r["ratings"][Rating.LEVEL_BEKYMRING]:
            reasons.append(REASON_BEKYMRING)
        if r["no_show_rate"] is not None and r["no_show_rate"] > NO_SHOW_THRESHOLD:
            reasons.append(REASON_NO_SHOWS)

        if reasons:
            computed = STANDING_CONCERN
        elif judged == 0 and not r["walk_ins"] and not any(r["ratings"].values()):
            computed = STANDING_NEW
        else:
            computed = STANDING_GOOD

        override = overrides.get(user_id)
        r["reasons"] = reasons
        r["computed"] = computed
        r["override"] = override.level if override else None
        r["override_note"] = override.note if override else ""
        r["standing"] = override.level if override else computed

    return rows
