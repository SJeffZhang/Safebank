from services.gateway.app.auth.pin import HASHER, verify_pin


def test_argon2_pin_and_unknown_customer():
    encoded = HASHER.hash("123456")
    assert verify_pin("123456", encoded)
    assert not verify_pin("123455", encoded)
    assert not verify_pin("123456", None)
    assert not verify_pin("123456", "invalid")
