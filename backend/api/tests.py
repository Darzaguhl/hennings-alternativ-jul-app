import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

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


def valid_registration_payload(**overrides):
    """A complete, valid /api/register/ body -- the contact fields
    (name/phone/address/birthdate) are required by RegisterSerializer, so
    any test exercising a successful registration needs all of them
    present, not just email/password."""

    payload = {
        "email": "new.volunteer@example.com",
        "password": "correct horse battery staple",
        "first_name": "Kari",
        "last_name": "Nordmann",
        "phone": "12345678",
        "address": "Testveien 1, 0123 Oslo",
        "birthdate": "1990-05-15",
    }
    payload.update(overrides)
    return payload


def make_slot(shift, skill_name="Oppgave", **kwargs):
    """Create (or reuse) an OppgaveSlot for a shift -- the unit a
    volunteer signs up for/gets assigned to, mirroring how make_event
    mirrors real event creation. Distinct shifts can share the same skill
    name freely -- uniqueness is per (shift, skill), not global."""

    skill, _ = Skill.objects.get_or_create(name=skill_name)
    return OppgaveSlot.objects.create(shift=shift, skill=skill, **kwargs)


def make_event(**kwargs):
    """Create an Event plus the owner Membership its creator would get via
    EventViewSet.perform_create in real usage. is_owner() has no fallback
    to created_by (ownership is purely a Membership role, so it stays
    revocable/transferable -- see Event.is_owner), so tests that create
    events directly instead of going through the API need this to end up
    with the same permissions a real created event would have.

    Defaults is_active=True, since most tests just need a working event and
    aren't concerned with activation -- pass is_active=False explicitly for
    tests that are."""

    creator = kwargs.get("created_by")
    kwargs.setdefault("is_active", True)
    event = Event.objects.create(**kwargs)
    if creator is not None:
        Membership.objects.create(event=event, user=creator, role=Membership.ROLE_OWNER)
    return event


class ShiftSignupTests(TestCase):
    """Signup is now just a candidate shortlist: a user may hold several
    ShiftSignups for the same day. The old one-vakt-per-day exclusivity
    moved to Assignment (see AssignmentConstraintTests)."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        self.kitchen = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            criticality=Shift.CRITICALITY_CRITICAL,
            created_by=self.organizer,
        )
        self.hosting = Shift.objects.create(
            event=self.event,
            title="Vertskap",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(23, 0),
            created_by=self.organizer,
        )
        self.kitchen_slot = make_slot(self.kitchen)
        self.hosting_slot = make_slot(self.hosting)

    def test_signup_for_one_shift_succeeds(self):
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        self.assertEqual(self.kitchen.signup_count, 1)

    def test_can_shortlist_multiple_shifts_same_day(self):
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.volunteer)
        self.assertEqual(ShiftSignup.objects.filter(user=self.volunteer).count(), 2)

    def test_cannot_signup_twice_for_same_oppgave_slot(self):
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)

    def test_can_shortlist_multiple_oppgaver_on_the_same_shift(self):
        other_slot_same_shift = make_slot(self.kitchen, skill_name="Kaldkjøkken")
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        ShiftSignup.objects.create(oppgave_slot=other_slot_same_shift, user=self.volunteer)
        self.assertEqual(ShiftSignup.objects.filter(user=self.volunteer).count(), 2)

    def test_signup_denormalizes_shift_event_and_date_from_oppgave_slot(self):
        signup = ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        self.assertEqual(signup.shift_id, self.kitchen.id)
        self.assertEqual(signup.event_id, self.event.id)
        self.assertEqual(signup.date, self.kitchen.date)

    def test_experience_fields_default_to_unset(self):
        signup = ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        self.assertIsNone(signup.has_relevant_experience)
        self.assertEqual(signup.experience_notes, "")


class AssignmentConstraintTests(TestCase):
    """The one-vakt-per-day guarantee now lives on Assignment, not signup."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        self.kitchen = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            created_by=self.organizer,
        )
        self.hosting = Shift.objects.create(
            event=self.event,
            title="Vertskap",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(23, 0),
            created_by=self.organizer,
        )
        self.next_day = Shift.objects.create(
            event=self.event,
            title="Vakthold",
            date=datetime.date(2026, 12, 25),
            start_time=datetime.time(0, 0),
            end_time=datetime.time(6, 0),
            created_by=self.organizer,
        )
        self.kitchen_slot = make_slot(self.kitchen)
        self.hosting_slot = make_slot(self.hosting)
        self.next_day_slot = make_slot(self.next_day)

    def test_second_assignment_same_day_is_rejected_at_db_level(self):
        Assignment.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer, confirmed_by=self.organizer)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Assignment.objects.create(oppgave_slot=self.hosting_slot, user=self.volunteer, confirmed_by=self.organizer)

    def test_assignments_on_different_days_both_succeed(self):
        Assignment.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer, confirmed_by=self.organizer)
        Assignment.objects.create(oppgave_slot=self.next_day_slot, user=self.volunteer, confirmed_by=self.organizer)
        self.assertEqual(Assignment.objects.filter(user=self.volunteer).count(), 2)

    def test_assignment_denormalizes_shift_event_and_date_from_oppgave_slot(self):
        assignment = Assignment.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer, confirmed_by=self.organizer)
        self.assertEqual(assignment.shift_id, self.kitchen.id)
        self.assertEqual(assignment.event_id, self.event.id)
        self.assertEqual(assignment.date, self.kitchen.date)


class EventCheckinResolutionTests(TestCase):
    """Personal-QR check-in (admin scans a volunteer's badge). Resolution
    behavior: auto-assign only when there's exactly one non-critical
    candidate for today; otherwise the person is checked in and parked in
    the pool for an admin to resolve via /assign."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.qr = QRCode.objects.create(user=self.volunteer)
        self.event = make_event(
            title="Alternativ Jul",
            created_by=self.organizer,
            checkin_mode=Event.CHECKIN_MODE_PERSONAL_QR,
        )
        self.today = datetime.date.today()
        self.kitchen = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            criticality=Shift.CRITICALITY_CRITICAL,
            created_by=self.organizer,
        )
        self.hosting = Shift.objects.create(
            event=self.event,
            title="Vertskap",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(23, 0),
            created_by=self.organizer,
        )
        self.kitchen_slot = make_slot(self.kitchen)
        self.hosting_slot = make_slot(self.hosting)
        self.client = APIClient()
        self.client.force_authenticate(user=self.organizer)

    def _checkin(self):
        return self.client.post(
            f"/api/events/{self.event.id}/checkin/",
            {"user_code": self.qr.data},
            format="json",
        )

    def test_no_candidates_goes_to_pool(self):
        response = self._checkin()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "pending_pool")
        self.assertTrue(EventCheckIn.objects.filter(event=self.event, user=self.volunteer, date=self.today).exists())
        self.assertFalse(Assignment.objects.filter(user=self.volunteer).exists())

    def test_single_noncritical_candidate_auto_assigns(self):
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.volunteer)

        response = self._checkin()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "assigned")
        self.assertEqual(response.data["shift"]["id"], self.hosting.id)
        self.assertTrue(Assignment.objects.filter(shift=self.hosting, user=self.volunteer).exists())

    def test_single_critical_candidate_goes_to_pool_not_auto_assigned(self):
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer, has_relevant_experience=True)

        response = self._checkin()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "pending_pool")
        self.assertFalse(Assignment.objects.exists())

    def test_multiple_candidates_goes_to_pool(self):
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.volunteer)
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)

        response = self._checkin()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "pending_pool")
        self.assertEqual(len(response.data["candidates"]), 2)

    def test_scanning_again_after_assignment_reports_already_assigned(self):
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.volunteer)
        self._checkin()

        response = self._checkin()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "already_assigned")
        self.assertEqual(Assignment.objects.filter(user=self.volunteer).count(), 1)

    def test_wrong_checkin_mode_is_rejected(self):
        self.event.checkin_mode = Event.CHECKIN_MODE_EVENT_QR
        self.event.save()

        response = self._checkin()

        self.assertEqual(response.status_code, 400)

    def test_only_event_creator_can_checkin(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self._checkin()
        self.assertEqual(response.status_code, 403)

    def test_unknown_qr_code_returns_404(self):
        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/",
            {"user_code": "not-a-real-code"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_manual_checkin_by_user_id_works(self):
        """Check-in staff picking someone from a list (no QR scan) --
        e.g. the admin dashboard's manual check-in flow."""

        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.volunteer)
        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/",
            {"user_id": self.volunteer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "assigned")
        self.assertTrue(EventCheckIn.objects.filter(event=self.event, user=self.volunteer).exists())

    def test_manual_checkin_by_user_id_ignores_checkin_mode(self):
        """Unlike user_code (tied to personal_qr mode), an admin manually
        checking someone in works regardless of the event's checkin_mode."""

        self.event.checkin_mode = Event.CHECKIN_MODE_EVENT_QR
        self.event.save()

        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/",
            {"user_id": self.volunteer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 202)  # pending_pool, no candidates signed up

    def test_manual_checkin_requires_checkin_staff(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/",
            {"user_id": self.organizer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_checkin_without_user_id_or_user_code_is_rejected(self):
        response = self.client.post(f"/api/events/{self.event.id}/checkin/", {}, format="json")
        self.assertEqual(response.status_code, 400)


class SelfCheckinTests(TestCase):
    """Event-QR self check-in: the volunteer scans one shared code, no
    admin scanning involved. Same resolution logic as personal-QR."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.event = make_event(
            title="Alternativ Jul",
            created_by=self.organizer,
            checkin_mode=Event.CHECKIN_MODE_EVENT_QR,
        )
        self.today = datetime.date.today()
        self.hosting = Shift.objects.create(
            event=self.event,
            title="Vertskap",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(23, 0),
            created_by=self.organizer,
        )
        self.hosting_slot = make_slot(self.hosting)
        self.client = APIClient()
        self.client.force_authenticate(user=self.volunteer)

    def test_volunteer_can_self_checkin(self):
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.volunteer)

        response = self.client.post(f"/api/events/{self.event.id}/self-checkin/")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "assigned")
        self.assertTrue(EventCheckIn.objects.filter(event=self.event, user=self.volunteer).exists())

    def test_wrong_checkin_mode_is_rejected(self):
        self.event.checkin_mode = Event.CHECKIN_MODE_PERSONAL_QR
        self.event.save()

        response = self.client.post(f"/api/events/{self.event.id}/self-checkin/")

        self.assertEqual(response.status_code, 400)


class PoolAndAssignTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.cook = User.objects.create_user(username="cook", password="pw")
        self.newbie = User.objects.create_user(username="newbie", password="pw")
        self.event = make_event(
            title="Alternativ Jul",
            created_by=self.organizer,
            checkin_mode=Event.CHECKIN_MODE_EVENT_QR,
        )
        self.today = datetime.date.today()
        self.kitchen = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            criticality=Shift.CRITICALITY_CRITICAL,
            capacity=2,
            created_by=self.organizer,
        )
        self.hosting = Shift.objects.create(
            event=self.event,
            title="Vertskap",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(23, 0),
            created_by=self.organizer,
        )
        self.kitchen_slot = make_slot(self.kitchen, capacity=2)
        self.hosting_slot = make_slot(self.hosting)
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.organizer)

    def _self_checkin_as(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post(f"/api/events/{self.event.id}/self-checkin/")

    def test_pool_lists_checked_in_unassigned_users_with_suggestion(self):
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.cook, has_relevant_experience=True)
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.cook)
        self._self_checkin_as(self.cook)

        response = self.admin_client.get(f"/api/events/{self.event.id}/pool/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["user"]["username"], "cook")
        self.assertEqual(len(entry["candidates"]), 2)
        # experienced + critical should be suggested ahead of the plain hosting slot
        self.assertEqual(entry["suggested_oppgave_slot"]["id"], self.kitchen_slot.id)

    def test_assigned_users_disappear_from_pool(self):
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.newbie)
        self._self_checkin_as(self.newbie)  # auto-assigns, single non-critical candidate

        response = self.admin_client.get(f"/api/events/{self.event.id}/pool/")

        self.assertEqual(response.data, [])

    def test_admin_can_assign_from_pool(self):
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.cook, has_relevant_experience=True)
        self._self_checkin_as(self.cook)

        response = self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.cook.id, "oppgave_slot_id": self.kitchen_slot.id},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Assignment.objects.filter(oppgave_slot=self.kitchen_slot, user=self.cook).exists())

    def test_cannot_assign_someone_who_has_not_checked_in(self):
        response = self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.newbie.id, "oppgave_slot_id": self.hosting_slot.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_assign_beyond_slot_capacity(self):
        first = User.objects.create_user(username="first-assignee", password="pw")
        second = User.objects.create_user(username="second-assignee", password="pw")
        third = User.objects.create_user(username="third-assignee", password="pw")
        for user in (first, second, third):
            EventCheckIn.objects.create(event=self.event, user=user, date=self.today)

        self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": first.id, "oppgave_slot_id": self.kitchen_slot.id},
            format="json",
        )
        self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": second.id, "oppgave_slot_id": self.kitchen_slot.id},
            format="json",
        )
        # kitchen_slot has capacity=2 -- a third assignment must be rejected.
        response = self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": third.id, "oppgave_slot_id": self.kitchen_slot.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Assignment.objects.filter(oppgave_slot=self.kitchen_slot).count(), 2)

    def test_cannot_double_assign_same_day(self):
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.newbie)
        self._self_checkin_as(self.newbie)  # auto-assigned to hosting already

        response = self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.newbie.id, "oppgave_slot_id": self.kitchen_slot.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_only_event_creator_can_view_pool_or_assign(self):
        other_client = APIClient()
        other_client.force_authenticate(user=self.cook)

        pool_response = other_client.get(f"/api/events/{self.event.id}/pool/")
        assign_response = other_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.newbie.id, "oppgave_slot_id": self.hosting_slot.id},
            format="json",
        )

        self.assertEqual(pool_response.status_code, 403)
        self.assertEqual(assign_response.status_code, 403)


class OppgaveSlotSignupEndpointTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        self.kitchen = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            criticality=Shift.CRITICALITY_CRITICAL,
            created_by=self.organizer,
        )
        # Deliberately not overlapping with self.kitchen (18:00-22:00) --
        # test_can_signup_for_multiple_shifts_same_day_now relies on both
        # being joinable together, which the overlap check in
        # OppgaveSlotSignupValidationTests below would otherwise correctly
        # reject.
        self.hosting = Shift.objects.create(
            event=self.event,
            title="Vertskap",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(9, 0),
            end_time=datetime.time(13, 0),
            created_by=self.organizer,
        )
        self.kitchen_slot = make_slot(self.kitchen, skill_name="Kokk")
        self.hosting_slot = make_slot(self.hosting, skill_name="Vertskap")
        self.client = APIClient()
        self.client.force_authenticate(user=self.volunteer)

    def test_signup_succeeds(self):
        response = self.client.post(f"/api/oppgave-slots/{self.hosting_slot.id}/signup/")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(ShiftSignup.objects.filter(oppgave_slot=self.hosting_slot, user=self.volunteer).exists())

    def test_signup_for_critical_shift_captures_experience_answer(self):
        response = self.client.post(
            f"/api/oppgave-slots/{self.kitchen_slot.id}/signup/",
            {"has_relevant_experience": True, "experience_notes": "5 years as a chef"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        signup = ShiftSignup.objects.get(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        self.assertTrue(signup.has_relevant_experience)
        self.assertEqual(signup.experience_notes, "5 years as a chef")

    def test_can_signup_for_multiple_shifts_same_day_now(self):
        self.client.post(
            f"/api/oppgave-slots/{self.kitchen_slot.id}/signup/", {"has_relevant_experience": False}, format="json"
        )
        response = self.client.post(f"/api/oppgave-slots/{self.hosting_slot.id}/signup/")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(ShiftSignup.objects.filter(user=self.volunteer).count(), 2)

    def test_signup_for_multiple_oppgaver_on_the_same_shift_is_allowed(self):
        # A volunteer may be a candidate for more than one oppgave on the
        # same vakt -- exactly one is resolved into an Assignment later.
        other_slot_same_shift = make_slot(self.kitchen, skill_name="Kaldkjøkken")

        self.client.post(f"/api/oppgave-slots/{self.kitchen_slot.id}/signup/")
        response = self.client.post(f"/api/oppgave-slots/{other_slot_same_shift.id}/signup/")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(ShiftSignup.objects.filter(user=self.volunteer, shift=self.kitchen).count(), 2)

    def test_signup_full_oppgave_slot_is_rejected(self):
        self.kitchen_slot.capacity = 0
        self.kitchen_slot.save()

        response = self.client.post(f"/api/oppgave-slots/{self.kitchen_slot.id}/signup/")

        self.assertEqual(response.status_code, 400)

    def test_withdraw_removes_signup(self):
        self.client.post(f"/api/oppgave-slots/{self.hosting_slot.id}/signup/")

        response = self.client.post(f"/api/oppgave-slots/{self.hosting_slot.id}/withdraw/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ShiftSignup.objects.filter(oppgave_slot=self.hosting_slot, user=self.volunteer).exists())


class OppgaveSlotSignupValidationTests(TestCase):
    """The real signup rules from the live site (read during planning):
    admin-declared vakt conflict pairs and no three numbered-consecutive
    vakter -- both still enforced at the vakt level regardless of which
    oppgave is picked. See _conflicting_signup /
    _would_complete_three_consecutive in views.py.

    _conflicting_signup used to be a computed time-overlap check instead
    of ShiftConflict -- test_overlapping_but_undeclared_shifts_are_allowed
    below is the regression test for why that was wrong (see its
    docstring and _conflicting_signup's)."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="validation-organizer", password="pw")
        self.volunteer = User.objects.create_user(username="validation-volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        self.client = APIClient()
        self.client.force_authenticate(user=self.volunteer)

    def _shift(self, date, start, end, **kwargs):
        return Shift.objects.create(
            event=self.event, title="Vakt", date=date, start_time=start, end_time=end,
            created_by=self.organizer, **kwargs
        )

    def _slot(self, shift):
        return make_slot(shift)

    def test_declared_conflict_is_rejected(self):
        # Mirrors the real vakt 6 (24 Des 22:00 - 25 Des 09:15) / vakt 7
        # (25 Des 08:30-16:00) pair, which the organizers have explicitly
        # declared can't be combined.
        night = self._shift(datetime.date(2026, 12, 24), datetime.time(22, 0), datetime.time(9, 15))
        morning = self._shift(datetime.date(2026, 12, 25), datetime.time(8, 30), datetime.time(16, 0))
        ShiftConflict.objects.create(event=self.event, shift_a=night, shift_b=morning)
        night_slot, morning_slot = self._slot(night), self._slot(morning)

        self.client.post(f"/api/oppgave-slots/{night_slot.id}/signup/")
        response = self.client.post(f"/api/oppgave-slots/{morning_slot.id}/signup/")

        self.assertEqual(response.status_code, 400)
        self.assertIn("kan ikke kombineres", response.data["detail"])
        self.assertFalse(ShiftSignup.objects.filter(oppgave_slot=morning_slot, user=self.volunteer).exists())

    def test_declared_conflict_is_rejected_in_either_direction(self):
        # Signing up for shift_a first, then shift_b, must be blocked too
        # -- ShiftConflict isn't directional.
        a = self._shift(datetime.date(2026, 12, 20), datetime.time(10, 0), datetime.time(14, 0))
        b = self._shift(datetime.date(2026, 12, 20), datetime.time(15, 0), datetime.time(19, 0))
        ShiftConflict.objects.create(event=self.event, shift_a=a, shift_b=b)
        a_slot, b_slot = self._slot(a), self._slot(b)

        self.client.post(f"/api/oppgave-slots/{b_slot.id}/signup/")
        response = self.client.post(f"/api/oppgave-slots/{a_slot.id}/signup/")

        self.assertEqual(response.status_code, 400)

    def test_overlapping_but_undeclared_shifts_are_allowed(self):
        # Real regression: the schedule has vakt 5 (24 Des 13:00-23:00)
        # genuinely overlapping vakt 6 (24 Des 22:00 - 25 Des 09:15) by an
        # hour, but the organizers don't forbid that combination -- only
        # vakt 6+7 and 9+10 are declared conflicts. A prior version of
        # this check computed time overlap directly and incorrectly
        # blocked this too.
        day = self._shift(datetime.date(2026, 12, 24), datetime.time(13, 0), datetime.time(23, 0))
        night = self._shift(datetime.date(2026, 12, 24), datetime.time(22, 0), datetime.time(9, 15))
        day_slot, night_slot = self._slot(day), self._slot(night)

        self.client.post(f"/api/oppgave-slots/{day_slot.id}/signup/")
        response = self.client.post(f"/api/oppgave-slots/{night_slot.id}/signup/")

        self.assertEqual(response.status_code, 201)

    def test_unrelated_pair_with_no_conflict_row_is_unaffected(self):
        a = self._shift(datetime.date(2026, 12, 20), datetime.time(10, 0), datetime.time(14, 0))
        b = self._shift(datetime.date(2026, 12, 21), datetime.time(10, 0), datetime.time(14, 0))
        other_a = self._shift(datetime.date(2026, 12, 24), datetime.time(9, 0), datetime.time(13, 0))
        other_b = self._shift(datetime.date(2026, 12, 24), datetime.time(13, 0), datetime.time(23, 0))
        ShiftConflict.objects.create(event=self.event, shift_a=other_a, shift_b=other_b)
        a_slot, b_slot = self._slot(a), self._slot(b)

        self.client.post(f"/api/oppgave-slots/{a_slot.id}/signup/")
        response = self.client.post(f"/api/oppgave-slots/{b_slot.id}/signup/")

        self.assertEqual(response.status_code, 201)

    def test_three_consecutive_shifts_rejected(self):
        a = self._shift(datetime.date(2026, 12, 20), datetime.time(10, 0), datetime.time(14, 0))
        b = self._shift(datetime.date(2026, 12, 20), datetime.time(14, 0), datetime.time(18, 0))
        c = self._shift(datetime.date(2026, 12, 20), datetime.time(18, 0), datetime.time(22, 0))
        a_slot, b_slot, c_slot = self._slot(a), self._slot(b), self._slot(c)

        self.client.post(f"/api/oppgave-slots/{a_slot.id}/signup/")
        self.client.post(f"/api/oppgave-slots/{b_slot.id}/signup/")
        response = self.client.post(f"/api/oppgave-slots/{c_slot.id}/signup/")

        self.assertEqual(response.status_code, 400)
        self.assertIn("tre sammenhengende", response.data["detail"])
        self.assertFalse(ShiftSignup.objects.filter(oppgave_slot=c_slot, user=self.volunteer).exists())

    def test_two_consecutive_shifts_allowed(self):
        a = self._shift(datetime.date(2026, 12, 20), datetime.time(10, 0), datetime.time(14, 0))
        b = self._shift(datetime.date(2026, 12, 20), datetime.time(14, 0), datetime.time(18, 0))
        a_slot, b_slot = self._slot(a), self._slot(b)

        self.client.post(f"/api/oppgave-slots/{a_slot.id}/signup/")
        response = self.client.post(f"/api/oppgave-slots/{b_slot.id}/signup/")

        self.assertEqual(response.status_code, 201)

    def test_non_consecutive_shifts_allowed(self):
        a = self._shift(datetime.date(2026, 12, 20), datetime.time(10, 0), datetime.time(14, 0))
        b = self._shift(datetime.date(2026, 12, 20), datetime.time(14, 0), datetime.time(18, 0))
        c = self._shift(datetime.date(2026, 12, 20), datetime.time(18, 0), datetime.time(22, 0))
        a_slot, c_slot = self._slot(a), self._slot(c)
        self._slot(b)

        self.client.post(f"/api/oppgave-slots/{a_slot.id}/signup/")
        response = self.client.post(f"/api/oppgave-slots/{c_slot.id}/signup/")

        self.assertEqual(response.status_code, 201)


class OppgaveSlotAdminTests(TestCase):
    """Only an event admin/owner can declare or remove an oppgave slot --
    same gating pattern as ShiftConflict's admin-only writes."""

    def setUp(self):
        self.admin = User.objects.create_user(username="slot-admin", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.admin)
        self.shift = Shift.objects.create(
            event=self.event, title="Vakt 5", date=datetime.date(2026, 12, 24),
            start_time=datetime.time(13, 0), end_time=datetime.time(23, 0), created_by=self.admin,
        )
        self.skill = Skill.objects.create(name="Kokk")
        self.client = APIClient()

    def test_admin_can_create_a_slot(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/oppgave-slots/",
            {"shift": self.shift.id, "skill": self.skill.id, "capacity": 4},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(OppgaveSlot.objects.filter(shift=self.shift, skill=self.skill).exists())

    def test_admin_can_delete_a_slot(self):
        slot = make_slot(self.shift, skill_name=self.skill.name)
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(f"/api/oppgave-slots/{slot.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(OppgaveSlot.objects.filter(pk=slot.pk).exists())

    def test_plain_volunteer_cannot_create_a_slot(self):
        bystander = User.objects.create_user(username="slot-bystander", password="pw")
        self.client.force_authenticate(user=bystander)
        response = self.client.post(
            "/api/oppgave-slots/",
            {"shift": self.shift.id, "skill": self.skill.id, "capacity": 4},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(OppgaveSlot.objects.filter(shift=self.shift, skill=self.skill).exists())

    def test_plain_volunteer_cannot_delete_a_slot(self):
        slot = make_slot(self.shift, skill_name=self.skill.name)
        bystander = User.objects.create_user(username="slot-bystander-2", password="pw")
        self.client.force_authenticate(user=bystander)
        response = self.client.delete(f"/api/oppgave-slots/{slot.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(OppgaveSlot.objects.filter(pk=slot.pk).exists())

    def test_slot_is_full_once_signup_capacity_reached(self):
        slot = make_slot(self.shift, skill_name=self.skill.name, capacity=1)
        volunteer = User.objects.create_user(username="slot-volunteer", password="pw")
        self.assertFalse(slot.is_full)

        ShiftSignup.objects.create(oppgave_slot=slot, user=volunteer)

        slot.refresh_from_db()
        self.assertTrue(slot.is_full)

    def test_slot_with_no_capacity_is_never_full(self):
        slot = make_slot(self.shift, skill_name=self.skill.name, capacity=None)
        for i in range(5):
            volunteer = User.objects.create_user(username=f"slot-volunteer-{i}", password="pw")
            ShiftSignup.objects.create(oppgave_slot=slot, user=volunteer)

        slot.refresh_from_db()
        self.assertFalse(slot.is_full)


class X1SignupTests(TestCase):
    """Vaktleder (X1) opt-in: 'Kombinasjon med påmelding på minst tre
    andre vakter, og derav minst to av vaktene fra 5-10.' Not a real
    time-bound vakt -- see X1Signup's docstring."""

    def setUp(self):
        self.admin = User.objects.create_user(username="x1-admin", password="pw")
        self.volunteer = User.objects.create_user(username="x1-volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.admin)
        self.client = APIClient()
        self.client.force_authenticate(user=self.volunteer)

    def _shift(self, title, vakt_number=None):
        return Shift.objects.create(
            event=self.event, title=title, date=datetime.date(2026, 12, 24),
            start_time=datetime.time(10, 0), end_time=datetime.time(14, 0),
            created_by=self.admin, vakt_number=vakt_number,
        )

    def _sign_up_for(self, shift):
        slot = make_slot(shift)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.volunteer)

    def test_rejected_with_fewer_than_three_vakt_signups(self):
        self._sign_up_for(self._shift("Vakt 5", vakt_number=5))
        self._sign_up_for(self._shift("Vakt 6", vakt_number=6))

        response = self.client.post("/api/x1-signups/", {"event": self.event.id}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(X1Signup.objects.filter(event=self.event, user=self.volunteer).exists())

    def test_rejected_with_three_vakter_but_only_one_from_5_to_10(self):
        self._sign_up_for(self._shift("Vakt 5", vakt_number=5))
        self._sign_up_for(self._shift("Vakt 1.A", vakt_number=None))
        self._sign_up_for(self._shift("Vakt 12", vakt_number=12))

        response = self.client.post("/api/x1-signups/", {"event": self.event.id}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(X1Signup.objects.filter(event=self.event, user=self.volunteer).exists())

    def test_accepted_with_three_vakter_and_two_from_5_to_10(self):
        self._sign_up_for(self._shift("Vakt 5", vakt_number=5))
        self._sign_up_for(self._shift("Vakt 6", vakt_number=6))
        self._sign_up_for(self._shift("Vakt 1.A", vakt_number=None))

        response = self.client.post("/api/x1-signups/", {"event": self.event.id}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertTrue(X1Signup.objects.filter(event=self.event, user=self.volunteer).exists())

    def test_rejected_when_already_signed_up(self):
        self._sign_up_for(self._shift("Vakt 5", vakt_number=5))
        self._sign_up_for(self._shift("Vakt 6", vakt_number=6))
        self._sign_up_for(self._shift("Vakt 7", vakt_number=7))
        X1Signup.objects.create(event=self.event, user=self.volunteer)

        response = self.client.post("/api/x1-signups/", {"event": self.event.id}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(X1Signup.objects.filter(event=self.event, user=self.volunteer).count(), 1)

    def test_only_counts_the_requesting_users_own_signups(self):
        other = User.objects.create_user(username="x1-other", password="pw")
        shift_a, shift_b, shift_c = self._shift("Vakt 5", 5), self._shift("Vakt 6", 6), self._shift("Vakt 7", 7)
        for shift in (shift_a, shift_b, shift_c):
            slot = make_slot(shift)
            ShiftSignup.objects.create(oppgave_slot=slot, user=other)
        # self.volunteer (the requester) has none of these -- other's
        # signups must not count toward the requester's own eligibility.

        response = self.client.post("/api/x1-signups/", {"event": self.event.id}, format="json")

        self.assertEqual(response.status_code, 400)

    def test_plain_volunteer_only_sees_their_own_x1_signups(self):
        other = User.objects.create_user(username="x1-other-2", password="pw")
        X1Signup.objects.create(event=self.event, user=other)
        X1Signup.objects.create(event=self.event, user=self.volunteer)

        response = self.client.get("/api/x1-signups/", {"event": self.event.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["user"], self.volunteer.id)

    def test_admin_sees_everyones_x1_signups(self):
        other = User.objects.create_user(username="x1-other-3", password="pw")
        X1Signup.objects.create(event=self.event, user=other)
        X1Signup.objects.create(event=self.event, user=self.volunteer)

        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        response = admin_client.get("/api/x1-signups/", {"event": self.event.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_can_withdraw_own_x1_signup(self):
        signup = X1Signup.objects.create(event=self.event, user=self.volunteer)
        response = self.client.delete(f"/api/x1-signups/{signup.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(X1Signup.objects.filter(pk=signup.pk).exists())

    def test_cannot_withdraw_someone_elses_x1_signup(self):
        # get_queryset already scopes a non-admin to their own X1Signups
        # (same as test_plain_volunteer_only_sees_their_own_x1_signups), so
        # someone else's row 404s rather than reaching perform_destroy's
        # ownership check -- also means it never leaks whether the row
        # exists.
        other = User.objects.create_user(username="x1-other-4", password="pw")
        signup = X1Signup.objects.create(event=self.event, user=other)
        response = self.client.delete(f"/api/x1-signups/{signup.id}/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(X1Signup.objects.filter(pk=signup.pk).exists())


class ShiftConflictAdminTests(TestCase):
    """Only an event admin/owner can declare or remove a vakt conflict --
    same gating pattern as Skill's admin-only writes."""

    def setUp(self):
        self.admin = User.objects.create_user(username="conflict-admin", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.admin)
        self.shift_a = Shift.objects.create(
            event=self.event, title="Vakt 6", date=datetime.date(2026, 12, 24),
            start_time=datetime.time(22, 0), end_time=datetime.time(9, 15), created_by=self.admin,
        )
        self.shift_b = Shift.objects.create(
            event=self.event, title="Vakt 7", date=datetime.date(2026, 12, 25),
            start_time=datetime.time(8, 30), end_time=datetime.time(16, 0), created_by=self.admin,
        )
        self.client = APIClient()

    def test_admin_can_create_a_conflict(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/shift-conflicts/",
            {"event": self.event.id, "shift_a": self.shift_a.id, "shift_b": self.shift_b.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(ShiftConflict.objects.filter(shift_a=self.shift_a, shift_b=self.shift_b).exists())

    def test_admin_can_delete_a_conflict(self):
        conflict = ShiftConflict.objects.create(event=self.event, shift_a=self.shift_a, shift_b=self.shift_b)
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(f"/api/shift-conflicts/{conflict.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(ShiftConflict.objects.filter(pk=conflict.pk).exists())

    def test_plain_volunteer_cannot_create_a_conflict(self):
        bystander = User.objects.create_user(username="conflict-bystander", password="pw")
        self.client.force_authenticate(user=bystander)
        response = self.client.post(
            "/api/shift-conflicts/",
            {"event": self.event.id, "shift_a": self.shift_a.id, "shift_b": self.shift_b.id},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ShiftConflict.objects.filter(shift_a=self.shift_a, shift_b=self.shift_b).exists())

    def test_plain_volunteer_cannot_delete_a_conflict(self):
        conflict = ShiftConflict.objects.create(event=self.event, shift_a=self.shift_a, shift_b=self.shift_b)
        bystander = User.objects.create_user(username="conflict-bystander-2", password="pw")
        self.client.force_authenticate(user=bystander)
        response = self.client.delete(f"/api/shift-conflicts/{conflict.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ShiftConflict.objects.filter(pk=conflict.pk).exists())


class UserSkillsTests(TestCase):
    """User.skills is kept on the model but no longer exposed or written
    via the API -- oppgave interest is now expressed per (vakt, oppgave)
    via OppgaveSlotViewSet.signup, not a blanket tag on the user."""

    def test_user_can_have_skills_and_experience_notes(self):
        user = User.objects.create_user(username="volunteer", password="pw")
        cooking = Skill.objects.create(name="Kokk")
        first_aid = Skill.objects.create(name="Førstehjelp")
        user.skills.set([cooking, first_aid])
        user.experience_notes = "10 years as a professional chef"
        user.save()

        self.assertEqual(set(user.skills.values_list("name", flat=True)), {"Kokk", "Førstehjelp"})

    def test_me_endpoint_returns_experience_notes_but_not_skills(self):
        user = User.objects.create_user(username="volunteer", password="pw")
        skill = Skill.objects.create(name="Vekter")
        user.skills.add(skill)
        user.experience_notes = "Security guard for 3 years"
        user.save()

        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get("/api/users/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["experience_notes"], "Security guard for 3 years")
        self.assertNotIn("skills", response.data)


class MeParticipationYearsTests(TestCase):
    """MeSerializer.participation_years is derived from real Assignment
    history (checked in AND placed in an oppgave -- see _resolve_checkin),
    not self-reported -- this is what lets the signup page's
    returning-volunteer login show real prior-year participation."""

    def test_participation_years_derived_from_assignment_history(self):
        organizer = User.objects.create_user(username="organizer", password="pw")
        volunteer = User.objects.create_user(username="volunteer", password="pw")

        event_2024 = make_event(
            title="Alternativ Jul 2024", created_by=organizer,
            date=datetime.datetime(2024, 12, 20, tzinfo=datetime.timezone.utc),
        )
        event_2025 = make_event(
            title="Alternativ Jul 2025", created_by=organizer, is_active=False,
            date=datetime.datetime(2025, 12, 20, tzinfo=datetime.timezone.utc),
        )
        shift_2024 = Shift.objects.create(
            event=event_2024, title="Vakt 5", date=datetime.date(2024, 12, 24),
            start_time=datetime.time(13, 0), end_time=datetime.time(23, 0), created_by=organizer,
        )
        shift_2025 = Shift.objects.create(
            event=event_2025, title="Vakt 5", date=datetime.date(2025, 12, 24),
            start_time=datetime.time(13, 0), end_time=datetime.time(23, 0), created_by=organizer,
        )
        Assignment.objects.create(oppgave_slot=make_slot(shift_2024), user=volunteer, confirmed_by=organizer)
        Assignment.objects.create(oppgave_slot=make_slot(shift_2025), user=volunteer, confirmed_by=organizer)

        client = APIClient()
        client.force_authenticate(user=volunteer)
        response = client.get("/api/users/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["participation_years"], ["2024", "2025"])

    def test_participation_years_empty_with_no_assignment_history(self):
        volunteer = User.objects.create_user(username="no-history", password="pw")
        client = APIClient()
        client.force_authenticate(user=volunteer)
        response = client.get("/api/users/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["participation_years"], [])

    def test_signup_alone_does_not_count_as_participation(self):
        # A no-show (signed up, never checked in/assigned) must not show
        # up as "you were here" -- only a real Assignment counts.
        organizer = User.objects.create_user(username="organizer2", password="pw")
        volunteer = User.objects.create_user(username="no-show", password="pw")
        event = make_event(
            title="Alternativ Jul 2026", created_by=organizer,
            date=datetime.datetime(2026, 12, 20, tzinfo=datetime.timezone.utc),
        )
        shift = Shift.objects.create(
            event=event, title="Vakt 5", date=datetime.date(2026, 12, 24),
            start_time=datetime.time(13, 0), end_time=datetime.time(23, 0), created_by=organizer,
        )
        ShiftSignup.objects.create(oppgave_slot=make_slot(shift), user=volunteer)

        client = APIClient()
        client.force_authenticate(user=volunteer)
        response = client.get("/api/users/me/")

        self.assertEqual(response.data["participation_years"], [])


class AdminNoteTests(TestCase):
    """admin_notes is private staff-only commentary on a volunteer (e.g.
    behavior from previous years) -- must never leak via the regular
    UserSerializer, and only event admins/owners can view or edit it."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.admin = User.objects.create_user(username="admin", password="pw")
        self.checkin_staff = User.objects.create_user(username="staff", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.owner)
        Membership.objects.create(event=self.event, user=self.admin, role=Membership.ROLE_ADMIN)
        Membership.objects.create(event=self.event, user=self.checkin_staff, role=Membership.ROLE_CHECKIN_STAFF)
        self.volunteer.admin_notes = "No-showed for an assigned vakt in 2025."
        self.volunteer.save()

    def test_event_admin_can_read_notes(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.get(f"/api/users/{self.volunteer.id}/notes/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["admin_notes"], "No-showed for an assigned vakt in 2025.")

    def test_owner_can_write_notes(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.patch(
            f"/api/users/{self.volunteer.id}/notes/", {"admin_notes": "Great with kids, ask for again."}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.volunteer.refresh_from_db()
        self.assertEqual(self.volunteer.admin_notes, "Great with kids, ask for again.")

    def test_checkin_staff_cannot_view_or_edit_notes(self):
        client = APIClient()
        client.force_authenticate(user=self.checkin_staff)
        response = client.get(f"/api/users/{self.volunteer.id}/notes/")
        self.assertEqual(response.status_code, 403)

    def test_plain_volunteer_cannot_view_or_edit_notes(self):
        client = APIClient()
        client.force_authenticate(user=self.volunteer)
        response = client.get(f"/api/users/{self.volunteer.id}/notes/")
        self.assertEqual(response.status_code, 403)

    def test_admin_notes_never_appear_on_regular_user_serializer(self):
        client = APIClient()
        client.force_authenticate(user=self.volunteer)
        response = client.get("/api/users/me/")
        self.assertNotIn("admin_notes", response.data)

        client.force_authenticate(user=self.admin)
        response = client.get(f"/api/users/{self.volunteer.id}/")
        self.assertNotIn("admin_notes", response.data)

    def test_created_by_alone_does_not_grant_notes_access(self):
        """Regression test for a closed privilege-escalation path:
        _is_any_event_admin used to also trust Event.created_by, which
        combined with any authenticated user being able to create an event
        (see OwnerRoleTests.test_plain_user_cannot_create_an_event) let
        anyone self-escalate into reading/editing every volunteer's
        private notes. Both halves of that chain are closed now, but this
        pins the notes-access half directly."""

        bystander = User.objects.create_user(username="bystander-notes", password="pw")
        Event.objects.create(title="Bystander's own event", created_by=bystander)

        client = APIClient()
        client.force_authenticate(user=bystander)
        response = client.get(f"/api/users/{self.volunteer.id}/notes/")
        self.assertEqual(response.status_code, 403)


class UserOwnershipTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.alice)

    def test_cannot_update_another_users_profile(self):
        response = self.client.patch(
            f"/api/users/{self.bob.id}/",
            {"experience_notes": "hijacked"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.experience_notes, "")

    def test_cannot_delete_another_user(self):
        response = self.client.delete(f"/api/users/{self.bob.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.bob.pk).exists())

    def test_can_update_own_profile(self):
        response = self.client.patch(
            f"/api/users/{self.alice.id}/",
            {"experience_notes": "5 years as a nurse"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.experience_notes, "5 years as a nurse")

    def test_can_set_own_name(self):
        response = self.client.patch(
            f"/api/users/{self.alice.id}/",
            {"first_name": "Alice", "last_name": "Nordmann"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.first_name, "Alice")
        self.assertEqual(self.alice.last_name, "Nordmann")

    def test_can_update_own_contact_and_bio_fields(self):
        # MeSerializer (phone/address/birthdate/about) is now used for
        # update/partial_update too, not just list/retrieve/me -- this is
        # what lets the signup page's returning-volunteer login PATCH
        # these fields after prefilling.
        response = self.client.patch(
            f"/api/users/{self.alice.id}/",
            {
                "phone": "99999999",
                "address": "Nyveien 2, 0200 Oslo",
                "birthdate": "1992-03-04",
                "about": "Glad i julestemning.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["phone"], "99999999")
        self.assertEqual(response.data["about"], "Glad i julestemning.")
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.phone, "99999999")
        self.assertEqual(self.alice.address, "Nyveien 2, 0200 Oslo")
        self.assertEqual(str(self.alice.birthdate), "1992-03-04")
        self.assertEqual(self.alice.about, "Glad i julestemning.")

    def test_cannot_update_another_users_contact_fields(self):
        response = self.client.patch(
            f"/api/users/{self.bob.id}/",
            {"phone": "00000000"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.bob.refresh_from_db()
        self.assertNotEqual(self.bob.phone, "00000000")


class UserDeletionByAdminTests(TestCase):
    """perform_destroy used to only allow deleting your own account -- an
    event admin removing a volunteer (e.g. a duplicate/spam signup) had no
    way to do it short of the Django admin. See UserOwnershipTests for the
    matching "still can't delete someone else's account" case for a
    non-admin."""

    def setUp(self):
        self.admin = User.objects.create_user(username="deleter-admin", password="pw")
        make_event(title="Deletion event", created_by=self.admin)
        self.volunteer = User.objects.create_user(username="deletable-volunteer", password="pw")
        self.client = APIClient()

    def test_event_admin_can_delete_another_user(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(f"/api/users/{self.volunteer.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=self.volunteer.pk).exists())

    def test_plain_volunteer_still_cannot_delete_another_user(self):
        bystander = User.objects.create_user(username="delete-bystander", password="pw")
        self.client.force_authenticate(user=bystander)
        response = self.client.delete(f"/api/users/{self.volunteer.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.volunteer.pk).exists())


class RosterVisibilityTests(TestCase):
    """UserViewSet list/retrieve used to be readable by any authenticated
    user -- every volunteer's email and experience_notes, not just people
    you happen to share a shift with. Only people who'd actually need the
    roster (owner/admin, check-in staff, oppgave leaders -- the same set
    the admin dashboard's own nav gates on) get more than themselves back."""

    def setUp(self):
        self.owner = User.objects.create_user(username="roster-owner", password="pw")
        self.checkin_staff = User.objects.create_user(username="roster-staff", password="pw")
        self.leader = User.objects.create_user(username="roster-leader", password="pw")
        self.volunteer_a = User.objects.create_user(username="roster-a", password="pw")
        self.volunteer_b = User.objects.create_user(username="roster-b", password="pw")
        self.event = make_event(title="Roster event", created_by=self.owner)
        Membership.objects.create(event=self.event, user=self.checkin_staff, role=Membership.ROLE_CHECKIN_STAFF)
        shift = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            created_by=self.owner,
        )
        shift.leaders.add(self.leader)
        self.client = APIClient()

    def test_plain_volunteer_only_sees_themselves_in_the_list(self):
        self.client.force_authenticate(user=self.volunteer_a)
        response = self.client.get("/api/users/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([u["id"] for u in response.data], [self.volunteer_a.id])

    def test_plain_volunteer_cannot_retrieve_another_user_by_id(self):
        self.client.force_authenticate(user=self.volunteer_a)
        response = self.client.get(f"/api/users/{self.volunteer_b.id}/")
        self.assertEqual(response.status_code, 404)

    def test_plain_volunteer_can_still_retrieve_their_own_id(self):
        self.client.force_authenticate(user=self.volunteer_a)
        response = self.client.get(f"/api/users/{self.volunteer_a.id}/")
        self.assertEqual(response.status_code, 200)

    def test_owner_sees_the_full_roster(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/users/")
        self.assertEqual(response.status_code, 200)
        ids = {u["id"] for u in response.data}
        self.assertIn(self.volunteer_a.id, ids)
        self.assertIn(self.volunteer_b.id, ids)

    def test_checkin_staff_sees_the_full_roster(self):
        self.client.force_authenticate(user=self.checkin_staff)
        response = self.client.get("/api/users/")
        ids = {u["id"] for u in response.data}
        self.assertIn(self.volunteer_a.id, ids)

    def test_oppgave_leader_sees_the_full_roster(self):
        self.client.force_authenticate(user=self.leader)
        response = self.client.get("/api/users/")
        ids = {u["id"] for u in response.data}
        self.assertIn(self.volunteer_a.id, ids)


class OwnershipEnforcementRegressionTests(TestCase):
    """These perform_update/perform_destroy checks used to raise
    `rest_framework.permissions.PermissionDenied`, which doesn't exist
    (the real class lives in rest_framework.exceptions) — every one of
    these calls 500'd instead of returning a clean 403. Locks in the fix
    across every viewset that had the same bug, not just UserViewSet."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.intruder = User.objects.create_user(username="intruder", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(user=self.intruder)

    def test_non_owner_cannot_update_or_delete_event(self):
        event = make_event(title="Alternativ Jul", created_by=self.owner)

        update = self.client.patch(f"/api/events/{event.id}/", {"title": "hijacked"}, format="json")
        delete = self.client.delete(f"/api/events/{event.id}/")

        self.assertEqual(update.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        self.assertTrue(Event.objects.filter(pk=event.pk, title="Alternativ Jul").exists())

    def test_non_owner_cannot_create_update_or_delete_shift_on_someone_elses_event(self):
        event = make_event(title="Alternativ Jul", created_by=self.owner)
        shift = Shift.objects.create(
            event=event,
            title="Kjøkken",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            created_by=self.owner,
        )

        create = self.client.post(
            "/api/shifts/",
            {
                "event": event.id,
                "title": "Vertskap",
                "date": "2026-12-24",
                "start_time": "18:00:00",
                "end_time": "23:00:00",
            },
            format="json",
        )
        update = self.client.patch(f"/api/shifts/{shift.id}/", {"title": "hijacked"}, format="json")
        delete = self.client.delete(f"/api/shifts/{shift.id}/")

        self.assertEqual(create.status_code, 403)
        self.assertEqual(update.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        self.assertTrue(Shift.objects.filter(pk=shift.pk, title="Kjøkken").exists())


class RegistrationAndEmailLoginTests(TestCase):
    def setUp(self):
        cache.clear()  # throttle counters live in the cache, not the DB -- see RequestPasswordSetupTests
        self.client = APIClient()
        make_event(title="Hennings Alternativ Jul")

    def _valid_payload(self, **overrides):
        return valid_registration_payload(**overrides)

    def test_register_creates_user_and_returns_tokens(self):
        response = self.client.post("/api/register/", self._valid_payload(), format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "new.volunteer@example.com")
        self.assertTrue(User.objects.filter(email="new.volunteer@example.com").exists())

    def test_register_stores_and_returns_contact_details(self):
        response = self.client.post("/api/register/", self._valid_payload(), format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["user"]["first_name"], "Kari")
        self.assertEqual(response.data["user"]["last_name"], "Nordmann")
        self.assertEqual(response.data["user"]["phone"], "12345678")
        self.assertEqual(response.data["user"]["address"], "Testveien 1, 0123 Oslo")
        self.assertEqual(response.data["user"]["birthdate"], "1990-05-15")

        user = User.objects.get(email="new.volunteer@example.com")
        self.assertEqual(user.first_name, "Kari")
        self.assertEqual(user.phone, "12345678")
        self.assertEqual(user.address, "Testveien 1, 0123 Oslo")
        self.assertEqual(str(user.birthdate), "1990-05-15")

    def test_register_accepts_optional_qualifications_and_about(self):
        response = self.client.post(
            "/api/register/",
            self._valid_payload(
                experience_notes="Førstehjelpskurs 2023",
                about="Glad i å lage mat til mange.",
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["user"]["experience_notes"], "Førstehjelpskurs 2023")
        self.assertEqual(response.data["user"]["about"], "Glad i å lage mat til mange.")

        user = User.objects.get(email="new.volunteer@example.com")
        self.assertEqual(user.experience_notes, "Førstehjelpskurs 2023")
        self.assertEqual(user.about, "Glad i å lage mat til mange.")

    def test_register_succeeds_without_qualifications_or_about(self):
        # Unlike the contact fields, these are optional -- omitting them
        # entirely must not block registration.
        payload = self._valid_payload()
        response = self.client.post("/api/register/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="new.volunteer@example.com")
        self.assertEqual(user.experience_notes, "")
        self.assertEqual(user.about, "")

    def test_register_rejects_missing_contact_fields(self):
        for field in ["first_name", "last_name", "phone", "address", "birthdate"]:
            payload = self._valid_payload(email=f"missing-{field}@example.com")
            del payload[field]
            response = self.client.post("/api/register/", payload, format="json")
            self.assertEqual(response.status_code, 400, f"expected {field} to be required")
            self.assertFalse(User.objects.filter(email=f"missing-{field}@example.com").exists())

    def test_register_rejects_future_birthdate(self):
        response = self.client.post(
            "/api/register/", self._valid_payload(birthdate="2099-01-01"), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="new.volunteer@example.com").exists())

    def test_register_without_password_still_works_and_returns_tokens(self):
        """Volunteers don't need a password -- they get a usable JWT session
        immediately from registration, same as everyone else."""

        response = self.client.post(
            "/api/register/",
            self._valid_payload(email="passwordless.volunteer@example.com", password=""),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("access", response.data)
        user = User.objects.get(email="passwordless.volunteer@example.com")
        self.assertFalse(user.has_usable_password())

    def test_register_without_password_cannot_be_logged_into_later(self):
        """An unusable password means nobody -- including an attacker
        guessing a password -- can log in via /api/token/."""

        self.client.post(
            "/api/register/",
            self._valid_payload(email="passwordless2@example.com", password=""),
            format="json",
        )
        response = self.client.post(
            "/api/token/", {"email": "passwordless2@example.com", "password": "some guessed password"}, format="json"
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("access", response.data)

    def test_register_response_does_not_expose_skills(self):
        # Oppgave interest is no longer collected at registration -- see
        # UserSkillsTests for why skills is no longer on the serializer.
        response = self.client.post(
            "/api/register/", self._valid_payload(email="chef.volunteer@example.com"), format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("skills", response.data["user"])

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user(username="existing", email="taken@example.com", password="pw12345678")
        response = self.client.post(
            "/api/register/", self._valid_payload(email="taken@example.com"), format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.filter(email="taken@example.com").count(), 1)

    def test_register_rejects_weak_password(self):
        response = self.client.post(
            "/api/register/",
            self._valid_payload(email="weak@example.com", password="1234"),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())

    def test_login_with_email_succeeds(self):
        User.objects.create_user(username="volunteer@example.com", email="volunteer@example.com", password="s3cret-password")
        response = self.client.post(
            "/api/token/",
            {"email": "volunteer@example.com", "password": "s3cret-password"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_login_with_wrong_password_fails(self):
        User.objects.create_user(username="volunteer@example.com", email="volunteer@example.com", password="s3cret-password")
        response = self.client.post(
            "/api/token/",
            {"email": "volunteer@example.com", "password": "wrong-password"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_login_with_unknown_email_fails(self):
        response = self.client.post(
            "/api/token/",
            {"email": "nobody@example.com", "password": "whatever12345"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)


class SignupWindowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_registration_succeeds_when_no_window_is_set(self):
        make_event(title="Hennings Alternativ Jul")
        response = self.client.post(
            "/api/register/", valid_registration_payload(email="open.window@example.com"), format="json"
        )
        self.assertEqual(response.status_code, 201)

    def test_registration_rejected_before_signup_opens(self):
        make_event(title="Hennings Alternativ Jul", signup_opens_at=timezone.now() + datetime.timedelta(days=1))
        response = self.client.post(
            "/api/register/", {"email": "too.early@example.com", "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email="too.early@example.com").exists())

    def test_registration_rejected_after_signup_closes(self):
        make_event(title="Hennings Alternativ Jul", signup_closes_at=timezone.now() - datetime.timedelta(days=1))
        response = self.client.post(
            "/api/register/", {"email": "too.late@example.com", "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email="too.late@example.com").exists())

    def test_registration_succeeds_inside_the_window(self):
        make_event(
            title="Hennings Alternativ Jul",
            signup_opens_at=timezone.now() - datetime.timedelta(days=1),
            signup_closes_at=timezone.now() + datetime.timedelta(days=1),
        )
        response = self.client.post(
            "/api/register/", valid_registration_payload(email="inside.window@example.com"), format="json"
        )
        self.assertEqual(response.status_code, 201)

    def test_registration_rejected_when_no_active_event(self):
        response = self.client.post(
            "/api/register/", {"email": "no.event@example.com", "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 404)

    def test_public_event_exposes_signup_window(self):
        make_event(
            title="Hennings Alternativ Jul",
            signup_opens_at=timezone.now() - datetime.timedelta(days=1),
            signup_closes_at=timezone.now() + datetime.timedelta(days=1),
        )
        response = self.client.get("/api/public/event/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["signups_open"])
        self.assertIsNotNone(response.data["signup_opens_at"])
        self.assertIsNotNone(response.data["signup_closes_at"])


class PasswordSetupTests(TestCase):
    """Registering without a password (the normal website path) should
    leave the volunteer a way back in to the mobile app -- a
    PasswordSetupToken, emailed to them, that lets them set one later."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        make_event(title="Hennings Alternativ Jul")

    def test_passwordless_registration_creates_a_setup_token(self):
        response = self.client.post(
            "/api/register/", valid_registration_payload(email="passwordless@example.com", password=""), format="json"
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="passwordless@example.com")
        self.assertTrue(PasswordSetupToken.objects.filter(user=user).exists())

    def test_registration_with_a_password_does_not_create_a_setup_token(self):
        response = self.client.post(
            "/api/register/", valid_registration_payload(email="haspassword@example.com"), format="json"
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="haspassword@example.com")
        self.assertFalse(PasswordSetupToken.objects.filter(user=user).exists())

    def test_preview_shows_email_for_a_usable_token(self):
        user = User.objects.create_user(username="preview@example.com", email="preview@example.com", password=None)
        token = PasswordSetupToken.objects.create(user=user)
        response = self.client.get(f"/api/password-setup/{token.token}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "preview@example.com")
        self.assertTrue(response.data["is_usable"])

    def test_preview_unknown_token_404s(self):
        response = self.client.get("/api/password-setup/not-a-real-token/")
        self.assertEqual(response.status_code, 404)

    def test_set_password_lets_the_user_log_in_afterward(self):
        user = User.objects.create_user(username="setme@example.com", email="setme@example.com", password=None)
        token = PasswordSetupToken.objects.create(user=user)

        response = self.client.post(
            "/api/password-setup/confirm/",
            {"token": token.token, "password": "correct horse battery staple"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        login = self.client.post(
            "/api/token/", {"email": "setme@example.com", "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn("access", login.data)

    def test_set_password_marks_the_token_used(self):
        user = User.objects.create_user(username="onceonly@example.com", email="onceonly@example.com", password=None)
        token = PasswordSetupToken.objects.create(user=user)
        self.client.post(
            "/api/password-setup/confirm/",
            {"token": token.token, "password": "correct horse battery staple"},
            format="json",
        )
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)
        self.assertFalse(token.is_usable)

    def test_set_password_rejects_an_already_used_token(self):
        user = User.objects.create_user(username="reused@example.com", email="reused@example.com", password=None)
        token = PasswordSetupToken.objects.create(user=user, used_at=timezone.now())
        response = self.client.post(
            "/api/password-setup/confirm/",
            {"token": token.token, "password": "correct horse battery staple"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_set_password_rejects_an_expired_token(self):
        user = User.objects.create_user(username="expired@example.com", email="expired@example.com", password=None)
        token = PasswordSetupToken.objects.create(
            user=user, expires_at=timezone.now() - datetime.timedelta(days=1)
        )
        response = self.client.post(
            "/api/password-setup/confirm/",
            {"token": token.token, "password": "correct horse battery staple"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_set_password_rejects_weak_password(self):
        user = User.objects.create_user(username="weak@example.com", email="weak@example.com", password=None)
        token = PasswordSetupToken.objects.create(user=user)
        response = self.client.post(
            "/api/password-setup/confirm/", {"token": token.token, "password": "1234"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        token.refresh_from_db()
        self.assertTrue(token.is_usable)


class RequestPasswordSetupTests(TestCase):
    """The in-app 'first time / lost the email' request for a fresh
    password-setup link -- always responds the same way so it can't be
    used to check which emails have accounts."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_request_for_passwordless_user_creates_a_new_token(self):
        user = User.objects.create_user(username="forgetful@example.com", email="forgetful@example.com", password=None)
        response = self.client.post("/api/password-setup/request/", {"email": "forgetful@example.com"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PasswordSetupToken.objects.filter(user=user).exists())

    def test_request_for_unknown_email_still_returns_200(self):
        response = self.client.post("/api/password-setup/request/", {"email": "nobody@example.com"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PasswordSetupToken.objects.exists())

    def test_request_for_user_with_a_password_also_creates_a_token(self):
        """This doubles as 'forgot password' -- someone who already has a
        password can request a fresh link too, since setting one for the
        first time and replacing a forgotten one are the same operation."""

        user = User.objects.create_user(username="haspw@example.com", email="haspw@example.com", password="correct horse battery staple")
        response = self.client.post("/api/password-setup/request/", {"email": "haspw@example.com"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PasswordSetupToken.objects.filter(user=user).exists())

    def test_reset_token_replaces_the_old_password(self):
        user = User.objects.create_user(username="forgot@example.com", email="forgot@example.com", password="old horse battery staple")
        token = PasswordSetupToken.objects.create(user=user)

        response = self.client.post(
            "/api/password-setup/confirm/", {"token": token.token, "password": "new horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 200)

        login_old = self.client.post("/api/token/", {"email": "forgot@example.com", "password": "old horse battery staple"}, format="json")
        self.assertNotEqual(login_old.status_code, 200)

        login_new = self.client.post("/api/token/", {"email": "forgot@example.com", "password": "new horse battery staple"}, format="json")
        self.assertEqual(login_new.status_code, 200)

    def test_request_is_case_insensitive_on_email(self):
        user = User.objects.create_user(username="mixedcase@example.com", email="MixedCase@Example.com", password=None)
        response = self.client.post("/api/password-setup/request/", {"email": "mixedcase@example.com"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PasswordSetupToken.objects.filter(user=user).exists())

    def test_request_rejects_invalid_email(self):
        response = self.client.post("/api/password-setup/request/", {"email": "not-an-email"}, format="json")
        self.assertEqual(response.status_code, 400)


class ThrottlingTests(TestCase):
    """The sensitive public endpoints (register, login, password-setup
    request/confirm) are individually rate-limited by IP -- see
    api.throttling and DEFAULT_THROTTLE_RATES. These confirm the
    configured limits actually bite, and that exhausting one endpoint's
    quota doesn't affect another's (each has its own scope)."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        make_event(title="Hennings Alternativ Jul")

    def test_register_endpoint_is_throttled(self):
        for i in range(10):
            response = self.client.post(
                "/api/register/", valid_registration_payload(email=f"throttle{i}@example.com"), format="json"
            )
            self.assertEqual(response.status_code, 201)

        response = self.client.post(
            "/api/register/", valid_registration_payload(email="one.too.many@example.com"), format="json"
        )
        self.assertEqual(response.status_code, 429)

    def test_login_endpoint_is_throttled(self):
        User.objects.create_user(username="rate@example.com", email="rate@example.com", password="correct horse battery staple")
        for _ in range(20):
            response = self.client.post("/api/token/", {"email": "rate@example.com", "password": "wrong"}, format="json")
            self.assertEqual(response.status_code, 401)

        response = self.client.post("/api/token/", {"email": "rate@example.com", "password": "wrong"}, format="json")
        self.assertEqual(response.status_code, 429)

    def test_password_setup_request_endpoint_is_throttled(self):
        for _ in range(5):
            response = self.client.post("/api/password-setup/request/", {"email": "throttled@example.com"}, format="json")
            self.assertEqual(response.status_code, 200)

        response = self.client.post("/api/password-setup/request/", {"email": "throttled@example.com"}, format="json")
        self.assertEqual(response.status_code, 429)

    def test_password_setup_confirm_endpoint_is_throttled(self):
        for _ in range(20):
            response = self.client.post(
                "/api/password-setup/confirm/", {"token": "not-a-real-token", "password": "correct horse battery staple"}, format="json"
            )
            self.assertEqual(response.status_code, 400)

        response = self.client.post(
            "/api/password-setup/confirm/", {"token": "not-a-real-token", "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 429)

    def test_throttle_scopes_are_independent(self):
        for _ in range(5):
            self.client.post("/api/password-setup/request/", {"email": "scoped@example.com"}, format="json")

        # password_setup_request's quota is now exhausted, but register --
        # a different scope -- should be completely unaffected.
        response = self.client.post(
            "/api/register/", valid_registration_payload(email="still.works@example.com"), format="json"
        )
        self.assertEqual(response.status_code, 201)


class MembershipRolesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="super", password="pw")
        self.staff = User.objects.create_user(username="staff", password="pw")
        self.leader = User.objects.create_user(username="leader", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.event = make_event(
            title="Alternativ Jul",
            created_by=self.admin,
            checkin_mode=Event.CHECKIN_MODE_PERSONAL_QR,
        )
        Membership.objects.create(event=self.event, user=self.staff, role=Membership.ROLE_CHECKIN_STAFF)
        self.today = datetime.date.today()
        self.kitchen = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            created_by=self.admin,
        )
        self.kitchen.leaders.add(self.leader)
        self.hosting = Shift.objects.create(
            event=self.event,
            title="Vertskap",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(23, 0),
            created_by=self.admin,
        )
        self.kitchen_slot = make_slot(self.kitchen)
        self.hosting_slot = make_slot(self.hosting)

    def test_creator_is_auto_admin(self):
        self.assertTrue(self.event.is_admin(self.admin))

    def test_checkin_staff_is_not_admin(self):
        self.assertFalse(self.event.is_admin(self.staff))
        self.assertTrue(self.event.is_checkin_staff(self.staff))

    def test_shift_leader_is_scoped_to_their_shift_only(self):
        self.assertTrue(self.kitchen.is_led_by(self.leader))
        self.assertFalse(self.hosting.is_led_by(self.leader))

    def test_checkin_staff_can_use_personal_qr_checkin(self):
        qr = QRCode.objects.create(user=self.volunteer)
        client = APIClient()
        client.force_authenticate(user=self.staff)
        response = client.post(
            f"/api/events/{self.event.id}/checkin/", {"user_code": qr.data}, format="json"
        )
        self.assertIn(response.status_code, (201, 202))

    def test_plain_volunteer_cannot_use_personal_qr_checkin(self):
        qr = QRCode.objects.create(user=self.volunteer)
        client = APIClient()
        client.force_authenticate(user=self.volunteer)
        response = client.post(
            f"/api/events/{self.event.id}/checkin/", {"user_code": qr.data}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_leader_can_assign_their_own_shift_but_not_others(self):
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        ShiftSignup.objects.create(oppgave_slot=self.hosting_slot, user=self.volunteer)
        EventCheckIn.objects.create(event=self.event, user=self.volunteer, date=self.today)

        client = APIClient()
        client.force_authenticate(user=self.leader)

        denied = client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.volunteer.id, "oppgave_slot_id": self.hosting_slot.id},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        allowed = client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.volunteer.id, "oppgave_slot_id": self.kitchen_slot.id},
            format="json",
        )
        self.assertEqual(allowed.status_code, 201)

    def test_leader_can_view_pool(self):
        EventCheckIn.objects.create(event=self.event, user=self.volunteer, date=self.today)
        client = APIClient()
        client.force_authenticate(user=self.leader)
        response = client.get(f"/api/events/{self.event.id}/pool/")
        self.assertEqual(response.status_code, 200)

    def test_leader_can_edit_their_shift_but_not_reassign_leaders(self):
        client = APIClient()
        client.force_authenticate(user=self.leader)

        ok = client.patch(f"/api/shifts/{self.kitchen.id}/", {"capacity": 5}, format="json")
        self.assertEqual(ok.status_code, 200)

        denied = client.patch(
            f"/api/shifts/{self.kitchen.id}/", {"leader_ids": [self.volunteer.id]}, format="json"
        )
        self.assertEqual(denied.status_code, 403)

    def test_leader_cannot_create_new_shifts(self):
        client = APIClient()
        client.force_authenticate(user=self.leader)
        response = client.post(
            "/api/shifts/",
            {
                "event": self.event.id,
                "title": "Vakthold",
                "date": str(self.today),
                "start_time": "00:00:00",
                "end_time": "06:00:00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_manage_memberships(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)

        response = client.post(
            f"/api/events/{self.event.id}/memberships/",
            {"user_id": self.volunteer.id, "role": Membership.ROLE_CHECKIN_STAFF},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(self.event.is_checkin_staff(self.volunteer))

        listing = client.get(f"/api/events/{self.event.id}/memberships/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.data), 3)  # creator's owner membership + staff + newly added volunteer

    def test_non_admin_cannot_manage_memberships(self):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        response = client.post(
            f"/api/events/{self.event.id}/memberships/",
            {"user_id": self.volunteer.id, "role": Membership.ROLE_CHECKIN_STAFF},
            format="json",
        )
        self.assertEqual(response.status_code, 403)


class OwnerRoleTests(TestCase):
    """Only an owner can grant or revoke owner/admin access -- a plain
    admin (granted via Membership, not the event creator) can still
    manage check-in staff but not the admin tier itself."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.granted_admin = User.objects.create_user(username="granted-super", password="pw")
        self.candidate = User.objects.create_user(username="candidate", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.owner)
        Membership.objects.create(event=self.event, user=self.granted_admin, role=Membership.ROLE_ADMIN)

    def test_creator_is_owner(self):
        self.assertTrue(self.event.is_owner(self.owner))
        self.assertTrue(self.event.is_admin(self.owner))

    def test_granted_admin_is_not_owner(self):
        self.assertFalse(self.event.is_owner(self.granted_admin))
        self.assertTrue(self.event.is_admin(self.granted_admin))

    def test_viewer_role_reports_owner_distinctly(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.get(f"/api/events/{self.event.id}/")
        self.assertEqual(response.data["viewer_role"], "owner")

        client.force_authenticate(user=self.granted_admin)
        response = client.get(f"/api/events/{self.event.id}/")
        self.assertEqual(response.data["viewer_role"], "admin")

    def test_owner_can_grant_admin(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.post(
            f"/api/events/{self.event.id}/memberships/",
            {"user_id": self.candidate.id, "role": Membership.ROLE_ADMIN},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(self.event.is_admin(self.candidate))

    def test_plain_admin_cannot_grant_admin_or_owner(self):
        client = APIClient()
        client.force_authenticate(user=self.granted_admin)

        response = client.post(
            f"/api/events/{self.event.id}/memberships/",
            {"user_id": self.candidate.id, "role": Membership.ROLE_ADMIN},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.event.is_admin(self.candidate))

        response = client.post(
            f"/api/events/{self.event.id}/memberships/",
            {"user_id": self.candidate.id, "role": Membership.ROLE_OWNER},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_plain_admin_can_still_grant_checkin_staff(self):
        client = APIClient()
        client.force_authenticate(user=self.granted_admin)
        response = client.post(
            f"/api/events/{self.event.id}/memberships/",
            {"user_id": self.candidate.id, "role": Membership.ROLE_CHECKIN_STAFF},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(self.event.is_checkin_staff(self.candidate))

    def test_plain_admin_cannot_remove_another_admin(self):
        other_super = User.objects.create_user(username="other-super", password="pw")
        membership = Membership.objects.create(event=self.event, user=other_super, role=Membership.ROLE_ADMIN)

        client = APIClient()
        client.force_authenticate(user=self.granted_admin)
        response = client.post(
            f"/api/events/{self.event.id}/remove-membership/", {"membership_id": membership.id}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Membership.objects.filter(pk=membership.pk).exists())

    def test_owner_can_remove_a_admin(self):
        membership = Membership.objects.get(user=self.granted_admin, event=self.event)

        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.post(
            f"/api/events/{self.event.id}/remove-membership/", {"membership_id": membership.id}, format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Membership.objects.filter(pk=membership.pk).exists())

    def test_removing_a_membership_revokes_the_users_sessions(self):
        """A JWT is otherwise stateless -- it doesn't care that the role it
        was issued under just got revoked. remove_membership blacklists
        the person's outstanding refresh tokens, so a previously-valid
        session actually stops working rather than staying live until it
        happens to expire."""

        membership = Membership.objects.get(user=self.granted_admin, event=self.event)
        refresh = RefreshToken.for_user(self.granted_admin)

        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.post(
            f"/api/events/{self.event.id}/remove-membership/", {"membership_id": membership.id}, format="json"
        )
        self.assertEqual(response.status_code, 204)

        refresh_response = APIClient().post("/api/token/refresh/", {"refresh": str(refresh)}, format="json")
        self.assertNotEqual(refresh_response.status_code, 200)

    def test_admin_creating_a_new_event_becomes_its_owner(self):
        """perform_create still grants the creator an owner Membership on
        the new event -- this now additionally requires the creator to
        already be an admin/owner of an existing event (see
        test_plain_user_cannot_create_an_event)."""

        client = APIClient()
        client.force_authenticate(user=self.granted_admin)
        response = client.post("/api/events/", {"title": "Alternativ Jul 2027"}, format="json")
        self.assertEqual(response.status_code, 201)
        event_id = response.data["id"]
        membership = Membership.objects.get(event_id=event_id, user=self.granted_admin)
        self.assertEqual(membership.role, Membership.ROLE_OWNER)

    def test_plain_user_cannot_create_an_event(self):
        """A user with no admin/owner Membership anywhere can't create an
        event at all -- previously they could, and became that event's
        owner, which was enough to self-escalate into "any event admin"
        (see AdminNoteTests.test_created_by_alone_does_not_grant_notes_access)."""

        client = APIClient()
        client.force_authenticate(user=self.candidate)
        response = client.post("/api/events/", {"title": "Sneaky event"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Event.objects.filter(title="Sneaky event").exists())

    def test_created_by_alone_grants_no_permissions(self):
        """The event is permanent, not owned forever by whoever happened to
        create the row -- created_by carries no special access unless
        there's also an explicit owner Membership."""

        bystander = User.objects.create_user(username="bystander", password="pw")
        bare_event = Event.objects.create(title="No membership row", created_by=bystander)
        self.assertFalse(bare_event.is_owner(bystander))
        self.assertFalse(bare_event.is_admin(bystander))

    def test_deleting_creator_account_does_not_delete_the_event(self):
        creator = User.objects.create_user(username="temp-creator", password="pw")
        event = make_event(title="Survives creator deletion", created_by=creator)
        event_id = event.id

        creator.delete()

        survived = Event.objects.get(pk=event_id)
        self.assertIsNone(survived.created_by)

    def test_cannot_remove_the_last_owner(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        owner_membership = Membership.objects.get(event=self.event, user=self.owner, role=Membership.ROLE_OWNER)

        response = client.post(
            f"/api/events/{self.event.id}/remove-membership/", {"membership_id": owner_membership.id}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Membership.objects.filter(pk=owner_membership.pk).exists())

    def test_can_remove_an_owner_when_another_owner_remains(self):
        second_owner = User.objects.create_user(username="second-owner", password="pw")
        Membership.objects.create(event=self.event, user=second_owner, role=Membership.ROLE_OWNER)

        client = APIClient()
        client.force_authenticate(user=self.owner)
        owner_membership = Membership.objects.get(event=self.event, user=self.owner, role=Membership.ROLE_OWNER)

        response = client.post(
            f"/api/events/{self.event.id}/remove-membership/", {"membership_id": owner_membership.id}, format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertTrue(self.event.is_owner(second_owner))


class EventActivationTests(TestCase):
    """Multiple Event rows can exist (e.g. next year's set up ahead of
    time), but exactly one is ever active -- activate() is exclusive, and
    only an owner can activate/deactivate/delete."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.admin = User.objects.create_user(username="admin", password="pw")
        self.event_a = make_event(title="Alternativ Jul 2026", created_by=self.owner, is_active=True)
        self.event_b = make_event(title="Alternativ Jul 2027", created_by=self.owner, is_active=False)
        Membership.objects.create(event=self.event_a, user=self.admin, role=Membership.ROLE_ADMIN)
        self.client = APIClient()

    def test_new_event_starts_inactive(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.post("/api/events/", {"title": "Alternativ Jul 2028"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["is_active"])

    def test_activating_one_event_deactivates_the_others(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.post(f"/api/events/{self.event_b.id}/activate/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_active"])

        self.event_a.refresh_from_db()
        self.event_b.refresh_from_db()
        self.assertFalse(self.event_a.is_active)
        self.assertTrue(self.event_b.is_active)

    def test_deactivate_turns_off_just_that_event(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        response = client.post(f"/api/events/{self.event_a.id}/deactivate/")
        self.assertEqual(response.status_code, 200)
        self.event_a.refresh_from_db()
        self.assertFalse(self.event_a.is_active)

    def test_plain_admin_cannot_activate_or_deactivate(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)

        response = client.post(f"/api/events/{self.event_b.id}/activate/")
        self.assertEqual(response.status_code, 403)

        response = client.post(f"/api/events/{self.event_a.id}/deactivate/")
        self.assertEqual(response.status_code, 403)

    def test_only_owner_can_delete_event(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.delete(f"/api/events/{self.event_b.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Event.objects.filter(pk=self.event_b.pk).exists())

        client.force_authenticate(user=self.owner)
        response = client.delete(f"/api/events/{self.event_b.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Event.objects.filter(pk=self.event_b.pk).exists())

    def test_public_event_only_returns_the_active_one(self):
        response = self.client.get("/api/public/event/")
        self.assertEqual(response.data["title"], "Alternativ Jul 2026")

        self.event_b.is_active = True
        self.event_b.save()
        self.event_a.is_active = False
        self.event_a.save()

        response = self.client.get("/api/public/event/")
        self.assertEqual(response.data["title"], "Alternativ Jul 2027")


class InviteTests(TestCase):
    """Admin/staff invites: owner-gated for owner/admin roles (same tiering
    as memberships), open to plain admins for check-in staff, and
    accept_invite handles both brand-new emails and emails that already
    have a passwordless volunteer account."""

    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pw")
        self.admin = User.objects.create_user(username="admin", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.owner)
        Membership.objects.create(event=self.event, user=self.admin, role=Membership.ROLE_ADMIN)
        self.client = APIClient()

    def test_owner_can_invite_admin(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            f"/api/events/{self.event.id}/invites/",
            {"email": "new-admin@example.com", "role": Membership.ROLE_ADMIN},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Invite.objects.filter(email="new-admin@example.com", role=Membership.ROLE_ADMIN).exists())

    def test_plain_admin_cannot_invite_admin_or_owner(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/events/{self.event.id}/invites/",
            {"email": "sneaky@example.com", "role": Membership.ROLE_ADMIN},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Invite.objects.filter(email="sneaky@example.com").exists())

    def test_plain_admin_can_invite_checkin_staff(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/events/{self.event.id}/invites/",
            {"email": "new-staff@example.com", "role": Membership.ROLE_CHECKIN_STAFF},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_volunteer_cannot_invite_anyone(self):
        volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.client.force_authenticate(user=volunteer)
        response = self.client.post(
            f"/api/events/{self.event.id}/invites/",
            {"email": "x@example.com", "role": Membership.ROLE_CHECKIN_STAFF},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_invite_preview_shows_role_and_event(self):
        invite = Invite.objects.create(
            event=self.event, email="preview@example.com", role=Membership.ROLE_CHECKIN_STAFF, invited_by=self.owner
        )
        response = self.client.get(f"/api/invites/{invite.token}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "preview@example.com")
        self.assertEqual(response.data["role"], Membership.ROLE_CHECKIN_STAFF)
        self.assertEqual(response.data["event_title"], "Alternativ Jul")
        self.assertTrue(response.data["is_usable"])

    def test_invite_preview_unknown_token_404s(self):
        response = self.client.get("/api/invites/not-a-real-token/")
        self.assertEqual(response.status_code, 404)

    def test_accept_invite_creates_new_user_and_membership(self):
        invite = Invite.objects.create(
            event=self.event, email="brandnew@example.com", role=Membership.ROLE_ADMIN, invited_by=self.owner
        )
        response = self.client.post(
            "/api/invites/accept/", {"token": invite.token, "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        user = User.objects.get(email="brandnew@example.com")
        self.assertTrue(user.check_password("correct horse battery staple"))
        self.assertTrue(Membership.objects.filter(event=self.event, user=user, role=Membership.ROLE_ADMIN).exists())
        invite.refresh_from_db()
        self.assertIsNotNone(invite.accepted_at)

    def test_accept_invite_reuses_existing_passwordless_account(self):
        """A volunteer who signed up passwordless via the website, later
        invited as admin, gets a password added to their SAME account --
        not a duplicate."""

        existing = User.objects.create_user(username="already@example.com", email="already@example.com", password=None)
        self.assertFalse(existing.has_usable_password())
        invite = Invite.objects.create(
            event=self.event, email="already@example.com", role=Membership.ROLE_CHECKIN_STAFF, invited_by=self.owner
        )

        response = self.client.post(
            "/api/invites/accept/", {"token": invite.token, "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email="already@example.com").count(), 1)
        existing.refresh_from_db()
        self.assertTrue(existing.has_usable_password())
        self.assertTrue(existing.check_password("correct horse battery staple"))

    def test_accept_invite_rejects_expired_token(self):
        invite = Invite.objects.create(
            event=self.event, email="late@example.com", role=Membership.ROLE_CHECKIN_STAFF, invited_by=self.owner
        )
        Invite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - datetime.timedelta(days=1))
        response = self.client.post(
            "/api/invites/accept/", {"token": invite.token, "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_accept_invite_rejects_already_accepted_token(self):
        invite = Invite.objects.create(
            event=self.event,
            email="reused@example.com",
            role=Membership.ROLE_CHECKIN_STAFF,
            invited_by=self.owner,
            accepted_at=timezone.now(),
        )
        response = self.client.post(
            "/api/invites/accept/", {"token": invite.token, "password": "correct horse battery staple"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_accept_invite_rejects_weak_password(self):
        invite = Invite.objects.create(
            event=self.event, email="weak@example.com", role=Membership.ROLE_CHECKIN_STAFF, invited_by=self.owner
        )
        response = self.client.post("/api/invites/accept/", {"token": invite.token, "password": "1234"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())

    def test_owner_can_revoke_admin_invite(self):
        invite = Invite.objects.create(
            event=self.event, email="revoke-me@example.com", role=Membership.ROLE_ADMIN, invited_by=self.owner
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            f"/api/events/{self.event.id}/revoke-invite/", {"invite_id": invite.id}, format="json"
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Invite.objects.filter(pk=invite.pk).exists())

    def test_plain_admin_cannot_revoke_admin_invite(self):
        invite = Invite.objects.create(
            event=self.event, email="protected@example.com", role=Membership.ROLE_ADMIN, invited_by=self.owner
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/events/{self.event.id}/revoke-invite/", {"invite_id": invite.id}, format="json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Invite.objects.filter(pk=invite.pk).exists())

    def test_plain_admin_can_revoke_checkin_staff_invite(self):
        invite = Invite.objects.create(
            event=self.event, email="staff-invite@example.com", role=Membership.ROLE_CHECKIN_STAFF, invited_by=self.owner
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/events/{self.event.id}/revoke-invite/", {"invite_id": invite.id}, format="json"
        )
        self.assertEqual(response.status_code, 204)


class PoolFifoOrderingTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.first_arrival = User.objects.create_user(username="first", password="pw")
        self.second_arrival = User.objects.create_user(username="second", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        self.today = datetime.date.today()

        first_checkin = EventCheckIn.objects.create(event=self.event, user=self.first_arrival, date=self.today)
        second_checkin = EventCheckIn.objects.create(event=self.event, user=self.second_arrival, date=self.today)
        # Force an explicit, unambiguous ordering regardless of auto_now_add clock resolution.
        now = timezone.now()
        EventCheckIn.objects.filter(pk=first_checkin.pk).update(checked_in_at=now - datetime.timedelta(minutes=5))
        EventCheckIn.objects.filter(pk=second_checkin.pk).update(checked_in_at=now)

    def test_pool_lists_earliest_arrival_first(self):
        client = APIClient()
        client.force_authenticate(user=self.organizer)
        response = client.get(f"/api/events/{self.event.id}/pool/")
        self.assertEqual(response.status_code, 200)
        usernames = [entry["user"]["username"] for entry in response.data]
        self.assertEqual(usernames, ["first", "second"])


class MetricsTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        self.today = datetime.date.today()
        self.kitchen = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            capacity=5,
            min_capacity=2,
            created_by=self.organizer,
        )
        self.kitchen_slot = make_slot(self.kitchen)
        ShiftSignup.objects.create(oppgave_slot=self.kitchen_slot, user=self.volunteer)
        EventCheckIn.objects.create(event=self.event, user=self.volunteer, date=self.today)

    def test_metrics_reports_headcounts_and_shift_utilization(self):
        client = APIClient()
        client.force_authenticate(user=self.organizer)
        response = client.get(f"/api/events/{self.event.id}/metrics/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["checked_in"], 1)
        self.assertEqual(response.data["assigned"], 0)
        self.assertEqual(response.data["in_pool"], 1)
        shift_metric = response.data["shifts"][0]
        self.assertEqual(shift_metric["title"], "Kjøkken")
        self.assertEqual(shift_metric["signup_count"], 1)
        self.assertEqual(shift_metric["assigned_count"], 0)
        self.assertTrue(shift_metric["is_understaffed"])


class OppgaveHistoryTests(TestCase):
    """Cross-event no-show/fill-rate tracking -- signups vs. actual
    Assignments, grouped by oppgave (Skill) via OppgaveSlot across every
    event regardless of which one is active, so an admin can see how much
    they need to oversubscribe a given oppgave to reliably end up with it
    filled. Grouped by the Skill itself, not by which numbered vakt hosted
    it a given year -- see oppgave_history's docstring for why grouping by
    shift title used to be wrong."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="history-organizer", password="pw")
        self.admin = User.objects.create_user(username="history-admin", password="pw")
        self.volunteer = User.objects.create_user(username="history-volunteer", password="pw")

        self.event_2026 = make_event(
            title="Alternativ Jul 2026",
            created_by=self.organizer,
            date=datetime.datetime(2026, 12, 20, tzinfo=datetime.timezone.utc),
        )
        Membership.objects.create(event=self.event_2026, user=self.admin, role=Membership.ROLE_ADMIN)

        self.event_2027 = make_event(
            title="Alternativ Jul 2027",
            created_by=self.organizer,
            is_active=False,
            date=datetime.datetime(2027, 12, 20, tzinfo=datetime.timezone.utc),
        )

        # Deliberately different vakt numbers/titles across years -- the
        # oppgave (Skill) is what history groups on, not the vakt title.
        kitchen_2026 = Shift.objects.create(
            event=self.event_2026, title="Vakt 5", date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=self.organizer,
        )
        kitchen_2027 = Shift.objects.create(
            event=self.event_2027, title="Vakt 7", date=datetime.date(2027, 12, 24),
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=self.organizer,
        )
        hosting_2027 = Shift.objects.create(
            event=self.event_2027, title="Vakt 6", date=datetime.date(2027, 12, 24),
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=self.organizer,
        )

        kitchen_2026_slot = make_slot(kitchen_2026, skill_name="Kjøkken")
        kitchen_2027_slot = make_slot(kitchen_2027, skill_name="Kjøkken")
        hosting_2027_slot = make_slot(hosting_2027, skill_name="Vertskap")

        volunteers_2026 = [User.objects.create_user(username=f"v2026-{i}", password="pw") for i in range(4)]
        for v in volunteers_2026:
            ShiftSignup.objects.create(oppgave_slot=kitchen_2026_slot, user=v)
        for v in volunteers_2026[:2]:
            Assignment.objects.create(oppgave_slot=kitchen_2026_slot, user=v, confirmed_by=self.organizer)

        volunteers_2027 = [User.objects.create_user(username=f"v2027-{i}", password="pw") for i in range(2)]
        for v in volunteers_2027:
            ShiftSignup.objects.create(oppgave_slot=kitchen_2027_slot, user=v)
            Assignment.objects.create(oppgave_slot=kitchen_2027_slot, user=v, confirmed_by=self.organizer)

        hosting_volunteers = [User.objects.create_user(username=f"h2027-{i}", password="pw") for i in range(3)]
        for v in hosting_volunteers:
            ShiftSignup.objects.create(oppgave_slot=hosting_2027_slot, user=v)

        self.client = APIClient()

    def _get(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get("/api/metrics/oppgave-history/")

    def test_plain_volunteer_cannot_view_history(self):
        response = self._get(self.volunteer)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_history(self):
        response = self._get(self.admin)
        self.assertEqual(response.status_code, 200)

    def test_same_oppgave_merges_across_years_regardless_of_vakt_title(self):
        response = self._get(self.organizer)
        kjokken = next(r for r in response.data if r["title"] == "Kjøkken")
        self.assertEqual(kjokken["total_signups"], 6)
        self.assertEqual(kjokken["total_assigned"], 4)
        self.assertEqual({y["year"] for y in kjokken["years"]}, {"2026", "2027"})

    def test_fill_rate_and_oversubscription_factor(self):
        response = self._get(self.organizer)
        kjokken = next(r for r in response.data if r["title"] == "Kjøkken")
        self.assertAlmostEqual(kjokken["fill_rate"], 4 / 6, places=3)
        self.assertAlmostEqual(kjokken["oversubscription_factor"], 6 / 4, places=2)

    def test_zero_fill_rate_has_no_oversubscription_factor(self):
        response = self._get(self.organizer)
        hosting = next(r for r in response.data if r["title"] == "Vertskap")
        self.assertEqual(hosting["total_signups"], 3)
        self.assertEqual(hosting["total_assigned"], 0)
        self.assertEqual(hosting["fill_rate"], 0)
        self.assertIsNone(hosting["oversubscription_factor"])


class ShiftCapacityTests(TestCase):
    def test_is_understaffed_reflects_min_capacity(self):
        organizer = User.objects.create_user(username="organizer", password="pw")
        event = make_event(title="Alternativ Jul", created_by=organizer)
        shift = Shift.objects.create(
            event=event,
            title="Kjøkken",
            date=datetime.date.today(),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            min_capacity=2,
            created_by=organizer,
        )
        slot = make_slot(shift)
        self.assertTrue(shift.is_understaffed)

        volunteer = User.objects.create_user(username="volunteer", password="pw")
        Assignment.objects.create(oppgave_slot=slot, user=volunteer, confirmed_by=organizer)
        another = User.objects.create_user(username="volunteer2", password="pw")
        Assignment.objects.create(oppgave_slot=slot, user=another, confirmed_by=organizer)

        shift.refresh_from_db()
        self.assertFalse(shift.is_understaffed)


class CancelAssignmentTests(TestCase):
    def test_volunteer_can_cancel_their_own_assignment(self):
        organizer = User.objects.create_user(username="organizer", password="pw")
        volunteer = User.objects.create_user(username="volunteer", password="pw")
        event = make_event(title="Alternativ Jul", created_by=organizer)
        shift = Shift.objects.create(
            event=event,
            title="Kjøkken",
            date=datetime.date.today(),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            created_by=organizer,
        )
        slot = make_slot(shift)
        Assignment.objects.create(oppgave_slot=slot, user=volunteer, confirmed_by=organizer)

        client = APIClient()
        client.force_authenticate(user=volunteer)
        response = client.post(f"/api/oppgave-slots/{slot.id}/cancel-assignment/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Assignment.objects.filter(oppgave_slot=slot, user=volunteer).exists())


class PublicEventEndpointTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", email="organizer@example.com", password="pw")
        self.event = make_event(title="Alternativ Jul", description="Julefeiring for alle", created_by=self.organizer)
        self.kitchen = Shift.objects.create(
            event=self.event,
            title="Kjøkken",
            date=datetime.date(2026, 12, 24),
            start_time=datetime.time(18, 0),
            end_time=datetime.time(22, 0),
            criticality=Shift.CRITICALITY_CRITICAL,
            created_by=self.organizer,
        )
        self.client = APIClient()  # deliberately unauthenticated

    def test_public_event_is_reachable_without_auth(self):
        response = self.client.get("/api/public/event/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "Alternativ Jul")
        self.assertEqual(len(response.data["shifts"]), 1)
        self.assertEqual(response.data["shifts"][0]["title"], "Kjøkken")
        self.assertTrue(response.data["shifts"][0]["is_critical"])

    def test_public_event_never_exposes_volunteer_or_admin_profiles(self):
        volunteer = User.objects.create_user(username="volunteer", email="volunteer@example.com", password="pw")
        slot = make_slot(self.kitchen)
        ShiftSignup.objects.create(oppgave_slot=slot, user=volunteer)

        response = self.client.get("/api/public/event/")

        body = str(response.content)
        self.assertNotIn("organizer@example.com", body)
        self.assertNotIn("volunteer@example.com", body)
        self.assertNotIn("created_by", response.data)
        self.assertNotIn("participants", response.data["shifts"][0])
        self.assertNotIn("leaders", response.data["shifts"][0])

    def test_public_event_exposes_oppgave_slots(self):
        slot = make_slot(self.kitchen, skill_name="Kokk", capacity=3)

        response = self.client.get("/api/public/event/")

        self.assertEqual(len(response.data["oppgave_slots"]), 1)
        slot_payload = response.data["oppgave_slots"][0]
        self.assertEqual(slot_payload["id"], slot.id)
        self.assertEqual(slot_payload["skill_name"], "Kokk")
        self.assertEqual(slot_payload["capacity"], 3)
        self.assertEqual(slot_payload["signup_count"], 0)
        self.assertFalse(slot_payload["is_full"])

    def test_public_event_returns_404_when_none_exists(self):
        self.event.delete()
        response = self.client.get("/api/public/event/")
        self.assertEqual(response.status_code, 404)

    def test_public_event_returns_whichever_event_is_active(self):
        # A newer event existing isn't enough on its own -- see
        # EventActivationTests for the full activate/deactivate behavior.
        # This just confirms public_event follows is_active, not recency.
        newer = make_event(title="Alternativ Jul 2027", created_by=self.organizer, is_active=False)
        response = self.client.get("/api/public/event/")
        self.assertEqual(response.data["id"], self.event.id)

        self.event.is_active = False
        self.event.save()
        newer.is_active = True
        newer.save()

        response = self.client.get("/api/public/event/")
        self.assertEqual(response.data["id"], newer.id)


class SkillPermissionTests(TestCase):
    """The oppgave catalogue is shared across every volunteer's signup and
    every event -- browsing it stays open to anyone authenticated, but
    only an admin/owner can add, rename, or delete entries out from under
    everyone else."""

    def setUp(self):
        self.admin = User.objects.create_user(username="skill-admin", password="pw")
        self.volunteer = User.objects.create_user(username="skill-volunteer", password="pw")
        make_event(title="Skill event", created_by=self.admin)
        self.skill = Skill.objects.create(name="Kokk")
        self.client = APIClient()

    def test_any_authenticated_user_can_list_skills(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.get("/api/skills/")
        self.assertEqual(response.status_code, 200)

    def test_plain_volunteer_cannot_create_a_skill(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.post("/api/skills/", {"name": "Sneaky new oppgave"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Skill.objects.filter(name="Sneaky new oppgave").exists())

    def test_plain_volunteer_cannot_rename_a_skill(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.patch(f"/api/skills/{self.skill.id}/", {"name": "Renamed"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.name, "Kokk")

    def test_plain_volunteer_cannot_delete_a_skill(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.delete(f"/api/skills/{self.skill.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Skill.objects.filter(pk=self.skill.pk).exists())

    def test_admin_can_create_a_skill(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post("/api/skills/", {"name": "Vaktleder"}, format="json")
        self.assertEqual(response.status_code, 201)


class PublicSkillsEndpointTests(TestCase):
    def setUp(self):
        Skill.objects.create(name="Kokk")
        Skill.objects.create(name="Vertskap")
        self.client = APIClient()  # deliberately unauthenticated

    def test_public_skills_is_reachable_without_auth(self):
        response = self.client.get("/api/public/skills/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({s["name"] for s in response.data}, {"Kokk", "Vertskap"})

    def test_public_skills_returns_empty_list_when_none_exist(self):
        Skill.objects.all().delete()
        response = self.client.get("/api/public/skills/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
