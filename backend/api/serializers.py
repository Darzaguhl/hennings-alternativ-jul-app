from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as validate_password_strength
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    Assignment,
    Event,
    EventCheckIn,
    Invite,
    Membership,
    PasswordSetupToken,
    QRCode,
    Shift,
    ShiftConflict,
    ShiftSignup,
    Skill,
)

User = get_user_model()


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "name", "allowed_in_setup", "allowed_in_guest", "allowed_in_teardown"]


class UserSerializer(serializers.ModelSerializer):
    skills = SkillSerializer(many=True, read_only=True)
    skill_ids = serializers.PrimaryKeyRelatedField(
        source="skills",
        queryset=Skill.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "skills", "skill_ids", "experience_notes"]


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
    """Public self-registration: email, optionally a password. `username` is
    set equal to the email internally so the rest of the codebase (which
    still refers to User.username in places) keeps working without change;
    login itself goes through EmailBackend, not username.

    Volunteers don't need a password -- they get a JWT session immediately
    on registration and that's enough to use the app/site they signed up
    from. Admins/staff who need to log in to the admin dashboard from
    scratch should supply a password so they can do a real email+password
    login later; omitting it leaves the account with Django's standard
    unusable-password marker (set_password(None)), not a guessable default."""

    password = serializers.CharField(write_only=True, required=False, allow_blank=True, default="")
    skill_ids = serializers.PrimaryKeyRelatedField(
        source="skills",
        queryset=Skill.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        model = User
        fields = ["id", "email", "password", "skill_ids"]

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

    def create(self, validated_data):
        skills = validated_data.pop("skills", [])
        password = validated_data.get("password") or None
        user = User.objects.create_user(
            username=validated_data["email"],
            email=validated_data["email"],
            password=password,
        )
        if skills:
            user.skills.set(skills)
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
            "phase",
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


class PublicEventSerializer(serializers.ModelSerializer):
    """Safe subset for the public website signup page -- no created_by
    (would embed the admin's email/profile), just enough to render a
    signup form: name, blurb, the day's oppgaver, and whether the signup
    window is currently open so the site can show the form or a
    closed/not-yet-open message."""

    shifts = PublicShiftSerializer(many=True, read_only=True)
    conflicts = PublicShiftConflictSerializer(many=True, read_only=True, source="shift_conflicts")
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
            "phase",
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


class ShiftSignupSerializer(serializers.ModelSerializer):
    shift = ShiftSerializer(read_only=True)
    user = UserSerializer(read_only=True)

    class Meta:
        model = ShiftSignup
        fields = [
            "id",
            "shift",
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
    user = UserSerializer(read_only=True)
    confirmed_by = UserSerializer(read_only=True)

    class Meta:
        model = Assignment
        fields = ["id", "shift", "user", "confirmed_by", "confirmed_at"]
        read_only_fields = fields
