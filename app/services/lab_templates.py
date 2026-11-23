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
        if path.name.startswith(
            "_"
        ):  # _categories.json is the category map, not a template
            continue
        t = json.loads(path.read_text(encoding="utf-8"))
        t["parts"] = [PartIn(**p).model_dump(exclude={"id"}) for p in t["parts"]]
        t["hardware"] = [HardwareIn(**h).model_dump() for h in t.get("hardware", [])]
        out[t["id"]] = t
    return out


# Each template in plain words, for guessing the kind of piece from a photo
# (the workshop PC compares the photo with these sentences).
LOOKS = {
    "bed-crown-headboard": "a wooden bed with a tall carved headboard whose top is a curved crown with a shell crest",
    "bed-square-posts": "a wooden bed with a tall flat rectangular headboard with a carved border and a lattice panel, and square posts",
    "bookshelf-open": "an open wooden bookshelf with shelves",
    "chair-dining": "a wooden dining chair",
    "chest-box": "a wooden storage chest with a lid",
    "diwan-couch": "a wooden diwan couch with a back and arms",
    "door-panelled": "a plain wooden door with two panels",
    "door-traditional-frame": "a traditional carved wooden temple door with a frame",
    "dressing-table": "a wooden dressing table with drawers and a mirror",
    "jhula-swing": "a wooden swing seat hanging from chains",
    "pillar-carved": "a carved wooden pillar with a base and a capital",
    "stool-4-leg": "a small wooden stool with four legs",
    "table-dining": "a wooden table with four legs",
    "tv-unit-2-door": "a low wooden TV unit with two doors",
    "wall-panel-deity": "a framed wooden wall panel carved with a deity",
    "wardrobe-2-door": "a wooden wardrobe with two doors",
}


def choices() -> List[Dict[str, str]]:
    """What a photo can be matched to: every template and how it looks."""
    return [
        {"id": i, "label": LOOKS.get(i, t["name"])} for i, t in all_templates().items()
    ]


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


@lru_cache(maxsize=1)
def category_map() -> Dict[str, Dict[str, str]]:
    """The shop's category names -> the template that fits and the typical size to start from."""
    raw = json.loads((FOLDER / "_categories.json").read_text(encoding="utf-8"))
    return {_key(name): {"name": name, **v} for name, v in raw.items()}


def _key(name: str) -> str:
    return " ".join((name or "").lower().split())


def suggestion(category_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """The template and typical size that fit a category (matched ignoring capitals and spacing), or None."""
    hit = category_map().get(_key(category_name or ""))
    template = all_templates().get(hit["template"]) if hit else None
    if not hit or template is None:
        return None
    sized = next(
        (s for s in template.get("typical_sizes", []) if s["name"] == hit["size"]), None
    )
    return {"category": hit["name"], "template": template["id"], "size": sized}
