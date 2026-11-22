"""CRM logic that doesn't touch HTTP: reading the free-text quantities and
prices people type into the sheets, guessing an outcome from old comments,
and turning last year's results into next year's suggested quantities.

The suggestions are deliberately simple and explainable -- every number comes
with the reasons that produced it, in Norwegian, so someone can disagree with
it. With only a year or two of history there is nothing for a statistical
model to learn from; what *is* available is last year's quantity plus whether
it ran out or was left over, and that is what the rules use. They get better
as outcomes are recorded properly (and as guest counts accumulate), not by
adding cleverness.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from .models import GuestCount, PlanLine

TWO = Decimal("0.01")

UNIT_ALIASES = {
    "kg": "kg", "g": "g", "l": "l", "liter": "l", "ltr": "l", "dl": "dl",
    "stk": "stk", "stykk": "stk", "ks": "stk",
    "pk": "pk", "pakke": "pk", "pakker": "pk",
    "kasser": "kasser", "kasse": "kasser",
    "poser": "poser", "pose": "poser",
    "glass": "glass", "esker": "esker", "eske": "esker",
    "spann": "spann", "kanner": "kanner", "kanne": "kanner", "ringer": "ringer",
}

_QTY = re.compile(
    r"^\s*(?P<a>\d+(?:[.,]\d+)?)\s*(?:[-–]\s*(?P<b>\d+(?:[.,]\d+)?))?\s*(?P<unit>[a-zæøåA-ZÆØÅ]+)?"
)


_TOKEN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(kg|g|l|liter|ltr|dl|stk|stykk|ks|pk|pakker|pakke|kasser|kasse|poser|pose|glass|esker|eske|spann|kanner|kanne|ringer)\b",
    re.IGNORECASE,
)


def _num(text):
    return Decimal(text.replace(",", "."))


def canonical_unit(text):
    return UNIT_ALIASES.get((text or "").strip().lower().rstrip("."), "")


def parse_quantity(value):
    """(quantity, unit, note) from whatever was typed in a quantity cell.

    "100 stk" -> (100, "stk", "");  "48 kg" -> (48, "kg", "");
    "7 kasser av hver" -> (7, "kasser", "av hver");
    "40-50kg" -> (45, "kg", "40-50kg")  (a range keeps its original text);
    "Fått en del fra ..." -> (None, "", "Fått en del fra ...").
    A bare number from a spreadsheet cell comes back with no unit.
    Anything the number+unit doesn't capture goes in the note, never lost."""

    if value is None or value == "":
        return None, "", ""
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return Decimal(str(value)).quantize(TWO, ROUND_HALF_UP), "", ""

    text = str(value).strip()
    match = _QTY.match(text)
    if not match:
        return None, "", text[:200]

    # "6kg fra X, 28,8kg kjøpt fra Y" is one total from two sources: when the
    # same unit appears more than once, add them up rather than reading only
    # the first. (Different units -- "4 spann, 4 kg" -- keep the first.)
    tokens = [(m.group(1), canonical_unit(m.group(2))) for m in _TOKEN.finditer(text)]
    if len(tokens) >= 2 and tokens[0][1] and len({unit for _, unit in tokens}) == 1 and not match.group("b"):
        total = sum((_num(n) for n, _ in tokens), Decimal("0"))
        return total.quantize(TWO, ROUND_HALF_UP), tokens[0][1], text[:200]

    low = _num(match.group("a"))
    high = _num(match.group("b")) if match.group("b") else None
    quantity = ((low + high) / 2) if high is not None else low
    unit = canonical_unit(match.group("unit"))
    if unit:
        rest = text[match.end():].strip(" ,;.?()")
    else:
        # The word after the number isn't a unit ("2 av hver") -- it's part
        # of what was written, so keep the whole thing.
        rest = text
    if high is not None:
        rest = text
    return quantity.quantize(TWO, ROUND_HALF_UP), unit, rest[:200]


_PRICE = re.compile(r"(\d+(?:,\d+)?)\s*,-\s*per\s*([\wæøåÆØÅ]+)", re.IGNORECASE)
_BARE_WORDS = {"gave", "spons", "betalt", "kjøpt", "kjøp", "betaler"}


def parse_acquisition(text, line_unit=""):
    """(acquisition, unit_price, note) from the "Betalt / gave" cell.

    "Gave" / "Spons" -> gift; "Betalt 209,- per kg" -> purchase at 209/kg;
    "Gave og kjøp" -> mixed. The price is only kept as a number when it is
    per the same unit as the quantity ("99,- per 500g" for a quantity in kg
    can't be multiplied, so it stays in the note)."""

    if not text or not str(text).strip():
        return PlanLine.ACQ_UNKNOWN, None, ""
    raw = str(text).strip()
    low = raw.lower()
    gift = "gave" in low or "spons" in low
    paid = any(word in low for word in ("betalt", "betaler", "kjøpt", "kjøp"))
    if gift and paid:
        acquisition = PlanLine.ACQ_MIXED
    elif gift:
        acquisition = PlanLine.ACQ_GIFT
    elif paid:
        acquisition = PlanLine.ACQ_PURCHASE
    else:
        acquisition = PlanLine.ACQ_UNKNOWN

    price = None
    match = _PRICE.search(raw)
    if match and acquisition == PlanLine.ACQ_UNKNOWN:
        acquisition = PlanLine.ACQ_PURCHASE  # a price with no other wording: we pay it
    if match and canonical_unit(match.group(2)) == line_unit and line_unit:
        price = _num(match.group(1)).quantize(TWO, ROUND_HALF_UP)
    note = "" if low.strip(" .") in _BARE_WORDS else raw[:200]
    return acquisition, price, note


# ---- Outcome from old comments ---------------------------------------------

_RAN_OUT = ("tomt", "tom for", "gikk tom", "gått tom", "ble tom")
_EARLY = ("juleaften", "tidlig", "før 18", "første juledag", "første dag", "før kvelden", "13:00 25", "ca. 13")
_LEFTOVER = ("igjen", "for mye", "ikke populært", "ikke populørt", "ikke spist", "ikke servert", "ikke brukt", "gått lite")
_RIGHT = ("bra mengde", "ok mengde", "passe mengde", "tilstrekkelig", "grei mengde", "bra antall")
_LEFTOVER_QTY = re.compile(r"igjen\s*(?:ca\.?\s*)?(\d+(?:[.,]\d+)?)\s*([a-zæøå]+)?")


def guess_outcome(ran_out_text, next_year_text, line_unit=""):
    """(outcome, leftover_quantity) guessed from the free-text "ran out when"
    and "comment for next year" cells -- or ("", None) when it's ambiguous.

    Only a guess: callers store it as outcome_source="imported" so the UI can
    say so and someone can confirm it. Where the comment contradicts itself
    ("ran out" and "lots left over") it's better to know nothing than to
    suggest the wrong direction."""

    text = f"{ran_out_text or ''} {next_year_text or ''}".lower()
    if not text.strip():
        return "", None
    ran = any(k in text for k in _RAN_OUT)
    early = ran and any(k in text for k in _EARLY)
    left = any(k in text for k in _LEFTOVER)
    right = any(k in text for k in _RIGHT)

    if right and not early:
        return PlanLine.OUTCOME_RIGHT, None
    if ran and left:
        return "", None
    if ran:
        return (PlanLine.OUTCOME_RAN_OUT_EARLY if early else PlanLine.OUTCOME_RAN_OUT), None
    if left:
        leftover = None
        match = _LEFTOVER_QTY.search(text)
        if match and line_unit and canonical_unit(match.group(2)) == line_unit:
            leftover = _num(match.group(1)).quantize(TWO, ROUND_HALF_UP)
        return PlanLine.OUTCOME_LEFTOVER, leftover
    return "", None


# ---- Suggestions -----------------------------------------------------------

_DROP = re.compile(r"\bdropp")

OUTCOME_FACTORS = {
    PlanLine.OUTCOME_RAN_OUT_EARLY: (Decimal("1.30"), "Ble tomt tidlig → +30 %"),
    PlanLine.OUTCOME_RAN_OUT: (Decimal("1.15"), "Ble tomt → +15 %"),
}


@dataclass
class Suggestion:
    quantity: Decimal | None
    unit: str
    action: str  # keep | increase | decrease | drop | none
    confidence: str  # high | medium | low
    reasons: list = field(default_factory=list)
    basis_year: int | None = None
    basis_quantity: Decimal | None = None


def round_nice(quantity):
    """Round to a figure someone would actually order: 0.5 under 5, whole
    numbers under 20, then steps of 5 / 10 / 50 as the amounts grow."""
    if quantity <= 0:
        return Decimal("0")
    if quantity < 5:
        step = Decimal("0.5")
    elif quantity < 20:
        step = Decimal("1")
    elif quantity < 100:
        step = Decimal("5")
    elif quantity < 500:
        step = Decimal("10")
    else:
        step = Decimal("50")
    return ((quantity / step).quantize(Decimal("1"), ROUND_HALF_UP) * step).quantize(TWO)


def line_base_quantity(line):
    """The amount a past line was really about: what was confirmed, else what
    we asked for, else what arrived."""
    for value in (line.quantity_confirmed, line.quantity_requested, line.quantity_received):
        if value is not None:
            return value
    return None


def wants_dropped(line):
    note = (line.next_year_note or "").lower()
    return line.status == PlanLine.STATUS_CANCELLED or (bool(_DROP.search(note)) and "ikke dropp" not in note)


def suggest_for_line(line, guest_ratio=None):
    """Suggested quantity for next year, from one past line and how it went."""
    base = line_base_quantity(line)
    if wants_dropped(line):
        why = "Merket som droppet" if line.status == PlanLine.STATUS_CANCELLED else "Notatet fra i fjor sier «Dropp»"
        return Suggestion(Decimal("0"), line.unit, "drop", "medium", [why], line.year, base)
    if base is None:
        return Suggestion(None, line.unit, "none", "low", ["Ingen mengde i fjor å bygge på"], line.year, None)

    reasons = []
    quantity = base
    outcome = line.outcome
    if outcome in OUTCOME_FACTORS:
        factor, reason = OUTCOME_FACTORS[outcome]
        quantity = base * factor
        reasons.append(reason)
    elif outcome == PlanLine.OUTCOME_LEFTOVER:
        leftover = line.leftover_quantity
        if leftover is not None and leftover > 0:
            quantity = max(base - leftover * Decimal("0.8"), base * Decimal("0.3"))
            reasons.append(f"Hadde {leftover.normalize():f} {line.unit} igjen → trekker fra 80 % av det".replace("  ", " "))
        else:
            quantity = base * Decimal("0.85")
            reasons.append("Hadde noe igjen → −15 %")
    elif outcome == PlanLine.OUTCOME_RIGHT:
        reasons.append("Passet bra → samme mengde")
    else:
        reasons.append("Ingen utfall registrert → samme mengde som i fjor")

    if guest_ratio is not None and guest_ratio != 1:
        quantity = quantity * guest_ratio
        reasons.append(f"Forventet antall gjester ×{guest_ratio.quantize(TWO)}")

    quantity = round_nice(quantity)
    if quantity > base * Decimal("1.02"):
        action = "increase"
    elif quantity < base * Decimal("0.98"):
        action = "decrease"
    else:
        action = "keep"

    if outcome and line.outcome_source != PlanLine.SOURCE_IMPORTED:
        confidence = "high"
    elif outcome:
        confidence = "medium"
    else:
        confidence = "low"
    return Suggestion(quantity, line.unit, action, confidence, reasons, line.year, base)


def guest_totals():
    """Guests served per year (sum over the days entered)."""
    totals = defaultdict(int)
    for counted in GuestCount.objects.all():
        totals[counted.date.year] += counted.guests
    return dict(totals)


def guest_ratio(basis_year, target_year, expected_guests=None):
    """How much bigger/smaller the crowd is expected to be than in the basis
    year, or None when we can't say (no counts for the basis year)."""
    totals = guest_totals()
    before = totals.get(basis_year)
    after = expected_guests if expected_guests else totals.get(target_year)
    if not before or not after:
        return None
    return (Decimal(after) / Decimal(before)).quantize(Decimal("0.001"))


def basis_lines(target_year):
    """The lines to learn from: for each item, those of the latest earlier
    year that has any quantity for it."""
    by_item = defaultdict(lambda: defaultdict(list))
    for line in PlanLine.objects.filter(year__lt=target_year).select_related("item", "donor", "via_donor"):
        by_item[line.item_id][line.year].append(line)
    chosen = {}
    for item_id, years in by_item.items():
        for year in sorted(years, reverse=True):
            lines = years[year]
            if any(line_base_quantity(line) is not None or wants_dropped(line) for line in lines):
                chosen[item_id] = lines
                break
    return chosen


def forecast(target_year, expected_guests=None):
    """One row per item with history: last year's total, the suggestion, and
    how it compares with what is already planned for `target_year`."""

    planned = defaultdict(list)
    for line in PlanLine.objects.filter(year=target_year).select_related("item"):
        planned[line.item_id].append(line)

    rows = []
    for item_id, lines in basis_lines(target_year).items():
        year = lines[0].year
        ratio = guest_ratio(year, target_year, expected_guests)
        suggestions = [(line, suggest_for_line(line, ratio)) for line in lines]
        units = [s.unit for _, s in suggestions if s.quantity is not None and s.unit is not None]
        unit = max(set(units), key=units.count) if units else ""
        live = [(line, s) for line, s in suggestions if s.action != "drop" and s.quantity is not None and s.unit == unit]
        dropped = all(s.action == "drop" for _, s in suggestions)

        total = sum((s.quantity for _, s in live), Decimal("0"))
        base_total = sum(
            (s.basis_quantity for _, s in suggestions if s.basis_quantity is not None and s.unit == unit), Decimal("0")
        )
        rank = {"high": 2, "medium": 1, "low": 0}
        confidence = min((s.confidence for _, s in suggestions), key=lambda c: rank[c])
        reasons = []
        for _, s in suggestions:
            for reason in s.reasons:
                if reason not in reasons:
                    reasons.append(reason)
        if dropped:
            action, total = "drop", Decimal("0")
        elif total > base_total * Decimal("1.02"):
            action = "increase"
        elif total < base_total * Decimal("0.98"):
            action = "decrease"
        else:
            action = "keep"

        target = planned.get(item_id, [])
        target_total = sum((l.quantity_requested for l in target if l.quantity_requested is not None and l.unit == unit), Decimal("0"))
        rows.append(
            {
                "item": item_id,
                "item_name": lines[0].item.name,
                "category": lines[0].item.category,
                "basis_year": year,
                "unit": unit,
                "basis_quantity": base_total or None,
                "suggested_quantity": total,
                "action": action,
                "confidence": confidence,
                "reasons": reasons,
                "planned_quantity": target_total if target else None,
            }
        )
    rows.sort(key=lambda r: (r["category"], r["item_name"].lower()))
    return rows


def plan_year(target_year, expected_guests=None):
    """Start next year's plan from last year's: one line per past line with
    the same donor, acquisition and price, status "planned" and the suggested
    quantity as the amount to ask for. Existing lines are never touched, so
    it's safe to run again, and items marked as dropped are left out."""
    created = existing = skipped = 0
    planned = defaultdict(list)
    for line in PlanLine.objects.filter(year=target_year):
        planned[line.item_id].append(line)

    for _, lines in basis_lines(target_year).items():
        ratio = guest_ratio(lines[0].year, target_year, expected_guests)
        for line in lines:
            suggestion = suggest_for_line(line, ratio)
            if suggestion.action == "drop" or suggestion.quantity is None:
                skipped += 1
                continue
            # Already planned? A line with no donor on either side counts as
            # the same line -- the donor just wasn't recorded in one of them.
            theirs = planned[line.item_id]
            if any(t.donor_id == line.donor_id or t.donor_id is None or line.donor_id is None for t in theirs):
                existing += 1
                continue
            new, was_created = PlanLine.objects.get_or_create(
                year=target_year,
                item=line.item,
                donor=line.donor,
                defaults={
                    "via_donor": line.via_donor,
                    "unit": line.unit,
                    "quantity_requested": suggestion.quantity,
                    "acquisition": line.acquisition,
                    "unit_price": line.unit_price,
                    "price_note": line.price_note,
                    "delivery_mode": line.delivery_mode,
                },
            )
            planned[line.item_id].append(new)
            if was_created:
                created += 1
            else:
                existing += 1
    return {"created": created, "existing": existing, "skipped_dropped": skipped}
