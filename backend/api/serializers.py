from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as validate_password_strength
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    Assignment,
    Event,
    EventCheckIn,
    Invite,
    Membership,
    OppgaveSlot,
    PasswordSetupToken,
    QRCode,
    Shift,
    ShiftConflict,
    ShiftSignup,
    Skill,
    X1Signup,
)

User = get_user_model()


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "name"]


class UserSerializer(serializers.ModelSerializer):
    """The shared, minimal profile shape embedded all over the API --
    shift participants, leaders, pool entries, assignments -- so every
    volunteer on a shift can already see this much about every other
    volunteer on it. Contact details (phone/address/birthdate) must never
    be added here; see MeSerializer for where those belong."""

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "experience_notes"]


class MeSerializer(UserSerializer):
    """UserSerializer plus contact details a volunteer submitted at signup
    -- fine for them to see about themselves, and for an admin/staff/leader
    to see on the roster (UserViewSet.list/retrieve is already gated to
    self-or-roster-viewer, see _can_view_roster), but never appropriate in
    the broadly-shared contexts plain UserSerializer is embedded in.

    participation_years is derived, not stored -- an Assignment only ever
    exists once a volunteer both checked in and was placed in an oppgave
    (see _resolve_checkin), so its mere existence across past events *is*
    "participated that year." Lets the signup page's returning-volunteer
    login show real history instead of a self-reported (and easily wrong)
    field."""

    participation_years = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ["phone", "address", "birthdate", "about", "participation_years"]

    def get_participation_years(self, obj):
        years = {a.event.year_label for a in obj.assignments.select_related("event")}
        return sorted(years)


class UserAdminNoteSerializer(serializers.ModelSerializer):
    """Deliberately separate from UserSerializer, which is embedded all over
    the API (shift participants, leaders, pool entries, /me) and returned to
    the volunteer themselves. admin_notes must never appear in that path --
    only fetched/edited via UserViewSet.notes, which checks the requester is
    an event admin/owner first."""

    class Meta:
        model = User
        fields = ["id", "email", "admin_notes"]
        read_only_fields = ["id", "email"]


class RegisterSerializer(serializers.ModelSerializer):
    """Public self-registration: email plus the contact details the org
    needs on file for a volunteer (name, phone, address, birthdate),
    optionally a password. `username` is set equal to the email internally
    so the rest of the codebase (which still refers to User.username in
    places) keeps working without change; login itself goes through
    EmailBackend, not username.

    Volunteers don't need a password -- they get a JWT session immediately
    on registration and that's enough to use the app/site they signed up
    from. Admins/staff who need to log in to the admin dashboard from
    scratch should supply a password so they can do a real email+password
    login later; omitting it leaves the account with Django's standard
    unusable-password marker (set_password(None)), not a guessable default.

    Oppgave interest is no longer collected here as a blanket skill
    tag -- it's expressed per (vakt, oppgave) via OppgaveSlotViewSet.signup
    once the account exists, see PublicEventSerializer."""

    password = serializers.CharField(write_only=True, required=False, allow_blank=True, default="")
    # first_name/last_name/phone/address are blank=True on the model (an
    # invited admin/staff account has no need for them), so the model
    # field alone wouldn't require them here -- explicit required=True
    # enforces it specifically for public self-registration.
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    phone = serializers.CharField(required=True)
    address = serializers.CharField(required=True)
    birthdate = serializers.DateField(required=True)
    # Kvalifikasjoner / "fortell om deg selv" -- unlike the contact fields
    # above, these are optional: open-ended bio fields, not core identity.
    experience_notes = serializers.CharField(required=False, allow_blank=True)
    about = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "password",
            "first_name",
            "last_name",
            "phone",
            "address",
            "birthdate",
            "experience_notes",
            "about",
        ]

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value):
        if not value:
            return value
        if len(value) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters.")
        validate_password_strength(value)
        return value

    def validate_birthdate(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("Birthdate can't be in the future.")
        return value

    def create(self, validated_data):
        password = validated_data.get("password") or None
        user = User.objects.create_user(
            username=validated_data["email"],
            email=validated_data["email"],
            password=password,
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            phone=validated_data["phone"],
            address=validated_data["address"],
            birthdate=validated_data["birthdate"],
            experience_notes=validated_data.get("experience_notes", ""),
            about=validated_data.get("about", ""),
        )
        return user


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Login with {"email": ..., "password": ...} instead of username.
    Resolved to a real user via EmailBackend (see AUTHENTICATION_BACKENDS)."""

    username_field = "email"


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(source="user", queryset=User.objects.all(), write_only=True)

    class Meta:
        model = Membership
        fields = ["id", "event", "user", "user_id", "role", "created_at"]
        read_only_fields = ["event", "user", "created_at"]


class InviteSerializer(serializers.ModelSerializer):
    """Admin-facing: creating/listing pending invites for an event. Token is
    intentionally excluded -- the invite link is only ever sent by email,
    never shown back to the inviter, so a shoulder-surfed screen can't be
    used to accept someone else's invite."""

    invited_by = UserSerializer(read_only=True)
    is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Invite
        fields = ["id", "event", "email", "role", "invited_by", "created_at", "expires_at", "accepted_at", "is_usable"]
        read_only_fields = ["event", "invited_by", "created_at", "expires_at", "accepted_at", "is_usable"]


class InvitePreviewSerializer(serializers.ModelSerializer):
    """Public: what the accept-invite page shows before the person sets a
    password. No token in the response body -- the frontend already has it
    from the URL, and echoing it back serves no purpose."""

    event_title = serializers.CharField(source="event.title", read_only=True)
    is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Invite
        fields = ["email", "role", "event_title", "is_usable"]
        read_only_fields = fields


class AcceptInviteSerializer(serializers.Serializer):
    token = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, min_length=8, validators=[validate_password_strength])

    def validate_token(self, value):
        invite = Invite.objects.filter(token=value).select_related("event").first()
        if not invite or not invite.is_usable:
            raise serializers.ValidationError("This invite link is invalid or has expired.")
        self.invite = invite
        return value


class PasswordSetupPreviewSerializer(serializers.Serializer):
    """Public: what the set-password page shows before the volunteer picks
    a password. No token in the response body, same reasoning as
    InvitePreviewSerializer."""

    email = serializers.EmailField(source="user.email", read_only=True)
    is_usable = serializers.BooleanField(read_only=True)


class SetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, min_length=8, validators=[validate_password_strength])

    def validate_token(self, value):
        setup_token = PasswordSetupToken.objects.filter(token=value).select_related("user").first()
        if not setup_token or not setup_token.is_usable:
            raise serializers.ValidationError("This link is invalid or has expired.")
        self.setup_token = setup_token
        return value


class RequestPasswordSetupSerializer(serializers.Serializer):
    """Public: 'first time / lost the email' request for a fresh
    password-setup link, entered directly in the app. Deliberately just an
    email -- the view always responds the same way regardless of whether
    an account exists, so this can't be used to check which emails are
    registered."""

    email = serializers.EmailField()


class EventSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    code = serializers.CharField(read_only=True)
    viewer_role = serializers.SerializerMethodField()
    signups_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "date",
            "code",
            "is_active",
            "signup_opens_at",
            "signup_closes_at",
            "signups_open",
            "checkin_mode",
            "created_by",
            "viewer_role",
        ]
        # is_active is read-only here on purpose -- it's exclusive (only one
        # event active at a time), so it only changes via the dedicated
        # activate/deactivate actions, which handle deactivating the rest.
        read_only_fields = ["created_by", "is_active"]

    def get_viewer_role(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None
        if obj.is_owner(user):
            return Membership.ROLE_OWNER
        if obj.is_admin(user):
            return Membership.ROLE_ADMIN
        if obj.is_checkin_staff(user):
            return Membership.ROLE_CHECKIN_STAFF
        if obj.shifts.filter(leaders=user).exists():
            return "shift_leader"
        return "volunteer"


class PublicShiftSerializer(serializers.ModelSerializer):
    """Safe subset for anonymous website visitors browsing oppgaver before
    signing up -- deliberately excludes participants/leaders (ShiftSerializer
    embeds full UserSerializer, i.e. emails, for every signed-up volunteer,
    which must never be public)."""

    is_full = serializers.BooleanField(read_only=True)
    is_critical = serializers.BooleanField(read_only=True)

    class Meta:
        model = Shift
        fields = [
            "id",
            "event",
            "title",
            "date",
            "start_time",
            "end_time",
            "capacity",
            "criticality",
            "is_critical",
            "is_full",
            "vakt_number",
        ]
        read_only_fields = fields


class PublicShiftConflictSerializer(serializers.ModelSerializer):
    """Just the shift ids -- the website already has the full shift objects
    from PublicShiftSerializer, it only needs to know which pairs it can't
    let a visitor combine. See ShiftConflict."""

    class Meta:
        model = ShiftConflict
        fields = ["shift_a", "shift_b"]
        read_only_fields = fields


class PublicOppgaveSlotSerializer(serializers.ModelSerializer):
    """Safe subset for the public website: which oppgave is offered on
    which vakt, with how much room is left -- what lets the signup form
    render "for Vakt 5, here are the oppgaver you can pick"."""

    skill_name = serializers.CharField(source="skill.name", read_only=True)
    signup_count = serializers.IntegerField(read_only=True)
    is_full = serializers.BooleanField(read_only=True)

    class Meta:
        model = OppgaveSlot
        fields = ["id", "shift", "skill", "skill_name", "capacity", "signup_count", "is_full"]
        read_only_fields = fields


class PublicEventSerializer(serializers.ModelSerializer):
    """Safe subset for the public website signup page -- no created_by
    (would embed the admin's email/profile), just enough to render a
    signup form: name, blurb, the day's oppgaver, and whether the signup
    window is currently open so the site can show the form or a
    closed/not-yet-open message."""

    shifts = PublicShiftSerializer(many=True, read_only=True)
    conflicts = PublicShiftConflictSerializer(many=True, read_only=True, source="shift_conflicts")
    oppgave_slots = PublicOppgaveSlotSerializer(many=True, read_only=True)
    signups_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "date",
            "shifts",
            "conflicts",
            "oppgave_slots",
            "signup_opens_at",
            "signup_closes_at",
            "signups_open",
        ]
        read_only_fields = fields


class QRCodeSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = QRCode
        fields = ["id", "data", "created_at", "updated_at", "user", "owner_username"]
        read_only_fields = ["user", "data", "created_at", "updated_at", "owner_username"]


class ShiftSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    participants = UserSerializer(many=True, read_only=True)
    leaders = UserSerializer(many=True, read_only=True)
    leader_ids = serializers.PrimaryKeyRelatedField(
        source="leaders",
        queryset=User.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )
    signup_count = serializers.IntegerField(read_only=True)
    assigned_count = serializers.IntegerField(read_only=True)
    is_full = serializers.BooleanField(read_only=True)
    is_critical = serializers.BooleanField(read_only=True)
    is_understaffed = serializers.BooleanField(read_only=True)
    is_led_by_viewer = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = [
            "id",
            "event",
            "title",
            "date",
            "start_time",
            "end_time",
            "capacity",
            "min_capacity",
            "criticality",
            "vakt_number",
            "is_critical",
            "is_understaffed",
            "created_by",
            "leaders",
            "leader_ids",
            "is_led_by_viewer",
            "participants",
            "signup_count",
            "assigned_count",
            "is_full",
        ]
        read_only_fields = ["created_by"]

    def get_is_led_by_viewer(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        return obj.is_led_by(user)


class ShiftConflictSerializer(serializers.ModelSerializer):
    # Titles alongside the ids so the admin dashboard can render "«Vakt 6» ↔
    # «Vakt 7»" without a second lookup against the shift list it already has.
    shift_a_title = serializers.CharField(source="shift_a.title", read_only=True)
    shift_b_title = serializers.CharField(source="shift_b.title", read_only=True)

    class Meta:
        model = ShiftConflict
        fields = ["id", "event", "shift_a", "shift_b", "shift_a_title", "shift_b_title"]


class X1SignupSerializer(serializers.ModelSerializer):
    """A volunteer's own opt-in to serve as vaktleder (X1) -- eligibility
    is checked once at creation time, see X1SignupViewSet.perform_create
    and X1Signup's docstring."""

    class Meta:
        model = X1Signup
        fields = ["id", "event", "user", "created_at"]
        read_only_fields = ["user", "created_at"]


class OppgaveSlotSerializer(serializers.ModelSerializer):
    """Admin-facing CRUD for the (vakt, oppgave) combinations an admin
    curates -- see OppgaveSlot and OppgaveSlotViewSet. skill_name alongside
    the id, same reasoning as ShiftConflictSerializer's shift_a_title/
    shift_b_title -- avoids a second lookup in the admin dashboard."""

    skill_name = serializers.CharField(source="skill.name", read_only=True)
    shift_title = serializers.CharField(source="shift.title", read_only=True)
    signup_count = serializers.IntegerField(read_only=True)
    assigned_count = serializers.IntegerField(read_only=True)
    is_full = serializers.BooleanField(read_only=True)

    class Meta:
        model = OppgaveSlot
        fields = [
            "id",
            "event",
            "shift",
            "shift_title",
            "skill",
            "skill_name",
            "capacity",
            "signup_count",
            "assigned_count",
            "is_full",
        ]
        read_only_fields = ["event"]


class ShiftSignupSerializer(serializers.ModelSerializer):
    shift = ShiftSerializer(read_only=True)
    oppgave_slot = OppgaveSlotSerializer(read_only=True)
    user = UserSerializer(read_only=True)

    class Meta:
        model = ShiftSignup
        fields = [
            "id",
            "shift",
            "oppgave_slot",
            "user",
            "has_relevant_experience",
            "experience_notes",
            "created_at",
        ]
        read_only_fields = fields


class EventCheckInSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = EventCheckIn
        fields = ["id", "event", "user", "date", "checked_in_at"]
        read_only_fields = fields


class AssignmentSerializer(serializers.ModelSerializer):
    shift = ShiftSerializer(read_only=True)
    oppgave_slot = OppgaveSlotSerializer(read_only=True)
    user = UserSerializer(read_only=True)
    confirmed_by = UserSerializer(read_only=True)

    class Meta:
        model = Assignment
        fields = ["id", "shift", "oppgave_slot", "user", "confirmed_by", "confirmed_at"]
        read_only_fields = fields
