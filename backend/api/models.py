import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Skill(models.Model):
    name = models.CharField(max_length=100, unique=True)

    # Which of Shift.phase this oppgave is valid for -- e.g. "Vertskap" is
    # guest-facing only, "Hva som helst på ryddevakt" is teardown only.
    # Default True on all three: until an admin actually curates these, an
    # oppgave is treated as unrestricted rather than silently blocking every
    # signup that uses it. See ShiftViewSet.signup for how this is enforced.
    allowed_in_setup = models.BooleanField(default=True)
    allowed_in_guest = models.BooleanField(default=True)
    allowed_in_teardown = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    phone = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    skills = models.ManyToManyField(Skill, blank=True, related_name="users")
    experience_notes = models.TextField(
        blank=True,
        help_text="Freeform background: previous experience, education, certifications.",
    )
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
    oppgave is what. See `phase`, and Skill.allowed_in_* for how the two
    are related."""

    CRITICALITY_NORMAL = "normal"
    CRITICALITY_CRITICAL = "critical"
    CRITICALITY_CHOICES = (
        (CRITICALITY_NORMAL, "Normal"),
        (CRITICALITY_CRITICAL, "Critical — requires relevant experience"),
    )

    PHASE_SETUP = "setup"
    PHASE_GUEST = "guest"
    PHASE_TEARDOWN = "teardown"
    PHASE_CHOICES = (
        (PHASE_SETUP, "Forberedelse"),
        (PHASE_GUEST, "Gjester til stede"),
        (PHASE_TEARDOWN, "Rydding"),
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
    # Blank = uncategorized, treated as compatible with every oppgave (see
    # ShiftViewSet.signup) so shifts created before this field existed don't
    # retroactively start rejecting signups.
    phase = models.CharField(max_length=10, choices=PHASE_CHOICES, blank=True)
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


class ShiftSignup(models.Model):
    """A user's expressed interest in doing a given oppgave on its day.

    A user may shortlist several oppgaver for the same day — this is a
    candidate list, not a commitment. Exactly one gets turned into an
    Assignment, at check-in time. event/date are denormalized from shift
    purely to make querying "this user's candidates for today" cheap.

    has_relevant_experience / experience_notes are only meaningful when the
    shift is critical (see Shift.is_critical) — the signup flow should ask
    for them in that case.
    """

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="signups")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shift_signups")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="shift_signups")
    date = models.DateField()
    has_relevant_experience = models.BooleanField(null=True, blank=True)
    experience_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("shift", "user")

    def save(self, *args, **kwargs):
        self.event = self.shift.event
        self.date = self.shift.date
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} → {self.shift}"


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
    """The final, admin-confirmed placement of a user onto one oppgave for
    the day — as opposed to ShiftSignup, which is just a candidate.

    event/date are denormalized from shift so the DB enforces at most one
    confirmed placement per user per event per day, even though a user may
    have shortlisted (ShiftSignup'd) several oppgaver that day.
    """

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
        self.event = self.shift.event
        self.date = self.shift.date
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} assigned to {self.shift}"
