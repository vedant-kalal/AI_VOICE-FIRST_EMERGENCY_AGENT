"""Pure-logic tests: severity scoring, resource ranking, taxonomy, geocoder, transcript helper."""
from app.services import geo_service, resource_service, severity
from app.utils import taxonomy


def test_severity_uses_only_stated_facts():
    r = severity.score_severity("road_accident", {"injuries": "serious", "people_trapped": True}, 2)
    assert r["score"] == 30 + 20 + 18 + 5 and r["level"] == "high"
    # Nothing about accent / language / emotion can change the score — signals are a closed set.
    same = severity.score_severity("road_accident", {"injuries": "serious", "people_trapped": True,
                                                     "accent": "strong", "panicked": True}, 2)
    assert same["score"] == r["score"]


def test_protocol_floor_and_llm_hint_cap():
    r = severity.score_severity("medical", {"unconscious_or_not_breathing": True})
    assert r["score"] >= 90 and r["level"] == "critical"
    low = severity.score_severity("animal_rescue", {}, 0, llm_hint=100)
    assert low["score"] == 10 + severity.LLM_NUDGE_MAX and low["disagreement"]


def test_merge_signals_never_downgrades():
    m = severity.merge_signals({"injuries": "serious", "people_trapped": True, "water_level": "waist"},
                               {"injuries": "minor", "people_trapped": False, "water_level": "knee"})
    assert m == {"injuries": "serious", "people_trapped": True, "water_level": "waist"}


def test_score_unit_prefers_capable_over_merely_close():
    close_incapable, _ = resource_service.score_unit(0.9, 0.0, True, 3.0, 0)
    far_capable, _ = resource_service.score_unit(3.8, 1.0, True, 8.0, 0)
    assert far_capable > close_incapable
    busy_queue, _ = resource_service.score_unit(3.8, 1.0, True, 8.0, 5)
    assert busy_queue < far_capable


def test_ranking_picks_industrial_truck_and_explains_rejections(db):
    out = resource_service.find_candidates(db, 23.0787, 72.5064, "fire_truck",
                                           ["industrial_fire_equipment"], 15)
    best = out["ranked"][0]
    assert "industrial_fire_equipment" in best["capabilities"]
    ftb = next(e for e in out["considered"] if e["callsign"] == "FT-B")
    assert ftb["decision"] == "rejected" and ftb["reason"].startswith("missing required equipment")


def test_eta_is_sane():
    assert 5 < resource_service.eta_minutes("ambulance", 3.2) < 12
    assert resource_service.eta_minutes("helicopter", 30) < resource_service.eta_minutes("ambulance", 30)


def test_taxonomy_is_consistent():
    tax = taxonomy.load_taxonomy()
    from app.models.resource import RESOURCE_TYPES
    for name, cat in tax["categories"].items():
        assert cat["safety_protocols"]["default"], name
        assert cat["triage_questions"], name
        assert all(d in tax["departments"] for d in cat["escalate_to"] + cat["departments"]), name
        assert all(r["type"] in RESOURCE_TYPES for r in cat["resources"]), name
    assert taxonomy.safety_protocol("medical", "he is not breathing")["protocol"] == "cpr"
    assert taxonomy.safety_protocol("fire", "")["protocol"] == "default"
    assert "supervisor" in taxonomy.escalation_targets("fire")


def test_geocoder_offline():
    g = geo_service.geocode("crash on the highway", "SG Mall")
    assert g["found"] and g["provider"] == "gazetteer" and abs(g["lat"] - 23.0787) < 0.01
    assert geo_service.geocode("iskon cross rd")["found"]  # fuzzy spelling
    assert not geo_service.geocode("qqqq zzzz")["found"]
    assert geo_service.haversine_km(23.0, 72.5, 23.0, 72.5) == 0


def test_generated_postgres_schema_is_in_sync_with_models():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "scripts/generate_schema_sql.py", "--check"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr   # fix: python scripts/generate_schema_sql.py
    sql = open("db/schema.sql").read()
    for must in ("CREATE EXTENSION IF NOT EXISTS postgis", "JSONB", "CONSTRAINT ck_incidents_status CHECK",
                 "REFERENCES incidents (id) ON DELETE CASCADE", "CREATE TABLE escalation_attempts",
                 "uq_escalation_attempts_handoff_step", "ix_resources_geog"):
        assert must in sql, must


def test_baton_rouge_drill_data_is_wired_up():
    """Drill F plays outside the default city: the gazetteer must resolve its address WITH its own city,
    the taxonomy must know the sub-type, and a traffic unit must exist within range."""
    from app.services import geo_service
    from app.services.seed import PLACED_BATON_ROUGE
    from app.utils import taxonomy

    g = geo_service.geocode("450 Laurel Street", "Baton Rouge")
    assert g["found"] and g["provider"] == "gazetteer"
    assert g["formatted"].endswith("Baton Rouge")            # not stamped with DEFAULT_CITY
    assert 30.4 < g["lat"] < 30.5 and -91.3 < g["lng"] < -91.1

    assert "open_manhole" in taxonomy.get_category("road_blockage")["sub_types"]
    assert "municipal_corp" in taxonomy.category_departments("road_blockage")

    police = [u for u in PLACED_BATON_ROUGE if u[1] == "police_unit"]
    assert police, "drill F dispatches a police_unit for traffic control"
    km = geo_service.haversine_km(g["lat"], g["lng"], police[0][2], police[0][3])
    assert km < 10, f"seeded traffic unit is {km:.1f} km away, outside the drill's search radius"
