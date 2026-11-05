"""
Built-in starting points for a furniture piece's parts (app/lab_templates/*.json).

A template is a list of parts (and the usual hardware) written as formulas of the
overall size L, W, H, so one template fits every size of that kind of piece. Applying
one gives a piece its own copy to edit; nothing links back to the template.
"""

import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.models.furniture_hardware import FurnitureHardware
from app.models.furniture_part import FurniturePart
from app.schemas.lab import HardwareIn, PartIn

FOLDER = Path(__file__).resolve().parent.parent / "lab_templates"


@lru_cache(maxsize=1)
def all_templates() -> Dict[str, Dict[str, Any]]:
    """Every template, checked against the same schemas the editor's saves use."""
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(FOLDER.glob("*.json")):
        t = json.loads(path.read_text(encoding="utf-8"))
        t["parts"] = [PartIn(**p).model_dump(exclude={"id"}) for p in t["parts"]]
        t["hardware"] = [HardwareIn(**h).model_dump() for h in t.get("hardware", [])]
        out[t["id"]] = t
    return out


def summary(t: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": t["id"],
        "name": t["name"],
        "description": t["description"],
        "size_hint": t["size_hint"],
        "typical_sizes": t.get("typical_sizes", []),
        "carve": t.get("carve", []),
        "parts": len(t["parts"]),
        "hardware": len(t["hardware"]),
    }


def build(
    t: Dict[str, Any],
    item_id: uuid.UUID,
    wood_type: Optional[str],
    with_hardware: bool,
) -> Tuple[List[FurniturePart], List[FurnitureHardware]]:
    """New (unsaved) rows for this piece. `wood_type` goes on every part when given."""
    parts = [
        FurniturePart(
            id=uuid.uuid4(),
            item_id=item_id,
            sort=n,
            **{**p, "wood_type": wood_type or p.get("wood_type")},
        )
        for n, p in enumerate(t["parts"])
    ]
    hardware = (
        [
            FurnitureHardware(id=uuid.uuid4(), item_id=item_id, sort=n, **h)
            for n, h in enumerate(t["hardware"])
        ]
        if with_hardware
        else []
    )
    return parts, hardware
