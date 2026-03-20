from decimal import Decimal


def to_decimal(value) -> Decimal:
    """Helper to ensure monetary precision in calculations."""
    if value is None:
        return Decimal("0.0")
    return Decimal(str(value))
