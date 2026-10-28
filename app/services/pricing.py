"""
The one place that turns what a piece cost into what it sells for.

    cost          = buying price + making cost
    selling price = cost + profit %          (stored on the item, WITHOUT GST)
    price with GST = selling price + GST %   (what a customer pays; billing adds the same GST)

The stored `selling_price` stays the price before GST, because bills add GST on top and
profit counts sales without GST. Everything that shows a customer price uses with_gst().
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

CENT = Decimal("0.01")


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def cost_of(purchase_price, production_cost) -> Decimal:
    return _round(_d(purchase_price) + _d(production_cost))


def price_from_cost(cost, profit_percent) -> Decimal:
    """Selling price before GST: the cost plus the profit percentage."""
    return _round(_d(cost) * (1 + _d(profit_percent) / 100))


def with_gst(net_price, gst_percent) -> Decimal:
    """What the customer pays for a price that does not yet include GST."""
    return _round(_d(net_price) * (1 + _d(gst_percent) / 100))


def net_from_gross(gross_price, gst_percent) -> Decimal:
    """The price before GST, from one that includes it."""
    return _round(_d(gross_price) / (1 + _d(gst_percent) / 100))


def profit_percent_of(cost, net_price) -> Optional[Decimal]:
    """The margin a typed price leaves over cost (None when there is no cost to compare)."""
    cost = _d(cost)
    if cost <= 0:
        return None
    return _round((_d(net_price) / cost - 1) * 100)


def reprice(item) -> None:
    """Set an item's selling price from its cost when it is priced by profit %."""
    if item.profit_percent is not None:
        item.selling_price = price_from_cost(
            cost_of(item.purchase_price, item.production_cost), item.profit_percent
        )
