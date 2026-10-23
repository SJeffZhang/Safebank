"""Unpredictable prefixed runtime IDs matching the PRD replay ID alphabet."""

import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(prefix: str) -> str:
    return prefix + "_" + "".join(secrets.choice(ALPHABET) for _ in range(26))
