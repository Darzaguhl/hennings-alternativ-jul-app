from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    Assignment,
    Event,
    EventCheckIn,
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


class EventSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    code = serializers.CharField(read_only=True)

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
        ]
        read_only_fields = ["created_by"]


class QRCodeSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = QRCode
        fields = ["id", "data", "created_at", "updated_at", "user", "owner_username"]
        read_only_fields = ["user", "data", "created_at", "updated_at", "owner_username"]


class ShiftSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    participants = UserSerializer(many=True, read_only=True)
    signup_count = serializers.IntegerField(read_only=True)
    assigned_count = serializers.IntegerField(read_only=True)
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
            "created_by",
            "participants",
            "signup_count",
            "assigned_count",
            "is_full",
        ]
        read_only_fields = ["created_by"]


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
