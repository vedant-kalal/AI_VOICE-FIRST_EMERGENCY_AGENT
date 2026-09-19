from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid,
)

from app.models.base import BaseUUIDAuditModel, JSONType, in_check

RESOURCE_TYPES = (
    "ambulance", "fire_truck", "police_unit", "rescue_team",
    "flood_rescue_boat", "hazmat_team", "tow_truck", "helicopter",
)
RESOURCE_STATUSES = ("available", "reserved", "en_route", "on_scene", "busy", "offline")
FACILITY_TYPES = ("hospital", "fire_station", "police_station", "shelter", "government_office", "other")
FACILITY_SOURCES = ("seed", "overpass", "google_places")


class Resource(BaseUUIDAuditModel):
    """A dispatchable unit (PDF §5.1 `resources`). lat/lng are plain columns; on Postgres the
    PostGIS path builds the geography point on the fly (see resource_service and the GiST expression
    index in alembic/versions/0001_initial_schema.py)."""

    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint(in_check("type", RESOURCE_TYPES), name="ck_resources_type"),
        CheckConstraint(in_check("status", RESOURCE_STATUSES), name="ck_resources_status"),
        CheckConstraint("lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180", name="ck_resources_coords"),
        Index("ix_resources_type_status", "type", "status"),
    )

    callsign = Column(String(50), nullable=False, unique=True)
    type = Column(String(30), nullable=False, index=True)
    status = Column(String(20), default="available", nullable=False, index=True)
    # available | reserved (awaiting human approval) | en_route | on_scene | busy | offline
    capabilities = Column(JSONType, default=list)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    speed_kmh = Column(Float, default=40.0)
    queue_load = Column(Integer, default=0)  # jobs already queued on this unit
    contact_number = Column(String(32))  # crew SMS/radio-relay number (synthetic in demo)
    department_key = Column(String(50), index=True)  # owning department (taxonomy key), e.g. "fire_dept"
    home_facility_id = Column(Uuid(as_uuid=True), ForeignKey("facilities.id", ondelete="SET NULL"), nullable=True)
    last_updated = Column(DateTime(timezone=True))
    is_synthetic = Column(Boolean, default=True, nullable=False)


class ResourceLocationPing(BaseUUIDAuditModel):
    """GPS history for a unit (PDF §5.4: 'vehicle icons animating toward the incident using resource GPS
    pings'). Written by the vehicle feed — in this demo by the fleet simulator; in production by the AVL/GPS
    integration. Append-only; the latest row per unit mirrors resources.lat/lng."""

    __tablename__ = "resource_location_pings"
    __table_args__ = (
        Index("ix_pings_resource_recorded", "resource_id", "recorded_at"),
        CheckConstraint("lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180", name="ck_pings_coords"),
    )

    resource_id = Column(Uuid(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    assignment_id = Column(Uuid(as_uuid=True), ForeignKey("assignments.id", ondelete="SET NULL"), nullable=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    speed_kmh = Column(Float)
    heading_deg = Column(Float)
    source = Column(String(30), default="simulator")  # simulator | gps | avl
    recorded_at = Column(DateTime(timezone=True), nullable=False)


class Facility(BaseUUIDAuditModel):
    """Hospitals, fire/police stations, shelters, offices (PDF §5.1 `facilities`). Rows come from the seed data
    OR are upserted from an external place service (Overpass / Google Places) by facility_locator."""

    __tablename__ = "facilities"
    __table_args__ = (
        CheckConstraint(in_check("type", FACILITY_TYPES), name="ck_facilities_type"),
        CheckConstraint(in_check("source", FACILITY_SOURCES), name="ck_facilities_source"),
        UniqueConstraint("source", "external_place_id", name="uq_facilities_source_place"),
        Index("ix_facilities_type_active", "type", "is_active"),
    )

    type = Column(String(30), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    address = Column(Text)
    phone = Column(String(40))
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    department_key = Column(String(50), index=True)
    # hospitals: {"beds": 40, "available_beds": 6, "icu": 4, "available_icu": 1,
    #             "trauma": true, "burn_unit": false, "toxicology": false, "cardiac": true}
    capacity = Column(JSONType)
    source = Column(String(20), nullable=False, default="seed")
    external_place_id = Column(String(120))  # e.g. "node/429410556" (OSM) or a Google place id
    last_synced_at = Column(DateTime(timezone=True))
    is_synthetic = Column(Boolean, default=True, nullable=False)


class FacilityLookup(BaseUUIDAuditModel):
    """Cache of external place-search responses (cost + latency + rate-limit control). Keyed by provider,
    department, coordinates rounded to ~110 m and radius; expired rows are ignored and overwritten."""

    __tablename__ = "facility_lookups"
    __table_args__ = (UniqueConstraint("cache_key", name="uq_facility_lookups_key"),)

    cache_key = Column(String(64), nullable=False)  # sha256 hex of the normalised query
    provider = Column(String(20), nullable=False)
    department_key = Column(String(50), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    radius_km = Column(Float, nullable=False)
    result_count = Column(Integer, default=0)
    response = Column(JSONType)  # normalised list of places
    error = Column(Text)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
