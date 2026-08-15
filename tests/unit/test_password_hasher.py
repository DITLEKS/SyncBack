"""
Проверяем, что пароль хранится только в виде bcrypt-хэша: исходный пароль нигде не
восстанавливается, хэш каждый раз разный за счёт соли, verify различает правильный
и неправильный пароль.
"""

from app.infrastructure.security.password_hasher import PasswordHasher


def test_hash_does_not_contain_plain_password():
    plain = "correct horse battery staple"
    hashed = PasswordHasher.hash(plain)
    assert hashed != plain
    assert plain not in hashed


def test_hash_is_salted_and_verify_roundtrips():
    plain = "correct horse battery staple"
    first_hash = PasswordHasher.hash(plain)
    second_hash = PasswordHasher.hash(plain)
    # Соль случайная — одинаковый пароль даёт разные хэши
    assert first_hash != second_hash
    assert PasswordHasher.verify(plain, first_hash) is True
    assert PasswordHasher.verify(plain, second_hash) is True


def test_verify_rejects_wrong_password():
    hashed = PasswordHasher.hash("real-password")
    assert PasswordHasher.verify("wrong-password", hashed) is False
