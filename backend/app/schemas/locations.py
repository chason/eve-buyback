from pydantic import BaseModel, ConfigDict

from app.domain.locations import LocationKind


class LocationOut(BaseModel):
    """An accepted buyback drop-off location (ADR-0030)."""

    model_config = ConfigDict(from_attributes=True)

    location_id: str
    kind: LocationKind
    name: str
    system_name: str | None = None


class LocationCreateRequest(BaseModel):
    """Add a drop-off location. For an NPC station the name is resolved server-side
    from the SDE (any supplied name is ignored); for a structure — which has no SDE —
    the name from the structure search is required."""

    location_id: str
    kind: LocationKind
    name: str | None = None
