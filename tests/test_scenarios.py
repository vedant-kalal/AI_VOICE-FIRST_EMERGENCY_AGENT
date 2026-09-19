"""The PDF's scenarios (§7) and the safety gates, run through the real tool executor."""
from app.core.config import settings
from app.models.dispatch import Assignment
from app.models.incident import Escalation, Incident
from app.models.resource import Resource


def _active(db):
    return db.query(Incident).filter(Incident.status != "merged").all()


# ── Scenario A: road accident, trapped person, duplicate merge, parallel dispatch ──────────────
def test_scenario_a_road_accident_end_to_end(db, new_call):
    # An earlier caller already reported the same crash.
    ctx0, run0 = new_call("+919000000010")
    run0("geocode_location", raw_location_text="crash on SG Highway near SG Mall")
    first = run0("create_incident", category="road_accident",
                 description="big crash on the highway near the mall, person trapped in a car")
    assert first["status"] == "success"

    ctx, run = new_call()
    g = run("geocode_location", raw_location_text="highway", nearby_landmark="SG Mall")
    assert g["found"] and "SG Highway" in g["formatted"]

    created = run("create_incident", category="road_accident", reported_people_affected=2,
                  description="huge crash on the highway, someone is stuck in the car")
    assert created["status"] == "success"
    assert created["_effects"]["load_context"] == {"category": "road_accident",
                                                    "departments": ["traffic_police", "ems"]}

    dup = run("check_duplicate_incident")
    assert dup["duplicate"] is True and dup["incident_id"] == first["incident_id"]
    assert ctx.incident_id == first["incident_id"]  # the call now follows the primary incident
    assert dup["report_count"] == 2

    sev = run("estimate_severity", signals={"injuries": "serious", "people_trapped": True,
                                            "people_affected": 2})
    # 30 baseline + 20 serious injuries + 18 trapped + 5 (two people affected)
    assert sev["level"] == "high" and sev["severity"] == 73 and not sev["auto_escalated"]

    steps = run("give_caller_safety_instructions", category="road_accident",
                situation_detail="someone is trapped")
    assert steps["steps"] and "hazard" in " ".join(steps["steps"]).lower()

    amb = run("find_nearest_resource", resource_type="ambulance")
    assert amb["status"] == "success" and amb["candidates"][0]["callsign"] == "AMB-A"
    res = run("find_nearest_resource", resource_type="rescue_team", required_equipment=["jaws_of_life"])
    assert res["candidates"][0]["callsign"] == "RES-A"

    # Severity is 'high' (not critical): location must be confirmed before dispatch.
    blocked = run("assign_resource", resource_id=amb["recommended"], eta_minutes=6)
    assert blocked["status"] == "rejected" and blocked["gate"] == "location_not_confirmed"
    assert run("geocode_location", caller_confirmed=True)["confirmed"] is True

    a1 = run("assign_resource", resource_id=amb["recommended"])
    a2 = run("assign_resource", resource_id=res["recommended"])
    assert a1["status"] == "success" and a2["status"] == "success"
    assert "on the way" in a1["say_to_caller"] and a1["eta_minutes"] > 0
    assert run("assign_resource", resource_id=amb["recommended"])["already_assigned"] is True

    for r in (a1, a2):
        n = run("notify_dispatch_team", resource_id=r["resource_id"], summary="crash, person trapped")
        assert n["status"] == "success"

    assert db.query(Assignment).count() == 2
    assert db.query(Resource).filter_by(callsign="AMB-A").one().status == "en_route"
    assert len(_active(db)) == 1  # one incident, not two


# ── Scenario B: industrial gas leak → automatic multi-department escalation ─────────────────────
def test_scenario_b_gas_leak_auto_escalates(db, new_call):
    ctx, run = new_call()
    run("geocode_location", raw_location_text="gas smell near a factory in Naroda GIDC")
    run("create_incident", category="industrial_chemical",
        description="strong gas smell near a factory, people can't breathe", reported_people_affected=6)
    run("check_duplicate_incident")
    sev = run("estimate_severity", signals={"hazardous_material": True, "difficulty_breathing": True})
    assert sev["level"] == "critical" and sev["auto_escalated"]
    assert {"fire_dept", "hazmat", "police", "supervisor"} <= set(sev["escalated_to"])
    assert db.query(Escalation).count() == 1 and db.query(Incident).one().escalated

    # Critical incidents dispatch on the best guess: no location-confirmation / duplicate-check gates.
    fire = run("find_nearest_resource", resource_type="fire_truck", max_radius_km=30,
               required_equipment=["industrial_fire_equipment"])
    assert "industrial_fire_equipment" in fire["candidates"][0]["capabilities"]
    assert run("assign_resource", resource_id=fire["recommended"])["status"] == "success"


# ── Scenario C: five callers, one living incident whose severity keeps rising ───────────────────
def test_scenario_c_flood_cluster(db, new_call):
    reports = [
        ("street near Vastrapur Lake is waterlogged, water is rising", {"water_level": "ankle"}),
        ("Vastrapur lake road flooded, cars stuck in the water", {"water_level": "knee"}),
        ("flood water on the street near Vastrapur Lake, shops flooding", {"water_level": "knee"}),
        ("water is now waist deep near Vastrapur Lake", {"water_level": "waist"}),
        ("Vastrapur Lake area flooded, children and elderly stranded on the road",
         {"water_level": "waist", "children_or_elderly_involved": True}),
    ]
    severities = []
    for i, (desc, signals) in enumerate(reports):
        ctx, run = new_call(f"+91900000002{i}")
        run("geocode_location", raw_location_text="Vastrapur Lake")
        run("create_incident", category="flood", description=desc)
        run("check_duplicate_incident")
        severities.append(run("estimate_severity", signals=signals)["severity"])
    live = _active(db)
    assert len(live) == 1 and live[0].report_count == 5
    assert severities == sorted(severities) and severities[-1] > severities[0]
    assert live[0].severity >= settings.CRITICAL_SEVERITY and live[0].escalated


# ── Scenario D: prank / low confidence → never dispatches ───────────────────────────────────────
def test_scenario_d_prank_goes_to_human_review(db, new_call):
    ctx, run = new_call("+919000000099")
    created = run("create_incident", category="other", confidence="low", description="hello test")
    assert created["flagged_for_review"] is True
    assert db.query(Incident).one().status == "needs_review"

    unit = db.query(Resource).filter_by(callsign="AMB-A").one()
    for _ in range(3):  # hard gate: no one-shot escape valve for a flagged incident
        r = run("assign_resource", resource_id=str(unit.id))
        assert r["status"] == "rejected" and r["gate"] == "needs_review"
    handoff = run("transfer_to_human_operator", department="police",
                  context_summary="caller gave contradictory answers")
    eff = handoff["_effects"]["transfer_call"]
    assert eff["department"] == "police" and handoff["contacts_in_ladder"] == 5   # 2 on-call x 2 cycles + director
    import uuid
    from app.models.department import CallHandoff
    h = db.get(CallHandoff, uuid.UUID(eff["handoff_id"]))
    assert h.status == "pending" and h.call_sid == ctx.call_sid and h.incident_id is not None


# ── Gates: instructive rejection, then a one-shot escape valve (no infinite loops) ──────────────
def test_gates_are_one_shot(db, new_call):
    ctx, run = new_call()
    run("geocode_location", raw_location_text="Law Garden")
    run("create_incident", category="medical", description="an elderly man has chest pain and is sweating")
    amb = run("find_nearest_resource", resource_type="ambulance")["recommended"]
    gates = []
    for _ in range(4):
        r = run("assign_resource", resource_id=amb)
        gates.append(r.get("gate") or r["status"])
    assert gates == ["severity_not_estimated", "duplicate_not_checked", "location_not_confirmed", "success"]


# ── Concurrency: one ambulance, two incidents, exactly one winner ───────────────────────────────
def test_resource_cannot_be_double_assigned(db, new_call):
    winners = []
    unit_id = str(db.query(Resource).filter_by(callsign="AMB-A").one().id)
    for i in range(2):
        ctx, run = new_call(f"+91900000003{i}")
        run("geocode_location", raw_location_text=["Iskcon Cross Road", "Thaltej Cross Road"][i])
        run("create_incident", category="medical", description=f"person collapsed and not breathing, case {i}")
        run("estimate_severity", signals={"unconscious_or_not_breathing": True})
        winners.append(run("assign_resource", resource_id=unit_id))
    assert [w["status"] for w in winners] == ["success", "error"]
    assert "no longer available" in winners[1]["message"]


# ── Human-in-the-loop dispatch mode ─────────────────────────────────────────────────────────────
def test_approval_mode_reserves_instead_of_dispatching(db, new_call, monkeypatch):
    monkeypatch.setattr(settings, "DISPATCH_MODE", "approval")
    ctx, run = new_call()
    run("geocode_location", raw_location_text="Kankaria Lake")
    run("create_incident", category="medical", description="man collapsed, not breathing at Kankaria")
    run("estimate_severity", signals={"unconscious_or_not_breathing": True})
    amb = run("find_nearest_resource", resource_type="ambulance")
    r = run("assign_resource", resource_id=amb["recommended"])
    assert r["pending_approval"] is True and "on the way" not in r["say_to_caller"]
    assert db.query(Resource).filter_by(callsign=r["callsign"]).one().status == "reserved"
    n = run("notify_dispatch_team", resource_id=r["resource_id"], summary="cardiac arrest")
    assert n["delivery"] == "held_for_approval"


def test_unknown_tool_and_bad_args_do_not_crash(new_call):
    ctx, run = new_call()
    assert run("nope")["status"] == "error"
    assert run("assign_resource", resource_id="x")["status"] == "error"        # no incident yet
    assert run("create_incident", category="alien_invasion", description="x")["status"] == "error"
    assert run("geocode_location", raw_location_text="qqqqzzzz nowhere")["status"] == "not_found"


# ── Live prompt context: which departments are loaded, and when it changes ───────────────────────
def test_load_context_follows_creation_merge_and_escalation(db, new_call):
    ctx, run = new_call()
    run("geocode_location", raw_location_text="Naroda GIDC")
    created = run("create_incident", category="industrial_chemical",
                  description="strong gas smell near a factory, people cannot breathe")
    assert created["_effects"]["load_context"]["departments"] == ["fire_dept", "hazmat", "pollution_control"]

    # auto-escalation at critical severity adds the escalated departments (police, supervisor) to the live prompt
    sev = run("estimate_severity", signals={"hazardous_material": True, "difficulty_breathing": True})
    eff = sev["_effects"]["load_context"]
    assert eff["category"] == "industrial_chemical"
    assert eff["departments"][:3] == ["fire_dept", "hazmat", "pollution_control"]      # lead departments stay first
    # 3 owning departments + police + supervisor = 5 > cap of 4: the supervisor desk (ordered last) is what gets dropped
    assert eff["departments"] == ["fire_dept", "hazmat", "pollution_control", "police"]

    # an explicit escalation with a new department is reflected too
    ctx2, run2 = new_call("+919000000042")
    run2("geocode_location", raw_location_text="Law Garden")
    run2("create_incident", category="medical", description="man collapsed at the garden, not breathing")
    esc = run2("escalate_incident", reason="crowd forming around the patient", escalate_to=["police"])
    assert esc["_effects"]["load_context"]["departments"] == ["ems", "police"]


def test_supervisor_is_loaded_when_there_is_room(db, new_call):
    ctx, run = new_call("+919000000055")
    run("geocode_location", raw_location_text="Kankaria Lake")
    run("create_incident", category="medical", description="man collapsed at the lake and is not breathing")
    sev = run("estimate_severity", signals={"unconscious_or_not_breathing": True})     # critical -> auto-escalates
    assert sev["_effects"]["load_context"]["departments"] == ["ems", "supervisor"]     # room left -> desk is loaded last
