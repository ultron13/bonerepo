from plimsoll_api.security.passwords import hash_password, verify_password


def test_hash_is_argon2id_and_salted() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first.startswith("$argon2id$")
    assert first != second


def test_verification_accepts_the_right_password() -> None:
    stored = hash_password("s3cret")
    assert verify_password(stored, "s3cret") is True


def test_verification_rejects_the_wrong_password() -> None:
    stored = hash_password("s3cret")
    assert verify_password(stored, "not-it") is False


def test_verification_rejects_a_malformed_hash() -> None:
    assert verify_password("not-a-hash", "anything") is False
