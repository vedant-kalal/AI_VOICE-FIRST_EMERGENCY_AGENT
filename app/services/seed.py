"""Synthetic demo data (PDF §10 "FAKE CONVINCINGLY — synthetic but clearly labeled as such").

Every row is flagged is_synthetic=True and named/labelled accordingly. Phone numbers use the
'+91 900000xxxx' pattern and are NOT real. A handful of units are hand-placed near "SG Mall" /
SG Highway so the PDF's flagship scenarios behave predictably on stage:
  * FT-B is the CLOSEST fire truck but has no industrial equipment; FT-C is farther but industrial-capable
    -> ranking picks FT-C (PDF §5.3: "Fire Truck C over closer Fire Truck B because of equipment").
"""
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.department import Department, DepartmentContact
from app.models.resource import Facility, Resource
from app.services.twilio_escalation import E164
from app.utils import taxonomy

CAPS = {
    "ambulance": [["trauma_kit", "als"], ["trauma_kit"], ["cardiac", "als"], ["neonatal"]],
    "fire_truck": [["water_tanker"], ["water_tanker", "aerial_ladder"], ["industrial_fire_equipment", "water_tanker"]],
    "police_unit": [["patrol"], ["patrol", "crowd_control"], ["patrol", "traffic"]],
    "rescue_team": [["jaws_of_life", "structural_rescue"], ["search_dog"], ["structural_rescue"], ["jaws_of_life"]],
    "flood_rescue_boat": [["boat", "life_jackets"]],
    "hazmat_team": [["hazmat_suit", "gas_detector"]],
    "tow_truck": [["heavy_tow"], ["light_tow"]],
    "helicopter": [["airlift", "search_light"]],
}
SPEED = {"ambulance": 45, "fire_truck": 40, "police_unit": 50, "rescue_team": 40,
         "flood_rescue_boat": 25, "hazmat_team": 40, "tow_truck": 35, "helicopter": 180}
# Which department owns which kind of unit / facility (drives the "nearest centre of any department" lookup).
UNIT_DEPARTMENT = {"ambulance": "ems", "fire_truck": "fire_dept", "police_unit": "police", "rescue_team": "disaster_management",
                   "flood_rescue_boat": "disaster_management", "hazmat_team": "hazmat", "tow_truck": "traffic_police",
                   "helicopter": "disaster_management"}
FACILITY_DEPARTMENT = {"hospital": "ems", "fire_station": "fire_dept", "police_station": "police"}
FACILITY_OF_DEPARTMENT = {"fire_dept": "fire_station", "hazmat": "fire_station", "police": "police_station",
                          "traffic_police": "police_station", "ems": "hospital"}
PREFIX = {"ambulance": "AMB", "fire_truck": "FT", "police_unit": "PU", "rescue_team": "RES",
          "flood_rescue_boat": "BOAT", "hazmat_team": "HAZ", "tow_truck": "TOW", "helicopter": "HELI"}

# (callsign, type, lat, lng, capabilities, status) — hand-placed around SG Highway / SG Mall (23.0787, 72.5064)
PLACED = [
    ("AMB-A", "ambulance", 23.0560, 72.5100, ["trauma_kit", "als"], "available"),
    ("AMB-B", "ambulance", 23.0300, 72.5200, ["trauma_kit"], "available"),
    ("RES-A", "rescue_team", 23.0600, 72.5000, ["jaws_of_life", "structural_rescue"], "available"),
    ("FT-B", "fire_truck", 23.0700, 72.5100, ["water_tanker"], "available"),
    ("FT-C", "fire_truck", 23.0450, 72.5100, ["industrial_fire_equipment", "water_tanker"], "available"),
    ("HAZ-A", "hazmat_team", 23.0500, 72.5400, ["hazmat_suit", "gas_detector"], "available"),
    ("PU-A", "police_unit", 23.0650, 72.5150, ["patrol", "traffic"], "available"),
    ("BOAT-A", "flood_rescue_boat", 23.0350, 72.5717, ["boat", "life_jackets"], "available"),
]

HOSPITALS = [
    ("Demo Hospital - Sola", 23.0771, 72.5180, dict(beds=60, available_beds=9, icu=8, available_icu=2, trauma=True, burn_unit=False, toxicology=False, cardiac=True)),
    ("Demo Hospital - Vastrapur", 23.0369, 72.5288, dict(beds=40, available_beds=6, icu=4, available_icu=1, trauma=True, burn_unit=False, toxicology=True, cardiac=True)),
    ("Demo Hospital - Civil Trauma & Burns", 23.0525, 72.6039, dict(beds=120, available_beds=2, icu=20, available_icu=0, trauma=True, burn_unit=True, toxicology=True, cardiac=True)),
    ("Demo Hospital - Bopal", 23.0338, 72.4622, dict(beds=30, available_beds=0, icu=2, available_icu=0, trauma=False, burn_unit=False, toxicology=False, cardiac=False)),
    ("Demo Hospital - Maninagar", 22.9968, 72.6077, dict(beds=50, available_beds=12, icu=6, available_icu=3, trauma=True, burn_unit=False, toxicology=False, cardiac=True)),
    ("Demo Hospital - Naroda", 23.0748, 72.6560, dict(beds=45, available_beds=4, icu=5, available_icu=1, trauma=True, burn_unit=True, toxicology=True, cardiac=False)),
]
STATIONS = [
    ("fire_station", "Demo Fire Station - Thaltej", 23.0498, 72.5075),
    ("fire_station", "Demo Fire Station - Navrangpura", 23.0367, 72.5610),
    ("fire_station", "Demo Fire Station - Odhav", 23.0215, 72.6740),
    ("police_station", "Demo Police Station - Sola", 23.0771, 72.5180),
    ("police_station", "Demo Police Station - Paldi", 23.0116, 72.5620),
    ("police_station", "Demo Police Station - Vatva", 22.9584, 72.6377),
]


def seed(db: Session, reset: bool = True, extra_random: int = 22, rng_seed: int = 9) -> dict:
    if reset:
        db.query(Resource).filter(Resource.is_synthetic.is_(True)).delete()
        db.query(Facility).filter(Facility.is_synthetic.is_(True), Facility.source == "seed").delete()
        db.flush()

    rng = random.Random(rng_seed)
    now = datetime.now(timezone.utc)
    n = 0

    def add(callsign, rtype, lat, lng, caps, status):
        nonlocal n
        n += 1
        db.add(Resource(callsign=callsign, type=rtype, status=status, capabilities=caps, lat=lat, lng=lng,
                        department_key=UNIT_DEPARTMENT[rtype],
                        speed_kmh=SPEED[rtype], queue_load=rng.choice([0, 0, 0, 1, 2]),
                        contact_number=f"+91900000{n:04d}", last_updated=now, is_synthetic=True))

    for callsign, rtype, lat, lng, caps, status in PLACED:
        add(callsign, rtype, lat, lng, caps, status)

    counters: dict[str, int] = {}
    types = (["ambulance"] * 6 + ["fire_truck"] * 4 + ["police_unit"] * 5 + ["rescue_team"] * 3
             + ["tow_truck"] * 2 + ["hazmat_team", "flood_rescue_boat", "helicopter"])
    for i in range(extra_random):
        rtype = types[i % len(types)]
        counters[rtype] = counters.get(rtype, 0) + 1
        callsign = f"{PREFIX[rtype]}-{counters[rtype]:02d}"
        lat = 23.0225 + rng.uniform(-0.09, 0.10)
        lng = 72.5714 + rng.uniform(-0.13, 0.12)
        status = rng.choices(["available", "busy", "offline"], weights=[80, 15, 5])[0]
        add(callsign, rtype, round(lat, 5), round(lng, 5), rng.choice(CAPS[rtype]), status)

    for name, lat, lng, cap in HOSPITALS:
        db.add(Facility(type="hospital", name=name, lat=lat, lng=lng, capacity=cap, is_synthetic=True,
                        department_key="ems", source="seed"))
    for ftype, name, lat, lng in STATIONS:
        db.add(Facility(type=ftype, name=name, lat=lat, lng=lng, capacity={}, is_synthetic=True,
                        department_key=FACILITY_DEPARTMENT.get(ftype), source="seed"))
    db.flush()
    depts = seed_departments(db)
    return {"resources": db.query(Resource).count(), "facilities": db.query(Facility).count(), "departments": depts}


def _demo_phone(idx: int) -> str:
    """Synthetic E.164 number, or ESCALATION_DEMO_NUMBER when set (so a team can test a REAL transfer with
    their own phone: every ladder rung then rings that number)."""
    override = (settings.ESCALATION_DEMO_NUMBER or "").strip()
    return override if E164.match(override) else f"+91900010{idx:04d}"


def seed_departments(db: Session) -> int:
    """Upsert every taxonomy department with an escalation ladder: two on-call contacts (rung 1, 2) and a director.
    Upsert (not delete + insert) so re-seeding never breaks call_handoffs that reference a department."""
    n = 0
    for idx, (key, meta) in enumerate(taxonomy.load_taxonomy()["departments"].items(), start=1):
        dept = db.query(Department).filter(Department.key == key).first()
        if dept is None:
            dept = Department(key=key, name=meta["name"], facility_type=FACILITY_OF_DEPARTMENT.get(key))
            db.add(dept)
            db.flush()
        dept.name, dept.dial_timeout_seconds, dept.repeat_cycles = meta["name"], 30, 2
        ladder = [("On-call officer A", "on_call", 1), ("On-call officer B", "on_call", 2), ("Department director", "director", 9)]
        for j, (label, role, priority) in enumerate(ladder):
            c = (db.query(DepartmentContact)
                 .filter(DepartmentContact.department_id == dept.id, DepartmentContact.priority == priority).first())
            if c is None:
                c = DepartmentContact(department_id=dept.id, priority=priority, name="", role=role, phone="")
                db.add(c)
            c.name, c.role, c.phone = f"{meta['name']} - {label} (synthetic)", role, _demo_phone(idx * 10 + j)
        n += 1
    db.flush()
    return n
