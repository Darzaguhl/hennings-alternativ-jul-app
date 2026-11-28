import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Skill(models.Model):
    """The oppgave catalogue -- role names like "Kokk" or "Vertskap". Which
    vakter a given oppgave actually applies to is no longer decided by a
    coarse phase tag here; it's simply whichever OppgaveSlot rows an admin
    has created for that (shift, skill) pair."""

    name = models.CharField(max_length=100, unique=True)
    is_flexible = models.BooleanField(
        default=False,
        help_text="\"Hva som helst\": the volunteer will help with anything. They wait in the pool at "
        "check-in and can be given any oppgave on that vakt. Can't be combined with specific oppgaver.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.CharField(max_length=255, blank=True)
    birthdate = models.DateField(null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    skills = models.ManyToManyField(Skill, blank=True, related_name="users")
    experience_notes = models.TextField(
        blank=True,
        help_text="Freeform background: previous experience, education, certifications.",
    )
    about = models.TextField(blank=True, help_text="Freeform 'tell us about yourself' from signup.")
    admin_notes = models.TextField(
        blank=True,
        help_text="Private notes for admins only, e.g. behavior from previous years. "
        "Never exposed to the volunteer themselves -- see UserAdminNoteSerializer.",
    )
    groups = models.ManyToManyField(
        Group,
        related_name="api_user_groups",
        blank=True,
        help_text="The groups this user belongs to.",
        verbose_name="groups",
    )
    user_permissions = models.ManyToManyField(
        Permission,
        related_name="api_user_permissions",
        blank=True,
        help_text="Specific permissions for this user.",
        verbose_name="user permissions",
    )

    class Meta:
        constraints = [
            # Case-insensitive uniqueness: AbstractUser.email has no
            # uniqueness at all by default, and every lookup in this
            # codebase already treats email as case-insensitive
            # (email__iexact) -- a plain unique=True wouldn't actually
            # match that and would still let "Foo@x.com" and "foo@x.com"
            # collide. EmailBackend.authenticate's User.objects.get(...)
            # throws an uncaught MultipleObjectsReturned (500) if two
            # accounts ever share an email, so this was a live crash-on-
            # login risk, not just a data-hygiene one.
            #
            # Excludes blank email: AbstractUser.email is blank=True, and
            # plenty of accounts legitimately have none (Django-admin-only
            # staff, various test fixtures) -- those aren't "the same
            # account" just because both happen to have no email.
            models.UniqueConstraint(
                Lower("email"),
                name="unique_user_email_ci",
                condition=~models.Q(email=""),
            ),
        ]

    def __str__(self) -> str:
        return self.username


class Event(models.Model):
    """One year's Alternativ Jul. Multiple Event rows can exist (next year's
    can be set up ahead of time while this year's is still live), but
    exactly one is ever "active" -- is_active -- which is what the public
    website/app show volunteers (see public_event) and what the app's
    single-event screen loads. Activating one deactivates all others (see
    EventViewSet.activate); a freshly created event starts inactive so
    setting one up ahead of time doesn't silently replace what's live.

    created_by is kept purely as a record of who originally set it up; it
    carries no ongoing permission weight (see is_owner) and losing that
    account must not take the event down with it, hence SET_NULL rather
    than CASCADE."""

    CHECKIN_MODE_PERSONAL_QR = "personal_qr"
    CHECKIN_MODE_EVENT_QR = "event_qr"
    RATING_VISIBILITY_OWN = "own"
    RATING_VISIBILITY_TEAM = "team"
    RATING_VISIBILITY_CHOICES = (
        (RATING_VISIBILITY_OWN, "Own — a leader sees only the ratings they gave themselves"),
        (RATING_VISIBILITY_TEAM, "Team — a leader also sees other leaders' ratings of people on their vakter"),
    )

    CHECKIN_MODE_CHOICES = (
        (CHECKIN_MODE_PERSONAL_QR, "Personal QR — an admin scans each volunteer's own badge"),
        (CHECKIN_MODE_EVENT_QR, "Event QR — volunteers scan one shared code themselves"),
    )

    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    date = models.DateTimeField(null=True, blank=True)
    code = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    is_active = models.BooleanField(
        default=False,
        help_text="The one event the public website/app show. Only one event is active at a time.",
    )
    signup_opens_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Volunteer registration is closed before this time. Blank = no lower bound.",
    )
    signup_closes_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Volunteer registration is closed after this time. Blank = no upper bound.",
    )
    checkin_mode = models.CharField(
        max_length=20,
        choices=CHECKIN_MODE_CHOICES,
        default=CHECKIN_MODE_EVENT_QR,
    )
    rating_visibility = models.CharField(
        max_length=10,
        choices=RATING_VISIBILITY_CHOICES,
        default=RATING_VISIBILITY_OWN,
        help_text="Who besides admins can read post-event ratings. See Rating.",
    )
    # Signup rules (see views.OppgaveSlotViewSet.signup and X1SignupViewSet),
    # editable per event on the dashboard's Vakter page. The public signup
    # page mirrors them to grey options out; these are what is enforced.
    max_oppgaver_per_vakt = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        default=2,
        help_text="Most oppgaver one volunteer may sign up for on one vakt. Blank = no limit. "
        "\"Hva som helst\" is chosen instead of oppgaver, never on top of them.",
    )
    max_consecutive_shifts = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        default=2,
        help_text="Most vakter in a row one volunteer may sign up for. Blank = no limit.",
    )
    x1_min_shifts = models.PositiveSmallIntegerField(default=3, help_text="Vaktleder (X1): vakter needed in total.")
    x1_min_core_shifts = models.PositiveSmallIntegerField(
        default=2, help_text="Vaktleder (X1): how many of those must be in the core range (0 = no such demand)."
    )
    x1_core_first = models.PositiveSmallIntegerField(default=5, help_text="First vakt number of the core range.")
    x1_core_last = models.PositiveSmallIntegerField(default=10, help_text="Last vakt number of the core range.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events_created",
    )

    def __str__(self) -> str:
        return f"{self.title} ({self.code})"

    @property
    def signups_open(self) -> bool:
        """Whether new volunteers can register right now. Both bounds are
        optional -- an unset opens_at/closes_at means no lower/upper bound,
        so a fresh event with neither set is open by default."""

        now = timezone.now()
        if self.signup_opens_at and now < self.signup_opens_at:
            return False
        if self.signup_closes_at and now > self.signup_closes_at:
            return False
        return True

    @property
    def year_label(self) -> str:
        """A stable per-event label for grouping oppgave history/volunteer
        participation by year -- falls back to the event's title when it
        has no date set. Shared by oppgave_history and MeSerializer's
        participation_years, so both group the same way."""

        if self.date:
            return str(self.date.year)
        return self.title

    def is_owner(self, user) -> bool:
        """Ownership is purely a Membership role -- no permanent fallback
        tied to whoever historically created the row. perform_create still
        grants the creator an owner Membership up front (see EventViewSet),
        but from then on it's a normal, revocable/transferable role like
        any other, and remove_membership refuses to remove the last one."""

        if not user.is_authenticated:
            return False
        return self.memberships.filter(user=user, role=Membership.ROLE_OWNER).exists()

    def is_admin(self, user) -> bool:
        if not user.is_authenticated:
            return False
        if self.is_owner(user):
            return True
        return self.memberships.filter(user=user, role=Membership.ROLE_ADMIN).exists()

    def is_checkin_staff(self, user) -> bool:
        if self.is_admin(user):
            return True
        return self.memberships.filter(user=user, role=Membership.ROLE_CHECKIN_STAFF).exists()


class Membership(models.Model):
    """Event-wide admin roles. The owner can manage everything, including
    granting/revoking admin access -- admins can manage everything
    else (vakter, check-in staff, pool/assignment) but can't create or
    remove other admins/owners, so no single compromised or careless
    admin account can lock out the rest. Oppgave-level leadership is
    scoped per-Shift instead (see Shift.leaders), since a leader's authority
    doesn't extend to the whole event.
    """

    ROLE_OWNER = "owner"
    ROLE_ADMIN = "admin"
    ROLE_CHECKIN_STAFF = "checkin_staff"
    ROLE_CHOICES = (
        (ROLE_OWNER, "Owner"),
        (ROLE_ADMIN, "Admin"),
        (ROLE_CHECKIN_STAFF, "Check-in staff"),
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user", "role")

    def __str__(self) -> str:
        return f"{self.user} — {self.role} @ {self.event}"


def generate_invite_token() -> str:
    return uuid.uuid4().hex


def default_invite_expiry():
    return timezone.now() + timezone.timedelta(days=7)


class Invite(models.Model):
    """An admin/staff invite: owner or admin invites someone by email to a
    role on an event. Distinct from Membership -- an Invite is a pending
    offer with its own token/expiry; accepting one (via /api/invites/accept/)
    creates (or reuses) the User and the actual Membership row.

    Unlike volunteer self-registration, invited roles need a real password
    so the person can log in to the admin dashboard -- accept_invite sets
    one, whether the account is new or already existed as a passwordless
    volunteer signup.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="invites")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=Membership.ROLE_CHOICES)
    token = models.CharField(max_length=64, unique=True, default=generate_invite_token, editable=False)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invites_sent"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_invite_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Invite({self.email} → {self.role} @ {self.event})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def is_usable(self) -> bool:
        return self.accepted_at is None and not self.is_expired


def generate_password_setup_token() -> str:
    return uuid.uuid4().hex


def default_password_setup_expiry():
    return timezone.now() + timezone.timedelta(days=90)


class PasswordSetupToken(models.Model):
    """Lets a volunteer set or reset their password by email -- covers both
    a volunteer who registered without one (the normal path, see
    RegisterSerializer: the public website deliberately doesn't collect
    one) and someone who had one and forgot it. Created automatically
    right after a passwordless registration, and on demand via
    views.request_password_setup (the app's "first time / forgot
    password" flow); single-use like Invite, and long-lived since
    volunteers often sign up months before the event and may not act on
    the email right away."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="password_setup_tokens"
    )
    token = models.CharField(max_length=64, unique=True, default=generate_password_setup_token, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_password_setup_expiry)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"PasswordSetupToken({self.user.email})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and not self.is_expired


def generate_qr_payload() -> str:
    """Return a stable, unique token to embed in a user's QR code."""

    return uuid.uuid4().hex


class QRCode(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qr_code",
    )
    data = models.CharField(max_length=64, unique=True, default=generate_qr_payload, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.data


class Shift(models.Model):
    """A vakt: a numbered time slot within an Event (e.g. "Vakt 5", 24 Dec
    13:00-23:00). Distinct from Skill ("oppgave") -- a single vakt has many
    volunteers doing many different oppgaver at once; a vakt is when, an
    oppgave is what. See OppgaveSlot for how the two combine."""

    CRITICALITY_NORMAL = "normal"
    CRITICALITY_CRITICAL = "critical"
    CRITICALITY_CHOICES = (
        (CRITICALITY_NORMAL, "Normal"),
        (CRITICALITY_CRITICAL, "Critical — requires relevant experience"),
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="shifts")
    title = models.CharField(max_length=100)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text="Maximum volunteers. Blank = no limit.")
    min_capacity = models.PositiveIntegerField(
        null=True, blank=True, help_text="Minimum volunteers needed. Informational only — used for understaffing alerts/metrics, not enforced at signup."
    )
    criticality = models.CharField(
        max_length=10,
        choices=CRITICALITY_CHOICES,
        default=CRITICALITY_NORMAL,
    )
    vakt_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="The vakt's number, if it's one of the main numbered vakter (e.g. 5 for "
        "'Vakt 5') -- used for the X1 vaktleder eligibility rule (>=2 of vakt 5-10). "
        "Blank for non-numbered vakter (lettered prep/teardown ones, etc.).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shifts_created",
    )
    leaders = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="shifts_led",
        blank=True,
        help_text="Oppgave leaders: can edit this shift and assign its pool candidates.",
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ShiftSignup",
        related_name="shifts_signed_up",
        blank=True,
    )

    class Meta:
        ordering = ["date", "start_time"]

    def __str__(self) -> str:
        return f"{self.event.title} — {self.title} ({self.date})"

    @property
    def is_critical(self) -> bool:
        return self.criticality == self.CRITICALITY_CRITICAL

    @property
    def signup_count(self) -> int:
        return self.signups.count()

    @property
    def assigned_count(self) -> int:
        return self.assignments.count()

    @property
    def is_full(self) -> bool:
        return self.capacity is not None and self.signup_count >= self.capacity

    @property
    def is_understaffed(self) -> bool:
        return self.min_capacity is not None and self.assigned_count < self.min_capacity

    def is_led_by(self, user) -> bool:
        return user.is_authenticated and self.leaders.filter(pk=user.pk).exists()


class OppgaveSlot(models.Model):
    """A specific oppgave (Skill) needed during a specific vakt (Shift),
    with its own capacity -- the actual unit a volunteer signs up for and
    an admin assigns them to.

    Replaces the old two-independent-things model: a global Skill interest
    tag on the user with no shift reference, and a Shift signup with no
    skill reference. Also replaces the old Shift.phase / Skill.allowed_in_*
    compatibility mechanism -- compatibility is now just "does a slot exist
    for this (shift, skill) pair", curated by an admin (see
    OppgaveSlotViewSet) rather than a coarse phase match."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="oppgave_slots")
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="oppgave_slots")
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="oppgave_slots")
    capacity = models.PositiveIntegerField(
        null=True, blank=True, help_text="Maximum volunteers for this oppgave on this vakt. Blank = no limit."
    )

    class Meta:
        unique_together = ("shift", "skill")
        ordering = ["shift__date", "shift__start_time", "skill__name"]

    def save(self, *args, **kwargs):
        self.event = self.shift.event
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.skill} — {self.shift}"

    @property
    def signup_count(self) -> int:
        return self.signups.count()

    @property
    def assigned_count(self) -> int:
        return self.assignments.count()

    @property
    def is_full(self) -> bool:
        # Mirrors Shift.is_full's existing semantics -- checks interest
        # (signups), not confirmed placements -- so it closes further
        # signups once enough interest exists. The separate over-capacity
        # check in EventViewSet.assign is what actually stops over-placing
        # people once assignment happens.
        return self.capacity is not None and self.signup_count >= self.capacity


class ShiftSignup(models.Model):
    """A user's expressed interest in doing a given oppgave on its day.

    A user may shortlist several oppgaver for the same day (including more
    than one for the same vakt, e.g. willing to do either Kjøkken or
    Vertskap on Vakt 5) — this is a candidate list, not a commitment.
    Exactly one gets turned into an Assignment, at check-in time. shift/
    event/date are denormalized from oppgave_slot purely to make querying
    "this user's candidates for today" cheap.

    has_relevant_experience / experience_notes are only meaningful when the
    shift is critical (see Shift.is_critical) — the signup flow should ask
    for them in that case.
    """

    oppgave_slot = models.ForeignKey(OppgaveSlot, on_delete=models.CASCADE, related_name="signups")
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="signups")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shift_signups")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="shift_signups")
    date = models.DateField()
    has_relevant_experience = models.BooleanField(null=True, blank=True)
    experience_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("oppgave_slot", "user")

    def save(self, *args, **kwargs):
        self.shift = self.oppgave_slot.shift
        self.event = self.shift.event
        self.date = self.shift.date
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} → {self.oppgave_slot}"


class ShiftConflict(models.Model):
    """An admin-declared pair of vakter that can't both be signed up for.

    Not a computable rule -- an earlier version of this validation
    rejected any two vakter whose times overlapped, which turned out
    wrong: the real event schedule has several overlapping pairs (e.g.
    a day vakt ending as the night vakt starts) that are fine to combine,
    and no overlap-duration threshold separates those from the pairs
    that genuinely aren't fine (e.g. an all-night vakt immediately
    followed by a full day vakt, with zero rest between). That's a
    judgment call about workload, not geometry, so it's data the
    organizers curate per event rather than logic computed from
    start/end times -- see ShiftViewSet.signup."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="shift_conflicts")
    shift_a = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="conflicts_as_a")
    shift_b = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="conflicts_as_b")

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.shift_a} ↔ {self.shift_b}"


class X1Signup(models.Model):
    """A volunteer's opt-in to serve as vaktleder (X1) for an event -- not
    a real time-bound vakt (no single date/time of its own; see
    OppgaveSlot for those), but a leadership role layered on top of a
    volunteer's other signups for the same event.

    Eligibility ("Kombinasjon med påmelding på minst tre andre vakter, og
    derav minst to av vaktene fra 5-10") is validated once, at creation
    time, in X1SignupViewSet.perform_create -- not re-checked afterward if
    the volunteer later withdraws an underlying vakt signup."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="x1_signups")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="x1_signups")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user")

    def __str__(self) -> str:
        return f"{self.user} — X1 @ {self.event}"


class InventoryCategory(models.Model):
    """The gift/donation catalogue -- category names like "Vinterjakker" or
    "Leker". Same shape and role as Skill (the oppgave catalogue): a
    small, admin-curated list, not scoped to a single event, so it doesn't
    need re-creating year to year."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Inventory categories"

    def __str__(self) -> str:
        return self.name


class InventoryItem(models.Model):
    """One logged gift/donation movement -- either DIRECTION_IN (a team
    inventorying what's arrived) or DIRECTION_OUT (something given away
    during the night, e.g. via a rally/game). Net stock per category is
    in-total minus out-total, computed on read (see
    InventoryItemViewSet.summary) rather than tracked as a running counter,
    so a corrected/deleted entry can never leave the total out of sync.

    quantity is always human-confirmed -- even when an AI suggestion seeds
    the form (see api.vision.identify_item), counting an irregular pile of
    donated goods from one photo is not reliable, so nothing here is ever
    saved from that suggestion without a person having seen and could-edit
    the number first.

    photo is the volunteer's actual photo of the item(s), kept (not just
    used transiently for identification) so an admin reviewing the log can
    see what was actually logged -- see Gotchas in CLAUDE.md for why this
    needs real object storage configured, not Render's own disk."""

    DIRECTION_IN = "in"
    DIRECTION_OUT = "out"
    DIRECTION_CHOICES = (
        (DIRECTION_IN, "Inn (mottatt)"),
        (DIRECTION_OUT, "Ut (gitt bort)"),
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="inventory_items")
    # PROTECT, not CASCADE (unlike Skill -> OppgaveSlot): this is a ledger
    # of real donations received/given away, so deleting a category should
    # never silently take a whole history of logged items with it.
    category = models.ForeignKey(InventoryCategory, on_delete=models.PROTECT, related_name="items")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES, default=DIRECTION_IN)
    photo = models.ImageField(upload_to="inventory/%Y/%m/", blank=True, null=True)
    # SET_NULL, not CASCADE: losing a volunteer's account shouldn't delete
    # the inventory records they logged -- same reasoning as other
    # "who did this" fields elsewhere (e.g. Shift.created_by).
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="inventory_items_logged"
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        sign = "+" if self.direction == self.DIRECTION_IN else "-"
        return f"{sign}{self.quantity} {self.category} @ {self.event}"


class EventCheckIn(models.Model):
    """Marks that a user has physically arrived at the event today.

    Deliberately decoupled from any specific oppgave: arriving puts a
    volunteer "in the pool" for today. It is only paired with an Assignment
    once one has been resolved (automatically or by an admin).
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="arrivals")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_arrivals")
    date = models.DateField()
    checked_in_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user", "date")
        ordering = ["-checked_in_at"]

    def __str__(self) -> str:
        return f"{self.user} arrived @ {self.event} ({self.date})"


class Assignment(models.Model):
    """The final, admin-confirmed placement of a user onto one oppgave slot
    for the day — as opposed to ShiftSignup, which is just a candidate.

    event/date are denormalized from oppgave_slot's shift so the DB
    enforces at most one confirmed placement per user per event per day,
    even though a user may have shortlisted (ShiftSignup'd) several
    oppgaver that day.
    """

    oppgave_slot = models.ForeignKey(OppgaveSlot, on_delete=models.CASCADE, related_name="assignments")
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assignments")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="assignments")
    date = models.DateField()
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="assignments_confirmed",
    )
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user", "date")
        ordering = ["-confirmed_at"]

    def save(self, *args, **kwargs):
        self.shift = self.oppgave_slot.shift
        self.event = self.shift.event
        self.date = self.shift.date
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} assigned to {self.oppgave_slot}"


class Commendation(models.Model):
    """A leader's "this person could take on more responsibility" flag,
    raised during the event ("Fremhev"). Positive-only by design -- concerns
    go through the post-event rating, not here -- and strictly internal:
    the flagged volunteer must never be able to read it (see
    CommendationViewSet, which scopes the queryset to admins and to the
    flag's own author, and never exposes it on any user-facing serializer).

    Tied to the Assignment the person actually worked (not just any
    signup), so a leader can only flag someone who was confirmed onto a
    shift they lead. shift/oppgave_slot are copied from that assignment
    rather than FK'd to it, so the flag survives the assignment being
    changed or removed later.

    Not unique per (event, user): the same person can be flagged by
    several leaders, or on several shifts -- repeated flags are the signal.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="commendations")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="commendations")
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="commendations")
    oppgave_slot = models.ForeignKey(
        OppgaveSlot, on_delete=models.SET_NULL, null=True, blank=True, related_name="commendations"
    )
    flagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="commendations_given",
    )
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} fremhevet @ {self.shift}"


class Rating(models.Model):
    """A leader's (or admin's) post-event assessment of one volunteer on one
    vakt: Utmerket / God / Ok / Bekymring. Strictly internal -- the rated
    volunteer must never be able to read it (see RatingViewSet, which scopes
    the queryset to admins, the rating's author and, if the event allows it,
    the other leaders of that vakt; it is never on a user-facing serializer).

    One rating per (shift, user, rated_by): a volunteer who worked two vakter
    can be rated by each vakt's leader, and a vakt with two leaders gets two
    independent opinions. A leader revisits their rating by editing it.

    "Bekymring" must carry a note saying what happened -- a bare concern
    flag is neither fair to the volunteer nor useful to whoever reads it
    later. Like Commendation, shift/oppgave_slot are copied from the
    Assignment the person actually worked, not FK'd to it, so the rating
    survives the assignment being changed or removed afterwards.
    """

    LEVEL_UTMERKET = "utmerket"
    LEVEL_GOD = "god"
    LEVEL_OK = "ok"
    LEVEL_BEKYMRING = "bekymring"
    LEVEL_CHOICES = (
        (LEVEL_UTMERKET, "Utmerket"),
        (LEVEL_GOD, "God"),
        (LEVEL_OK, "Ok"),
        (LEVEL_BEKYMRING, "Bekymring"),
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="ratings")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ratings")
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="ratings")
    oppgave_slot = models.ForeignKey(
        OppgaveSlot, on_delete=models.SET_NULL, null=True, blank=True, related_name="ratings"
    )
    rated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ratings_given",
    )
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["shift", "user", "rated_by"], name="unique_rating_per_leader_per_shift"),
        ]

    def __str__(self) -> str:
        return f"{self.user}: {self.level} @ {self.shift}"


class StandingOverride(models.Model):
    """An admin's manual decision about someone's standing, which wins over
    the automatic one (see api/standing.py) -- e.g. clearing a single bad
    night's "Bekymring", or flagging a concern the ratings never captured.
    Internal only, like everything about standing: the volunteer never
    sees it. A required note says why, so the next admin can tell.

    Per user, not per event, because standing spans years; the data it
    overrides is always read through the requesting admin's own events
    (see StandingViewSet), so it never exposes another event's records.
    """

    LEVEL_GOOD = "good"
    LEVEL_CONCERN = "concern"
    LEVEL_CHOICES = (
        (LEVEL_GOOD, "Good"),
        (LEVEL_CONCERN, "Concern"),
    )

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="standing_override")
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES)
    note = models.CharField(max_length=300)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="standing_overrides_set"
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user}: {self.level}"


class AbsenceExcuse(models.Model):
    """An admin's decision that one specific absence shouldn't count against
    someone -- a late sick call, a family emergency -- recorded per person
    per day. Avmelding in time already removes the signup, so this is for
    the cases that came too late (or never made it into the system).

    An excused absence is neither a no-show nor attendance: it simply drops
    out of the volunteer's no-show rate. Internal only, like all standing
    data. Only meaningful for a day the person actually signed up for and
    missed; attendance_excuse refuses to excuse anything else.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="absence_excuses")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="absence_excuses")
    date = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    excused_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="absence_excuses_given"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "user", "date"], name="unique_absence_excuse_per_day"),
        ]

    def __str__(self) -> str:
        return f"{self.user} excused @ {self.event} ({self.date})"


class SocialIdentity(models.Model):
    """Links a User to an account at an external identity provider
    ("Sign in with Google/Microsoft"). The provider only ever proves *who
    someone is*; it never grants anything here -- admin/leader access stays
    entirely in our own Memberships and shift leaders, so linking a Google
    account to a staff user doesn't change what that user can do.

    Looked up by (provider, subject), not e-mail: a person can change their
    address at the provider without losing access, and nobody can claim an
    identity by registering an address. One identity per provider per user.
    """

    PROVIDER_CHOICES = (("google", "Google"), ("microsoft", "Microsoft"))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="social_identities")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    subject = models.CharField(max_length=255)
    email = models.EmailField(blank=True, help_text="The address the provider reported when linked; informational.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="unique_social_identity_subject"),
            models.UniqueConstraint(fields=["user", "provider"], name="unique_social_identity_per_provider"),
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.provider}"


# ---------------------------------------------------------------------------
# CRM: donors/suppliers, the yearly food plan, and what happened with it.
#
# Organisation-wide, not per event: the relationships and the plan span
# years (and events), and only admins (of any event) can see any of it --
# contact details of business contacts, prices and gift values included.
# ---------------------------------------------------------------------------


class Donor(models.Model):
    """A company or person we get food/drink from, as a sponsor (gift), a
    supplier (we pay) or both. The relationship outlives any one Christmas:
    year-specific facts live on PlanLine, who to talk to on DonorContact."""

    KIND_COMPANY = "company"
    KIND_INDIVIDUAL = "individual"
    KIND_OTHER = "other"
    KIND_CHOICES = ((KIND_COMPANY, "Company"), (KIND_INDIVIDUAL, "Individual"), (KIND_OTHER, "Other"))

    REL_SPONSOR = "sponsor"
    REL_SUPPLIER = "supplier"
    REL_BOTH = "both"
    REL_UNKNOWN = "unknown"
    RELATIONSHIP_CHOICES = (
        (REL_SPONSOR, "Sponsor -- gives for free"),
        (REL_SUPPLIER, "Supplier -- we buy"),
        (REL_BOTH, "Sponsor and supplier"),
        (REL_UNKNOWN, "Unknown"),
    )

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=KIND_COMPANY)
    relationship = models.CharField(max_length=10, choices=RELATIONSHIP_CHOICES, default=REL_UNKNOWN)
    owner_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Who on our side handles this relationship. Free text: they don't all have accounts.",
    )
    notes = models.TextField(blank=True)
    last_contacted_on = models.DateField(null=True, blank=True)
    next_contact_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # Case-insensitive, so "BAMA" and "Bama" can't become two donors.
            models.UniqueConstraint(models.functions.Lower("name"), name="unique_donor_name_ci"),
        ]

    def __str__(self) -> str:
        return self.name


class DonorContact(models.Model):
    """A person at a donor. Business contact details are personal data, so
    they're admin-only and deleted with the donor."""

    donor = models.ForeignKey(Donor, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    is_primary = models.BooleanField(default=False)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-is_primary", "name", "email"]

    def __str__(self) -> str:
        return f"{self.name or self.email} @ {self.donor}"


class FoodItem(models.Model):
    """What we plan for, independent of year and supplier ("Bacon",
    "Kransekake") -- the thing forecasts follow across years."""

    name = models.CharField(max_length=150)
    category = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["category", "name"]
        constraints = [models.UniqueConstraint(models.functions.Lower("name"), name="unique_food_item_name_ci")]

    def __str__(self) -> str:
        return self.name


class PlanLine(models.Model):
    """One item for one Christmas, from one donor: what we wanted, what was
    confirmed, what actually arrived, and how it went.

    Quantities are a number plus a unit; the sheets also say things like
    "7 kasser av hver" or "100kg Costa finmalt", so anything the number+unit
    doesn't capture is kept in quantity_note rather than lost. A forecast only
    compares quantities in the same unit.

    The outcome is what makes next year's suggestion possible: without a
    record of "ran out early" or "200 kg left over" there is nothing to learn
    from. It is entered by hand; imported lines carry a *guess* from the old
    comments (outcome_source) that someone should confirm.
    """

    STATUS_PLANNED = "planned"
    STATUS_ASKED = "asked"
    STATUS_CONFIRMED = "confirmed"
    STATUS_DELIVERED = "delivered"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_PLANNED, "Planned"),
        (STATUS_ASKED, "Asked"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    ACQ_GIFT = "gift"
    ACQ_PURCHASE = "purchase"
    ACQ_MIXED = "mixed"
    ACQ_UNKNOWN = "unknown"
    ACQUISITION_CHOICES = (
        (ACQ_GIFT, "Gift"),
        (ACQ_PURCHASE, "Purchase"),
        (ACQ_MIXED, "Gift and purchase"),
        (ACQ_UNKNOWN, "Unknown"),
    )

    MODE_PICKUP = "pickup"
    MODE_DELIVERY = "delivery"
    MODE_UNKNOWN = "unknown"
    MODE_CHOICES = ((MODE_PICKUP, "We pick up"), (MODE_DELIVERY, "They deliver"), (MODE_UNKNOWN, "Unknown"))

    OUTCOME_RAN_OUT_EARLY = "ran_out_early"
    OUTCOME_RAN_OUT = "ran_out"
    OUTCOME_RIGHT = "right"
    OUTCOME_LEFTOVER = "leftover"
    OUTCOME_CHOICES = (
        (OUTCOME_RAN_OUT_EARLY, "Ran out early"),
        (OUTCOME_RAN_OUT, "Ran out"),
        (OUTCOME_RIGHT, "About right"),
        (OUTCOME_LEFTOVER, "Leftover"),
    )
    SOURCE_MANUAL = "manual"
    SOURCE_IMPORTED = "imported"

    year = models.PositiveSmallIntegerField(db_index=True, help_text="The Christmas this is for, e.g. 2025.")
    item = models.ForeignKey(FoodItem, on_delete=models.CASCADE, related_name="lines")
    donor = models.ForeignKey(Donor, on_delete=models.SET_NULL, null=True, blank=True, related_name="lines")
    via_donor = models.ForeignKey(
        Donor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lines_arranged",
        help_text="Someone who arranged it for us (e.g. 'Mills via Roy'): the donor is Mills, this is Roy's company.",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PLANNED)

    unit = models.CharField(max_length=20, blank=True)
    quantity_requested = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    quantity_confirmed = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    quantity_received = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    quantity_note = models.CharField(max_length=200, blank=True)

    acquisition = models.CharField(max_length=10, choices=ACQUISITION_CHOICES, default=ACQ_UNKNOWN)
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="NOK per `unit`, when we pay."
    )
    price_note = models.CharField(max_length=200, blank=True)

    delivery_date = models.DateField(null=True, blank=True)
    delivery_mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_UNKNOWN)

    outcome = models.CharField(max_length=14, choices=OUTCOME_CHOICES, blank=True)
    outcome_source = models.CharField(max_length=10, blank=True)
    leftover_quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    ran_out_note = models.CharField(max_length=200, blank=True, help_text="When it ran out, in words.")
    comment = models.TextField(blank=True)
    next_year_note = models.TextField(blank=True, help_text="What to do differently next year.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["year", "item__category", "item__name"]
        constraints = [models.UniqueConstraint(fields=["year", "item", "donor"], name="unique_plan_line_per_donor")]

    def __str__(self) -> str:
        return f"{self.year} {self.item}"


class GuestCount(models.Model):
    """How many guests were served on a day -- the number food quantities
    really scale with. Organisation-wide (not tied to an event) so earlier
    years can be entered too, even ones from before this system."""

    date = models.DateField(unique=True)
    guests = models.PositiveIntegerField()
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["date"]

    def __str__(self) -> str:
        return f"{self.date}: {self.guests}"
