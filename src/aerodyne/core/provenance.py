"""Provenance and record metadata.

The platform must never present simulated or estimated information as measured
flight data. Every value produced or ingested is tagged with a :class:`DataKind`,
and every stored engineering object carries :class:`RecordMeta`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar


class DataKind(str, Enum):
    """What a number *is*. Never silently convert between kinds."""

    MEASURED = "MEASURED"          # read from an instrument
    SIMULATED = "SIMULATED"        # output of a model run
    ESTIMATED = "ESTIMATED"        # engineering estimate / empirical correlation
    DERIVED = "DERIVED"            # computed from measured data (e.g. integrated velocity)
    HYPOTHETICAL = "HYPOTHETICAL"  # illustrative / what-if / synthetic example data


class DataQuality(str, Enum):
    """Source quality of a dataset (motor curves, aero tables, test data)."""

    CERTIFIED = "CERTIFIED"
    MANUFACTURER = "MANUFACTURER"
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    HYPOTHETICAL = "HYPOTHETICAL"
    UNKNOWN = "UNKNOWN"


class RecordStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"      # released for build/test; edits create a new revision
    FLOWN = "FLOWN"        # has flown; immutable forever
    RETIRED = "RETIRED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RecordMeta:
    """Metadata required on every engineering object (ID, VERSION, CREATED_AT,
    UPDATED_AT, AUTHOR, SOURCE, STATUS)."""

    author: str = "unknown"
    source: str = "unspecified"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    status: RecordStatus = RecordStatus.DRAFT

    def touch(self) -> None:
        self.updated_at = _utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "author": self.author,
            "source": self.source,
            "status": self.status.value,
        }


T = TypeVar("T")


@dataclass(frozen=True)
class Tagged(Generic[T]):
    """A value together with its provenance and (optional) 1-sigma uncertainty."""

    value: T
    kind: DataKind
    units: str = ""
    uncertainty: float | None = None
    note: str = ""

    def __str__(self) -> str:  # pragma: no cover - formatting only
        unc = f" ± {self.uncertainty:.4g}" if self.uncertainty is not None else ""
        return f"{self.value}{unc} {self.units} [{self.kind.value}]".strip()


def _jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if isinstance(obj, float):
        return float(repr(obj))
    return obj


def stable_hash(obj: Any) -> str:
    """SHA-256 of a canonical JSON serialization. Used for configuration hashes."""
    blob = json.dumps(_jsonable(obj), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()
