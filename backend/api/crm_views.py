"""Admin-only API for the donor/food CRM (see models.Donor and friends).

Everything here is limited to admins of *some* event: the data is
organisation-wide (relationships and the yearly plan span events), and it
holds business contacts' personal details and prices. Anyone else gets 403.
"""

from collections import Counter, defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch, Q
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, parser_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from .crm import forecast, guest_totals, plan_year
from .crm_import import ImportError_, import_workbook
from .crm_serializers import (
    DonorContactSerializer,
    DonorSerializer,
    FoodItemSerializer,
    GuestCountSerializer,
    PlanLineSerializer,
    line_cost,
)
from .models import Donor, DonorContact, FoodItem, GuestCount, PlanLine
from .views import _is_any_event_admin

MAX_IMPORT_BYTES = 10 * 1024 * 1024


class IsCrmAdmin(permissions.BasePermission):
    message = "Only an admin can use the CRM."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and _is_any_event_admin(request.user))


def _year_param(request, default=None):
    value = request.query_params.get("year") or request.data.get("year")
    if value in (None, ""):
        if default is not None:
            return default
        raise ValidationError({"year": "year is required."})
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise ValidationError({"year": "year must be a number."})
    if not 2000 <= year <= 2100:
        raise ValidationError({"year": "year is out of range."})
    return year


def _expected_guests(request):
    raw = request.query_params.get("expected_guests") or request.data.get("expected_guests")
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({"expected_guests": "must be a whole number."})
    if value <= 0:
        raise ValidationError({"expected_guests": "must be positive."})
    return value


class DonorViewSet(viewsets.ModelViewSet):
    serializer_class = DonorSerializer
    permission_classes = [IsCrmAdmin]

    def get_queryset(self):
        queryset = Donor.objects.prefetch_related(
            "contacts", Prefetch("lines", queryset=PlanLine.objects.select_related("item"))
        )
        params = self.request.query_params
        if params.get("search"):
            term = params["search"]
            queryset = queryset.filter(
                Q(name__icontains=term) | Q(contacts__name__icontains=term) | Q(contacts__email__icontains=term)
            ).distinct()
        if params.get("relationship"):
            queryset = queryset.filter(relationship=params["relationship"])
        if params.get("active") in ("true", "1"):
            queryset = queryset.filter(is_active=True)
        return queryset

    @action(detail=True, methods=["post"], url_path="merge")
    def merge(self, request, pk=None):
        """POST {into: <donor id>}: fold this donor into another -- for the
        duplicates a spreadsheet import inevitably makes ("Bama" / "BAMA
        Dagligvare"). Contacts and lines move over; where both already have a
        line for the same item and year, the target's is kept and only its
        blank fields are filled from the other. The donor is then deleted."""
        source = self.get_object()
        try:
            target = Donor.objects.get(pk=request.data.get("into"))
        except (Donor.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"into": "Choose the donor to merge into."})
        if target.pk == source.pk:
            raise ValidationError({"into": "A donor can't be merged into itself."})

        with transaction.atomic():
            for contact in source.contacts.all():
                duplicate = (
                    target.contacts.filter(email__iexact=contact.email).exists()
                    if contact.email
                    else target.contacts.filter(name__iexact=contact.name).exists()
                )
                if duplicate:
                    contact.delete()
                else:
                    contact.donor, contact.is_primary = target, False
                    contact.save()
            for line in source.lines.all():
                clash = PlanLine.objects.filter(year=line.year, item=line.item, donor=target).first()
                if clash is None:
                    line.donor = target
                    line.save()
                    continue
                for field in ("unit", "quantity_requested", "quantity_confirmed", "quantity_received", "quantity_note",
                              "unit_price", "price_note", "delivery_date", "ran_out_note", "comment", "next_year_note",
                              "leftover_quantity"):
                    if getattr(clash, field) in (None, "") and getattr(line, field) not in (None, ""):
                        setattr(clash, field, getattr(line, field))
                clash.save()
                line.delete()
            source.lines_arranged.update(via_donor=target)
            if source.notes:
                target.notes = f"{target.notes}\n{source.notes}".strip()
            for field in ("owner_name", "next_contact_on", "last_contacted_on"):
                if not getattr(target, field) and getattr(source, field):
                    setattr(target, field, getattr(source, field))
            if target.relationship == Donor.REL_UNKNOWN:
                target.relationship = source.relationship
            elif source.relationship not in (Donor.REL_UNKNOWN, target.relationship):
                target.relationship = Donor.REL_BOTH
            target.save()
            source.delete()
        return Response(self.get_serializer(self.get_queryset().get(pk=target.pk)).data)


class DonorContactViewSet(viewsets.ModelViewSet):
    serializer_class = DonorContactSerializer
    permission_classes = [IsCrmAdmin]

    def get_queryset(self):
        queryset = DonorContact.objects.all()
        if self.request.query_params.get("donor"):
            queryset = queryset.filter(donor_id=self.request.query_params["donor"])
        return queryset


class FoodItemViewSet(viewsets.ModelViewSet):
    serializer_class = FoodItemSerializer
    permission_classes = [IsCrmAdmin]
    queryset = FoodItem.objects.all()

    def perform_destroy(self, instance):
        # Deleting an item would take every year's lines for it with it.
        if instance.lines.exists():
            raise ValidationError("This item has plan lines. Remove or cancel those first.")
        instance.delete()


class PlanLineViewSet(viewsets.ModelViewSet):
    serializer_class = PlanLineSerializer
    permission_classes = [IsCrmAdmin]

    def get_queryset(self):
        queryset = PlanLine.objects.select_related("item", "donor", "via_donor")
        params = self.request.query_params
        for param, field in (("year", "year"), ("donor", "donor_id"), ("item", "item_id"), ("status", "status")):
            if params.get(param):
                queryset = queryset.filter(**{field: params[param]})
        if params.get("search"):
            queryset = queryset.filter(Q(item__name__icontains=params["search"]) | Q(donor__name__icontains=params["search"]))
        return queryset


class GuestCountViewSet(viewsets.ModelViewSet):
    serializer_class = GuestCountSerializer
    permission_classes = [IsCrmAdmin]

    def get_queryset(self):
        queryset = GuestCount.objects.all()
        if self.request.query_params.get("year"):
            queryset = queryset.filter(date__year=_year_param(self.request))
        return queryset


@api_view(["GET"])
@permission_classes([IsCrmAdmin])
def crm_forecast(request):
    """GET ?year=2026[&expected_guests=N]: a suggested quantity per item for
    that year, learned from earlier years, each with its reasons and a
    comparison to what is already planned."""
    year = _year_param(request)
    return Response({"year": year, "guest_totals": guest_totals(), "items": forecast(year, _expected_guests(request))})


@api_view(["POST"])
@permission_classes([IsCrmAdmin])
def crm_plan_year(request):
    """POST {year[, expected_guests]}: start that year's plan from the latest
    earlier one, with the suggested quantities. Never touches existing lines."""
    year = _year_param(request)
    with transaction.atomic():
        result = plan_year(year, _expected_guests(request))
    return Response({"year": year, **result}, status=status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsCrmAdmin])
def crm_summary(request):
    """GET ?year=: headline numbers for one year's plan, plus which years exist."""
    years = sorted(set(PlanLine.objects.values_list("year", flat=True)), reverse=True)
    year = _year_param(request, default=years[0] if years else 0)
    lines = list(PlanLine.objects.filter(year=year).exclude(status=PlanLine.STATUS_CANCELLED).select_related("item"))
    by_status = Counter(line.status for line in lines)
    by_acquisition = Counter(line.acquisition for line in lines)
    cost = sum((c for c in (line_cost(line) for line in lines) if c is not None), Decimal("0"))
    unpriced = sum(
        1 for line in lines if line.acquisition in ("purchase", "mixed") and line_cost(line) is None
    )
    outcomes = Counter(line.outcome for line in lines if line.outcome)
    return Response(
        {
            "years": years,
            "year": year,
            "lines": len(lines),
            "by_status": dict(by_status),
            "by_acquisition": dict(by_acquisition),
            "estimated_cost": cost,
            "unpriced_purchase_lines": unpriced,
            "outcomes": dict(outcomes),
            "guests": guest_totals().get(year),
        }
    )


@api_view(["POST"])
@permission_classes([IsCrmAdmin])
@parser_classes([MultiPartParser])
def crm_import(request):
    """POST multipart {file, dry_run?}: read the food workbook into the CRM
    (see crm_import.py). With dry_run=true nothing is saved -- the report
    shows what *would* happen, including how supplier names were matched."""
    upload = request.FILES.get("file")
    if not upload:
        raise ValidationError({"file": "Choose an Excel file (.xlsx)."})
    if not upload.name.lower().endswith(".xlsx"):
        raise ValidationError({"file": "The file must be an Excel workbook (.xlsx)."})
    if upload.size > MAX_IMPORT_BYTES:
        raise ValidationError({"file": "The file is too large (limit 10 MB)."})
    dry_run = str(request.data.get("dry_run", "")).lower() in ("1", "true", "yes")
    try:
        report = import_workbook(upload, dry_run=dry_run)
    except ImportError_ as err:
        raise ValidationError({"file": str(err)})
    return Response(report)
