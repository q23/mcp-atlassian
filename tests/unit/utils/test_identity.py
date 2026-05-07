"""Tests for Atlassian identity guard helpers."""

import pytest

from mcp_atlassian.utils.identity import (
    ExpectedIdentity,
    IdentityGuardConfig,
    actual_identity_from_user_data,
    expected_identity_from_headers,
    verify_expected_identity,
)


def test_identity_guard_config_from_env_explicit_user(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_IDENTITY_GUARD_MODE", "write")
    monkeypatch.setenv("ATLASSIAN_IDENTITY_GUARD_USER", "andrej@example.com")

    config = IdentityGuardConfig.from_env()

    assert config.mode == "write"
    assert config.expected.user == "andrej@example.com"
    assert config.enforce_writes is True


def test_identity_guard_boolean_env_enables_write_mode(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_IDENTITY_GUARD", "true")

    config = IdentityGuardConfig.from_env()

    assert config.mode == "write"


def test_identity_guard_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_IDENTITY_GUARD_MODE", "invalid")

    with pytest.raises(ValueError, match="Invalid ATLASSIAN_IDENTITY_GUARD_MODE"):
        IdentityGuardConfig.from_env()


def test_expected_identity_from_headers():
    expected = expected_identity_from_headers(
        {
            b"x-atlassian-expected-user": b"Andrej Daiker",
            b"x-atlassian-expected-account-id": b"abc-123",
            b"x-atlassian-expected-email": b"andrej@example.com",
        }
    )

    assert expected.user == "Andrej Daiker"
    assert expected.account_id == "abc-123"
    assert expected.email == "andrej@example.com"


def test_verify_expected_user_matches_any_actual_identity_field():
    actual = actual_identity_from_user_data(
        {
            "accountId": "abc-123",
            "emailAddress": "andrej@example.com",
            "displayName": "Andrej Daiker",
        }
    )

    is_valid, reason = verify_expected_identity(
        actual, [ExpectedIdentity(user="andrej@example.com")]
    )

    assert is_valid is True
    assert reason is None


def test_verify_expected_identity_mismatch_reports_reason():
    actual = actual_identity_from_user_data(
        {"accountId": "abc-123", "displayName": "Wrong User"}
    )

    is_valid, reason = verify_expected_identity(
        actual, [ExpectedIdentity(account_id="expected-account")]
    )

    assert is_valid is False
    assert reason is not None
    assert "expected account_id=expected-account" in reason
