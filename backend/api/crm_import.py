"""Reads the organisation's food workbook ("Mat 2025.xlsx" and its yearly
successors) into the CRM: donors and their contacts from the contact sheet,
and the item-by-item plan with last year's amounts, what was confirmed, what
happened and what is wanted next year from the food sheets.

It is built around how that workbook is actually laid out, but follows its
*headers* rather than column letters (years included: "Bekreftet mengde 2025"
tells it which year the sheet is about), so next year's copy of the file
imports the same way.

Safe to run repeatedly: existing records only get their *blank* fields filled,
never overwritten, so edits made in the CRM since are kept. With dry_run=True
everything is read and reported but nothing is saved. The uploaded file holds
business contacts' personal details and is read from memory only -- never
written to disk or logged.
"""

import difflib
import re
from collections import defaultdict
from datetime import date, datetime

import openpyxl
from django.db import transaction
from django.utils import timezone

from .crm import guess_outcome, parse_acquisition, parse_quantity
from .models import Donor, DonorContact, FoodItem, PlanLine

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)


class ImportError_(Exception):
    """The file isn't something we can read as the food workbook."""


def _text(value):
    return "" if value is None else str(value).strip()


def _truthy(value):
    return value is True or _text(value).lower() in {"true", "ja", "x", "1", "yes"}


def _norm(text):
    """Comparison key for a name: lower-case, punctuation and spacing gone."""
    return " ".join(re.sub(r"[^\w]+", " ", _text(text).lower()).split())


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


class DonorResolver:
    """Maps the many spellings in the sheets ("BAMA Dagligvare", "Kjøpt på
    Havaristen", "Mills via Roy") onto donors, creating one only when nothing
    plausible matches -- and reporting every decision so mistakes are visible
    (donors can be merged afterwards)."""

    def __init__(self, report):
        self.report = report
        self.by_norm = {_norm(d.name): d for d in Donor.objects.all()}
        self.by_contact = {}
        for contact in DonorContact.objects.select_related("donor"):
            if contact.name:
                self.by_contact.setdefault(_norm(contact.name), contact.donor)
        self.decisions = {}

    def _find(self, name):
        key = _norm(name)
        if not key:
            return None, ""
        if key in self.by_norm:
            return self.by_norm[key], "exact"
        if len(key) >= 4:
            for existing_key, donor in self.by_norm.items():
                if existing_key.startswith(key + " ") or key.startswith(existing_key + " "):
                    return donor, "prefix"
        if key in self.by_contact:
            return self.by_contact[key], "contact"
        close = difflib.get_close_matches(key, list(self.by_norm), n=1, cutoff=0.8)
        if close:
            return self.by_norm[close[0]], "similar"
        return None, ""

    def _create(self, name):
        donor = Donor.objects.create(name=name[:150])
        self.by_norm[_norm(name)] = donor
        self.report["donors"]["created"] += 1
        return donor

    def resolve(self, raw):
        """(donor, via_donor) for a supplier cell."""
        text = re.sub(r"^\s*kjøpt på\s+", "", _text(raw), flags=re.IGNORECASE).strip(" ,;")
        if not text:
            return None, None
        if text in self.decisions:
            return self.decisions[text]

        via = None
        left = text
        match = re.split(r"\bvia\b", text, maxsplit=1, flags=re.IGNORECASE)
        dashed = re.match(r"^(.+?)\s+-\s+(.+)$", text)
        if len(match) == 2:
            left, right = match[0].strip(" ,;"), match[1].strip(" ,;")
            via, _ = self._find(right)
            via = via or self._create(right)
        elif dashed and self._find(dashed.group(1))[0] is not None:
            # "Roy - Mills": spelled the other way round, the arranger first.
            via, _ = self._find(dashed.group(1))
            left = dashed.group(2).strip()

        donor, how = self.by_norm.get(_norm(left)), "exact"
        if donor is None and "/" in left:
            # "Havaristen / Roy": the first name is the supplier, the second
            # someone who arranged it. (A whole-name match, like "Lerøy / Laks
            # & Vilt", is tried first, above.)
            first, second = [part.strip() for part in left.split("/", 1)]
            donor, how = self._find(first)
            if donor is not None and via is None:
                via, _ = self._find(second)
        if donor is None:
            donor, how = self._find(left)
        if donor is None:
            donor, how = self._create(left), "created"

        self.decisions[text] = (donor, via)
        self.report["supplier_resolution"].append(
            {"text": text, "donor": donor.name, "via": via.name if via else "", "how": how}
        )
        return donor, via


def _headers(ws):
    return {idx: _text(cell.value) for idx, cell in enumerate(ws[1]) if _text(cell.value)}


# ---- Contacts ----------------------------------------------------------------

REL_BY_WORD = {"spons": Donor.REL_SPONSOR, "kjøpt": Donor.REL_SUPPLIER, "spons og kjøp": Donor.REL_BOTH}


def _split_names(raw):
    people = []
    for part in re.split(r"\s+og\s+", _text(raw)):
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^(.*?)\s*\((.*)\)\s*$", part)
        people.append((match.group(1).strip(), match.group(2).strip()) if match else (part, ""))
    return people


def _import_contacts(ws, report):
    headers = _headers(ws)
    find = lambda word: next((i for i, h in headers.items() if word in h.lower()), None)
    c_donor, c_person, c_info, c_rel = find("leverandør"), find("kontaktperson"), find("kontaktinfo"), find("spons")
    if c_donor is None:
        return
    # The owner ("Henning", "Christine") sits in an unlabelled first column.
    c_owner = 0 if 0 not in headers else None
    owners = []

    resolver = DonorResolver(report)
    current = None
    for row in ws.iter_rows(min_row=2, values_only=True):
        name = _text(row[c_donor]) if c_donor < len(row) else ""
        person = _text(row[c_person]) if c_person is not None and c_person < len(row) else ""
        info = _text(row[c_info]) if c_info is not None and c_info < len(row) else ""
        word = _text(row[c_rel]).lower() if c_rel is not None and c_rel < len(row) else ""
        owner = _text(row[c_owner]) if c_owner is not None and c_owner < len(row) else ""

        if name:
            donor, _ = resolver._find(name)
            if donor is None:
                donor = resolver._create(name)
            else:
                report["donors"]["matched"] += 1
            if owner:
                close = difflib.get_close_matches(owner.lower(), [o.lower() for o in owners], n=1, cutoff=0.8)
                canonical = next((o for o in owners if o.lower() == close[0]), owner) if close else owner
                if canonical not in owners:
                    owners.append(canonical)
                if not donor.owner_name:
                    donor.owner_name = canonical
            if donor.relationship == Donor.REL_UNKNOWN and word in REL_BY_WORD:
                donor.relationship = REL_BY_WORD[word]
            donor.save()
            current = donor
        elif current is None or not (person or info):
            continue

        emails = [e.lower() for e in EMAIL.findall(info)]
        people = _split_names(person)
        if not people and emails:
            people = [("", "")] * 1
        pairs = list(zip(people, emails)) if len(people) == len(emails) and len(people) > 1 else None
        if pairs is None:
            pairs = [(p, emails[0] if emails else "") for p in people[:1]] + [(p, "") for p in people[1:]]
        extra = "; ".join(emails[1:]) if len(people) <= 1 and len(emails) > 1 else ""

        for (contact_name, role), email in pairs:
            if not (contact_name or email):
                continue
            existing = (
                current.contacts.filter(email__iexact=email).first() if email else current.contacts.filter(name__iexact=contact_name).first()
            )
            if existing:
                changed = False
                for field, value in (("name", contact_name), ("role", role), ("email", email)):
                    if value and not getattr(existing, field):
                        setattr(existing, field, value)
                        changed = True
                if changed:
                    existing.save()
                report["contacts"]["updated"] += int(changed)
            else:
                DonorContact.objects.create(
                    donor=current,
                    name=contact_name,
                    role=role,
                    email=email,
                    notes=f"Annen e-post: {extra}"[:300] if extra else "",
                    is_primary=not current.contacts.exists(),
                )
                report["contacts"]["created"] += 1


# ---- Food plan ---------------------------------------------------------------

def _column_map(ws):
    """Column index per meaning, and the years the headers talk about."""
    cols = {"requested": {}, "confirmed": {}, "actual": {}, "next_note": {}}
    for idx, header in _headers(ws).items():
        low = header.lower()
        year = re.search(r"(\d{4})", low)
        year = int(year.group(1)) if year else None
        if "sikret" in low:
            cols["secured"] = idx
        elif low == "kategori":
            cols["category"] = idx
        elif low == "matvare":
            cols["item"] = idx
        elif re.fullmatch(r"mengde \d{4}", low):
            cols["actual"][year] = idx
        elif low.startswith("ønsket mengde"):
            cols["requested"][year] = idx
        elif low.startswith("bekreftet mengde"):
            cols["confirmed"][year] = idx
        elif low.startswith("kommentar til"):
            cols["next_note"][year] = idx
        elif low == "kommentar":
            cols.setdefault("comment", idx)
        elif low.startswith("leverandør"):
            cols["supplier"] = idx
        elif low.startswith("betalt"):
            cols["acquisition"] = idx
        elif low.startswith("leveransedato"):
            cols["date"] = idx
        elif low.startswith("hentes"):
            cols["mode"] = idx
        elif low.startswith("mottatt"):
            cols["received"] = idx
        elif low.startswith("tomt når"):
            cols["ran_out"] = idx
    return cols


def _cell(row, cols, key, year=None):
    idx = cols.get(key)
    if isinstance(idx, dict):
        idx = idx.get(year)
    return row[idx] if idx is not None and idx < len(row) else None


_BLANK = (None, "", PlanLine.ACQ_UNKNOWN, PlanLine.MODE_UNKNOWN)


def _fill(obj, **values):
    """Set only fields that are currently empty, and only to a real value
    ("unknown" is the placeholder for nothing, not something to copy over).
    Returns True if any changed."""
    changed = False
    for field, value in values.items():
        if value not in _BLANK and getattr(obj, field) in _BLANK:
            setattr(obj, field, value)
            changed = True
    return changed


class LineStore:
    """Every plan line held in memory for the length of one import.

    The import used to look each row up and save it on its own -- around 1,500
    queries for one workbook. Against a database on another machine that is a
    minute or more, longer than the web server waits, so the request was cut
    off. Reading everything once and writing the changes in two bulk
    statements keeps it to a handful of round trips.

    A line with no donor is a wildcard: the main sheet often leaves the
    supplier blank because it is written inside the quantity ("6kg fra X via
    Y"), while the next-year sheet's "supplier last year" column names it --
    both must end up as the *same* line."""

    # Everything an import can change; id/created_at never are.
    UPDATE_FIELDS = [f.name for f in PlanLine._meta.concrete_fields if f.name not in ("id", "created_at")]

    def __init__(self, report):
        self.report = report
        self.new = []
        self.dirty = {}
        self._by_item_year = defaultdict(list)
        for line in PlanLine.objects.all():
            self._by_item_year[(line.year, line.item_id)].append(line)

    def find(self, year, item, donor):
        lines = self._by_item_year.get((year, item.pk), [])
        donor_id = donor.pk if donor else None
        exact = next((line for line in lines if line.donor_id == donor_id), None)
        if exact:
            return exact
        if donor_id is None:
            return lines[0] if len(lines) == 1 else None
        return next((line for line in lines if line.donor_id is None), None)

    def upsert(self, year, item, donor, via, create=True, **values):
        line = self.find(year, item, donor)
        if line is None:
            if not create:
                return None
            line = PlanLine(year=year, item=item, donor=donor, via_donor=via)
            for field, value in values.items():
                if value not in (None, ""):  # keep the model's defaults for blanks
                    setattr(line, field, value)
            self.new.append(line)
            self._by_item_year[(year, item.pk)].append(line)
            self.report["lines"]["created"] += 1
            return line
        changed = False
        if line.donor_id is None and donor is not None:
            line.donor, changed = donor, True
        if _fill(line, **({"via_donor": via} if via else {}), **values):
            changed = True
        if changed:
            if line.pk:
                self.dirty[line.pk] = line
            self.report["lines"]["updated"] += 1
        return line

    def flush(self):
        PlanLine.objects.bulk_create(self.new)
        now = timezone.now()
        for line in self.dirty.values():
            line.updated_at = now  # bulk_update doesn't run auto_now
        PlanLine.objects.bulk_update(list(self.dirty.values()), self.UPDATE_FIELDS)


def _quantities(report, raw, item_name, field):
    quantity, unit, note = parse_quantity(raw)
    if quantity is None and _text(raw):
        report["unparsed_quantities"].append({"item": item_name, "field": field, "text": _text(raw)[:80]})
    return quantity, unit, note


def _item_name(raw):
    """(clean name, dropped?) for an item cell -- \"Nøtter - DROPPET\" is a dropped item."""
    name = _text(raw)
    dropped = bool(re.search(r"\bdroppet\b", name, re.IGNORECASE))
    return re.sub(r"\s*-\s*droppet\s*$", "", name, flags=re.IGNORECASE).strip(), dropped


def _import_food(ws, report, resolver, main, items, store):
    cols = _column_map(ws)
    if "item" not in cols or "category" not in cols:
        return
    requested_years = sorted(cols["requested"])
    actual_years = sorted(cols["actual"])
    confirmed_years = sorted(cols["confirmed"])
    current = (confirmed_years or requested_years[:1] or [None])[0]
    if current is None:
        return
    nxt = current + 1
    previous = actual_years[0] if actual_years else current - 1
    report["years"] = {"previous": previous, "current": current, "next": nxt}

    for row in ws.iter_rows(min_row=2, values_only=True):
        category = _text(_cell(row, cols, "category"))
        name = _text(_cell(row, cols, "item"))
        if not (category and name):
            continue
        name, dropped = _item_name(name)
        item = items[name.lower()]

        donor, via = resolver.resolve(_cell(row, cols, "supplier"))

        # Last year's amount -- no donor recorded for it in the sheet.
        if main and previous in cols["actual"]:
            q, unit, note = _quantities(report, _cell(row, cols, "actual", previous), name, "actual")
            if q is not None:
                store.upsert(
                    previous, item, None, None,
                    quantity_confirmed=q, unit=unit, quantity_note=note, status=PlanLine.STATUS_DELIVERED,
                )

        # The year the sheet is about.
        if main:
            rq, run, rnote = _quantities(report, _cell(row, cols, "requested", current), name, "requested")
            cq, cun, cnote = _quantities(report, _cell(row, cols, "confirmed", current), name, "confirmed")
            unit = cun or run
            acquisition, price, price_note = parse_acquisition(_cell(row, cols, "acquisition"), unit)
            delivered = _truthy(_cell(row, cols, "received"))
            secured = _truthy(_cell(row, cols, "secured"))
            status = (
                PlanLine.STATUS_CANCELLED if dropped
                else PlanLine.STATUS_DELIVERED if delivered
                else PlanLine.STATUS_CONFIRMED if secured
                else PlanLine.STATUS_PLANNED
            )
            ran_out = _text(_cell(row, cols, "ran_out"))
            next_note = _text(_cell(row, cols, "next_note", nxt))
            outcome, leftover = guess_outcome(ran_out, next_note, unit)
            mode_word = _text(_cell(row, cols, "mode")).lower()
            store.upsert(
                current, item, donor, via,
                status=status, unit=unit, quantity_requested=rq, quantity_confirmed=cq,
                quantity_note=cnote or rnote, acquisition=acquisition, unit_price=price, price_note=price_note,
                delivery_date=_as_date(_cell(row, cols, "date")),
                delivery_mode=PlanLine.MODE_PICKUP if mode_word.startswith("hent") else PlanLine.MODE_DELIVERY if mode_word.startswith("lever") else "",
                outcome=outcome, outcome_source=PlanLine.SOURCE_IMPORTED if outcome else "",
                leftover_quantity=leftover, ran_out_note=ran_out[:200],
                comment=_text(_cell(row, cols, "comment")), next_year_note=next_note,
            )

        # The next-year copy names last year's supplier: use it to fill in a
        # current-year line the main sheet left without one.
        if not main and donor is not None:
            store.upsert(current, item, donor, via, create=False)

        # What is wanted next year: this sheet's own column, in the main sheet
        # (partly filled) or the dedicated next-year copy (nearly complete).
        wanted = _cell(row, cols, "requested", nxt)
        if _text(wanted) and not dropped:
            q, unit, note = _quantities(report, wanted, name, "requested_next")
            acquisition, price, price_note = parse_acquisition(_cell(row, cols, "acquisition"), unit)
            if q is not None or note:
                store.upsert(
                    nxt, item, donor, via,
                    quantity_requested=q, unit=unit, quantity_note=note,
                    acquisition=acquisition, unit_price=price, price_note=price_note,
                )


def _ensure_items(food_sheets, report):
    """Every item named in the food sheets, creating the missing ones in one
    batch. Returns {lower-case name: FoodItem}."""
    items = {item.name.lower(): item for item in FoodItem.objects.all()}
    missing = {}
    for ws in food_sheets:
        cols = _column_map(ws)
        if "item" not in cols or "category" not in cols:
            continue
        for row in ws.iter_rows(min_row=2, values_only=True):
            category, (name, _) = _text(_cell(row, cols, "category")), _item_name(_cell(row, cols, "item"))
            if category and name and name.lower() not in items and name.lower() not in missing:
                missing[name.lower()] = FoodItem(name=name[:150], category=category[:60])
    if missing:
        FoodItem.objects.bulk_create(list(missing.values()))
        if any(item.pk is None for item in missing.values()):  # database didn't hand back ids
            items.update({item.name.lower(): item for item in FoodItem.objects.filter(name__in=[i.name for i in missing.values()])})
        else:
            items.update(missing)
        report["items"]["created"] += len(missing)
    return items


def import_workbook(file, dry_run=False):
    """Import the workbook in `file` (a path or file-like object). Returns a
    report dict. Raises ImportError_ if it doesn't look like the food workbook."""
    try:
        wb = openpyxl.load_workbook(file, data_only=True, read_only=False)
    except Exception as exc:  # corrupt file, not an xlsx, password protected...
        raise ImportError_("Kunne ikke lese filen som en Excel-arbeidsbok (.xlsx).") from exc

    food = [ws for ws in wb.worksheets if "matvare" in " ".join(_headers(ws).values()).lower()]
    contacts = [ws for ws in wb.worksheets if "kontakt" in ws.title.lower()]
    if not food and not contacts:
        raise ImportError_("Fant verken et matvare-ark eller et kontakt-ark i filen.")

    report = {
        "dry_run": dry_run,
        "years": {},
        "donors": {"created": 0, "matched": 0},
        "contacts": {"created": 0, "updated": 0},
        "items": {"created": 0},
        "lines": {"created": 0, "updated": 0},
        "supplier_resolution": [],
        "unparsed_quantities": [],
    }

    with transaction.atomic():
        for ws in contacts:
            _import_contacts(ws, report)
        resolver = DonorResolver(report)
        items = _ensure_items(food, report)
        store = LineStore(report)
        # The sheet with last year's actual amounts goes first; copies for
        # the coming year only top up what the main sheet leaves blank.
        food.sort(key=lambda ws: 0 if any(re.fullmatch(r"mengde \d{4}", h.lower()) for h in _headers(ws).values()) else 1)
        for ws in food:
            is_main = any(re.fullmatch(r"mengde \d{4}", h.lower()) for h in _headers(ws).values())
            _import_food(ws, report, resolver, is_main, items, store)
        store.flush()
        _set_relationships_from_lines()
        if dry_run:
            transaction.set_rollback(True)

    report["unparsed_quantities"] = report["unparsed_quantities"][:30]
    return report


def _set_relationships_from_lines():
    """A donor with no stated relationship takes it from how its lines were
    acquired (all gifts -> sponsor, all purchases -> supplier, mixed -> both)."""
    kinds = defaultdict(set)
    for donor_id, acquisition in (
        PlanLine.objects.exclude(acquisition=PlanLine.ACQ_UNKNOWN).exclude(donor=None).values_list("donor_id", "acquisition").distinct()
    ):
        kinds[donor_id].add(acquisition)
    for donor in Donor.objects.filter(relationship=Donor.REL_UNKNOWN, pk__in=list(kinds)):
        acquired = kinds[donor.pk]
        if acquired == {PlanLine.ACQ_GIFT}:
            donor.relationship = Donor.REL_SPONSOR
        elif acquired == {PlanLine.ACQ_PURCHASE}:
            donor.relationship = Donor.REL_SUPPLIER
        else:
            donor.relationship = Donor.REL_BOTH
        donor.save(update_fields=["relationship"])
