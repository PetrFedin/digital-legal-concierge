from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_OPERATOR,
    ROLE_SUPERADMIN,
    has_role,
    hash_password,
    normalize_roles,
    serialize_roles,
    verify_password,
)


def test_superadmin_implies_admin():
    roles = normalize_roles([ROLE_SUPERADMIN, ROLE_LAWYER])
    assert ROLE_SUPERADMIN in roles
    assert ROLE_ADMIN in roles
    assert ROLE_LAWYER in roles
    assert has_role(roles, ROLE_ADMIN)


def test_multiple_roles_are_serialized_without_duplicates():
    value = serialize_roles([ROLE_LAWYER, ROLE_OPERATOR, ROLE_LAWYER])
    assert value == "lawyer,operator"


def test_password_is_hashed_and_verified():
    encoded = hash_password("StrongPass123!")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("StrongPass123!", encoded)
    assert not verify_password("WrongPass123!", encoded)
