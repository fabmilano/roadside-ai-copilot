from pydantic import BaseModel


class CoverageCitation(BaseModel):
    section: str
    snippet: str


class CoverageResult(BaseModel):
    covered: bool | None
    event_type: str
    applicable_section: str
    services_entitled: list[str]
    exclusions_flagged: list[str]
    reasoning: str
    citations: list[CoverageCitation]
    confidence: float


class SmsParts(BaseModel):
    greeting: str
    status_line: str
    action_line: str
    eta_line: str
    services_line: str
    case_ref_line: str
    emergency_footer: str
