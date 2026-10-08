from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from uuid import UUID, uuid4

from server.utils.config_loader import get_auth_secret

VIEWER_SESSION_MINUTES = int(os.getenv("VIEWER_SESSION_MINUTES", "15"))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class ViewerSessionService:
    @staticmethod
    def generate_token(
        user_id: UUID,
        tenant_id: UUID,
        grant_id: UUID | str | None = None,
        asset_id: UUID | str | None = None,
        version_id: UUID | str | None = None,
    ) -> str:
        now = int(time.time())
        exp = now + (VIEWER_SESSION_MINUTES * 60)
        payload = {
            "iss": "byaan-api",
            "aud": "byaan-viewer",
            "uid": str(user_id),
            "tid": str(tenant_id),
            "jti": str(uuid4()),
            "iat": now,
            "nbf": now,
            "exp": exp,
        }
        if grant_id is not None:
            payload["grant_id"] = str(grant_id)
        if asset_id is not None:
            payload["asset_id"] = str(asset_id)
        if version_id is not None:
            payload["version_id"] = str(version_id)
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        payload_b64 = _b64url_encode(payload_bytes)
        secret = get_auth_secret().encode()
        signature = hmac.new(secret, payload_b64.encode(), hashlib.sha256).digest()
        return f"{payload_b64}.{_b64url_encode(signature)}"

    @staticmethod
    def verify(token: str) -> dict[str, str] | None:
        try:
            payload_b64, signature_b64 = token.split(".", 1)
        except ValueError:
            return None

        try:
            secret = get_auth_secret().encode()
            expected_sig = hmac.new(secret, payload_b64.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(_b64url_encode(expected_sig), signature_b64):
                return None

            payload = json.loads(_b64url_decode(payload_b64))
            if int(payload.get("exp", 0)) <= int(time.time()):
                return None
            if int(payload.get("nbf", 0)) > int(time.time()):
                return None
            if payload.get("iss", "byaan-api") != "byaan-api":
                return None
            if payload.get("aud", "byaan-viewer") != "byaan-viewer":
                return None

            return payload
        except Exception:
            return None
