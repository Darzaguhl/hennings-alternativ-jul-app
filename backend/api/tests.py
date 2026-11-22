import datetime
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    AbsenceExcuse,
    Assignment,
    Commendation,
    Event,
    EventCheckIn,
    Invite,
    InventoryCategory,
    InventoryItem,
    Membership,
    OppgaveSlot,
    PasswordSetupToken,
    QRCode,
    Rating,
    Shift,
    ShiftConflict,
    ShiftSignup,
    Skill,
    SocialIdentity,
    StandingOverride,
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


class RemoveCheckinTests(TestCase):
    """Undoing a check-in (a mis-scan, or someone leaving before being
    assigned) -- the admin dashboard's Pool & tildeling tab needs a way to
    drop someone off the pool list without touching their signups."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        self.today = datetime.date.today()
        self.client = APIClient()
        self.client.force_authenticate(user=self.organizer)

    def _checkin(self):
        EventCheckIn.objects.create(event=self.event, user=self.volunteer, date=self.today)

    def test_admin_can_remove_a_checkin(self):
        self._checkin()

        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/remove/",
            {"user_id": self.volunteer.id},
            format="json",
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(EventCheckIn.objects.filter(event=self.event, user=self.volunteer, date=self.today).exists())

    def test_remove_does_not_touch_signups(self):
        shift = Shift.objects.create(
            event=self.event,
            title="Vertskap",
            date=self.today,
            start_time=datetime.time(18, 0),
            end_time=datetime.time(23, 0),
            created_by=self.organizer,
        )
        slot = make_slot(shift)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.volunteer)
        self._checkin()

        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/remove/",
            {"user_id": self.volunteer.id},
            format="json",
        )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(ShiftSignup.objects.filter(oppgave_slot=slot, user=self.volunteer).exists())

    def test_plain_volunteer_cannot_remove_a_checkin(self):
        self._checkin()
        self.client.force_authenticate(user=self.volunteer)

        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/remove/",
            {"user_id": self.volunteer.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(EventCheckIn.objects.filter(event=self.event, user=self.volunteer, date=self.today).exists())

    def test_removing_a_nonexistent_checkin_returns_404(self):
        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/remove/",
            {"user_id": self.volunteer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_user_id_is_rejected(self):
        response = self.client.post(f"/api/events/{self.event.id}/checkin/remove/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_removes_only_the_given_date(self):
        yesterday = self.today - datetime.timedelta(days=1)
        EventCheckIn.objects.create(event=self.event, user=self.volunteer, date=yesterday)
        self._checkin()

        response = self.client.post(
            f"/api/events/{self.event.id}/checkin/remove/",
            {"user_id": self.volunteer.id, "date": str(self.today)},
            format="json",
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(EventCheckIn.objects.filter(event=self.event, user=self.volunteer, date=self.today).exists())
        self.assertTrue(EventCheckIn.objects.filter(event=self.event, user=self.volunteer, date=yesterday).exists())


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

    def test_admin_can_update_a_slots_capacity(self):
        slot = make_slot(self.shift, skill_name=self.skill.name, capacity=2)
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(f"/api/oppgave-slots/{slot.id}/", {"capacity": 5}, format="json")
        self.assertEqual(response.status_code, 200)
        slot.refresh_from_db()
        self.assertEqual(slot.capacity, 5)

    def test_plain_volunteer_cannot_update_a_slot(self):
        # Regression test: perform_update previously had no override, so
        # ModelViewSet's default (IsAuthenticated only) let any signed-in
        # volunteer PATCH another event's oppgave slot.
        slot = make_slot(self.shift, skill_name=self.skill.name, capacity=2)
        bystander = User.objects.create_user(username="slot-bystander-3", password="pw")
        self.client.force_authenticate(user=bystander)
        response = self.client.patch(f"/api/oppgave-slots/{slot.id}/", {"capacity": 99}, format="json")
        self.assertEqual(response.status_code, 403)
        slot.refresh_from_db()
        self.assertEqual(slot.capacity, 2)

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


class InventoryCategoryAdminTests(TestCase):
    """The gift/donation catalogue -- same admin-only-writes gating as
    Skill (see SkillViewSet's docstring on why this matters: it used to
    be open to any authenticated user)."""

    def setUp(self):
        self.admin = User.objects.create_user(username="inv-cat-admin", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.admin)
        self.client = APIClient()

    def test_admin_can_create_a_category(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post("/api/inventory-categories/", {"name": "Vinterjakker"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(InventoryCategory.objects.filter(name="Vinterjakker").exists())

    def test_plain_volunteer_cannot_create_a_category(self):
        bystander = User.objects.create_user(username="inv-cat-bystander", password="pw")
        self.client.force_authenticate(user=bystander)
        response = self.client.post("/api/inventory-categories/", {"name": "Leker"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(InventoryCategory.objects.filter(name="Leker").exists())

    def test_admin_cannot_delete_a_category_still_in_use(self):
        # Regression test: category.on_delete=PROTECT (see InventoryItem's
        # docstring) used to surface as an unhandled 500 -- perform_destroy
        # must turn Django's ProtectedError into a clean 400.
        category = InventoryCategory.objects.create(name="Vinterjakker")
        InventoryItem.objects.create(event=self.event, category=category, quantity=1)
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(f"/api/inventory-categories/{category.id}/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(InventoryCategory.objects.filter(pk=category.pk).exists())

    def test_any_authenticated_user_can_list_categories(self):
        InventoryCategory.objects.create(name="Bøker")
        bystander = User.objects.create_user(username="inv-cat-bystander-2", password="pw")
        self.client.force_authenticate(user=bystander)
        response = self.client.get("/api/inventory-categories/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)


class InventoryItemTests(TestCase):
    """Logging intake (direction="in") is open to any authenticated
    volunteer -- deliberately, since many different teams do this, not
    just staff. Logging a distribution (direction="out") and
    editing/deleting any entry are admin-only."""

    def setUp(self):
        self.admin = User.objects.create_user(username="inv-item-admin", password="pw")
        self.volunteer = User.objects.create_user(username="inv-item-volunteer", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.admin)
        self.category = InventoryCategory.objects.create(name="Vinterjakker")
        self.client = APIClient()

    def test_any_volunteer_can_log_an_intake_item(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.post(
            "/api/inventory-items/",
            {"event": self.event.id, "category": self.category.id, "quantity": 3, "description": "Blå, str M"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        item = InventoryItem.objects.get(pk=response.json()["id"])
        self.assertEqual(item.direction, InventoryItem.DIRECTION_IN)
        self.assertEqual(item.quantity, 3)
        # logged_by is set server-side from the request, not client-supplied
        self.assertEqual(item.logged_by, self.volunteer)

    def test_plain_volunteer_cannot_log_a_distribution(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.post(
            "/api/inventory-items/",
            {"event": self.event.id, "category": self.category.id, "quantity": 1, "direction": "out"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(InventoryItem.objects.filter(event=self.event, direction="out").exists())

    def test_admin_can_log_a_distribution(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/inventory-items/",
            {"event": self.event.id, "category": self.category.id, "quantity": 2, "direction": "out"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_plain_volunteer_cannot_edit_any_entry(self):
        item = InventoryItem.objects.create(
            event=self.event, category=self.category, quantity=5, logged_by=self.volunteer
        )
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.patch(f"/api/inventory-items/{item.id}/", {"quantity": 99}, format="json")
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 5)

    def test_admin_can_edit_and_delete_any_entry(self):
        item = InventoryItem.objects.create(
            event=self.event, category=self.category, quantity=5, logged_by=self.volunteer
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(f"/api/inventory-items/{item.id}/", {"quantity": 7}, format="json")
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 7)

        response = self.client.delete(f"/api/inventory-items/{item.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(InventoryItem.objects.filter(pk=item.pk).exists())

    def test_summary_nets_in_and_out_per_category(self):
        other_category = InventoryCategory.objects.create(name="Leker")
        InventoryItem.objects.create(event=self.event, category=self.category, quantity=10, direction="in")
        InventoryItem.objects.create(event=self.event, category=self.category, quantity=3, direction="out")
        InventoryItem.objects.create(event=self.event, category=other_category, quantity=4, direction="in")

        self.client.force_authenticate(user=self.volunteer)
        response = self.client.get("/api/inventory-items/summary/", {"event": self.event.id})
        self.assertEqual(response.status_code, 200)
        by_category = {row["category_name"]: row for row in response.json()}
        self.assertEqual(by_category["Vinterjakker"]["in_total"], 10)
        self.assertEqual(by_category["Vinterjakker"]["out_total"], 3)
        self.assertEqual(by_category["Vinterjakker"]["net"], 7)
        self.assertEqual(by_category["Leker"]["net"], 4)

    def test_summary_requires_event_param(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.get("/api/inventory-items/summary/")
        self.assertEqual(response.status_code, 400)


class InventoryIdentifyTests(TestCase):
    """With ANTHROPIC_API_KEY unset (the test default -- see settings.py),
    api.vision.identify_item degrades gracefully rather than requiring a
    real key to pass CI, same contract as the Resend-backed email tests."""

    def setUp(self):
        self.volunteer = User.objects.create_user(username="inv-identify-volunteer", password="pw")
        self.client = APIClient()

    def test_identify_without_api_key_returns_empty_suggestion(self):
        self.client.force_authenticate(user=self.volunteer)
        photo = SimpleUploadedFile("item.jpg", b"not-a-real-image", content_type="image/jpeg")
        response = self.client.post("/api/inventory/identify/", {"photo": photo}, format="multipart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {})

    def test_identify_requires_authentication(self):
        photo = SimpleUploadedFile("item.jpg", b"not-a-real-image", content_type="image/jpeg")
        response = self.client.post("/api/inventory/identify/", {"photo": photo}, format="multipart")
        self.assertEqual(response.status_code, 401)

    def test_identify_requires_a_photo(self):
        self.client.force_authenticate(user=self.volunteer)
        response = self.client.post("/api/inventory/identify/", {}, format="multipart")
        self.assertEqual(response.status_code, 400)


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


class CommendationTests(TestCase):
    """Leaders' "fremhev" flags: raised during the event on someone
    confirmed onto a vakt they lead, strictly internal -- the flagged
    volunteer (or anyone who isn't an admin or the flag's author) must
    never be able to read it."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.leader = User.objects.create_user(username="leader", password="pw")
        self.other_leader = User.objects.create_user(username="other_leader", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw", first_name="Kari", last_name="Nordmann")
        self.bystander = User.objects.create_user(username="bystander", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        today = datetime.date.today()
        self.shift = Shift.objects.create(
            event=self.event, title="Kjøkken", date=today,
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=self.organizer,
        )
        self.other_shift = Shift.objects.create(
            event=self.event, title="Vertskap", date=today,
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=self.organizer,
        )
        self.shift.leaders.add(self.leader)
        self.other_shift.leaders.add(self.other_leader)
        self.slot = make_slot(self.shift, skill_name="Kokk")
        self.other_slot = make_slot(self.other_shift, skill_name="Vert")
        Assignment.objects.create(oppgave_slot=self.slot, user=self.volunteer, confirmed_by=self.organizer)
        self.client = APIClient()

    def _flag(self, as_user, shift=None, user=None, **extra):
        self.client.force_authenticate(user=as_user)
        return self.client.post(
            "/api/commendations/",
            {"shift": (shift or self.shift).id, "user": (user or self.volunteer).id, **extra},
            format="json",
        )

    def test_leader_can_flag_someone_assigned_on_their_vakt(self):
        response = self._flag(self.leader, note="Tok initiativ")

        self.assertEqual(response.status_code, 201)
        flag = Commendation.objects.get()
        self.assertEqual(flag.user, self.volunteer)
        self.assertEqual(flag.flagged_by, self.leader)
        self.assertEqual(flag.event, self.event)
        self.assertEqual(flag.oppgave_slot, self.slot)
        self.assertEqual(flag.note, "Tok initiativ")
        self.assertEqual(response.data["user_name"], "Kari Nordmann")
        self.assertEqual(response.data["skill_name"], "Kokk")

    def test_client_cannot_set_flagged_by_or_event(self):
        response = self._flag(self.leader, flagged_by=self.organizer.id, event=999)

        self.assertEqual(response.status_code, 201)
        flag = Commendation.objects.get()
        self.assertEqual(flag.flagged_by, self.leader)
        self.assertEqual(flag.event, self.event)

    def test_leader_cannot_flag_on_a_vakt_they_do_not_lead(self):
        response = self._flag(self.other_leader, shift=self.shift)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Commendation.objects.exists())

    def test_plain_volunteer_cannot_flag(self):
        response = self._flag(self.bystander)

        self.assertEqual(response.status_code, 403)

    def test_cannot_flag_someone_not_assigned_to_the_vakt(self):
        response = self._flag(self.leader, user=self.bystander)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Commendation.objects.exists())

    def test_cannot_flag_yourself(self):
        Assignment.objects.create(
            oppgave_slot=make_slot(self.shift, skill_name="Servering"), user=self.leader, confirmed_by=self.organizer
        )

        response = self._flag(self.leader, user=self.leader)

        self.assertEqual(response.status_code, 400)

    def test_admin_can_flag_anyone_assigned_on_their_event(self):
        response = self._flag(self.organizer)

        self.assertEqual(response.status_code, 201)

    def test_flagged_volunteer_cannot_see_their_own_flag(self):
        self._flag(self.leader)
        flag = Commendation.objects.get()
        self.client.force_authenticate(user=self.volunteer)

        self.assertEqual(self.client.get("/api/commendations/").data, [])
        self.assertEqual(self.client.get(f"/api/commendations/{flag.id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/commendations/?user={self.volunteer.id}").data, [])

    def test_flag_is_not_exposed_through_the_users_endpoint(self):
        self._flag(self.leader, note="Hemmelig")
        self.client.force_authenticate(user=self.volunteer)

        me = self.client.get("/api/users/me/")

        self.assertNotIn("Hemmelig", str(me.data))
        self.assertNotIn("commendation", str(me.data).lower())

    def test_a_different_leader_cannot_see_someone_elses_flag(self):
        self._flag(self.leader)
        self.client.force_authenticate(user=self.other_leader)

        self.assertEqual(self.client.get("/api/commendations/").data, [])

    def test_leader_sees_only_their_own_flags_and_admin_sees_all(self):
        self._flag(self.leader)
        self._flag(self.organizer)

        self.client.force_authenticate(user=self.leader)
        self.assertEqual(len(self.client.get("/api/commendations/").data), 1)
        self.client.force_authenticate(user=self.organizer)
        self.assertEqual(len(self.client.get("/api/commendations/").data), 2)

    def test_list_can_be_filtered_by_event_and_shift(self):
        self._flag(self.organizer)
        self.client.force_authenticate(user=self.organizer)

        self.assertEqual(len(self.client.get(f"/api/commendations/?event={self.event.id}").data), 1)
        self.assertEqual(len(self.client.get(f"/api/commendations/?shift={self.other_shift.id}").data), 0)

    def test_author_can_edit_the_note_but_not_retarget_the_flag(self):
        self._flag(self.leader, note="Før")
        flag = Commendation.objects.get()
        self.client.force_authenticate(user=self.leader)

        ok = self.client.patch(f"/api/commendations/{flag.id}/", {"note": "Etter"}, format="json")
        bad = self.client.patch(f"/api/commendations/{flag.id}/", {"user": self.bystander.id}, format="json")

        self.assertEqual(ok.status_code, 200)
        self.assertEqual(bad.status_code, 400)
        flag.refresh_from_db()
        self.assertEqual(flag.note, "Etter")
        self.assertEqual(flag.user, self.volunteer)

    def test_author_and_admin_can_delete_but_another_leader_cannot(self):
        self._flag(self.leader)
        flag = Commendation.objects.get()

        self.client.force_authenticate(user=self.other_leader)
        self.assertEqual(self.client.delete(f"/api/commendations/{flag.id}/").status_code, 404)
        self.assertTrue(Commendation.objects.exists())

        self.client.force_authenticate(user=self.leader)
        self.assertEqual(self.client.delete(f"/api/commendations/{flag.id}/").status_code, 204)

        self._flag(self.leader)
        self.client.force_authenticate(user=self.organizer)
        self.assertEqual(self.client.delete(f"/api/commendations/{Commendation.objects.get().id}/").status_code, 204)

    def test_flag_survives_the_assignment_being_removed(self):
        self._flag(self.leader)
        Assignment.objects.all().delete()

        self.assertEqual(Commendation.objects.count(), 1)

    def test_unauthenticated_cannot_list(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/commendations/").status_code, 401)


class RatingTests(TestCase):
    """Post-event ratings: per person, per vakt, per leader. Strictly
    internal -- the rated volunteer (or anyone who isn't an admin, the
    rater, or -- when the event allows it -- a fellow leader of that vakt)
    must never be able to read one."""

    def setUp(self):
        self.organizer = User.objects.create_user(username="organizer", password="pw")
        self.leader = User.objects.create_user(username="leader", password="pw")
        self.co_leader = User.objects.create_user(username="co_leader", password="pw")
        self.other_leader = User.objects.create_user(username="other_leader", password="pw")
        self.volunteer = User.objects.create_user(username="volunteer", password="pw", first_name="Kari", last_name="Nordmann")
        self.bystander = User.objects.create_user(username="bystander", password="pw")
        self.event = make_event(title="Alternativ Jul", created_by=self.organizer)
        today = datetime.date.today()
        self.shift = Shift.objects.create(
            event=self.event, title="Kjøkken", date=today,
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=self.organizer,
        )
        self.second_shift = Shift.objects.create(
            event=self.event, title="Vertskap", date=today + datetime.timedelta(days=1),
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=self.organizer,
        )
        self.shift.leaders.add(self.leader, self.co_leader)
        self.second_shift.leaders.add(self.other_leader)
        self.slot = make_slot(self.shift, skill_name="Kokk")
        self.second_slot = make_slot(self.second_shift, skill_name="Vert")
        Assignment.objects.create(oppgave_slot=self.slot, user=self.volunteer, confirmed_by=self.organizer)
        Assignment.objects.create(oppgave_slot=self.second_slot, user=self.volunteer, confirmed_by=self.organizer)
        self.client = APIClient()

    def _rate(self, as_user, shift=None, user=None, **extra):
        self.client.force_authenticate(user=as_user)
        body = {"shift": (shift or self.shift).id, "user": (user or self.volunteer).id, "level": "god", **extra}
        return self.client.post("/api/ratings/", body, format="json")

    def _list(self, as_user, query=""):
        self.client.force_authenticate(user=as_user)
        response = self.client.get(f"/api/ratings/{query}")
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_leader_can_rate_someone_assigned_on_their_vakt(self):
        response = self._rate(self.leader, level="utmerket", note="Ledet kjøkkenet")

        self.assertEqual(response.status_code, 201)
        rating = Rating.objects.get()
        self.assertEqual(rating.user, self.volunteer)
        self.assertEqual(rating.rated_by, self.leader)
        self.assertEqual(rating.event, self.event)
        self.assertEqual(rating.oppgave_slot, self.slot)
        self.assertEqual(rating.level, "utmerket")
        self.assertEqual(response.data["user_name"], "Kari Nordmann")
        self.assertEqual(response.data["skill_name"], "Kokk")

    def test_admin_can_rate_on_any_vakt(self):
        self.assertEqual(self._rate(self.organizer, shift=self.second_shift).status_code, 201)

    def test_client_cannot_set_rated_by_or_event(self):
        response = self._rate(self.leader, rated_by=self.organizer.id, event=999)

        self.assertEqual(response.status_code, 201)
        rating = Rating.objects.get()
        self.assertEqual(rating.rated_by, self.leader)
        self.assertEqual(rating.event, self.event)

    def test_leader_cannot_rate_on_a_vakt_they_do_not_lead(self):
        response = self._rate(self.other_leader, shift=self.shift)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Rating.objects.exists())

    def test_plain_volunteer_cannot_rate(self):
        self.assertEqual(self._rate(self.bystander).status_code, 403)

    def test_cannot_rate_someone_not_assigned_to_the_vakt(self):
        response = self._rate(self.leader, user=self.bystander)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Rating.objects.exists())

    def test_cannot_rate_yourself(self):
        Assignment.objects.create(
            oppgave_slot=make_slot(self.shift, skill_name="Oppvask"), user=self.leader, confirmed_by=self.organizer
        )
        self.assertEqual(self._rate(self.leader, user=self.leader).status_code, 400)

    def test_invalid_level_rejected(self):
        self.assertEqual(self._rate(self.leader, level="fantastisk").status_code, 400)

    def test_level_is_required(self):
        self.client.force_authenticate(user=self.leader)
        response = self.client.post(
            "/api/ratings/", {"shift": self.shift.id, "user": self.volunteer.id}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_bekymring_requires_a_note(self):
        self.assertEqual(self._rate(self.leader, level="bekymring").status_code, 400)
        self.assertEqual(self._rate(self.leader, level="bekymring", note="   ").status_code, 400)
        self.assertFalse(Rating.objects.exists())

        response = self._rate(self.leader, level="bekymring", note="Dukket ikke opp til oppvasken")
        self.assertEqual(response.status_code, 201)

    def test_other_levels_do_not_require_a_note(self):
        for level in ("utmerket", "god", "ok"):
            Rating.objects.all().delete()
            self.assertEqual(self._rate(self.leader, level=level).status_code, 201, level)

    def test_one_rating_per_leader_per_vakt(self):
        self.assertEqual(self._rate(self.leader).status_code, 201)

        self.assertEqual(self._rate(self.leader, level="ok").status_code, 400)
        self.assertEqual(Rating.objects.count(), 1)

    def test_two_leaders_of_same_vakt_can_each_rate(self):
        self.assertEqual(self._rate(self.leader).status_code, 201)
        self.assertEqual(self._rate(self.co_leader, level="ok").status_code, 201)

        self.assertEqual(Rating.objects.count(), 2)

    def test_same_person_can_be_rated_on_two_vakter_by_different_leaders(self):
        self.assertEqual(self._rate(self.leader).status_code, 201)
        self.assertEqual(self._rate(self.other_leader, shift=self.second_shift, level="utmerket").status_code, 201)

        self.assertEqual(Rating.objects.filter(user=self.volunteer).count(), 2)

    def test_rated_volunteer_cannot_see_their_own_rating(self):
        self._rate(self.leader)

        self.assertEqual(self._list(self.volunteer), [])
        self.client.force_authenticate(user=self.volunteer)
        rating = Rating.objects.get()
        self.assertEqual(self.client.get(f"/api/ratings/{rating.id}/").status_code, 404)

    def test_bystander_sees_nothing(self):
        self._rate(self.leader)
        self.assertEqual(self._list(self.bystander), [])

    def test_admin_sees_all_ratings(self):
        self._rate(self.leader)
        self._rate(self.other_leader, shift=self.second_shift)

        self.assertEqual(len(self._list(self.organizer)), 2)

    def test_leader_sees_only_own_by_default(self):
        self._rate(self.leader)
        self._rate(self.co_leader, level="ok")
        self._rate(self.other_leader, shift=self.second_shift)

        rows = self._list(self.leader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rated_by_email"], self.leader.email)

    def test_team_visibility_shows_fellow_leaders_ratings_on_own_vakt_only(self):
        self._rate(self.leader)
        self._rate(self.co_leader, level="ok")
        self._rate(self.other_leader, shift=self.second_shift)
        self.event.rating_visibility = Event.RATING_VISIBILITY_TEAM
        self.event.save()

        rows = self._list(self.leader)
        self.assertEqual({r["shift"] for r in rows}, {self.shift.id})
        self.assertEqual(len(rows), 2)

    def test_team_visibility_does_not_expose_ratings_to_the_volunteer(self):
        self._rate(self.leader)
        self.event.rating_visibility = Event.RATING_VISIBILITY_TEAM
        self.event.save()

        self.assertEqual(self._list(self.volunteer), [])

    def test_list_filters(self):
        self._rate(self.leader)
        self._rate(self.other_leader, shift=self.second_shift)

        self.assertEqual(len(self._list(self.organizer, f"?shift={self.second_shift.id}")), 1)
        self.assertEqual(len(self._list(self.organizer, f"?user={self.volunteer.id}")), 2)
        self.assertEqual(len(self._list(self.organizer, f"?event={self.event.id}")), 2)

    def test_author_can_edit_level_and_note(self):
        rating_id = self._rate(self.leader).data["id"]

        response = self.client.patch(f"/api/ratings/{rating_id}/", {"level": "utmerket", "note": "Bedre enn først antatt"}, format="json")

        self.assertEqual(response.status_code, 200)
        rating = Rating.objects.get()
        self.assertEqual((rating.level, rating.note), ("utmerket", "Bedre enn først antatt"))

    def test_editing_to_bekymring_without_note_rejected(self):
        rating_id = self._rate(self.leader).data["id"]

        response = self.client.patch(f"/api/ratings/{rating_id}/", {"level": "bekymring"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Rating.objects.get().level, "god")

    def test_clearing_note_on_a_bekymring_rejected(self):
        rating_id = self._rate(self.leader, level="bekymring", note="Kom for sent").data["id"]

        response = self.client.patch(f"/api/ratings/{rating_id}/", {"note": ""}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Rating.objects.get().note, "Kom for sent")

    def test_cannot_retarget_a_rating(self):
        rating_id = self._rate(self.leader).data["id"]

        response = self.client.patch(f"/api/ratings/{rating_id}/", {"user": self.bystander.id}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Rating.objects.get().user, self.volunteer)

    def test_other_leader_cannot_edit_or_delete_even_if_they_can_see_it(self):
        rating_id = self._rate(self.leader).data["id"]
        self.event.rating_visibility = Event.RATING_VISIBILITY_TEAM
        self.event.save()

        self.client.force_authenticate(user=self.co_leader)
        self.assertEqual(self.client.patch(f"/api/ratings/{rating_id}/", {"level": "ok"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/ratings/{rating_id}/").status_code, 403)
        self.assertEqual(Rating.objects.get().level, "god")

    def test_author_and_admin_can_delete(self):
        first = self._rate(self.leader).data["id"]
        second = self._rate(self.co_leader).data["id"]

        self.client.force_authenticate(user=self.leader)
        self.assertEqual(self.client.delete(f"/api/ratings/{first}/").status_code, 204)
        self.client.force_authenticate(user=self.organizer)
        self.assertEqual(self.client.delete(f"/api/ratings/{second}/").status_code, 204)
        self.assertFalse(Rating.objects.exists())

    def test_rating_survives_assignment_removal(self):
        self._rate(self.leader)
        Assignment.objects.filter(user=self.volunteer, oppgave_slot=self.slot).delete()

        self.assertEqual(Rating.objects.count(), 1)

    def test_rating_visibility_is_an_admin_editable_event_setting(self):
        self.client.force_authenticate(user=self.organizer)
        response = self.client.patch(f"/api/events/{self.event.id}/", {"rating_visibility": "team"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.rating_visibility, "team")

        self.client.force_authenticate(user=self.leader)
        response = self.client.patch(f"/api/events/{self.event.id}/", {"rating_visibility": "own"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_gets_401(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/ratings/").status_code, 401)


class StandingTests(TestCase):
    """Internal volunteer standing, derived from ratings, check-ins and flags
    (api/standing.py). Admin-only; the volunteer never sees it."""

    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="pw")
        self.other_admin = User.objects.create_user(username="other_admin", password="pw")
        self.leader = User.objects.create_user(username="leader", password="pw")
        self.anchor = User.objects.create_user(username="anchor", password="pw")  # always shows up
        self.volunteer = User.objects.create_user(username="volunteer", password="pw", first_name="Kari", last_name="Nordmann")
        self.event = make_event(title="Alternativ Jul", created_by=self.admin)
        self.other_event = make_event(title="Annet", created_by=self.other_admin)
        self.client = APIClient()
        self._day = 0

    def _shift(self, event=None, days_ago=1):
        event = event or self.event
        shift = Shift.objects.create(
            event=event, title=f"Vakt {self._day}", date=datetime.date.today() - datetime.timedelta(days=days_ago),
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=event.created_by,
        )
        self._day += 1
        return shift

    def _worked(self, user, *, showed_up, event=None, days_ago=None, anchor_present=True):
        """A past vakt the user signed up for; showed_up controls whether they
        checked in (and so, as in real use, got an Assignment), anchor_present
        whether anyone at all checked in that day."""
        event = event or self.event
        self._day += 1
        shift = self._shift(event, days_ago=days_ago if days_ago is not None else self._day)
        slot = make_slot(shift, skill_name=f"Oppgave {self._day}")
        ShiftSignup.objects.create(oppgave_slot=slot, user=user)
        if showed_up:
            Assignment.objects.create(oppgave_slot=slot, user=user, confirmed_by=event.created_by)
        if anchor_present:
            EventCheckIn.objects.get_or_create(event=event, user=self.anchor, date=shift.date)
        if showed_up:
            EventCheckIn.objects.get_or_create(event=event, user=user, date=shift.date)
        return shift

    def _rate(self, shift, level, user=None, event=None, note=""):
        if level == "bekymring" and not note:
            note = "Se notat"
        return Rating.objects.create(
            event=event or self.event, user=user or self.volunteer, shift=shift, rated_by=self.leader, level=level, note=note
        )

    def _row(self, as_user=None, user=None):
        self.client.force_authenticate(user=as_user or self.admin)
        response = self.client.get(f"/api/standings/{(user or self.volunteer).id}/")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_volunteer_with_only_an_upcoming_assignment_is_new(self):
        shift = self._shift(days_ago=-3)
        ShiftSignup.objects.create(oppgave_slot=make_slot(shift), user=self.volunteer)

        row = self._row()
        self.assertEqual(row["standing"], "new")
        self.assertEqual(row["upcoming"], 1)
        self.assertIsNone(row["no_show_rate"])

    def test_attending_makes_good_standing(self):
        self._worked(self.volunteer, showed_up=True)

        row = self._row()
        self.assertEqual(row["standing"], "good")
        self.assertEqual((row["attended"], row["no_shows"], row["no_show_rate"]), (1, 0, 0.0))
        self.assertEqual(row["reasons"], [])

    def test_no_show_when_others_checked_in_but_they_did_not(self):
        self._worked(self.volunteer, showed_up=False)

        row = self._row()
        self.assertEqual(row["no_shows"], 1)
        self.assertEqual(row["no_show_rate"], 1.0)
        self.assertEqual(row["standing"], "concern")
        self.assertEqual(row["reasons"], ["no_shows"])

    def test_day_with_no_check_ins_at_all_counts_as_no_show(self):
        # Check-in is expected every day, so nobody checking in is absence,
        # not "unknown" (an admin can excuse the day in bulk if need be).
        self._worked(self.volunteer, showed_up=False, anchor_present=False)

        row = self._row()
        self.assertEqual(row["no_shows"], 1)
        self.assertEqual(row["standing"], "concern")

    def test_excused_absence_does_not_count_and_is_reported(self):
        shift = self._worked(self.volunteer, showed_up=False)
        AbsenceExcuse.objects.create(event=self.event, user=self.volunteer, date=shift.date, excused_by=self.admin)

        row = self._row()
        self.assertEqual((row["no_shows"], row["excused"]), (0, 1))
        self.assertIsNone(row["no_show_rate"])
        self.assertEqual(row["standing"], "new")  # nothing left to judge by

    def test_excused_days_drop_out_of_the_rate_but_other_days_still_count(self):
        for _ in range(3):
            self._worked(self.volunteer, showed_up=True)
        excused = self._worked(self.volunteer, showed_up=False)
        self._worked(self.volunteer, showed_up=False)
        AbsenceExcuse.objects.create(event=self.event, user=self.volunteer, date=excused.date, excused_by=self.admin)

        row = self._row()
        self.assertEqual((row["attended"], row["no_shows"], row["excused"]), (3, 1, 1))
        self.assertEqual(row["no_show_rate"], 0.25)
        self.assertEqual(row["standing"], "good")

    def test_no_show_threshold_boundary(self):
        # 1 no-show in 4 judged days = 0.25, which is not *over* the threshold.
        for _ in range(3):
            self._worked(self.volunteer, showed_up=True)
        self._worked(self.volunteer, showed_up=False)
        self.assertEqual(self._row()["standing"], "good")

        # 1 in 3 = 0.33 is over it.
        # Drop one attended day (the most recent; the no-show is the oldest).
        attended_day = ShiftSignup.objects.filter(user=self.volunteer).order_by("-date").first().date
        for model in (ShiftSignup, Assignment, EventCheckIn):
            model.objects.filter(user=self.volunteer, date=attended_day).delete()
        row = self._row()
        self.assertEqual(row["no_show_rate"], 0.333)
        self.assertEqual(row["standing"], "concern")

    def test_withdrawn_signup_is_not_a_no_show(self):
        shift = self._worked(self.volunteer, showed_up=False)
        ShiftSignup.objects.filter(shift=shift, user=self.volunteer).delete()
        self._rate(shift, "ok")

        row = self._row()
        self.assertEqual(row["no_shows"], 0)
        self.assertEqual(row["standing"], "good")

    def test_bekymring_rating_means_concern_even_with_perfect_attendance(self):
        shift = self._worked(self.volunteer, showed_up=True)
        self._rate(shift, "bekymring", note="Uhøflig mot gjester")

        row = self._row()
        self.assertEqual(row["standing"], "concern")
        self.assertEqual(row["reasons"], ["bekymring"])
        self.assertEqual(row["ratings"]["bekymring"], 1)

    def test_both_reasons_are_reported(self):
        shift = self._worked(self.volunteer, showed_up=False)
        self._rate(shift, "bekymring")

        self.assertEqual(self._row()["reasons"], ["bekymring", "no_shows"])

    def test_good_ratings_alone_give_good_standing(self):
        shift = self._shift()
        self._rate(shift, "utmerket")

        row = self._row()
        self.assertEqual(row["standing"], "good")
        self.assertEqual(row["ratings"]["utmerket"], 1)

    def test_checking_in_without_signing_up_counts_as_attendance_not_a_no_show(self):
        shift = self._shift()
        EventCheckIn.objects.create(event=self.event, user=self.volunteer, date=shift.date)

        row = self._row()
        self.assertEqual((row["walk_ins"], row["attended"], row["no_shows"]), (1, 0, 0))
        self.assertEqual(row["standing"], "good")

    def test_several_signups_for_one_missed_day_is_one_no_show(self):
        shift = self._shift()
        for name in ("Kokk", "Vert", "Oppvask"):
            ShiftSignup.objects.create(oppgave_slot=make_slot(shift, skill_name=name), user=self.volunteer)
        EventCheckIn.objects.create(event=self.event, user=self.anchor, date=shift.date)

        self.assertEqual(self._row()["no_shows"], 1)

    def test_commendations_are_counted_but_do_not_change_standing(self):
        shift = self._worked(self.volunteer, showed_up=True)
        Commendation.objects.create(event=self.event, user=self.volunteer, shift=shift, flagged_by=self.leader)

        row = self._row()
        self.assertEqual(row["commendations"], 1)
        self.assertEqual(row["standing"], "good")

    def test_override_wins_over_computed_and_keeps_computed_visible(self):
        shift = self._worked(self.volunteer, showed_up=True)
        self._rate(shift, "bekymring")
        self.client.force_authenticate(user=self.admin)

        response = self.client.put(
            f"/api/standings/{self.volunteer.id}/override/", {"level": "good", "note": "Engangshendelse, avklart"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["standing"], "good")
        self.assertEqual(response.data["computed"], "concern")
        self.assertEqual(response.data["override_note"], "Engangshendelse, avklart")
        self.assertEqual(StandingOverride.objects.get().set_by, self.admin)

    def test_override_can_flag_concern_on_someone_who_looks_fine(self):
        self._worked(self.volunteer, showed_up=True)
        self.client.force_authenticate(user=self.admin)

        response = self.client.put(
            f"/api/standings/{self.volunteer.id}/override/", {"level": "concern", "note": "Meldt av gjest"}, format="json"
        )

        self.assertEqual(response.data["standing"], "concern")
        self.assertEqual(response.data["computed"], "good")

    def test_override_requires_valid_level_and_a_note(self):
        self._worked(self.volunteer, showed_up=True)
        self.client.force_authenticate(user=self.admin)
        url = f"/api/standings/{self.volunteer.id}/override/"

        self.assertEqual(self.client.put(url, {"level": "good", "note": ""}, format="json").status_code, 400)
        self.assertEqual(self.client.put(url, {"level": "good", "note": "   "}, format="json").status_code, 400)
        self.assertEqual(self.client.put(url, {"level": "excellent", "note": "x"}, format="json").status_code, 400)
        self.assertEqual(self.client.put(url, {"level": "good", "note": "x" * 301}, format="json").status_code, 400)
        self.assertFalse(StandingOverride.objects.exists())

    def test_override_can_be_updated_and_cleared(self):
        shift = self._worked(self.volunteer, showed_up=True)
        self._rate(shift, "bekymring")
        self.client.force_authenticate(user=self.admin)
        url = f"/api/standings/{self.volunteer.id}/override/"
        self.client.put(url, {"level": "good", "note": "Første"}, format="json")
        self.client.put(url, {"level": "concern", "note": "Andre"}, format="json")

        self.assertEqual(StandingOverride.objects.count(), 1)
        self.assertEqual(StandingOverride.objects.get().note, "Andre")

        self.assertEqual(self.client.delete(url).status_code, 204)
        row = self._row()
        self.assertIsNone(row["override"])
        self.assertEqual(row["standing"], "concern")  # back to the automatic one

    def test_list_filters_by_standing_and_sorts_by_name(self):
        good = User.objects.create_user(username="good", password="pw", first_name="Anne", last_name="Berg")
        self._worked(good, showed_up=True)
        self._worked(self.volunteer, showed_up=False)
        self.client.force_authenticate(user=self.admin)

        everyone = self.client.get("/api/standings/").data
        self.assertEqual([r["user_name"] for r in everyone if r["user"] in (good.id, self.volunteer.id)], ["Anne Berg", "Kari Nordmann"])

        concerned = self.client.get("/api/standings/?standing=concern").data
        self.assertEqual([r["user"] for r in concerned], [self.volunteer.id])

    def test_non_admins_get_an_empty_list_and_404(self):
        self._worked(self.volunteer, showed_up=True)
        self.event.shifts.first().leaders.add(self.leader)

        for who in (self.leader, self.volunteer):
            self.client.force_authenticate(user=who)
            self.assertEqual(self.client.get("/api/standings/").data, [])
            self.assertEqual(self.client.get(f"/api/standings/{self.volunteer.id}/").status_code, 404)
            self.assertEqual(
                self.client.put(f"/api/standings/{self.volunteer.id}/override/", {"level": "good", "note": "x"}, format="json").status_code,
                404,
            )
        self.assertFalse(StandingOverride.objects.exists())

    def test_admin_only_sees_people_and_data_from_their_own_events(self):
        # Kari worked event A fine, but was rated Bekymring at event B.
        self._worked(self.volunteer, showed_up=True)
        other_shift = self._worked(self.volunteer, showed_up=True, event=self.other_event)
        self._rate(other_shift, "bekymring", event=self.other_event)

        self.assertEqual(self._row(self.admin)["standing"], "good")  # B's rating isn't theirs to count
        self.assertEqual(self._row(self.other_admin)["standing"], "concern")

        stranger = User.objects.create_user(username="stranger", password="pw")
        self._worked(stranger, showed_up=True, event=self.other_event)
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get(f"/api/standings/{stranger.id}/").status_code, 404)
        self.assertNotIn(stranger.id, [r["user"] for r in self.client.get("/api/standings/").data])

    def test_unauthenticated_gets_401(self):
        self.assertEqual(self.client.get("/api/standings/").status_code, 401)


class AttendanceTests(TestCase):
    """Per-day attendance for an event: who signed up, who arrived, who
    didn't. Admin-only; the data behind "no-show" in standing."""

    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="pw")
        self.other_admin = User.objects.create_user(username="other_admin", password="pw")
        self.leader = User.objects.create_user(username="leader", password="pw")
        self.kari = User.objects.create_user(username="kari", password="pw", first_name="Kari", last_name="Nordmann")
        self.ola = User.objects.create_user(username="ola", password="pw", first_name="Ola", last_name="Hansen")
        self.walkin = User.objects.create_user(username="walkin", password="pw", first_name="Per", last_name="Spontan")
        self.event = make_event(title="Alternativ Jul", created_by=self.admin)
        make_event(title="Annet", created_by=self.other_admin)
        self.client = APIClient()
        self.today = datetime.date.today()

    def _day(self, days_ago):
        shift = Shift.objects.create(
            event=self.event, title=f"Vakt {days_ago}", date=self.today - datetime.timedelta(days=days_ago),
            start_time=datetime.time(18, 0), end_time=datetime.time(22, 0), created_by=self.admin,
        )
        return shift, make_slot(shift, skill_name="Kjøkken")

    def _get(self, as_user=None):
        self.client.force_authenticate(user=as_user or self.admin)
        return self.client.get(f"/api/events/{self.event.id}/attendance/")

    def test_marks_who_showed_up_and_who_did_not(self):
        shift, slot = self._day(2)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.kari)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.ola)
        Assignment.objects.create(oppgave_slot=slot, user=self.kari, confirmed_by=self.admin)
        EventCheckIn.objects.create(event=self.event, user=self.kari, date=shift.date)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        (day,) = response.data["days"]
        self.assertEqual(day["date"], shift.date.isoformat())
        self.assertEqual((day["signed_up"], day["checked_in"], day["no_shows"], day["excused"]), (2, 1, 1, 0))
        by_name = {p["user_name"]: p for p in day["people"]}
        self.assertEqual(by_name["Ola Hansen"]["status"], "no_show")
        self.assertEqual(by_name["Kari Nordmann"]["status"], "attended")
        self.assertEqual(by_name["Kari Nordmann"]["assigned"], "Kjøkken")
        self.assertEqual(day["people"][0]["user_name"], "Ola Hansen")  # absentees sort first

    def test_walk_in_without_signup_is_listed_and_not_counted_as_signed_up(self):
        shift, slot = self._day(1)
        EventCheckIn.objects.create(event=self.event, user=self.walkin, date=shift.date)

        (day,) = self._get().data["days"]
        self.assertEqual(day["people"][0]["status"], "walk_in")
        self.assertEqual((day["signed_up"], day["checked_in"], day["no_shows"]), (0, 1, 0))

    def test_day_without_any_check_ins_reads_as_everyone_absent(self):
        shift, slot = self._day(3)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.kari)

        (day,) = self._get().data["days"]
        self.assertEqual(day["no_shows"], 1)
        self.assertEqual(day["people"][0]["status"], "no_show")

    def test_upcoming_day_is_not_a_no_show(self):
        shift, slot = self._day(-2)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.kari)

        (day,) = self._get().data["days"]
        self.assertEqual(day["people"][0]["status"], "upcoming")

    def test_withdrawn_signup_disappears(self):
        shift, slot = self._day(2)
        signup = ShiftSignup.objects.create(oppgave_slot=slot, user=self.kari)
        EventCheckIn.objects.create(event=self.event, user=self.ola, date=shift.date)
        signup.delete()

        (day,) = self._get().data["days"]
        self.assertEqual([p["user_name"] for p in day["people"]], ["Ola Hansen"])

    def test_days_are_returned_in_date_order(self):
        for days_ago in (1, 5, 3):
            shift, slot = self._day(days_ago)
            ShiftSignup.objects.create(oppgave_slot=slot, user=self.kari)
        dates = [d["date"] for d in self._get().data["days"]]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), 3)

    def test_only_event_admins_can_see_it(self):
        self.assertEqual(self._get(self.leader).status_code, 403)
        self.assertEqual(self._get(self.kari).status_code, 403)
        self.assertIn(self._get(self.other_admin).status_code, (403, 404))
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(f"/api/events/{self.event.id}/attendance/").status_code, 401)

    # --- excusing absences -------------------------------------------------

    def _missed_day(self, days_ago=2):
        """Kari signed up and never came; Ola came."""
        shift, slot = self._day(days_ago)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.kari)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.ola)
        EventCheckIn.objects.create(event=self.event, user=self.ola, date=shift.date)
        return shift

    def _excuse(self, body, as_user=None, method="post"):
        self.client.force_authenticate(user=as_user or self.admin)
        return getattr(self.client, method)(f"/api/events/{self.event.id}/attendance/excuse/", body, format="json")

    def test_admin_can_excuse_one_absence_with_a_note(self):
        shift = self._missed_day()

        response = self._excuse({"user_id": self.kari.id, "date": shift.date.isoformat(), "note": "Syk, ringte kl 16"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"excused": 1})
        excuse = AbsenceExcuse.objects.get()
        self.assertEqual((excuse.user, excuse.excused_by, excuse.note), (self.kari, self.admin, "Syk, ringte kl 16"))
        (day,) = self._get().data["days"]
        kari = next(p for p in day["people"] if p["user"] == self.kari.id)
        self.assertEqual((kari["status"], kari["excuse_note"]), ("excused", "Syk, ringte kl 16"))
        self.assertEqual((day["no_shows"], day["excused"]), (0, 1))

    def test_note_is_optional(self):
        shift = self._missed_day()
        self.assertEqual(self._excuse({"user_id": self.kari.id, "date": shift.date.isoformat()}).status_code, 200)

    def test_excusing_twice_is_harmless_but_the_second_is_rejected_as_not_absent(self):
        shift = self._missed_day()
        body = {"user_id": self.kari.id, "date": shift.date.isoformat()}
        self._excuse(body)

        self.assertEqual(self._excuse(body).status_code, 400)
        self.assertEqual(AbsenceExcuse.objects.count(), 1)

    def test_cannot_excuse_someone_who_attended_or_never_signed_up_or_is_upcoming(self):
        shift = self._missed_day()
        date = shift.date.isoformat()
        self.assertEqual(self._excuse({"user_id": self.ola.id, "date": date}).status_code, 400)  # attended
        self.assertEqual(self._excuse({"user_id": self.walkin.id, "date": date}).status_code, 400)  # never signed up
        future, slot = self._day(-2)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.kari)
        self.assertEqual(self._excuse({"user_id": self.kari.id, "date": future.date.isoformat()}).status_code, 400)
        self.assertFalse(AbsenceExcuse.objects.exists())

    def test_bulk_excuse_covers_every_no_show_that_day_only(self):
        shift = self._missed_day(days_ago=3)
        other_day = self._missed_day(days_ago=1)
        third, slot = self._day(5)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.walkin)
        ShiftSignup.objects.create(oppgave_slot=slot, user=self.kari)  # nobody checked in on that day

        response = self._excuse({"date": third.date.isoformat(), "all": True, "note": "Innsjekk ble ikke kjørt"})

        self.assertEqual(response.data, {"excused": 2})
        self.assertEqual(AbsenceExcuse.objects.filter(date=third.date).count(), 2)
        self.assertEqual(AbsenceExcuse.objects.count(), 2)  # the other days are untouched
        days = {d["date"]: d for d in self._get().data["days"]}
        self.assertEqual(days[shift.date.isoformat()]["no_shows"], 1)
        self.assertEqual(days[other_day.date.isoformat()]["no_shows"], 1)

    def test_excuse_can_be_withdrawn(self):
        shift = self._missed_day()
        body = {"user_id": self.kari.id, "date": shift.date.isoformat()}
        self._excuse(body)

        self.assertEqual(self._excuse(body, method="delete").status_code, 204)
        (day,) = self._get().data["days"]
        self.assertEqual(day["no_shows"], 1)
        self.assertEqual(self._excuse(body, method="delete").status_code, 404)

    def test_excuse_needs_a_valid_date_and_user(self):
        self._missed_day()
        self.assertEqual(self._excuse({"user_id": self.kari.id, "date": "garbage"}).status_code, 400)
        self.assertEqual(self._excuse({"user_id": self.kari.id}).status_code, 400)
        shift_date = (self.today - datetime.timedelta(days=2)).isoformat()
        self.assertEqual(self._excuse({"date": shift_date}).status_code, 400)
        self.assertEqual(self._excuse({"user_id": self.kari.id, "date": shift_date, "note": "x" * 301}).status_code, 400)

    def test_only_event_admins_can_excuse(self):
        shift = self._missed_day()
        body = {"user_id": self.kari.id, "date": shift.date.isoformat()}
        self.assertEqual(self._excuse(body, as_user=self.leader).status_code, 403)
        self.assertEqual(self._excuse(body, as_user=self.kari).status_code, 403)
        self.assertEqual(self._excuse(body, as_user=self.leader, method="delete").status_code, 403)
        self.assertFalse(AbsenceExcuse.objects.exists())


GOOGLE_WEB = "web-client.apps.googleusercontent.com"
GOOGLE_IOS = "ios-client.apps.googleusercontent.com"
MS_CLIENT = "00000000-ms-client"
MS_PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
MS_WORK_TENANT = "11111111-2222-3333-4444-555555555555"


def _rsa_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem, private.public_key()


class _FakeJwkClient:
    """Stands in for PyJWKClient: hands back our test public key instead of
    fetching the provider's key set over the network."""

    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return mock.Mock(key=self.public_key)


@override_settings(
    GOOGLE_OAUTH_WEB_CLIENT_ID=GOOGLE_WEB,
    GOOGLE_OAUTH_EXTRA_CLIENT_IDS=[GOOGLE_IOS],
    MICROSOFT_OAUTH_CLIENT_ID=MS_CLIENT,
)
class SocialTokenVerificationTests(TestCase):
    """api/social.py: ID tokens are checked for signature, issuer, audience
    and expiry, and the e-mail is only called verified when the provider
    really vouches for it."""

    @classmethod
    def setUpClass(cls):
        # Not setUpTestData: Django deep-copies those attributes per test,
        # and RSA key objects can't be copied. Generating keys is slow-ish,
        # so do it once per class.
        super().setUpClass()
        cls.private_pem, cls.public_key = _rsa_keypair()
        cls.other_pem, _ = _rsa_keypair()

    def setUp(self):
        patcher = mock.patch("api.social._jwk_client", return_value=_FakeJwkClient(self.public_key))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mint(self, claims, key=None):
        import jwt

        now = int(time.time())
        payload = {"iat": now, "exp": now + 600, **claims}
        return jwt.encode(payload, key or self.private_pem, algorithm="RS256", headers={"kid": "test"})

    def _google(self, **overrides):
        claims = {
            "iss": "https://accounts.google.com", "aud": GOOGLE_WEB, "sub": "g-123",
            "email": "Kari@Example.com", "email_verified": True, "given_name": "Kari", "family_name": "Nordmann",
        }
        claims.update(overrides)
        return self._mint(claims)

    def _microsoft(self, tenant=MS_PERSONAL_TENANT, **overrides):
        claims = {
            "iss": f"https://login.microsoftonline.com/{tenant}/v2.0", "tid": tenant, "aud": MS_CLIENT,
            "sub": "m-456", "email": "Ola@Example.com", "name": "Ola Hansen",
        }
        claims.update(overrides)
        return self._mint(claims)

    def test_valid_google_token(self):
        from .social import verify_id_token

        claims = verify_id_token("google", self._google())

        self.assertEqual(
            (claims.subject, claims.email, claims.email_verified, claims.first_name, claims.last_name),
            ("g-123", "kari@example.com", True, "Kari", "Nordmann"),
        )

    def test_google_accepts_any_configured_client_id_as_audience(self):
        from .social import verify_id_token

        self.assertEqual(verify_id_token("google", self._google(aud=GOOGLE_IOS)).subject, "g-123")

    def test_google_rejects_token_minted_for_another_app(self):
        from .social import SocialAuthError, verify_id_token

        with self.assertRaises(SocialAuthError) as ctx:
            verify_id_token("google", self._google(aud="someone-elses-app"))
        self.assertEqual(ctx.exception.code, "invalid_token")

    def test_google_rejects_wrong_issuer_expired_and_badly_signed_tokens(self):
        from .social import SocialAuthError, verify_id_token

        bad = [
            self._google(iss="https://evil.example.com"),
            self._google(exp=int(time.time()) - 60),
            self._mint(
                {"iss": "https://accounts.google.com", "aud": GOOGLE_WEB, "sub": "x", "email": "a@b.no"}, key=self.other_pem
            ),
            "not.a.jwt",
        ]
        for token in bad:
            with self.assertRaises(SocialAuthError):
                verify_id_token("google", token)

    def test_google_unverified_email_is_reported_as_such(self):
        from .social import verify_id_token

        self.assertFalse(verify_id_token("google", self._google(email_verified=False)).email_verified)
        self.assertTrue(verify_id_token("google", self._google(email_verified="true")).email_verified)
        claims = verify_id_token("google", self._google(email_verified=None))
        self.assertFalse(claims.email_verified)

    def test_microsoft_personal_account_email_is_verified_and_name_is_split(self):
        from .social import verify_id_token

        claims = verify_id_token("microsoft", self._microsoft())

        self.assertEqual((claims.email, claims.email_verified), ("ola@example.com", True))
        self.assertEqual((claims.first_name, claims.last_name), ("Ola", "Hansen"))

    def test_microsoft_personal_account_falls_back_to_preferred_username(self):
        from .social import verify_id_token

        token = self._microsoft(email=None, preferred_username="Ola@Outlook.com")
        claims = verify_id_token("microsoft", token)
        self.assertEqual((claims.email, claims.email_verified), ("ola@outlook.com", True))

    def test_microsoft_work_account_email_is_not_trusted_without_domain_proof(self):
        from .social import verify_id_token

        self.assertFalse(verify_id_token("microsoft", self._microsoft(tenant=MS_WORK_TENANT)).email_verified)
        proven = verify_id_token("microsoft", self._microsoft(tenant=MS_WORK_TENANT, xms_edov=True))
        self.assertTrue(proven.email_verified)

    def test_microsoft_work_account_never_trusts_preferred_username(self):
        from .social import verify_id_token

        token = self._microsoft(tenant=MS_WORK_TENANT, email=None, preferred_username="boss@victim.no")
        claims = verify_id_token("microsoft", token)
        self.assertEqual((claims.email, claims.email_verified), ("", False))

    def test_microsoft_rejects_issuer_that_does_not_match_tenant(self):
        from .social import SocialAuthError, verify_id_token

        token = self._microsoft(iss=f"https://login.microsoftonline.com/{MS_WORK_TENANT}/v2.0")  # tid says personal
        with self.assertRaises(SocialAuthError):
            verify_id_token("microsoft", token)

    def test_microsoft_rejects_wrong_audience(self):
        from .social import SocialAuthError, verify_id_token

        with self.assertRaises(SocialAuthError):
            verify_id_token("microsoft", self._microsoft(aud="another-client"))

    def test_unknown_provider_and_empty_token_rejected(self):
        from .social import SocialAuthError, verify_id_token

        for provider, token in (("apple", "x"), ("google", ""), ("google", None), ("google", 123)):
            with self.assertRaises(SocialAuthError):
                verify_id_token(provider, token)

    def test_unconfigured_provider_is_reported_as_not_set_up(self):
        from .social import SocialAuthError, verify_id_token

        with override_settings(GOOGLE_OAUTH_WEB_CLIENT_ID="", GOOGLE_OAUTH_EXTRA_CLIENT_IDS=[]):
            with self.assertRaises(SocialAuthError) as ctx:
                verify_id_token("google", self._google())
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("provider_not_configured", 503))
        with override_settings(MICROSOFT_OAUTH_CLIENT_ID=""):
            with self.assertRaises(SocialAuthError) as ctx:
                verify_id_token("microsoft", self._microsoft())
        self.assertEqual(ctx.exception.status, 503)


@override_settings(
    GOOGLE_OAUTH_WEB_CLIENT_ID=GOOGLE_WEB,
    MICROSOFT_OAUTH_CLIENT_ID=MS_CLIENT,
)
class SocialLoginTests(TestCase):
    """POST /api/auth/social/ and friends. The token check itself is covered
    above; here the verified claims are supplied directly."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def _claims(self, **overrides):
        from .social import SocialClaims

        base = dict(
            provider="google", subject="g-1", email="kari@example.com", email_verified=True,
            first_name="Kari", last_name="Nordmann",
        )
        base.update(overrides)
        return SocialClaims(**base)

    def _login(self, claims=None, **body):
        with mock.patch("api.social_views.verify_id_token", return_value=claims or self._claims()):
            return self.client.post(
                "/api/auth/social/", {"provider": "google", "id_token": "tok", **body}, format="json"
            )

    def test_unknown_email_creates_a_volunteer_account(self):
        response = self._login()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["created"])
        self.assertFalse(response.data["profile_complete"])
        user = User.objects.get(email="kari@example.com")
        self.assertEqual((user.username, user.first_name, user.last_name), ("kari@example.com", "Kari", "Nordmann"))
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertFalse(Membership.objects.filter(user=user).exists())
        identity = SocialIdentity.objects.get()
        self.assertEqual((identity.user, identity.provider, identity.subject), (user, "google", "g-1"))

    def test_issued_tokens_work_against_the_api(self):
        response = self._login()

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me = self.client.get("/api/users/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["email"], "kari@example.com")

    def test_create_false_does_not_create_an_account(self):
        response = self._login(create=False)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "no_account")
        self.assertFalse(User.objects.exists())
        self.assertFalse(SocialIdentity.objects.exists())

    def test_returning_identity_logs_in_even_if_the_provider_email_changed(self):
        first = self._login()
        user = User.objects.get(email="kari@example.com")

        again = self._login(self._claims(email="kari.new@example.com"))

        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.data["created"])
        self.assertEqual(again.data["user"]["id"], user.id)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(SocialIdentity.objects.count(), 1)
        self.assertEqual(first.data["user"]["id"], user.id)

    def test_returning_identity_still_requires_a_verified_email_only_for_first_link(self):
        self._login()
        again = self._login(self._claims(email_verified=False))
        self.assertEqual(again.status_code, 200)

    def test_passwordless_account_with_matching_email_is_linked_and_old_sessions_revoked(self):
        existing = User.objects.create_user(username="kari@example.com", email="kari@example.com")
        existing.set_unusable_password()
        existing.save()
        stale = RefreshToken.for_user(existing)  # e.g. the session a pre-registrant was handed

        response = self._login(self._claims(email="Kari@Example.com"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["created"])
        self.assertEqual(response.data["user"]["id"], existing.id)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(SocialIdentity.objects.get().user, existing)
        refresh = self.client.post("/api/token/refresh/", {"refresh": str(stale)}, format="json")
        self.assertEqual(refresh.status_code, 401)

    def test_account_with_a_password_is_not_auto_linked(self):
        existing = User.objects.create_user(username="kari@example.com", email="kari@example.com", password="s3cret-pass!")
        session = RefreshToken.for_user(existing)

        response = self._login()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "password_account")
        self.assertNotIn("access", response.data)
        self.assertFalse(SocialIdentity.objects.exists())
        still_valid = self.client.post("/api/token/refresh/", {"refresh": str(session)}, format="json")
        self.assertEqual(still_valid.status_code, 200)  # a refused attempt must not log anyone out

    def test_staff_account_with_password_cannot_be_taken_over_by_email_alone(self):
        admin = User.objects.create_user(username="boss@example.com", email="boss@example.com", password="s3cret-pass!")
        event = make_event(title="Jul", created_by=admin)

        response = self._login(self._claims(subject="attacker", email="boss@example.com"))

        self.assertEqual(response.status_code, 409)
        self.assertFalse(SocialIdentity.objects.exists())
        self.assertTrue(event.is_admin(admin))

    def test_unverified_email_is_refused(self):
        response = self._login(self._claims(email_verified=False))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "email_unverified")
        self.assertFalse(User.objects.exists())

    def test_missing_email_is_refused(self):
        self.assertEqual(self._login(self._claims(email="", email_verified=False)).status_code, 400)

    def test_inactive_account_cannot_sign_in(self):
        user = User.objects.create_user(username="kari@example.com", email="kari@example.com", is_active=False)
        user.set_unusable_password()
        user.save()

        response = self._login()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SocialIdentity.objects.exists())

        SocialIdentity.objects.create(user=user, provider="google", subject="g-1")
        self.assertEqual(self._login().status_code, 403)

    def test_same_subject_cannot_be_claimed_by_a_second_email_account(self):
        self._login()
        other = User.objects.create_user(username="ola@example.com", email="ola@example.com")
        other.set_unusable_password()
        other.save()

        response = self._login(self._claims(email="ola@example.com"))  # same Google subject

        self.assertEqual(response.data["user"]["email"], "kari@example.com")
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_verification_failure_is_reported_with_its_code(self):
        from .social import SocialAuthError

        with mock.patch("api.social_views.verify_id_token", side_effect=SocialAuthError("invalid_token", "nope")):
            response = self.client.post("/api/auth/social/", {"provider": "google", "id_token": "x"}, format="json")
        self.assertEqual((response.status_code, response.data["code"]), (400, "invalid_token"))

    def test_garbage_token_and_unknown_provider_rejected_without_mocks(self):
        for body in ({"provider": "google", "id_token": "garbage"}, {"provider": "apple", "id_token": "x"}, {}):
            response = self.client.post("/api/auth/social/", body, format="json")
            self.assertEqual(response.status_code, 400, body)
        self.assertFalse(User.objects.exists())

    def test_social_created_account_cannot_log_in_with_a_guessed_password(self):
        self._login()
        for guess in ("", "password", "kari@example.com"):
            response = self.client.post("/api/token/", {"email": "kari@example.com", "password": guess}, format="json")
            self.assertIn(response.status_code, (400, 401))
            self.assertNotIn("access", response.data)

    def test_profile_can_be_completed_afterwards(self):
        response = self._login()
        user_id = response.data["user"]["id"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

        patch = self.client.patch(
            f"/api/users/{user_id}/",
            {"phone": "12345678", "address": "Testveien 1, 0123 Oslo", "birthdate": "1990-05-15"},
            format="json",
        )
        self.assertEqual(patch.status_code, 200)

        self.client.credentials()
        again = self._login()
        self.assertTrue(again.data["profile_complete"])

    def test_microsoft_provider_creates_its_own_identity(self):
        self._login()
        response = self._login(self._claims(provider="microsoft", subject="m-9"))

        self.assertEqual(response.status_code, 200)  # same verified address -> same (passwordless) account
        self.assertEqual(SocialIdentity.objects.filter(user__email="kari@example.com").count(), 2)

    def test_providers_endpoint_lists_only_configured_providers_with_public_ids(self):
        response = self.client.get("/api/auth/social/providers/")
        self.assertEqual(
            response.data, {"google": {"client_id": GOOGLE_WEB}, "microsoft": {"client_id": MS_CLIENT}}
        )
        with override_settings(MICROSOFT_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_WEB_CLIENT_ID=""):
            self.assertEqual(self.client.get("/api/auth/social/providers/").data, {})


@override_settings(GOOGLE_OAUTH_WEB_CLIENT_ID=GOOGLE_WEB, MICROSOFT_OAUTH_CLIENT_ID=MS_CLIENT)
class SocialLinkingTests(TestCase):
    """Connecting / disconnecting a provider from a signed-in account -- how
    someone with a password (e.g. an admin) gets social sign-in."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="boss@example.com", email="boss@example.com", password="s3cret-pass!")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _claims(self, **overrides):
        from .social import SocialClaims

        base = dict(provider="google", subject="g-boss", email="boss@gmail.com", email_verified=True, first_name="", last_name="")
        base.update(overrides)
        return SocialClaims(**base)

    def _link(self, claims=None, provider="google"):
        with mock.patch("api.social_views.verify_id_token", return_value=claims or self._claims()):
            return self.client.post("/api/auth/social/identities/", {"provider": provider, "id_token": "t"}, format="json")

    def test_signed_in_user_can_link_a_provider_with_a_different_email(self):
        response = self._link()

        self.assertEqual(response.status_code, 201)
        self.assertEqual([i["provider"] for i in response.data], ["google"])
        identity = SocialIdentity.objects.get()
        self.assertEqual((identity.user, identity.subject), (self.user, "g-boss"))

    def test_linked_account_can_then_sign_in_with_the_provider_and_keeps_its_roles(self):
        event = make_event(title="Jul", created_by=self.user)
        self._link()

        self.client.force_authenticate(user=None)
        with mock.patch("api.social_views.verify_id_token", return_value=self._claims()):
            response = self.client.post(
                "/api/auth/social/", {"provider": "google", "id_token": "t", "create": False}, format="json"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user"]["id"], self.user.id)
        self.assertTrue(event.is_admin(self.user))

    def test_linking_is_idempotent_for_the_same_identity(self):
        self._link()
        again = self._link()

        self.assertEqual(again.status_code, 200)
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_identity_belonging_to_someone_else_cannot_be_linked(self):
        other = User.objects.create_user(username="ola@example.com", email="ola@example.com")
        SocialIdentity.objects.create(user=other, provider="google", subject="g-boss")

        response = self._link()

        self.assertEqual((response.status_code, response.data["code"]), (409, "already_linked"))
        self.assertEqual(SocialIdentity.objects.get().user, other)

    def test_only_one_identity_per_provider(self):
        self._link()
        response = self._link(self._claims(subject="g-second"))

        self.assertEqual((response.status_code, response.data["code"]), (409, "provider_already_linked"))

    def test_can_link_both_providers(self):
        self._link()
        response = self._link(self._claims(provider="microsoft", subject="m-1"), provider="microsoft")

        self.assertEqual([i["provider"] for i in response.data], ["google", "microsoft"])

    def test_list_shows_only_my_own_identities(self):
        self._link()
        other = User.objects.create_user(username="ola@example.com", email="ola@example.com")
        SocialIdentity.objects.create(user=other, provider="microsoft", subject="m-ola")

        response = self.client.get("/api/auth/social/identities/")
        self.assertEqual([i["provider"] for i in response.data], ["google"])

    def test_unlink(self):
        self._link()

        self.assertEqual(self.client.delete("/api/auth/social/identities/google/").status_code, 204)
        self.assertFalse(SocialIdentity.objects.exists())
        self.assertEqual(self.client.delete("/api/auth/social/identities/google/").status_code, 404)

    def test_cannot_unlink_the_only_way_to_sign_in(self):
        self.user.set_unusable_password()
        self.user.save()
        self._link()

        response = self.client.delete("/api/auth/social/identities/google/")

        self.assertEqual((response.status_code, response.data["code"]), (400, "last_login_method"))
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_can_unlink_one_of_two_without_a_password(self):
        self.user.set_unusable_password()
        self.user.save()
        self._link()
        self._link(self._claims(provider="microsoft", subject="m-1"), provider="microsoft")

        self.assertEqual(self.client.delete("/api/auth/social/identities/google/").status_code, 204)

    def test_cannot_unlink_someone_elses_identity(self):
        other = User.objects.create_user(username="ola@example.com", email="ola@example.com")
        SocialIdentity.objects.create(user=other, provider="google", subject="g-ola")

        self.assertEqual(self.client.delete("/api/auth/social/identities/google/").status_code, 404)
        self.assertEqual(SocialIdentity.objects.count(), 1)

    def test_link_endpoints_need_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/auth/social/identities/").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/social/identities/", {}, format="json").status_code, 401)
        self.assertEqual(self.client.delete("/api/auth/social/identities/google/").status_code, 401)


@override_settings(GOOGLE_OAUTH_WEB_CLIENT_ID=GOOGLE_WEB, MICROSOFT_OAUTH_CLIENT_ID=MS_CLIENT)
class LegacyEmailAccountsStillWorkTests(TestCase):
    """Social sign-in is purely additive: with Google/Microsoft switched on,
    accounts that only ever used an e-mail address -- with a password, or
    the passwordless website registration -- keep working exactly as before,
    and an account never needs a provider linked."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_email_and_password_account_logs_in_without_any_provider_linked(self):
        User.objects.create_user(username="old@example.com", email="old@example.com", password="s3cret-pass!")

        response = self.client.post("/api/token/", {"email": "old@example.com", "password": "s3cret-pass!"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertFalse(SocialIdentity.objects.exists())

    def test_passwordless_website_registration_still_works_and_gives_a_session(self):
        make_event(title="Jul", created_by=User.objects.create_user(username="org", password="pw"))

        # The website form sends no password (valid_registration_payload includes
        # one by default, so blank it to match what the site really submits).
        response = self.client.post("/api/register/", valid_registration_payload(password=""), format="json")

        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="new.volunteer@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.social_identities.exists())
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(self.client.get("/api/users/me/").status_code, 200)

    def test_passwordless_account_can_still_get_a_password_by_e_mail_link(self):
        user = User.objects.create_user(username="old@example.com", email="old@example.com")
        user.set_unusable_password()
        user.save()

        self.client.post("/api/password-setup/request/", {"email": "old@example.com"}, format="json")
        token = PasswordSetupToken.objects.get(user=user)
        confirm = self.client.post(
            "/api/password-setup/confirm/", {"token": str(token.token), "password": "Brand-new-pass-1"}, format="json"
        )
        self.assertEqual(confirm.status_code, 200)

        login = self.client.post("/api/token/", {"email": "old@example.com", "password": "Brand-new-pass-1"}, format="json")
        self.assertEqual(login.status_code, 200)

    def test_account_created_by_social_sign_in_can_add_an_e_mail_password_later(self):
        from .social import SocialClaims

        claims = SocialClaims("google", "g-1", "kari@example.com", True, "Kari", "Nordmann")
        with mock.patch("api.social_views.verify_id_token", return_value=claims):
            self.assertEqual(
                self.client.post("/api/auth/social/", {"provider": "google", "id_token": "t"}, format="json").status_code,
                201,
            )
        user = User.objects.get(email="kari@example.com")
        self.assertFalse(user.has_usable_password())

        self.client.post("/api/password-setup/request/", {"email": "kari@example.com"}, format="json")
        token = PasswordSetupToken.objects.get(user=user)
        self.client.post(
            "/api/password-setup/confirm/", {"token": str(token.token), "password": "Brand-new-pass-1"}, format="json"
        )

        login = self.client.post("/api/token/", {"email": "kari@example.com", "password": "Brand-new-pass-1"}, format="json")
        self.assertEqual(login.status_code, 200)
        self.assertEqual(user.social_identities.count(), 1)  # both ways in now work

    def test_a_signed_in_password_user_who_never_links_anything_is_unaffected(self):
        user = User.objects.create_user(username="old@example.com", email="old@example.com", password="s3cret-pass!")
        self.client.force_authenticate(user=user)

        self.assertEqual(self.client.get("/api/users/me/").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/social/identities/").data, [])

    def test_social_sign_in_never_removes_or_changes_an_existing_password(self):
        user = User.objects.create_user(username="old@example.com", email="old@example.com", password="s3cret-pass!")
        from .social import SocialClaims

        claims = SocialClaims("google", "g-1", "old@example.com", True, "", "")
        with mock.patch("api.social_views.verify_id_token", return_value=claims):
            refused = self.client.post("/api/auth/social/", {"provider": "google", "id_token": "t"}, format="json")

        self.assertEqual(refused.status_code, 409)
        user.refresh_from_db()
        self.assertTrue(user.check_password("s3cret-pass!"))
