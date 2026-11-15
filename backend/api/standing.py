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

A no-show is a confirmed Assignment on a past date with no EventCheckIn for
that person that day -- but only on days where *anyone* was checked in at
all, so an event day where check-in simply wasn't used doesn't turn every
volunteer into a no-show. Avmelding removes the Assignment, so a volunteer
who withdrew in time is not a no-show.
"""

from collections import defaultdict

from django.utils import timezone

from .models import Assignment, Commendation, EventCheckIn, Rating, StandingOverride

NO_SHOW_THRESHOLD = 0.25

STANDING_GOOD = "good"
STANDING_CONCERN = "concern"
STANDING_NEW = "new"

REASON_BEKYMRING = "bekymring"
REASON_NO_SHOWS = "no_shows"


def compute_standings(event_ids, today=None):
    """Standing rows for every user with an Assignment or Rating in
    `event_ids`, keyed by user id. Only data from those events is counted,
    so an admin's view never reflects events they don't administer."""

    today = today or timezone.localdate()
    event_ids = list(event_ids)

    # (event, date) pairs on which at least one person was checked in --
    # the only days we can meaningfully call someone absent.
    arrivals_by_day = defaultdict(set)
    for event_id, user_id, date in EventCheckIn.objects.filter(event_id__in=event_ids).values_list(
        "event_id", "user_id", "date"
    ):
        arrivals_by_day[(event_id, date)].add(user_id)

    rows = {}

    def row(user_id):
        if user_id not in rows:
            rows[user_id] = {
                "user": user_id,
                "ratings": {level: 0 for level, _ in Rating.LEVEL_CHOICES},
                "commendations": 0,
                "attended": 0,
                "no_shows": 0,
                "upcoming": 0,
                "events": set(),
            }
        return rows[user_id]

    for event_id, user_id, date in Assignment.objects.filter(event_id__in=event_ids).values_list(
        "event_id", "user_id", "date"
    ):
        r = row(user_id)
        r["events"].add(event_id)
        if date >= today:
            r["upcoming"] += 1
        elif user_id in arrivals_by_day.get((event_id, date), ()):
            r["attended"] += 1
        elif arrivals_by_day.get((event_id, date)):
            r["no_shows"] += 1
        # else: a past day with no check-ins recorded at all -- unknowable.

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
        elif judged == 0 and not any(r["ratings"].values()):
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
