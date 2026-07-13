from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    Assignment,
    Event,
    EventCheckIn,
    EventGroupInvite,
    EventInvite,
    Notification,
    QRCode,
    Shift,
    ShiftSignup,
    Skill,
    UserGroup,
    GroupInvite,
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


class UserGroupSerializer(serializers.ModelSerializer):
    members = UserSerializer(many=True, read_only=True)
    member_ids = serializers.PrimaryKeyRelatedField(
        source="members",
        queryset=User.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )
    created_by = UserSerializer(read_only=True)
    code = serializers.CharField(read_only=True)

    class Meta:
        model = UserGroup
        fields = ["id", "name", "code", "created_by", "members", "member_ids"]

    def create(self, validated_data):
        members = validated_data.pop("members", [])
        request = self.context["request"]
        group = UserGroup.objects.create(created_by=request.user, **validated_data)
        if members:
            group.members.set(members)
        return group

    def update(self, instance, validated_data):
        members = validated_data.pop("members", None)
        group = super().update(instance, validated_data)
        if members is not None:
            group.members.set(members)
        return group


class EventSerializer(serializers.ModelSerializer):
    participants = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        many=True,
        required=False,
    )
    participant_details = UserSerializer(
        source="participants",
        many=True,
        read_only=True,
    )
    groups = serializers.PrimaryKeyRelatedField(
        queryset=UserGroup.objects.all(),
        many=True,
        required=False,
    )
    group_details = UserGroupSerializer(
        source="groups",
        many=True,
        read_only=True,
    )
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
            "auto_approve",
            "created_by",
            "participants",
            "participant_details",
            "groups",
            "group_details",
        ]

    def create(self, validated_data):
        participants = validated_data.pop("participants", [])
        groups = validated_data.pop("groups", [])
        event = Event.objects.create(**validated_data)
        if participants:
            event.participants.set(participants)
        if groups:
            event.groups.set(groups)
        return event

    def update(self, instance, validated_data):
        participants = validated_data.pop("participants", None)
        groups = validated_data.pop("groups", None)
        event = super().update(instance, validated_data)
        if participants is not None:
            event.participants.set(participants)
        if groups is not None:
            event.groups.set(groups)
        return event


class GroupInviteSerializer(serializers.ModelSerializer):
    group = UserGroupSerializer(read_only=True)
    invitee = UserSerializer(read_only=True)
    invited_by = UserSerializer(read_only=True)

    class Meta:
        model = GroupInvite
        fields = [
            "id",
            "group",
            "invitee",
            "invited_by",
            "status",
            "created_at",
            "responded_at",
        ]
        read_only_fields = ["status", "created_at", "responded_at"]


class EventInviteSerializer(serializers.ModelSerializer):
    event = EventSerializer(read_only=True)
    invitee = UserSerializer(read_only=True)
    invited_by = UserSerializer(read_only=True)

    class Meta:
        model = EventInvite
        fields = [
            "id",
            "event",
            "invitee",
            "invited_by",
            "status",
            "created_at",
            "responded_at",
        ]
        read_only_fields = ["status", "created_at", "responded_at"]


class EventGroupInviteSerializer(serializers.ModelSerializer):
    event = EventSerializer(read_only=True)
    group = UserGroupSerializer(read_only=True)
    invited_by = UserSerializer(read_only=True)

    class Meta:
        model = EventGroupInvite
        fields = [
            "id",
            "event",
            "group",
            "invited_by",
            "status",
            "created_at",
            "responded_at",
        ]
        read_only_fields = ["status", "created_at", "responded_at"]


class NotificationSerializer(serializers.ModelSerializer):
    event_invite = EventInviteSerializer(read_only=True)
    group_invite = GroupInviteSerializer(read_only=True)
    event_group_invite = EventGroupInviteSerializer(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "type",
            "message",
            "is_read",
            "created_at",
            "event_invite",
            "group_invite",
            "event_group_invite",
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
            "auto_approve",
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
