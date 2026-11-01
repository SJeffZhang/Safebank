"""Exact saved-phone comparison; no search, inference or authorization state."""

import re
import unicodedata


def phone_number(value: object) -> str | None:
    """A whole 11-digit phone slot, with only the existing NFKC normalization.

    Do not strip spaces, prefixes, masking or punctuation from a source span.
    Matching a phone never substitutes for caller ownership or scope checks.
    """
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value)
    return normalized if re.fullmatch(r"[0-9]{11}", normalized) else None


def phone_matches(number: str, saved_phone: object) -> bool:
    parsed = phone_number(number)
    return parsed is not None and parsed == phone_number(saved_phone)
