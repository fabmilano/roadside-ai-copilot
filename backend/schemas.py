from typing import Annotated, Literal

from pydantic import BaseModel, Field

ServiceName = Literal[
    "Roadside Attempt",
    "Local Recovery",
    "National Recovery",
    "Home Start",
    "Labour",
    "Hire Car",
    "Hotel Accommodation",
    "Rail Travel",
]

EventType = Literal[
    "Breakdown",
    "Flat Battery",
    "Flat Tyre",
    "Fuel",
    "Accident",
    "Key Issue",
    "Commercial Use Exclusion",
    "Other",
]


class CoverageCitation(BaseModel):
    section: str
    snippet: str


class CoverageResult(BaseModel):
    covered: bool | None
    event_type: EventType
    applicable_section: str
    services_entitled: list[ServiceName]
    exclusions_flagged: list[str]
    reasoning: str
    citations: list[CoverageCitation]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class SmsParts(BaseModel):
    greeting: str
    status_line: str
    action_line: str
    eta_line: str
    services_line: str
    case_ref_line: str
    emergency_footer: str
