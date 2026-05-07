"""Identity guard helpers for Atlassian-authenticated requests."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from mcp_atlassian.utils.env import is_env_extended_truthy

IdentityGuardMode = Literal["off", "write", "all"]


@dataclass(frozen=True)
class ExpectedIdentity:
    """Expected Atlassian identity values for request validation."""

    account_id: str | None = None
    email: str | None = None
    username: str | None = None
    display_name: str | None = None
    user: str | None = None

    @property
    def is_configured(self) -> bool:
        """Return whether any expected identity field is configured."""
        return any(
            [
                self.account_id,
                self.email,
                self.username,
                self.display_name,
                self.user,
            ]
        )

    def describe(self) -> str:
        """Return a concise, non-secret description for logs/errors."""
        parts: list[str] = []
        if self.account_id:
            parts.append(f"account_id={self.account_id}")
        if self.email:
            parts.append(f"email={self.email}")
        if self.username:
            parts.append(f"username={self.username}")
        if self.display_name:
            parts.append(f"display_name={self.display_name}")
        if self.user:
            parts.append(f"user={self.user}")
        return ", ".join(parts) if parts else "none"


@dataclass(frozen=True)
class ActualIdentity:
    """Normalized Atlassian current-user identity."""

    account_id: str | None = None
    email: str | None = None
    username: str | None = None
    display_name: str | None = None

    @property
    def candidate_values(self) -> list[str]:
        """All non-empty identity values useful for flexible matching."""
        return [
            value
            for value in [
                self.account_id,
                self.email,
                self.username,
                self.display_name,
            ]
            if value
        ]

    def describe(self) -> str:
        """Return a concise current-user description for errors."""
        parts: list[str] = []
        if self.display_name:
            parts.append(self.display_name)
        if self.email:
            parts.append(f"<{self.email}>")
        if self.account_id:
            parts.append(f"({self.account_id})")
        if not parts and self.username:
            parts.append(self.username)
        return " ".join(parts) if parts else "unknown"


@dataclass(frozen=True)
class IdentityGuardConfig:
    """Runtime configuration for Atlassian identity enforcement."""

    mode: IdentityGuardMode = "off"
    expected: ExpectedIdentity = ExpectedIdentity()
    require_request_auth: bool = False

    @property
    def enabled(self) -> bool:
        """Return whether any identity guard mode is active."""
        return self.mode != "off"

    @property
    def enforce_writes(self) -> bool:
        """Return whether write tools require identity guard checks."""
        return self.mode in ("write", "all")

    @property
    def enforce_all(self) -> bool:
        """Return whether read and write fetcher creation requires checks."""
        return self.mode == "all"

    @classmethod
    def from_env(cls) -> IdentityGuardConfig:
        """Create identity guard configuration from environment variables."""
        mode = _parse_identity_guard_mode()
        expected = ExpectedIdentity(
            account_id=_get_env_str("ATLASSIAN_IDENTITY_GUARD_ACCOUNT_ID"),
            email=_get_env_str("ATLASSIAN_IDENTITY_GUARD_EMAIL"),
            username=_get_env_str("ATLASSIAN_IDENTITY_GUARD_USERNAME"),
            display_name=_get_env_str("ATLASSIAN_IDENTITY_GUARD_DISPLAY_NAME"),
            user=_get_env_str("ATLASSIAN_IDENTITY_GUARD_USER"),
        )
        require_request_auth = is_env_extended_truthy(
            "ATLASSIAN_IDENTITY_GUARD_REQUIRE_REQUEST_AUTH", "false"
        )
        return cls(
            mode=mode,
            expected=expected,
            require_request_auth=require_request_auth,
        )


def expected_identity_from_headers(headers: dict[bytes, bytes]) -> ExpectedIdentity:
    """Extract expected identity headers from ASGI headers."""
    return ExpectedIdentity(
        account_id=_get_header_str(headers, b"x-atlassian-expected-account-id"),
        email=_get_header_str(headers, b"x-atlassian-expected-email"),
        username=_get_header_str(headers, b"x-atlassian-expected-username"),
        display_name=_get_header_str(headers, b"x-atlassian-expected-display-name"),
        user=_get_header_str(headers, b"x-atlassian-expected-user"),
    )


def actual_identity_from_user_data(user_data: Any) -> ActualIdentity:
    """Normalize Jira/Confluence current-user data into comparable fields."""
    if isinstance(user_data, str):
        return ActualIdentity(account_id=user_data)

    if not isinstance(user_data, dict):
        return ActualIdentity()

    account_id = _first_str(user_data, "accountId", "account_id")
    username = _first_str(user_data, "name", "key", "username", "userName")

    if not account_id:
        account_id = _first_str(user_data, "key", "name")

    return ActualIdentity(
        account_id=account_id,
        email=_first_str(user_data, "emailAddress", "email"),
        username=username,
        display_name=_first_str(user_data, "displayName", "display_name"),
    )


def verify_expected_identity(
    actual: ActualIdentity,
    expected_values: list[ExpectedIdentity],
) -> tuple[bool, str | None]:
    """Verify that actual identity satisfies every configured expected identity."""
    for expected in expected_values:
        if not expected.is_configured:
            continue

        checks: list[tuple[str, str | None, Sequence[str | None]]] = [
            ("account_id", expected.account_id, [actual.account_id]),
            ("email", expected.email, [actual.email]),
            ("username", expected.username, [actual.username]),
            ("display_name", expected.display_name, [actual.display_name]),
            ("user", expected.user, actual.candidate_values),
        ]
        for label, expected_value, actual_values in checks:
            if not expected_value:
                continue
            if not _matches_any(expected_value, actual_values):
                actual_display = actual.describe()
                return (
                    False,
                    f"expected {label}={expected_value}, "
                    f"authenticated as {actual_display}",
                )

    return True, None


def _parse_identity_guard_mode() -> IdentityGuardMode:
    raw = _get_env_str("ATLASSIAN_IDENTITY_GUARD_MODE") or _get_env_str(
        "ATLASSIAN_IDENTITY_GUARD"
    )
    if raw is None:
        return "off"

    normalized = raw.casefold()
    if normalized in {"off", "false", "0", "no"}:
        return "off"
    if normalized in {"write", "true", "1", "yes", "on"}:
        return "write"
    if normalized == "all":
        return "all"

    raise ValueError(
        "Invalid ATLASSIAN_IDENTITY_GUARD_MODE. "
        "Expected one of: off, write, all."
    )


def _get_env_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _get_header_str(headers: dict[bytes, bytes], name: bytes) -> str | None:
    value = headers.get(name)
    if value is None:
        return None
    stripped = value.decode("latin-1").strip()
    return stripped or None


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _matches_any(expected: str, actual_values: Sequence[str | None]) -> bool:
    normalized_expected = _normalize(expected)
    return any(
        _normalize(value) == normalized_expected for value in actual_values if value
    )


def _normalize(value: str) -> str:
    return value.strip().casefold()
