from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    Assignment,
    Event,
    EventCheckIn,
    Membership,
    QRCode,
    Shift,
    ShiftSignup,
    Skill,
)

User = get_user_model()


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "name"]


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
        fields = ["id", "username", "email", "skills", "skill_ids", "experience_notes"]


class RegisterSerializer(serializers.ModelSerializer):
    """Public self-registration: email + password. `username` is set equal
    to the email internally so the rest of the codebase (which still refers
    to User.username in places) keeps working without change; login itself
    goes through EmailBackend, not username."""

    password = serializers.CharField(write_only=True, min_length=8, validators=[validate_password])

    class Meta:
        model = User
        fields = ["id", "email", "password"]

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data["email"],
            email=validated_data["email"],
            password=validated_data["password"],
        )


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


class EventSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    code = serializers.CharField(read_only=True)
    viewer_role = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "date",
            "code",
            "checkin_mode",
            "created_by",
            "viewer_role",
        ]
        read_only_fields = ["created_by"]

    def get_viewer_role(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None
        if obj.is_superadmin(user):
            return Membership.ROLE_SUPERADMIN
        if obj.is_checkin_staff(user):
            return Membership.ROLE_CHECKIN_STAFF
        if obj.shifts.filter(leaders=user).exists():
            return "shift_leader"
        return "volunteer"


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
