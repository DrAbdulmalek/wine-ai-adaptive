from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class EvidenceStatus(StrEnum):
    PROVEN = "PROVEN"
    PARTIALLY_PROVEN = "PARTIALLY_PROVEN"
    UNPROVEN = "UNPROVEN"
    CONTRADICTED = "CONTRADICTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Evidence:
    key: str
    status: EvidenceStatus
    summary: str
    source: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result
