from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Dict
from urllib.parse import urlencode

import httpx2 as httpx

from shared.jarvis_common.config import settings
from shared.jarvis_common.db.oauth_store import OAuthTokenRecord, oauth_token_store

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
)


def start_oauth(account_key: str = "household") -> Dict[str, str]:
    if not settings.google_client_id or not settings.google_redirect_uri:
        raise ValueError("Google OAuth is not configured.")
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": f"{state}:{account_key}",
    }
    return {
        "auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}",
        "state": state,
        "account_key": account_key,
    }


def complete_oauth(code: str, state: str) -> OAuthTokenRecord:
    if not settings.google_client_id or not settings.google_client_secret or not settings.google_redirect_uri:
        raise ValueError("Google OAuth is not configured.")
    account_key = "household"
    if ":" in state:
        _, account_key = state.rsplit(":", 1)
    payload = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }
    token_data = _post_token(payload)
    return _save_token(account_key, token_data)


def refresh_oauth_token(account_key: str = "household") -> OAuthTokenRecord:
    record = oauth_token_store.get("google", account_key)
    if record is None or not record.refresh_token:
        raise ValueError("No refresh token available.")
    payload = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": record.refresh_token,
        "grant_type": "refresh_token",
    }
    token_data = _post_token(payload)
    token_data.setdefault("refresh_token", record.refresh_token)
    return _save_token(account_key, token_data)


def oauth_status(account_key: str = "household") -> Dict[str, Any]:
    record = oauth_token_store.get("google", account_key)
    if record is None:
        return {"connected": False, "account_key": account_key}
    expired = record.expires_at is not None and record.expires_at <= datetime.now(UTC)
    return {
        "connected": True,
        "account_key": account_key,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "expired": expired,
        "scopes": record.scopes,
    }


def get_access_token(account_key: str = "household") -> str:
    record = oauth_token_store.get("google", account_key)
    if record is None:
        raise ValueError("Google account not connected.")
    if record.expires_at is not None and record.expires_at <= datetime.now(UTC) + timedelta(minutes=1):
        record = refresh_oauth_token(account_key)
    return record.access_token


def _post_token(payload: Dict[str, str]) -> Dict[str, Any]:
    with httpx.Client(timeout=15.0) as client:
        response = client.post(GOOGLE_TOKEN_URL, data=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid token response.")
        return data


def _save_token(account_key: str, token_data: Dict[str, Any]) -> OAuthTokenRecord:
    expires_in = token_data.get("expires_in")
    expires_at = None
    if expires_in is not None:
        expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
    scope_raw = str(token_data.get("scope", ""))
    scopes = scope_raw.split() if scope_raw else list(GOOGLE_SCOPES)
    record = OAuthTokenRecord(
        provider="google",
        account_key=account_key,
        access_token=str(token_data["access_token"]),
        refresh_token=token_data.get("refresh_token"),
        expires_at=expires_at,
        scopes=scopes,
        metadata={"token_type": token_data.get("token_type", "Bearer")},
    )
    oauth_token_store.upsert(record)
    return record
