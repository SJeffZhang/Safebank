import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel


def derive(plan_id: str, step_id: str, kind: str, leg: str = "main") -> str:
    return f"{kind}_{uuid5(NAMESPACE_URL, f'safebank:{plan_id}:{step_id}:{kind}:{leg}')}"


def canonical(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
