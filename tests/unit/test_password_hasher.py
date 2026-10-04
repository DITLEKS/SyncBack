"""Пароль хранится только как bcrypt-хэш; verify различает пароли и не падает на мусоре."""

import pytest

from app.domain.exceptions import InvalidPasswordError
from app.domain.policies import PasswordPolicy
from app.infrastructure.security.password_hasher import PasswordHasher

# Хэш пароля "Correct-Horse-Battery-9", созданный через passlib до перехода на bcrypt напрямую.
_LEGACY_PASSLIB_HASH = "$2b$12$OIRwAEM8TF/yMBCIlEG3wuurvMPgFBASH.EOVQ35dclH2uGh55Ajq"


def test_hash_does_not_contain_plain_password():
    plain = "correct horse battery staple"
    hashed = PasswordHasher.hash(plain)
    assert hashed != plain
    assert plain not in hashed
    assert hashed.startswith("$2b$")


def test_hash_is_salted_and_verify_roundtrips():
    plain = "correct horse battery staple"
    first_hash = PasswordHasher.hash(plain)
    second_hash = PasswordHasher.hash(plain)
    assert first_hash != second_hash
    assert PasswordHasher.verify(plain, first_hash) is True
    assert PasswordHasher.verify(plain, second_hash) is True


def test_verify_rejects_wrong_password():
    hashed = PasswordHasher.hash("real-password")
    assert PasswordHasher.verify("wrong-password", hashed) is False


def test_verify_accepts_hashes_created_by_passlib():
    assert PasswordHasher.verify("Correct-Horse-Battery-9", _LEGACY_PASSLIB_HASH) is True
    assert PasswordHasher.verify("Correct-Horse-Battery-0", _LEGACY_PASSLIB_HASH) is False


@pytest.mark.parametrize("broken", ["", "not-a-hash", "$2b$12$short", "plain-text-password"])
def test_verify_returns_false_for_malformed_hash(broken: str):
    assert PasswordHasher.verify("anything", broken) is False


def test_unicode_passwords_roundtrip():
    plain = "пароль-с-кириллицей-и-эмодзи-🙂"
    assert PasswordHasher.verify(plain, PasswordHasher.hash(plain)) is True


def test_policy_rejects_passwords_over_bcrypt_byte_limit():
    policy = PasswordPolicy()
    policy.validate("a" * 72)
    with pytest.raises(InvalidPasswordError):
        policy.validate("a" * 73)
    # 37 кириллических букв — 74 байта в UTF-8 при 37 символах
    with pytest.raises(InvalidPasswordError):
        policy.validate("я" * 37)


def test_policy_length_bounds():
    policy = PasswordPolicy(min_length=8, max_length=16)
    with pytest.raises(InvalidPasswordError):
        policy.validate("short")
    with pytest.raises(InvalidPasswordError):
        policy.validate("x" * 17)
    policy.validate("exactly-8")
