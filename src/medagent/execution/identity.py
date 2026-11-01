"""Lossless directory identifiers across the Python / JavaScript boundary."""

MAX_ID = (1 << 128) - 1
MAX_SAFE_NUMBER = (1 << 53) - 1


def encode_identity(identity):
    if (
        not isinstance(identity, (tuple, list))
        or len(identity) != 2
        or any(type(value) is not int or not 0 <= value <= MAX_ID for value in identity)
    ):
        raise ValueError("Invalid workspace identity")
    return [str(value) for value in identity]


def decode_identity(identity):
    if not isinstance(identity, (tuple, list)) or len(identity) != 2:
        raise ValueError("Invalid workspace identity")
    if all(type(value) is int and 0 <= value <= MAX_SAFE_NUMBER for value in identity):
        return tuple(identity)
    if all(
        isinstance(value, str)
        and 1 <= len(value) <= 39
        and value.isascii()
        and value.isdigit()
        and (value == "0" or not value.startswith("0"))
        for value in identity
    ):
        values = tuple(int(value) for value in identity)
        if all(value <= MAX_ID for value in values):
            return values
    # Unsafe JSON numbers may already have been rounded by JavaScript. Never
    # adopt them or loosen the subsequent directory identity comparison.
    raise ValueError("Invalid workspace identity")
