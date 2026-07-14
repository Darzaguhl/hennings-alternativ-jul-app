import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .email import send_invite_email
from .models import (
    Assignment,
    Event,
    EventCheckIn,
    Invite,
    Membership,
    QRCode,
    Shift,
    ShiftSignup,
    Skill,
)
from .serializers import (
    AcceptInviteSerializer,
    AssignmentSerializer,
    EmailTokenObtainPairSerializer,
    EventSerializer,
    InvitePreviewSerializer,
    InviteSerializer,
    MembershipSerializer,
    PublicEventSerializer,
    QRCodeSerializer,
    RegisterSerializer,
    ShiftSerializer,
    ShiftSignupSerializer,
    SkillSerializer,
    UserAdminNoteSerializer,
    UserSerializer,
)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def public_event(request):
    """Unauthenticated: the current event + its oppgaver, for the public
    website signup page. Deliberately a separate, minimal endpoint rather
    than opening up EventViewSet/ShiftViewSet -- those embed full volunteer
    profiles (emails) in participants/leaders, which must stay private."""

    event = Event.objects.filter(is_active=True).prefetch_related("shifts").first()
    if not event:
        return Response({"detail": "No event configured yet."}, status=status.HTTP_404_NOT_FOUND)
    return Response(PublicEventSerializer(event, context={"request": request}).data)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def public_skills(request):
    """Unauthenticated: the catalogue of oppgaver (roles like Kokk,
    Lydtekniker, Vaktleder) a volunteer can express interest in at
    signup, independent of which vakt/shift they pick. See public_event
    for why this is a separate minimal endpoint rather than opening up
    SkillViewSet."""

    skills = Skill.objects.all()
    return Response(SkillSerializer(skills, many=True).data)


class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer

User = get_user_model()


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def invite_preview(request, token):
    """Unauthenticated: what the accept-invite page shows before the person
    sets a password -- which role, which event, whether the link is still
    usable. Looked up by token in the URL, not authenticated, since the
    invitee has no account (or isn't logged in) yet."""

    invite = Invite.objects.select_related("event").filter(token=token).first()
    if not invite:
        return Response({"detail": "Invite not found."}, status=status.HTTP_404_NOT_FOUND)
    return Response(InvitePreviewSerializer(invite).data)


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def accept_invite(request):
    """Unauthenticated: set a password and redeem an invite token in one
    step. Reuses the existing User if the email already has an account
    (e.g. a passwordless volunteer signup being promoted to admin) rather
    than creating a duplicate -- this is the one path that sets a password
    on an already-existing account, which is why it's a dedicated endpoint
    rather than going through RegisterSerializer."""

    serializer = AcceptInviteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    invite = serializer.invite
    password = serializer.validated_data["password"]

    with transaction.atomic():
        user = User.objects.filter(email__iexact=invite.email).first()
        if not user:
            user = User(username=invite.email, email=invite.email)
        user.set_password(password)
        user.save()
        Membership.objects.get_or_create(event=invite.event, user=user, role=invite.role)
        invite.accepted_at = timezone.now()
        invite.save(update_fields=["accepted_at"])

    refresh = RefreshToken.for_user(user)
    return Response(
        {
            "user": UserSerializer(user, context={"request": request}).data,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "event": EventSerializer(invite.event, context={"request": request}).data,
        }
    )


class RegisterView(generics.CreateAPIView):
    """Public self-registration: email + password. Returns JWT tokens
    immediately so the caller (website or app) can go straight into
    signing up for oppgaver without a separate login step."""

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    queryset = User.objects.all()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": UserSerializer(user, context={"request": request}).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


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


def _can_view_pool(event, user) -> bool:
    if event.is_checkin_staff(user):
        return True
    return Shift.objects.filter(event=event, leaders=user).exists()


def _is_any_event_admin(user) -> bool:
    """Admin notes on a User aren't scoped to one event -- the org runs one
    event at a time in practice -- so this checks admin/owner status on any
    event rather than requiring an event in the URL."""

    if not user.is_authenticated:
        return False
    if Event.objects.filter(created_by=user).exists():
        return True
    return Membership.objects.filter(user=user, role__in=[Membership.ROLE_OWNER, Membership.ROLE_ADMIN]).exists()


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

    @action(detail=True, methods=["get", "patch"], url_path="notes")
    def notes(self, request, pk=None):
        """Private admin notes about a volunteer (behavior, previous years,
        etc.) -- deliberately not on the regular User serializer, which the
        volunteer themselves can see via /me and which is embedded all over
        the API (shift participants, pool, leaders)."""

        if not _is_any_event_admin(request.user):
            return Response({"detail": "Only an event admin can view or edit notes."}, status=status.HTTP_403_FORBIDDEN)

        user = self.get_object()
        if request.method == "GET":
            return Response(UserAdminNoteSerializer(user).data)

        serializer = UserAdminNoteSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class EventViewSet(viewsets.ModelViewSet):
    queryset = Event.objects.all().select_related("created_by")
    serializer_class = EventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        event = serializer.save(created_by=self.request.user)
        Membership.objects.get_or_create(event=event, user=self.request.user, role=Membership.ROLE_OWNER)

    def perform_destroy(self, instance):
        if not instance.is_owner(self.request.user):
            raise PermissionDenied("Only an owner can delete this event.")
        instance.delete()

    def perform_update(self, serializer):
        if not self.get_object().is_admin(self.request.user):
            raise PermissionDenied("Only an admin can update this event.")
        serializer.save()

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        """Make this the one active event -- deactivates every other event
        in the same transaction, since exactly one is ever active."""

        event = self.get_object()
        if not event.is_owner(request.user):
            raise PermissionDenied("Only an owner can activate an event.")
        with transaction.atomic():
            Event.objects.exclude(pk=event.pk).filter(is_active=True).update(is_active=False)
            event.is_active = True
            event.save(update_fields=["is_active"])
        return Response(EventSerializer(event, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        event = self.get_object()
        if not event.is_owner(request.user):
            raise PermissionDenied("Only an owner can deactivate an event.")
        event.is_active = False
        event.save(update_fields=["is_active"])
        return Response(EventSerializer(event, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="checkin")
    def checkin(self, request, pk=None):
        """Check in a volunteer for today; resolves automatically when the
        person has exactly one non-critical oppgave signed up for today,
        otherwise they're added to the pool for admin assignment.

        Accepts either `user_code` (scan a personal QR badge -- only valid
        when checkin_mode is personal_qr) or `user_id` (check-in staff pick
        someone from a list manually, e.g. the admin dashboard's Innsjekk
        page when there's no QR to scan -- available regardless of mode)."""

        event = self.get_object()
        if not event.is_checkin_staff(request.user):
            return Response({"detail": "Only check-in staff can check in attendees."}, status=status.HTTP_403_FORBIDDEN)

        user_id = request.data.get("user_id")
        user_code = request.data.get("user_code")

        if user_id:
            attendee = get_object_or_404(User, pk=user_id)
        elif user_code:
            if event.checkin_mode != Event.CHECKIN_MODE_PERSONAL_QR:
                return Response(
                    {"detail": "This event uses event-QR self check-in, not personal-QR admit."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qr = QRCode.objects.filter(data=user_code).select_related("user").first()
            if not qr:
                return Response({"detail": "QR code not found"}, status=status.HTTP_404_NOT_FOUND)
            attendee = qr.user
        else:
            return Response({"detail": "user_id or user_code is required"}, status=status.HTTP_400_BAD_REQUEST)

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
        oppgave, oldest arrival first (first-come-first-served queue), with
        their candidate signups (ranked) and a suggestion."""

        event = self.get_object()
        if not _can_view_pool(event, request.user):
            return Response({"detail": "You don't have access to this event's pool."}, status=status.HTTP_403_FORBIDDEN)

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
            .order_by("checked_in_at")
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
        """Admin or the target oppgave's leader confirms a specific
        oppgave for a checked-in, unassigned volunteer — the final step out
        of the pool."""

        event = self.get_object()

        user_id = request.data.get("user_id")
        shift_id = request.data.get("shift_id")
        if not user_id or not shift_id:
            return Response({"detail": "user_id and shift_id are required"}, status=status.HTTP_400_BAD_REQUEST)

        attendee = get_object_or_404(User, pk=user_id)
        shift = get_object_or_404(Shift, pk=shift_id, event=event)

        if not (event.is_admin(request.user) or shift.is_led_by(request.user)):
            return Response(
                {"detail": "Only an admin or this oppgave's leader can assign it."},
                status=status.HTTP_403_FORBIDDEN,
            )

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

    @action(detail=True, methods=["get", "post"], url_path="memberships")
    def memberships(self, request, pk=None):
        """List (GET) or add (POST) owner/admin/check-in-staff roles
        for this event. Oppgave leadership isn't managed here — see
        Shift.leaders.

        Granting owner or admin requires being an owner yourself, so no
        single admin can unilaterally create more admins or lock
        out the rest. A plain admin can still add/remove check-in
        staff."""

        event = self.get_object()
        if not event.is_admin(request.user):
            return Response({"detail": "Only an admin can manage members."}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "GET":
            qs = Membership.objects.filter(event=event).select_related("user")
            return Response(MembershipSerializer(qs, many=True, context={"request": request}).data)

        requested_role = request.data.get("role")
        if requested_role in (Membership.ROLE_OWNER, Membership.ROLE_ADMIN) and not event.is_owner(request.user):
            return Response(
                {"detail": "Only an owner can grant owner or admin access."}, status=status.HTTP_403_FORBIDDEN
            )

        serializer = MembershipSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                membership = serializer.save(event=event)
        except IntegrityError:
            return Response({"detail": "This user already has that role."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            MembershipSerializer(membership, context={"request": request}).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="remove-membership")
    def remove_membership(self, request, pk=None):
        event = self.get_object()
        if not event.is_admin(request.user):
            return Response({"detail": "Only an admin can manage members."}, status=status.HTTP_403_FORBIDDEN)

        membership_id = request.data.get("membership_id")
        membership = Membership.objects.filter(event=event, pk=membership_id).first()
        if not membership:
            return Response({"detail": "Membership not found."}, status=status.HTTP_404_NOT_FOUND)

        if membership.role in (Membership.ROLE_OWNER, Membership.ROLE_ADMIN) and not event.is_owner(
            request.user
        ):
            return Response(
                {"detail": "Only an owner can remove owner or admin access."}, status=status.HTTP_403_FORBIDDEN
            )

        if membership.role == Membership.ROLE_OWNER:
            other_owners = Membership.objects.filter(event=event, role=Membership.ROLE_OWNER).exclude(pk=membership.pk)
            if not other_owners.exists():
                return Response(
                    {"detail": "Cannot remove the last owner. Make someone else an owner first."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="invites")
    def invites(self, request, pk=None):
        """List (GET) or send (POST) admin/staff invites for this event.

        Same tiering as memberships: only an owner can invite someone as
        owner/admin; a plain admin can still invite check-in staff. Unlike
        memberships, this doesn't require the invitee to already have an
        account -- accept_invite creates one if needed."""

        event = self.get_object()
        if not event.is_admin(request.user):
            return Response({"detail": "Only an admin can manage invites."}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "GET":
            qs = Invite.objects.filter(event=event).select_related("invited_by")
            return Response(InviteSerializer(qs, many=True, context={"request": request}).data)

        requested_role = request.data.get("role")
        if requested_role in (Membership.ROLE_OWNER, Membership.ROLE_ADMIN) and not event.is_owner(request.user):
            return Response(
                {"detail": "Only an owner can invite someone as owner or admin."}, status=status.HTTP_403_FORBIDDEN
            )

        email = (request.data.get("email") or "").strip()
        if not email:
            return Response({"detail": "email is required"}, status=status.HTTP_400_BAD_REQUEST)
        if requested_role not in dict(Membership.ROLE_CHOICES):
            return Response({"detail": "Invalid role"}, status=status.HTTP_400_BAD_REQUEST)

        invite = Invite.objects.create(event=event, email=email, role=requested_role, invited_by=request.user)
        send_invite_email(invite)
        return Response(InviteSerializer(invite, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="revoke-invite")
    def revoke_invite(self, request, pk=None):
        event = self.get_object()
        if not event.is_admin(request.user):
            return Response({"detail": "Only an admin can manage invites."}, status=status.HTTP_403_FORBIDDEN)

        invite_id = request.data.get("invite_id")
        invite = Invite.objects.filter(event=event, pk=invite_id).first()
        if not invite:
            return Response({"detail": "Invite not found."}, status=status.HTTP_404_NOT_FOUND)

        if invite.role in (Membership.ROLE_OWNER, Membership.ROLE_ADMIN) and not event.is_owner(request.user):
            return Response(
                {"detail": "Only an owner can revoke an owner or admin invite."}, status=status.HTTP_403_FORBIDDEN
            )

        invite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="metrics")
    def metrics(self, request, pk=None):
        """Headcount + per-oppgave utilization for a given day (defaults
        to today)."""

        event = self.get_object()

        date_param = request.query_params.get("date")
        if date_param:
            try:
                target_date = datetime.date.fromisoformat(date_param)
            except ValueError:
                return Response({"detail": "date must be YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            target_date = timezone.localdate()

        checked_in_count = EventCheckIn.objects.filter(event=event, date=target_date).count()
        assigned_count = Assignment.objects.filter(event=event, date=target_date).count()

        shift_metrics = [
            {
                "id": shift.id,
                "title": shift.title,
                "criticality": shift.criticality,
                "capacity": shift.capacity,
                "min_capacity": shift.min_capacity,
                "signup_count": shift.signup_count,
                "assigned_count": shift.assigned_count,
                "is_full": shift.is_full,
                "is_understaffed": shift.is_understaffed,
            }
            for shift in Shift.objects.filter(event=event, date=target_date)
        ]

        return Response(
            {
                "date": target_date.isoformat(),
                "checked_in": checked_in_count,
                "assigned": assigned_count,
                "in_pool": max(checked_in_count - assigned_count, 0),
                "shifts": shift_metrics,
            }
        )


class ShiftViewSet(viewsets.ModelViewSet):
    queryset = Shift.objects.select_related("event", "created_by").prefetch_related("participants", "leaders")
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
        if not event.is_admin(self.request.user):
            raise PermissionDenied("Only an admin can add vakter.")
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        shift = self.get_object()
        is_admin = shift.event.is_admin(self.request.user)
        if not (is_admin or shift.is_led_by(self.request.user)):
            raise PermissionDenied("Only an admin or this oppgave's leader can edit it.")
        if "leaders" in serializer.validated_data and not is_admin:
            raise PermissionDenied("Only an admin can change this oppgave's leaders.")
        serializer.save()

    def perform_destroy(self, instance):
        if not instance.event.is_admin(self.request.user):
            raise PermissionDenied("Only an admin can delete this vakt.")
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

    @action(detail=True, methods=["post"], url_path="cancel-assignment")
    def cancel_assignment(self, request, pk=None):
        """Let a volunteer cancel their own confirmed assignment to this
        oppgave (plans changed after being placed)."""

        shift = self.get_object()
        deleted, _ = Assignment.objects.filter(shift=shift, user=request.user).delete()
        if not deleted:
            return Response({"detail": "You don't have a confirmed assignment for this vakt."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ShiftSerializer(shift, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="assignments")
    def assignments(self, request, pk=None):
        shift = self.get_object()
        if not (shift.event.is_admin(request.user) or shift.is_led_by(request.user)):
            return Response(
                {"detail": "Only an admin or this oppgave's leader can view assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )
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
