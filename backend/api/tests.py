import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Assignment,
    Event,
    EventCheckIn,
    Invite,
    Membership,
    PasswordSetupToken,
    QRCode,
    Shift,
    ShiftSignup,
    Skill,
)

User = get_user_model()


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

    def test_signup_for_one_shift_succeeds(self):
        ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)
        self.assertEqual(self.kitchen.signup_count, 1)

    def test_can_shortlist_multiple_shifts_same_day(self):
        ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)
        ShiftSignup.objects.create(shift=self.hosting, user=self.volunteer)
        self.assertEqual(ShiftSignup.objects.filter(user=self.volunteer).count(), 2)

    def test_cannot_signup_twice_for_same_shift(self):
        ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)

    def test_signup_denormalizes_event_and_date_from_shift(self):
        signup = ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)
        self.assertEqual(signup.event_id, self.event.id)
        self.assertEqual(signup.date, self.kitchen.date)

    def test_experience_fields_default_to_unset(self):
        signup = ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)
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

    def test_second_assignment_same_day_is_rejected_at_db_level(self):
        Assignment.objects.create(shift=self.kitchen, user=self.volunteer, confirmed_by=self.organizer)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Assignment.objects.create(shift=self.hosting, user=self.volunteer, confirmed_by=self.organizer)

    def test_assignments_on_different_days_both_succeed(self):
        Assignment.objects.create(shift=self.kitchen, user=self.volunteer, confirmed_by=self.organizer)
        Assignment.objects.create(shift=self.next_day, user=self.volunteer, confirmed_by=self.organizer)
        self.assertEqual(Assignment.objects.filter(user=self.volunteer).count(), 2)

    def test_assignment_denormalizes_event_and_date_from_shift(self):
        assignment = Assignment.objects.create(shift=self.kitchen, user=self.volunteer, confirmed_by=self.organizer)
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
        ShiftSignup.objects.create(shift=self.hosting, user=self.volunteer)

        response = self._checkin()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "assigned")
        self.assertEqual(response.data["shift"]["id"], self.hosting.id)
        self.assertTrue(Assignment.objects.filter(shift=self.hosting, user=self.volunteer).exists())

    def test_single_critical_candidate_goes_to_pool_not_auto_assigned(self):
        ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer, has_relevant_experience=True)

        response = self._checkin()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "pending_pool")
        self.assertFalse(Assignment.objects.exists())

    def test_multiple_candidates_goes_to_pool(self):
        ShiftSignup.objects.create(shift=self.hosting, user=self.volunteer)
        ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)

        response = self._checkin()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "pending_pool")
        self.assertEqual(len(response.data["candidates"]), 2)

    def test_scanning_again_after_assignment_reports_already_assigned(self):
        ShiftSignup.objects.create(shift=self.hosting, user=self.volunteer)
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

        ShiftSignup.objects.create(shift=self.hosting, user=self.volunteer)
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
        self.client = APIClient()
        self.client.force_authenticate(user=self.volunteer)

    def test_volunteer_can_self_checkin(self):
        ShiftSignup.objects.create(shift=self.hosting, user=self.volunteer)

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
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(user=self.organizer)

    def _self_checkin_as(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post(f"/api/events/{self.event.id}/self-checkin/")

    def test_pool_lists_checked_in_unassigned_users_with_suggestion(self):
        ShiftSignup.objects.create(shift=self.kitchen, user=self.cook, has_relevant_experience=True)
        ShiftSignup.objects.create(shift=self.hosting, user=self.cook)
        self._self_checkin_as(self.cook)

        response = self.admin_client.get(f"/api/events/{self.event.id}/pool/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["user"]["username"], "cook")
        self.assertEqual(len(entry["candidates"]), 2)
        # experienced + critical should be suggested ahead of the plain hosting slot
        self.assertEqual(entry["suggested_shift"]["id"], self.kitchen.id)

    def test_assigned_users_disappear_from_pool(self):
        ShiftSignup.objects.create(shift=self.hosting, user=self.newbie)
        self._self_checkin_as(self.newbie)  # auto-assigns, single non-critical candidate

        response = self.admin_client.get(f"/api/events/{self.event.id}/pool/")

        self.assertEqual(response.data, [])

    def test_admin_can_assign_from_pool(self):
        ShiftSignup.objects.create(shift=self.kitchen, user=self.cook, has_relevant_experience=True)
        self._self_checkin_as(self.cook)

        response = self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.cook.id, "shift_id": self.kitchen.id},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Assignment.objects.filter(shift=self.kitchen, user=self.cook).exists())

    def test_cannot_assign_someone_who_has_not_checked_in(self):
        response = self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.newbie.id, "shift_id": self.hosting.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_double_assign_same_day(self):
        ShiftSignup.objects.create(shift=self.hosting, user=self.newbie)
        self._self_checkin_as(self.newbie)  # auto-assigned to hosting already

        response = self.admin_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.newbie.id, "shift_id": self.kitchen.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_only_event_creator_can_view_pool_or_assign(self):
        other_client = APIClient()
        other_client.force_authenticate(user=self.cook)

        pool_response = other_client.get(f"/api/events/{self.event.id}/pool/")
        assign_response = other_client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.newbie.id, "shift_id": self.hosting.id},
            format="json",
        )

        self.assertEqual(pool_response.status_code, 403)
        self.assertEqual(assign_response.status_code, 403)


class ShiftSignupEndpointTests(TestCase):
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
        self.client = APIClient()
        self.client.force_authenticate(user=self.volunteer)

    def test_signup_succeeds(self):
        response = self.client.post(f"/api/shifts/{self.hosting.id}/signup/")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(ShiftSignup.objects.filter(shift=self.hosting, user=self.volunteer).exists())

    def test_signup_for_critical_shift_captures_experience_answer(self):
        response = self.client.post(
            f"/api/shifts/{self.kitchen.id}/signup/",
            {"has_relevant_experience": True, "experience_notes": "5 years as a chef"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        signup = ShiftSignup.objects.get(shift=self.kitchen, user=self.volunteer)
        self.assertTrue(signup.has_relevant_experience)
        self.assertEqual(signup.experience_notes, "5 years as a chef")

    def test_can_signup_for_multiple_shifts_same_day_now(self):
        self.client.post(f"/api/shifts/{self.kitchen.id}/signup/", {"has_relevant_experience": False}, format="json")
        response = self.client.post(f"/api/shifts/{self.hosting.id}/signup/")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(ShiftSignup.objects.filter(user=self.volunteer).count(), 2)

    def test_signup_full_shift_is_rejected(self):
        self.kitchen.capacity = 0
        self.kitchen.save()

        response = self.client.post(f"/api/shifts/{self.kitchen.id}/signup/")

        self.assertEqual(response.status_code, 400)

    def test_withdraw_removes_signup(self):
        self.client.post(f"/api/shifts/{self.hosting.id}/signup/")

        response = self.client.post(f"/api/shifts/{self.hosting.id}/withdraw/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ShiftSignup.objects.filter(shift=self.hosting, user=self.volunteer).exists())


class UserSkillsTests(TestCase):
    def test_user_can_have_skills_and_experience_notes(self):
        user = User.objects.create_user(username="volunteer", password="pw")
        cooking = Skill.objects.create(name="Kokk")
        first_aid = Skill.objects.create(name="Førstehjelp")
        user.skills.set([cooking, first_aid])
        user.experience_notes = "10 years as a professional chef"
        user.save()

        self.assertEqual(set(user.skills.values_list("name", flat=True)), {"Kokk", "Førstehjelp"})

    def test_me_endpoint_returns_skills_and_experience(self):
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
        self.assertEqual([s["name"] for s in response.data["skills"]], ["Vekter"])


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
        self.client = APIClient()
        make_event(title="Hennings Alternativ Jul")

    def test_register_creates_user_and_returns_tokens(self):
        response = self.client.post(
            "/api/register/",
            {"email": "new.volunteer@example.com", "password": "correct horse battery staple"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "new.volunteer@example.com")
        self.assertTrue(User.objects.filter(email="new.volunteer@example.com").exists())

    def test_register_without_password_still_works_and_returns_tokens(self):
        """Volunteers don't need a password -- they get a usable JWT session
        immediately from registration, same as everyone else."""

        response = self.client.post(
            "/api/register/",
            {"email": "passwordless.volunteer@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("access", response.data)
        user = User.objects.get(email="passwordless.volunteer@example.com")
        self.assertFalse(user.has_usable_password())

    def test_register_without_password_cannot_be_logged_into_later(self):
        """An unusable password means nobody -- including an attacker
        guessing a password -- can log in via /api/token/."""

        self.client.post("/api/register/", {"email": "passwordless2@example.com"}, format="json")
        response = self.client.post(
            "/api/token/", {"email": "passwordless2@example.com", "password": "some guessed password"}, format="json"
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("access", response.data)

    def test_register_accepts_skill_ids_and_sets_them_on_the_user(self):
        kokk = Skill.objects.create(name="Kokk")
        vertskap = Skill.objects.create(name="Vertskap")
        response = self.client.post(
            "/api/register/",
            {
                "email": "chef.volunteer@example.com",
                "password": "correct horse battery staple",
                "skill_ids": [kokk.id, vertskap.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="chef.volunteer@example.com")
        self.assertEqual(set(user.skills.values_list("name", flat=True)), {"Kokk", "Vertskap"})
        self.assertEqual({s["name"] for s in response.data["user"]["skills"]}, {"Kokk", "Vertskap"})

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user(username="existing", email="taken@example.com", password="pw12345678")
        response = self.client.post(
            "/api/register/",
            {"email": "taken@example.com", "password": "correct horse battery staple"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.filter(email="taken@example.com").count(), 1)

    def test_register_rejects_weak_password(self):
        response = self.client.post(
            "/api/register/",
            {"email": "weak@example.com", "password": "1234"},
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
        self.client = APIClient()

    def test_registration_succeeds_when_no_window_is_set(self):
        make_event(title="Hennings Alternativ Jul")
        response = self.client.post(
            "/api/register/", {"email": "open.window@example.com", "password": "correct horse battery staple"}, format="json"
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
            "/api/register/", {"email": "inside.window@example.com", "password": "correct horse battery staple"}, format="json"
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
        self.client = APIClient()
        make_event(title="Hennings Alternativ Jul")

    def test_passwordless_registration_creates_a_setup_token(self):
        response = self.client.post("/api/register/", {"email": "passwordless@example.com"}, format="json")
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="passwordless@example.com")
        self.assertTrue(PasswordSetupToken.objects.filter(user=user).exists())

    def test_registration_with_a_password_does_not_create_a_setup_token(self):
        response = self.client.post(
            "/api/register/", {"email": "haspassword@example.com", "password": "correct horse battery staple"}, format="json"
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
        ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)
        ShiftSignup.objects.create(shift=self.hosting, user=self.volunteer)
        EventCheckIn.objects.create(event=self.event, user=self.volunteer, date=self.today)

        client = APIClient()
        client.force_authenticate(user=self.leader)

        denied = client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.volunteer.id, "shift_id": self.hosting.id},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        allowed = client.post(
            f"/api/events/{self.event.id}/assign/",
            {"user_id": self.volunteer.id, "shift_id": self.kitchen.id},
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

    def test_new_event_creator_gets_owner_membership(self):
        client = APIClient()
        client.force_authenticate(user=self.candidate)
        response = client.post("/api/events/", {"title": "Alternativ Jul 2027"}, format="json")
        self.assertEqual(response.status_code, 201)
        event_id = response.data["id"]
        membership = Membership.objects.get(event_id=event_id, user=self.candidate)
        self.assertEqual(membership.role, Membership.ROLE_OWNER)

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
        ShiftSignup.objects.create(shift=self.kitchen, user=self.volunteer)
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
        self.assertTrue(shift.is_understaffed)

        volunteer = User.objects.create_user(username="volunteer", password="pw")
        Assignment.objects.create(shift=shift, user=volunteer, confirmed_by=organizer)
        another = User.objects.create_user(username="volunteer2", password="pw")
        Assignment.objects.create(shift=shift, user=another, confirmed_by=organizer)

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
        Assignment.objects.create(shift=shift, user=volunteer, confirmed_by=organizer)

        client = APIClient()
        client.force_authenticate(user=volunteer)
        response = client.post(f"/api/shifts/{shift.id}/cancel-assignment/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Assignment.objects.filter(shift=shift, user=volunteer).exists())


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
        ShiftSignup.objects.create(shift=self.kitchen, user=volunteer)

        response = self.client.get("/api/public/event/")

        body = str(response.content)
        self.assertNotIn("organizer@example.com", body)
        self.assertNotIn("volunteer@example.com", body)
        self.assertNotIn("created_by", response.data)
        self.assertNotIn("participants", response.data["shifts"][0])
        self.assertNotIn("leaders", response.data["shifts"][0])

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
