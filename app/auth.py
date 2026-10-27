import hashlib
import hmac
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Principal:
    name: str
    is_admin: bool = False

    def can_access(self, owner: str) -> bool:
        return self.is_admin or self.name == owner


ANONYMOUS_ADMIN = Principal(name="anonymous", is_admin=True)


def _digest(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


@lru_cache
def _parse_keys(raw: str) -> tuple[tuple[bytes, Principal], ...]:
    entries = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
            raise ValueError("API_KEYS entries must look like name:key or name:key:admin")
        entries.append((_digest(parts[1]), Principal(parts[0], len(parts) == 3 and parts[2] == "admin")))
    return tuple(entries)


def authenticate(key: str | None, settings: Settings) -> Principal | None:
    if not settings.auth_enabled:
        return ANONYMOUS_ADMIN
    if not key:
        return None
    digest = _digest(key)
    match = None
    # Compare against every key so timing does not reveal which one matched.
    for known, principal in _parse_keys(settings.api_keys):
        if hmac.compare_digest(known, digest):
            match = principal
    return match


def get_principal(
    key: str | None = Security(api_key_header), settings: Settings = Depends(get_settings)
) -> Principal:
    principal = authenticate(key, settings)
    if principal is None:
        raise HTTPException(
            status_code=401, detail="Missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"}
        )
    return principal


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return principal
