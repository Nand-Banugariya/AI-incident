"""Settings validation — especially the JWT secret guard."""

import warnings

import pytest
from pydantic import ValidationError

from app.core.config import MIN_JWT_SECRET_LENGTH, Settings

STRONG = "s" * MIN_JWT_SECRET_LENGTH


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize("env", ["staging", "production"])
@pytest.mark.parametrize("secret", [None, "", "short", "changeme" + "x" * 40, "default_" + "x" * 40])
def test_weak_or_missing_secret_fails_outside_development(env, secret):
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make(APP_ENV=env, JWT_SECRET=secret)


@pytest.mark.parametrize("env", ["development", "staging", "production"])
def test_strong_secret_is_accepted_everywhere(env):
    settings = make(APP_ENV=env, JWT_SECRET=STRONG)
    assert settings.JWT_SECRET.get_secret_value() == STRONG


def test_development_without_secret_gets_ephemeral_one_and_warns():
    with pytest.warns(UserWarning, match="ephemeral"):
        first = make(APP_ENV="development", JWT_SECRET=None)
    with pytest.warns(UserWarning):
        second = make(APP_ENV="development", JWT_SECRET=None)
    a, b = first.JWT_SECRET.get_secret_value(), second.JWT_SECRET.get_secret_value()
    assert len(a) >= MIN_JWT_SECRET_LENGTH
    assert a != b


def test_strong_secret_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        make(APP_ENV="production", JWT_SECRET=STRONG)


def test_invalid_app_env_rejected():
    with pytest.raises(ValidationError):
        make(APP_ENV="prod", JWT_SECRET=STRONG)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", []),
        ("http://a.com", ["http://a.com"]),
        ("http://a.com, http://b.com", ["http://a.com", "http://b.com"]),
        ('["http://a.com","http://b.com"]', ["http://a.com", "http://b.com"]),
    ],
)
def test_list_settings_parse_csv_and_json(raw, expected):
    assert make(JWT_SECRET=STRONG, CORS_ALLOWED_ORIGINS=raw).CORS_ALLOWED_ORIGINS == expected
