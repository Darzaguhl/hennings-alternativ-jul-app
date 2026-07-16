import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .email import send_invite_email, send_password_setup_email
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
)
from .serializers import (
    AcceptInviteSerializer,
    AssignmentSerializer,
    EmailTokenObtainPairSerializer,
    EventSerializer,
    InvitePreviewSerializer,
    InviteSerializer,
    MeSerializer,
    MembershipSerializer,
    OppgaveSlotSerializer,
    PasswordSetupPreviewSerializer,
    PublicEventSerializer,
    QRCodeSerializer,
    RegisterSerializer,
    RequestPasswordSetupSerializer,
    SetPasswordSerializer,
    ShiftConflictSerializer,
    ShiftSerializer,
    ShiftSignupSerializer,
    SkillSerializer,
    UserAdminNoteSerializer,
    UserSerializer,
)
from .throttling import (
    LoginRateThrottle,
    PasswordSetupConfirmRateThrottle,
    PasswordSetupRequestRateThrottle,
    RegisterRateThrottle,
)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def public_event(request):
    """Unauthenticated: the current event + its oppgaver, for the public
    website signup page. Deliberately a separate, minimal endpoint rather
    than opening up EventViewSet/ShiftViewSet -- those embed full volunteer
    profiles (emails) in participants/leaders, which must stay private."""

    event = (
        Event.objects.filter(is_active=True)
        .prefetch_related("shifts", "shift_conflicts", "oppgave_slots", "oppgave_slots__skill")
        .first()
    )
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
    throttle_classes = [LoginRateThrottle]

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
    """Public self-registration: email, optionally a password. Returns JWT
    tokens immediately so the caller (website or app) can go straight into
    signing up for oppgaver without a separate login step.

    Gated by the active event's signup window (Event.signups_open) -- if
    there's no active event, or its window is closed, registration is
    refused rather than silently creating an account nobody can act on
    yet.

    Registering without a password (the normal path -- the public website
    deliberately doesn't collect one) also emails a link to set one later,
    so the volunteer can log in to the mobile app instead of being stuck
    with the one-time JWT this response returns. See PasswordSetupToken."""

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterRateThrottle]
    queryset = User.objects.all()

    def create(self, request, *args, **kwargs):
        event = Event.objects.filter(is_active=True).first()
        if not event:
            return Response({"detail": "No event configured yet."}, status=status.HTTP_404_NOT_FOUND)
        if not event.signups_open:
            return Response({"detail": "Signups are not open right now."}, status=status.HTTP_403_FORBIDDEN)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        if not user.has_usable_password():
            setup_token = PasswordSetupToken.objects.create(user=user)
            send_password_setup_email(setup_token)

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": MeSerializer(user, context={"request": request}).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def password_setup_preview(request, token):
    """Unauthenticated: what the set-password page shows before the
    volunteer picks a password -- which email, whether the link is still
    usable. Same shape as invite_preview."""

    setup_token = PasswordSetupToken.objects.select_related("user").filter(token=token).first()
    if not setup_token:
        return Response({"detail": "Link not found."}, status=status.HTTP_404_NOT_FOUND)
    return Response(PasswordSetupPreviewSerializer(setup_token).data)


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([PasswordSetupConfirmRateThrottle])
def set_password(request):
    """Unauthenticated: redeem a PasswordSetupToken and set a password in
    one step. Doesn't log the user in -- the whole point is they'll log in
    later from the mobile app, which is a separate device/session."""

    serializer = SetPasswordSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    setup_token = serializer.setup_token
    password = serializer.validated_data["password"]

    with transaction.atomic():
        user = setup_token.user
        user.set_password(password)
        user.save(update_fields=["password"])
        setup_token.used_at = timezone.now()
        setup_token.save(update_fields=["used_at"])

    return Response({"detail": "Password set. You can now log in with the app."})


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([PasswordSetupRequestRateThrottle])
def request_password_setup(request):
    """Unauthenticated: request a fresh password-setup/reset link, entered
    directly in the app (see the mobile app's set-password screen) instead
    of only ever being sent automatically at registration. Doubles as
    "forgot password" -- setting one for the first time and replacing one
    you've forgotten are the same operation here, so this issues a token
    regardless of whether the account already has a password.

    Always responds with the same generic message regardless of whether
    the email is registered or anything else about it -- otherwise this
    endpoint could be used to check which emails have accounts."""

    serializer = RequestPasswordSetupSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    email = serializer.validated_data["email"]

    user = User.objects.filter(email__iexact=email).first()
    if user:
        setup_token = PasswordSetupToken.objects.create(user=user)
        send_password_setup_email(setup_token)

    return Response({"detail": "If an account exists for that email, a link has been sent."})


def _rank_candidates(signups):
    """Order a user's candidate ShiftSignups for a day, best suggestion first.

    Critical shifts with a confirmed "yes" on relevant experience sort ahead
    of ones with an unconfirmed/"no" answer. Ties break on which oppgave
    slot is most understaffed relative to its own capacity, since that's
    the one most worth filling. This is intentionally a simple, explainable
    v1 — the admin always has final say via EventViewSet.assign.
    """

    def sort_key(signup):
        shift = signup.shift
        oppgave_slot = signup.oppgave_slot
        if shift.is_critical:
            if signup.has_relevant_experience is True:
                experience_score = 0
            elif signup.has_relevant_experience is None:
                experience_score = 1
            else:
                experience_score = 2
        else:
            experience_score = 0

        if oppgave_slot.capacity is not None:
            urgency = oppgave_slot.capacity - oppgave_slot.assigned_count
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
    event rather than requiring an event in the URL.

    Deliberately Membership-only, same as Event.is_owner/is_admin --
    created_by carries no permission weight (see
    OwnershipEnforcementRegressionTests.test_created_by_alone_grants_no_permissions).
    It used to also treat "created this event" as sufficient here, which
    combined with EventViewSet.perform_create being open to any
    authenticated user, meant anyone could self-escalate to "any event
    admin" by creating a throwaway event. perform_create is now gated by
    this same check, closing the loop."""

    if not user.is_authenticated:
        return False
    return Membership.objects.filter(user=user, role__in=[Membership.ROLE_OWNER, Membership.ROLE_ADMIN]).exists()


def _can_view_roster(user) -> bool:
    """Who can browse the full volunteer list (UserViewSet list/retrieve)
    rather than just their own /me. Broader than _is_any_event_admin --
    check-in staff and oppgave leaders legitimately need to look someone
    up (manual check-in, coordinating their team), not just owners/admins
    -- mirroring the admin dashboard's own canSeePool/canSeeInnsjekk gates
    (Layout.tsx), so the backend enforces exactly what the UI already
    implies rather than a stricter or looser rule."""

    if not user.is_authenticated:
        return False
    if _is_any_event_admin(user):
        return True
    if Membership.objects.filter(user=user, role=Membership.ROLE_CHECKIN_STAFF).exists():
        return True
    return Shift.objects.filter(leaders=user).exists()


def _revoke_all_sessions_for(user) -> None:
    """Force a person's existing app/dashboard sessions to stop working,
    rather than leaving them valid until they happen to expire on their
    own (up to REFRESH_TOKEN_LIFETIME) -- a JWT is otherwise stateless and
    doesn't care that the role it was issued under just got revoked. Used
    when an owner/admin role is removed (see remove_membership); relies on
    SIMPLE_JWT's ROTATE_REFRESH_TOKENS, which is what makes every issued
    refresh token show up in OutstandingToken in the first place."""

    for outstanding in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=outstanding)


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

    candidates = list(
        ShiftSignup.objects.filter(event=event, date=today, user=user).select_related(
            "shift", "oppgave_slot", "oppgave_slot__skill"
        )
    )

    if not candidates:
        return {"status": "pending_pool", "reason": "no_candidates", "candidates": []}

    # Auto-resolve only when there's exactly one candidate signup today --
    # same vakt AND same oppgave, unambiguous. A person interested in two
    # different oppgaver on the same vakt is just as ambiguous as two
    # different vakter now that a vakt alone no longer says which oppgave
    # they'd be doing -- both go to the pool for admin review.
    if len(candidates) == 1 and not candidates[0].shift.is_critical:
        oppgave_slot = candidates[0].oppgave_slot
        Assignment.objects.create(oppgave_slot=oppgave_slot, user=user, confirmed_by=performed_by)
        return {"status": "assigned", "shift": oppgave_slot.shift}

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
        payload["candidates"] = ShiftSignupSerializer(result["candidates"], many=True, context={"request": request}).data
        if result["reason"] == "no_candidates":
            payload["message"] = "Checked in. Not signed up for any vakt today — added to the pool for manual assignment."
        else:
            payload["message"] = "Checked in. Needs admin review to pick a vakt — added to the pool."
        status_code = status.HTTP_202_ACCEPTED

    return Response(payload, status=status_code)


def _event_year_label(event) -> str:
    """A stable per-event label for grouping oppgave history by year --
    falls back to the event's title when it has no date set."""

    if event.date:
        return str(event.date.year)
    return event.title


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def oppgave_history(request):
    """Cross-event history: for each distinct oppgave (Skill), across every
    event regardless of which one is active, how many people signed up vs.
    how many actually ended up assigned (i.e. showed up and were placed) --
    summed across every vakt that oppgave was offered on that year.

    Grouped by skill via OppgaveSlot, not by shift title -- shift titles
    are per-vakt ("Vakt 5"), not per-oppgave, so grouping by them used to
    conflate every oppgave offered on same-numbered vakter across different
    years instead of tracking the oppgave itself.

    The gap between signups and assigned is no-shows -- signing up doesn't
    mean showing up, and Assignment only happens at check-in time (see
    _resolve_checkin). fill_rate is assigned/signups; oversubscription_
    factor is its inverse, i.e. roughly how many signups to accept per
    seat you actually want filled, based on history. Only meaningful once
    a few years of real data exist -- with one or two, it's a rough
    starting point, not a forecast."""

    if not _is_any_event_admin(request.user):
        return Response({"detail": "Only an admin can view this."}, status=status.HTTP_403_FORBIDDEN)

    slots = OppgaveSlot.objects.select_related("event", "skill").annotate(
        signup_total=Count("signups", distinct=True),
        assigned_total=Count("assignments", distinct=True),
    )

    groups: dict = {}
    for slot in slots:
        key = slot.skill_id
        year = _event_year_label(slot.event)
        entry = groups.setdefault(
            key, {"title": slot.skill.name, "years": {}, "total_signups": 0, "total_assigned": 0}
        )
        yearly = entry["years"].setdefault(year, {"signups": 0, "assigned": 0})
        yearly["signups"] += slot.signup_total
        yearly["assigned"] += slot.assigned_total
        entry["total_signups"] += slot.signup_total
        entry["total_assigned"] += slot.assigned_total

    results = []
    for entry in groups.values():
        fill_rate = entry["total_assigned"] / entry["total_signups"] if entry["total_signups"] else None
        results.append(
            {
                "title": entry["title"],
                "years": [
                    {"year": year, "signups": v["signups"], "assigned": v["assigned"]}
                    for year, v in sorted(entry["years"].items())
                ],
                "total_signups": entry["total_signups"],
                "total_assigned": entry["total_assigned"],
                "fill_rate": round(fill_rate, 3) if fill_rate is not None else None,
                "oversubscription_factor": round(1 / fill_rate, 2) if fill_rate else None,
            }
        )

    results.sort(key=lambda r: r["title"].lower())
    return Response(results)


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        # list/retrieve is already restricted (see get_queryset) to either
        # your own record or a roster-viewing admin/staff/leader -- either
        # way, contact details are appropriate here. Other actions (update,
        # nested UserSerializer usages elsewhere for shift participants/
        # pool/assignments) stay on the plain minimal serializer.
        if self.action in ("list", "retrieve", "me"):
            return MeSerializer
        return UserSerializer

    def get_queryset(self):
        # list/retrieve used to be readable by anyone authenticated --
        # every volunteer's email and experience_notes, not just people
        # you happen to share a shift with. perform_update/perform_destroy
        # already restricted writes to "your own account"; this closes the
        # matching gap on reads. Non-staff callers just see themselves,
        # rather than a 403, so /api/users/<own id>/ keeps working as a
        # /me equivalent.
        queryset = super().get_queryset()
        if self.action in ("list", "retrieve") and not _can_view_roster(self.request.user):
            return queryset.filter(pk=self.request.user.pk)
        return queryset

    def perform_update(self, serializer):
        if serializer.instance != self.request.user:
            raise PermissionDenied("You can only update your own profile.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance != self.request.user and not _is_any_event_admin(self.request.user):
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
        # Requires already being an admin/owner of an existing event (or
        # Django staff, for bootstrapping the very first event in a fresh
        # deployment via the Django admin) -- previously any authenticated
        # user could create an event and become its owner, which (via
        # _is_any_event_admin) was enough to self-escalate into reading and
        # editing every volunteer's private admin notes.
        if not (self.request.user.is_staff or _is_any_event_admin(self.request.user)):
            raise PermissionDenied("Only an existing admin can create a new event.")
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
            .order_by("checked_in_at")
        )

        entries = []
        for arrival in arrivals:
            candidates = list(
                ShiftSignup.objects.filter(event=event, date=target_date, user=arrival.user).select_related(
                    "shift", "shift__event", "oppgave_slot", "oppgave_slot__skill"
                )
            )
            ranked = _rank_candidates(candidates)
            entries.append(
                {
                    "user": UserSerializer(arrival.user, context={"request": request}).data,
                    "checked_in_at": arrival.checked_in_at,
                    "candidates": ShiftSignupSerializer(ranked, many=True, context={"request": request}).data,
                    "suggested_oppgave_slot": (
                        OppgaveSlotSerializer(ranked[0].oppgave_slot, context={"request": request}).data
                        if ranked
                        else None
                    ),
                }
            )

        return Response(entries)

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        """Admin or the target oppgave's leader confirms a specific
        oppgave slot for a checked-in, unassigned volunteer — the final
        step out of the pool."""

        event = self.get_object()

        user_id = request.data.get("user_id")
        oppgave_slot_id = request.data.get("oppgave_slot_id")
        if not user_id or not oppgave_slot_id:
            return Response({"detail": "user_id and oppgave_slot_id are required"}, status=status.HTTP_400_BAD_REQUEST)

        attendee = get_object_or_404(User, pk=user_id)
        oppgave_slot = get_object_or_404(OppgaveSlot, pk=oppgave_slot_id, event=event)
        shift = oppgave_slot.shift

        if not (event.is_admin(request.user) or shift.is_led_by(request.user)):
            return Response(
                {"detail": "Only an admin or this vakt's leader can assign it."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not EventCheckIn.objects.filter(event=event, user=attendee, date=shift.date).exists():
            return Response({"detail": "This person has not checked in today."}, status=status.HTTP_400_BAD_REQUEST)

        if oppgave_slot.assigned_count >= (oppgave_slot.capacity or float("inf")):
            return Response(
                {"detail": "Denne oppgaven er full på denne vakten."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                assignment = Assignment.objects.create(oppgave_slot=oppgave_slot, user=attendee, confirmed_by=request.user)
        except IntegrityError:
            return Response(
                {"detail": "This person already has a confirmed vakt today."},
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

        removed_user = membership.user
        membership.delete()
        # Their role here is gone, but any session issued while they still
        # had it stays valid otherwise -- cut it off rather than leaving
        # that to chance.
        _revoke_all_sessions_for(removed_user)
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


def _conflicting_signup(user, shift):
    """The user's existing ShiftSignup in this event for a vakt that's been
    declared (by an admin, via ShiftConflict) to conflict with `shift`, if
    any. Deliberately not a computed time-overlap check -- an earlier
    version rejected any two vakter whose times overlapped, on the theory
    that the real site's "can't combine vakt 6+7 or 9+10" rule was just
    two instances of that. It wasn't: the real schedule also has vakt 5
    overlapping vakt 6, and vakt 8 overlapping vakt 9, neither of which
    the real rule forbids, and there's no overlap-duration threshold that
    separates the forbidden pairs (45 min) from the allowed ones
    (44-60 min). Which combinations are actually too demanding is a
    judgment call about workload, not something derivable from start/end
    times -- see ShiftConflict."""

    conflict_shift_ids = set(
        ShiftConflict.objects.filter(Q(shift_a=shift) | Q(shift_b=shift), event=shift.event).values_list(
            "shift_a_id", "shift_b_id"
        )
    )
    other_ids = {shift_id for pair in conflict_shift_ids for shift_id in pair if shift_id != shift.id}
    if not other_ids:
        return None
    signup = (
        ShiftSignup.objects.filter(event=shift.event, user=user, shift_id__in=other_ids)
        .select_related("shift")
        .first()
    )
    return signup.shift if signup else None


def _would_complete_three_consecutive(user, shift):
    """True if signing up for `shift` would give the user 3 (or more)
    numbered-consecutive vakter in the event's chronological order --
    the real site's burnout rule ("ikke mulig å melde seg på tre
    sammenhengende vakter"). "Consecutive" means adjacent positions in the
    event's full vakt sequence (date, start_time order), not merely
    touching in time."""

    ordered_ids = list(Shift.objects.filter(event=shift.event).order_by("date", "start_time").values_list("id", flat=True))
    if shift.id not in ordered_ids:
        return False
    index = ordered_ids.index(shift.id)

    signed_up_ids = set(
        ShiftSignup.objects.filter(event=shift.event, user=user).values_list("shift_id", flat=True)
    )
    signed_up_ids.add(shift.id)

    for window_start in (index - 2, index - 1, index):
        window = range(window_start, window_start + 3)
        if window_start < 0 or window.stop > len(ordered_ids):
            continue
        if all(ordered_ids[i] in signed_up_ids for i in window):
            return True
    return False


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
            raise PermissionDenied("Only an admin or this vakt's leader can edit it.")
        if "leaders" in serializer.validated_data and not is_admin:
            raise PermissionDenied("Only an admin can change this vakt's leaders.")
        serializer.save()

    def perform_destroy(self, instance):
        if not instance.event.is_admin(self.request.user):
            raise PermissionDenied("Only an admin can delete this vakt.")
        instance.delete()

    @action(detail=True, methods=["get"], url_path="assignments")
    def assignments(self, request, pk=None):
        shift = self.get_object()
        if not (shift.event.is_admin(request.user) or shift.is_led_by(request.user)):
            return Response(
                {"detail": "Only an admin or this vakt's leader can view assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = AssignmentSerializer(
            shift.assignments.select_related("user", "confirmed_by"), many=True, context={"request": request}
        )
        return Response(serializer.data)


class OppgaveSlotViewSet(viewsets.ModelViewSet):
    """Admin-curated (vakt, oppgave) combinations -- see OppgaveSlot.
    Signup/withdraw/cancel-assignment live here now (moved from
    ShiftViewSet) since a volunteer expresses interest in a specific
    oppgave on a vakt, not just the vakt itself."""

    queryset = OppgaveSlot.objects.select_related("event", "shift", "skill")
    serializer_class = OppgaveSlotSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        event_id = self.request.query_params.get("event")
        if event_id:
            queryset = queryset.filter(event_id=event_id)
        shift_id = self.request.query_params.get("shift")
        if shift_id:
            queryset = queryset.filter(shift_id=shift_id)
        return queryset

    def perform_create(self, serializer):
        shift = serializer.validated_data["shift"]
        if not shift.event.is_admin(self.request.user):
            raise PermissionDenied("Only an admin can add oppgave slots.")
        serializer.save()

    def perform_destroy(self, instance):
        if not instance.shift.event.is_admin(self.request.user):
            raise PermissionDenied("Only an admin can remove oppgave slots.")
        instance.delete()

    @action(detail=True, methods=["post"], url_path="signup")
    def signup(self, request, pk=None):
        """Express interest in this oppgave on this vakt. A user may hold
        several candidate signups for the same day, including more than
        one oppgave on the same vakt — the actual placement is resolved
        later, at check-in (see EventViewSet._resolve_checkin and .assign).
        If the oppgave is critical, the caller should ask for
        has_relevant_experience/experience_notes before submitting."""

        oppgave_slot = self.get_object()
        shift = oppgave_slot.shift
        user = request.user

        if oppgave_slot.is_full:
            return Response({"detail": "Denne oppgaven er full på denne vakten."}, status=status.HTTP_400_BAD_REQUEST)

        if ShiftSignup.objects.filter(oppgave_slot=oppgave_slot, user=user).exists():
            return Response(
                {"detail": "Allerede påmeldt denne oppgaven på denne vakten."}, status=status.HTTP_400_BAD_REQUEST
            )

        conflicting = _conflicting_signup(user, shift)
        if conflicting:
            return Response(
                {"detail": f"Denne vakten kan ikke kombineres med «{conflicting.title}», som du allerede er påmeldt til."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if _would_complete_three_consecutive(user, shift):
            return Response(
                {"detail": "Det er ikke mulig å melde seg på tre sammenhengende vakter."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        has_experience = request.data.get("has_relevant_experience")
        if isinstance(has_experience, str):
            has_experience = has_experience.lower() in {"1", "true", "yes", "on"}
        elif has_experience is not None:
            has_experience = bool(has_experience)

        try:
            with transaction.atomic():
                signup = ShiftSignup.objects.create(
                    oppgave_slot=oppgave_slot,
                    user=user,
                    has_relevant_experience=has_experience,
                    experience_notes=request.data.get("experience_notes", ""),
                )
        except IntegrityError:
            return Response(
                {"detail": "Allerede påmeldt denne oppgaven på denne vakten."}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(ShiftSignupSerializer(signup, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="withdraw")
    def withdraw(self, request, pk=None):
        oppgave_slot = self.get_object()
        deleted, _ = ShiftSignup.objects.filter(oppgave_slot=oppgave_slot, user=request.user).delete()
        if not deleted:
            return Response({"detail": "Not signed up for this oppgave."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OppgaveSlotSerializer(oppgave_slot, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="cancel-assignment")
    def cancel_assignment(self, request, pk=None):
        """Let a volunteer cancel their own confirmed assignment to this
        oppgave (plans changed after being placed)."""

        oppgave_slot = self.get_object()
        deleted, _ = Assignment.objects.filter(oppgave_slot=oppgave_slot, user=request.user).delete()
        if not deleted:
            return Response(
                {"detail": "You don't have a confirmed assignment for this oppgave."}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(OppgaveSlotSerializer(oppgave_slot, context={"request": request}).data)


class ShiftConflictViewSet(viewsets.ModelViewSet):
    """Admin-curated pairs of vakter that can't both be signed up for --
    see ShiftConflict for why this is data rather than a computed rule."""

    queryset = ShiftConflict.objects.select_related("shift_a", "shift_b")
    serializer_class = ShiftConflictSerializer
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
            raise PermissionDenied("Only an admin can declare vakt conflicts.")
        shift_a = serializer.validated_data["shift_a"]
        shift_b = serializer.validated_data["shift_b"]
        if shift_a == shift_b:
            raise ValidationError("A vakt can't conflict with itself.")
        if shift_a.event_id != event.id or shift_b.event_id != event.id:
            raise ValidationError("Both vakter must belong to the same event as the conflict.")
        serializer.save()

    def perform_destroy(self, instance):
        if not instance.event.is_admin(self.request.user):
            raise PermissionDenied("Only an admin can remove vakt conflicts.")
        instance.delete()


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
    """The shared oppgave catalogue every volunteer picks from at signup
    and every event reads from -- list/retrieve stays open to any
    volunteer (they need to browse it), but writes used to be too, letting
    any authenticated user rename or delete a skill out from under
    everyone else."""

    queryset = Skill.objects.all()
    serializer_class = SkillSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        if not _is_any_event_admin(self.request.user):
            raise PermissionDenied("Only an admin can add oppgaver.")
        serializer.save()

    def perform_update(self, serializer):
        if not _is_any_event_admin(self.request.user):
            raise PermissionDenied("Only an admin can edit oppgaver.")
        serializer.save()

    def perform_destroy(self, instance):
        if not _is_any_event_admin(self.request.user):
            raise PermissionDenied("Only an admin can delete oppgaver.")
        instance.delete()
