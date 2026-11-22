from datetime import date
from decimal import Decimal

from rest_framework import serializers

from .models import Donor, DonorContact, FoodItem, GuestCount, PlanLine


class DonorContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = DonorContact
        fields = ["id", "donor", "name", "role", "email", "phone", "is_primary", "notes"]

    def validate(self, attrs):
        # A row with neither a name nor an e-mail is just noise.
        name = attrs.get("name", getattr(self.instance, "name", ""))
        email = attrs.get("email", getattr(self.instance, "email", ""))
        if not (name or email):
            raise serializers.ValidationError("A contact needs a name or an e-mail address.")
        return attrs


def _check_name_free(model, value, instance):
    """Names are unique ignoring case -- say so, rather than hit the database
    constraint and answer 500."""
    clash = model.objects.filter(name__iexact=value.strip())
    if instance is not None:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        raise serializers.ValidationError("There is already one with this name.")
    return value.strip()


class FoodItemSerializer(serializers.ModelSerializer):
    line_count = serializers.IntegerField(source="lines.count", read_only=True)

    class Meta:
        model = FoodItem
        fields = ["id", "name", "category", "line_count"]

    def validate_name(self, value):
        return _check_name_free(FoodItem, value, self.instance)


def line_cost(line):
    """What a line costs when we pay for it: the amount (confirmed, else
    asked for) times the unit price. None for gifts or when either is unknown."""
    if line.unit_price is None or line.acquisition not in (PlanLine.ACQ_PURCHASE, PlanLine.ACQ_MIXED):
        return None
    quantity = line.quantity_confirmed if line.quantity_confirmed is not None else line.quantity_requested
    if quantity is None:
        return None
    return (quantity * line.unit_price).quantize(Decimal("0.01"))


class PlanLineSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    category = serializers.CharField(source="item.category", read_only=True)
    donor_name = serializers.CharField(source="donor.name", read_only=True, default=None)
    via_donor_name = serializers.CharField(source="via_donor.name", read_only=True, default=None)
    estimated_cost = serializers.SerializerMethodField()
    # Create a line for an item that doesn't exist yet in one step.
    new_item_name = serializers.CharField(write_only=True, required=False, allow_blank=False, max_length=150)
    new_item_category = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=60)

    class Meta:
        model = PlanLine
        fields = [
            "id", "year", "item", "item_name", "category", "new_item_name", "new_item_category",
            "donor", "donor_name", "via_donor", "via_donor_name", "status",
            "unit", "quantity_requested", "quantity_confirmed", "quantity_received", "quantity_note",
            "acquisition", "unit_price", "price_note", "estimated_cost",
            "delivery_date", "delivery_mode",
            "outcome", "outcome_source", "leftover_quantity", "ran_out_note", "comment", "next_year_note",
            "updated_at",
        ]
        read_only_fields = ["outcome_source", "updated_at"]
        extra_kwargs = {"item": {"required": False}}
        validators = []  # uniqueness is checked in validate() for a friendlier message

    def get_estimated_cost(self, obj):
        return line_cost(obj)

    def validate(self, attrs):
        instance = self.instance
        item = attrs.get("item") or (instance.item if instance else None)
        new_name = attrs.get("new_item_name")
        if item is None and not new_name:
            raise serializers.ValidationError({"item": "Choose an item or give a name for a new one."})
        if new_name and "item" not in attrs and instance is None:
            item, _ = FoodItem.objects.get_or_create(
                name__iexact=new_name.strip(),
                defaults={"name": new_name.strip(), "category": attrs.get("new_item_category", "").strip()},
            )
            attrs["item"] = item
        attrs.pop("new_item_name", None)
        attrs.pop("new_item_category", None)

        year = attrs.get("year", instance.year if instance else None)
        donor = attrs["donor"] if "donor" in attrs else (instance.donor if instance else None)
        clash = PlanLine.objects.filter(year=year, item=item, donor=donor)
        if instance:
            clash = clash.exclude(pk=instance.pk)
        if clash.exists():
            raise serializers.ValidationError("There is already a line for this item and donor that year.")
        return attrs

    def _note_outcome_source(self, validated, instance=None):
        # An outcome someone types in is a fact; one carried in from old
        # comments stays marked as a guess until it's touched.
        if "outcome" in validated and (instance is None or validated["outcome"] != instance.outcome):
            validated["outcome_source"] = PlanLine.SOURCE_MANUAL if validated["outcome"] else ""
        return validated

    def create(self, validated_data):
        return super().create(self._note_outcome_source(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._note_outcome_source(validated_data, instance))


class GuestCountSerializer(serializers.ModelSerializer):
    class Meta:
        model = GuestCount
        fields = ["id", "date", "guests", "note"]


class DonorSerializer(serializers.ModelSerializer):
    contacts = DonorContactSerializer(many=True, read_only=True)
    years = serializers.SerializerMethodField()
    needs_attention = serializers.SerializerMethodField()

    class Meta:
        model = Donor
        fields = [
            "id", "name", "kind", "relationship", "owner_name", "notes",
            "last_contacted_on", "next_contact_on", "is_active", "created_at",
            "contacts", "years", "needs_attention",
        ]
        read_only_fields = ["created_at"]

    def validate_name(self, value):
        return _check_name_free(Donor, value, self.instance)

    def _lines(self, obj):
        # Prefetched by the viewset; falls back to a query for one-off use.
        return list(obj.lines.all())

    def get_years(self, obj):
        """Per year: how many lines, how many were confirmed, how many
        actually arrived, and which confirmed items did not -- the plain
        measure of whether this donor delivered what it promised."""
        by_year = {}
        for line in self._lines(obj):
            if line.status == PlanLine.STATUS_CANCELLED:
                continue
            row = by_year.setdefault(line.year, {"year": line.year, "lines": 0, "confirmed": 0, "delivered": 0, "missing": []})
            row["lines"] += 1
            if line.status in (PlanLine.STATUS_CONFIRMED, PlanLine.STATUS_DELIVERED):
                row["confirmed"] += 1
            if line.status == PlanLine.STATUS_DELIVERED:
                row["delivered"] += 1
            elif line.status == PlanLine.STATUS_CONFIRMED and line.year < date.today().year:
                row["missing"].append(line.item.name)
        return sorted(by_year.values(), key=lambda r: -r["year"])

    def get_needs_attention(self, obj):
        reasons = []
        if obj.is_active and obj.next_contact_on and obj.next_contact_on <= date.today():
            reasons.append("Kontakt-dato er nådd")
        lines = self._lines(obj)
        if lines:
            newest = max(line.year for line in lines)
            open_lines = [l for l in lines if l.year == newest and l.status in (PlanLine.STATUS_PLANNED, PlanLine.STATUS_ASKED)]
            if open_lines and newest >= date.today().year:
                reasons.append(f"{len(open_lines)} linje(r) ikke bekreftet for {newest}")
        return reasons
