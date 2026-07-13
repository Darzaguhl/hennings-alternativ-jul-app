import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .models import (
    Assignment,
    Event,
    EventCheckIn,
    QRCode,
    Shift,
    ShiftSignup,
    Skill,
)
from .serializers import (
    AssignmentSerializer,
    EventSerializer,
    QRCodeSerializer,
    ShiftSerializer,
    ShiftSignupSerializer,
    SkillSerializer,
    UserSerializer,
)

User = get_user_model()


def _rank_candidates(signups):
    """Order a user's candidate ShiftSignups for a day, best suggestion first.

    Critical shifts with a confirmed "yes" on relevant experience sort ahead
    of ones with an unconfirmed/"no" answer. Ties break on which shift is
    most understaffed relative to its capacity, since that's the one most
    worth filling. This is intentionally a simple, explainable v1 — the
    admin always has final say via EventViewSet.assign.
    """

    def sort_key(signup):
        shift = signup.shift
        if shift.is_critical:
            if signup.has_relevant_experience is True:
                experience_score = 0
            elif signup.has_relevant_experience is None:
                experience_score = 1
            else:
                experience_score = 2
        else:
            experience_score = 0

        if shift.capacity is not None:
            urgency = shift.capacity - shift.assigned_count
        else:
            urgency = float("inf")

        return (experience_score, urgency)

    return sorted(signups, key=sort_key)


def _resolve_checkin(event, user, performed_by):
    """Check `user` in to `event` for today and try to resolve a single
    Assignment automatically.

    Returns a dict describing the outcome. Never raises for the expected
    "nothing to resolve yet" / "ambiguous" cases — those become pool
    entries for an admin to resolve via EventViewSet.assign.
    """

    today = timezone.localdate()
    EventCheckIn.objects.get_or_create(event=event, user=user, date=today)

    existing = Assignment.objects.filter(event=event, user=user, date=today).select_related("shift").first()
    if existing:
        return {"status": "already_assigned", "shift": existing.shift}

    candidates = list(Shift.objects.filter(event=event, date=today, signups__user=user).distinct())

    if not candidates:
        return {"status": "pending_pool", "reason": "no_candidates", "candidates": []}

    if len(candidates) == 1 and not candidates[0].is_critical:
        shift = candidates[0]
        Assignment.objects.create(shift=shift, user=user, confirmed_by=performed_by)
        return {"status": "assigned", "shift": shift}

    return {"status": "pending_pool", "reason": "needs_admin_review", "candidates": candidates}


def _checkin_response(attendee, result, request):
    attendee_payload = UserSerializer(attendee, context={"request": request}).data
    payload = {"status": result["status"], "user": attendee_payload}
    status_code = status.HTTP_200_OK

    if result["status"] == "already_assigned":
        payload["shift"] = ShiftSerializer(result["shift"], context={"request": request}).data
        payload["message"] = f"Already checked in and assigned to {result['shift'].title}."
    elif result["status"] == "assigned":
        payload["shift"] = ShiftSerializer(result["shift"], context={"request": request}).data
        payload["message"] = f"Checked in and assigned to {result['shift'].title}."
        status_code = status.HTTP_201_CREATED
    else:  # pending_pool
        payload["candidates"] = ShiftSerializer(result["candidates"], many=True, context={"request": request}).data
        if result["reason"] == "no_candidates":
            payload["message"] = "Checked in. Not signed up for any oppgave today — added to the pool for manual assignment."
        else:
            payload["message"] = "Checked in. Needs admin review to pick an oppgave — added to the pool."
        status_code = status.HTTP_202_ACCEPTED

    return Response(payload, status=status_code)


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_update(self, serializer):
        if serializer.instance != self.request.user:
            raise PermissionDenied("You can only update your own profile.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance != self.request.user:
            raise PermissionDenied("You can only delete your own account.")
        instance.delete()

    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class EventViewSet(viewsets.ModelViewSet):
    queryset = Event.objects.all().select_related("created_by")
    serializer_class = EventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_destroy(self, instance):
        if instance.created_by != self.request.user:
            raise PermissionDenied("Only the creator can delete this event.")
        instance.delete()

    def perform_update(self, serializer):
        if self.get_object().created_by != self.request.user:
            raise PermissionDenied("Only the creator can update this event.")
        serializer.save()

    @action(detail=True, methods=["post"], url_path="checkin")
    def checkin(self, request, pk=None):
        """Personal-QR check-in: an admin scans a volunteer's own badge.

        Used when Event.checkin_mode == CHECKIN_MODE_PERSONAL_QR. Resolves
        automatically when the scanned person has exactly one non-critical
        oppgave signed up for today; otherwise they're checked in and added
        to the pool for admin assignment via `assign`.
        """

        event = self.get_object()
        if event.checkin_mode != Event.CHECKIN_MODE_PERSONAL_QR:
            return Response(
                {"detail": "This event uses event-QR self check-in, not personal-QR admit."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if event.created_by != request.user:
            return Response({"detail": "Only the creator can check in attendees."}, status=status.HTTP_403_FORBIDDEN)

        user_code = request.data.get("user_code")
        if not user_code:
            return Response({"detail": "user_code is required"}, status=status.HTTP_400_BAD_REQUEST)

        qr = QRCode.objects.filter(data=user_code).select_related("user").first()
        if not qr:
            return Response({"detail": "QR code not found"}, status=status.HTTP_404_NOT_FOUND)

        attendee = qr.user
        result = _resolve_checkin(event, attendee, performed_by=request.user)
        return _checkin_response(attendee, result, request)

    @action(detail=True, methods=["post"], url_path="self-checkin")
    def self_checkin(self, request, pk=None):
        """Event-QR check-in: the volunteer scans one shared code themselves.

        Used when Event.checkin_mode == CHECKIN_MODE_EVENT_QR. Any
        authenticated user can call this for themselves — no admin
        involvement needed to arrive. Resolution logic is identical to the
        personal-QR flow.
        """

        event = self.get_object()
        if event.checkin_mode != Event.CHECKIN_MODE_EVENT_QR:
            return Response(
                {"detail": "This event uses personal-QR check-in — ask an admin to scan your badge."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = _resolve_checkin(event, request.user, performed_by=request.user)
        return _checkin_response(request.user, result, request)

    @action(detail=True, methods=["get"], url_path="pool")
    def pool(self, request, pk=None):
        """List everyone checked in today who doesn't yet have a confirmed
        oppgave, with their candidate signups (ranked) and a suggestion."""

        event = self.get_object()
        if event.created_by != request.user:
            return Response({"detail": "Only the creator can view the pool."}, status=status.HTTP_403_FORBIDDEN)

        date_param = request.query_params.get("date")
        if date_param:
            try:
                target_date = datetime.date.fromisoformat(date_param)
            except ValueError:
                return Response({"detail": "date must be YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            target_date = timezone.localdate()

        assigned_user_ids = Assignment.objects.filter(event=event, date=target_date).values_list("user_id", flat=True)
        arrivals = (
            EventCheckIn.objects.filter(event=event, date=target_date)
            .exclude(user_id__in=assigned_user_ids)
            .select_related("user")
            .prefetch_related("user__skills")
        )

        entries = []
        for arrival in arrivals:
            candidates = list(
                ShiftSignup.objects.filter(event=event, date=target_date, user=arrival.user).select_related(
                    "shift", "shift__event"
                )
            )
            ranked = _rank_candidates(candidates)
            entries.append(
                {
                    "user": UserSerializer(arrival.user, context={"request": request}).data,
                    "checked_in_at": arrival.checked_in_at,
                    "candidates": ShiftSignupSerializer(ranked, many=True, context={"request": request}).data,
                    "suggested_shift": (
                        ShiftSerializer(ranked[0].shift, context={"request": request}).data if ranked else None
                    ),
                }
            )

        return Response(entries)

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        """Admin confirms a specific oppgave for a checked-in, unassigned
        volunteer — the final step out of the pool."""

        event = self.get_object()
        if event.created_by != request.user:
            return Response({"detail": "Only the creator can assign attendees."}, status=status.HTTP_403_FORBIDDEN)

        user_id = request.data.get("user_id")
        shift_id = request.data.get("shift_id")
        if not user_id or not shift_id:
            return Response({"detail": "user_id and shift_id are required"}, status=status.HTTP_400_BAD_REQUEST)

        attendee = get_object_or_404(User, pk=user_id)
        shift = get_object_or_404(Shift, pk=shift_id, event=event)

        if not EventCheckIn.objects.filter(event=event, user=attendee, date=shift.date).exists():
            return Response({"detail": "This person has not checked in today."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                assignment = Assignment.objects.create(shift=shift, user=attendee, confirmed_by=request.user)
        except IntegrityError:
            return Response(
                {"detail": "This person already has a confirmed oppgave today."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            AssignmentSerializer(assignment, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ShiftViewSet(viewsets.ModelViewSet):
    queryset = Shift.objects.select_related("event", "created_by").prefetch_related("participants")
    serializer_class = ShiftSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        event_id = self.request.query_params.get("event")
        if event_id:
            queryset = queryset.filter(event_id=event_id)
        return queryset

    def perform_create(self, serializer):
        event = serializer.validated_data["event"]
        if event.created_by != self.request.user:
            raise PermissionDenied("Only the event creator can add vakter.")
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if self.get_object().event.created_by != self.request.user:
            raise PermissionDenied("Only the event creator can edit this vakt.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.event.created_by != self.request.user:
            raise PermissionDenied("Only the event creator can delete this vakt.")
        instance.delete()

    @action(detail=True, methods=["post"], url_path="signup")
    def signup(self, request, pk=None):
        """Express interest in this oppgave for its day. A user may hold
        several candidate signups for the same day — the actual placement
        is resolved later, at check-in (see EventViewSet._resolve_checkin
        and .assign). If the oppgave is critical, the caller should ask
        for has_relevant_experience/experience_notes before submitting."""

        shift = self.get_object()
        user = request.user

        if shift.is_full:
            return Response({"detail": "This vakt is full."}, status=status.HTTP_400_BAD_REQUEST)

        if ShiftSignup.objects.filter(shift=shift, user=user).exists():
            return Response({"detail": "Already signed up for this vakt."}, status=status.HTTP_400_BAD_REQUEST)

        has_experience = request.data.get("has_relevant_experience")
        if isinstance(has_experience, str):
            has_experience = has_experience.lower() in {"1", "true", "yes", "on"}
        elif has_experience is not None:
            has_experience = bool(has_experience)

        try:
            with transaction.atomic():
                signup = ShiftSignup.objects.create(
                    shift=shift,
                    user=user,
                    has_relevant_experience=has_experience,
                    experience_notes=request.data.get("experience_notes", ""),
                )
        except IntegrityError:
            return Response({"detail": "Already signed up for this vakt."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ShiftSignupSerializer(signup, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="withdraw")
    def withdraw(self, request, pk=None):
        shift = self.get_object()
        deleted, _ = ShiftSignup.objects.filter(shift=shift, user=request.user).delete()
        if not deleted:
            return Response({"detail": "Not signed up for this vakt."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ShiftSerializer(shift, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="assignments")
    def assignments(self, request, pk=None):
        shift = self.get_object()
        if shift.event.created_by != request.user:
            return Response({"detail": "Only the event creator can view assignments."}, status=status.HTTP_403_FORBIDDEN)
        serializer = AssignmentSerializer(
            shift.assignments.select_related("user", "confirmed_by"), many=True, context={"request": request}
        )
        return Response(serializer.data)


class QRCodeViewSet(viewsets.ModelViewSet):
    serializer_class = QRCodeSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = QRCode.objects.select_related("user").all()
    http_method_names = ["get", "post"]

    def get_queryset(self):
        return QRCode.objects.select_related("user").filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        qr_code, _ = QRCode.objects.get_or_create(user=request.user)
        serializer = self.get_serializer([qr_code], many=True)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        qr_code, created = QRCode.objects.get_or_create(user=request.user)
        serializer = self.get_serializer(qr_code)
        status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(serializer.data, status=status_code)


class SkillViewSet(viewsets.ModelViewSet):
    queryset = Skill.objects.all()
    serializer_class = SkillSerializer
    permission_classes = [permissions.IsAuthenticated]
