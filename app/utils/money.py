from decimal import Decimal
from typing import Any


def to_decimal(value: Any) -> Decimal:
    """
    Convert a value to a Decimal for high-precision monetary calculations.

    Args:
        value (Any): The value to convert (string, int, float, or None).

    Returns:
        Decimal: The decimal representation of the value, or 0.0 if None.
    """
    if value is None:
        return Decimal("0.0")
    return Decimal(str(value))
