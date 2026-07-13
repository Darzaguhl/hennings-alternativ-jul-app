import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from django.utils import timezone


class Skill(models.Model):
    name = models.CharField(max_length=100, unique=True)

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

    def __str__(self) -> str:
        return self.username


class Event(models.Model):
    """A single year's Alternativ Jul. One dedicated org, one event at a
    time in practice, but modeled per-year since the org runs annually."""

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
    checkin_mode = models.CharField(
        max_length=20,
        choices=CHECKIN_MODE_CHOICES,
        default=CHECKIN_MODE_EVENT_QR,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="events_created",
    )

    def __str__(self) -> str:
        return f"{self.title} ({self.code})"


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
    """A single oppgave (task/role) within an Event on a given day, e.g.
    'Kjøkken' on 24 Dec, 18:00-22:00."""

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
    capacity = models.PositiveIntegerField(null=True, blank=True)
    criticality = models.CharField(
        max_length=10,
        choices=CRITICALITY_CHOICES,
        default=CRITICALITY_NORMAL,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shifts_created",
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
