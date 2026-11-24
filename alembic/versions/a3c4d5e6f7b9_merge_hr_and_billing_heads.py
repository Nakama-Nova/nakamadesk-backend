"""Join the HR and billing migration chains

Revision ID: a3c4d5e6f7b9
Revises: f2b3c4d5e6a8, e2b3c4d5f6a7
Create Date: 2026-11-24 20:00:00.000000

Both the HR upgrade and billing B3/B4 started from d1e2f3a4b5c6, so main had two heads
once both were merged. This changes no table; it only makes one head again.
"""

from typing import Sequence, Union

revision: str = "a3c4d5e6f7b9"
down_revision: Union[str, Sequence[str], None] = ("f2b3c4d5e6a8", "e2b3c4d5f6a7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
