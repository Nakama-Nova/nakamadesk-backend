"""
Automatic product codes: FUR-0001 for furniture, SCU-0001 for sculptures (temple carvings).

The number comes from one row per prefix that is bumped in a single statement, so two
people saving at the same moment get different numbers. A code someone typed by hand
(say FUR-0003) is skipped, never reused.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.item import Item

PREFIXES = {"furniture": "FUR", "sculpture": "SCU"}
WIDTH = 4  # FUR-0001; the number simply grows past 9999


def prefix_for(item_type: str) -> str:
    return PREFIXES.get(item_type or "furniture", "FUR")


def next_sku(db: Session, item_type: str) -> str:
    """The next free code for this kind of item. Runs in the caller's transaction, and the
    counter row stays locked until it commits, which is what keeps concurrent saves apart.
    """
    prefix = prefix_for(item_type)
    while True:
        n = db.execute(
            text(
                "INSERT INTO sku_counters (prefix, last_no) VALUES (:p, 1) "
                "ON CONFLICT (prefix) DO UPDATE SET last_no = sku_counters.last_no + 1 "
                "RETURNING last_no"
            ),
            {"p": prefix},
        ).scalar_one()
        sku = f"{prefix}-{n:0{WIDTH}d}"
        if not db.query(Item.id).filter(Item.sku == sku).first():
            return sku
