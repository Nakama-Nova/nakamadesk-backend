"""
3D Lab: solve a furniture piece's parts for a size, and build the cutting list.

Read-only maths. The formulas live in the database (furniture_parts) and are
solved HERE, on the server, with app/services/formula.py, so the viewer and the
printed cutting list can never disagree. Nothing computed here is saved.
"""

import math
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from app.models.design import Design
from app.models.furniture_carving import FurnitureCarving
from app.models.furniture_hardware import FurnitureHardware
from app.models.furniture_part import FurniturePart
from app.models.item import Item
from app.schemas.lab import HARDWARE_KINDS
from app.services import formula

SIZE_LABELS = {"S": "Small", "M": "Medium", "L": "Large"}
CUSTOM_MIN, CUSTOM_MAX = 50.0, 5000.0  # mm, for each of L W H the achari may type
MAX_PARTS = 150
MAX_HARDWARE = 100
MAX_CARVINGS = 60
# which two box extents (x left-right, y up, z front-back) a face spans: across, then up
FACE_SPANS = {
    "left": ("z", "y"),
    "right": ("z", "y"),
    "front": ("x", "y"),
    "back": ("x", "y"),
    "top": ("x", "z"),
    "bottom": ("x", "z"),
}


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _triple(d: Any) -> Optional[Dict[str, float]]:
    if not isinstance(d, dict):
        return None
    l_, w_, h_ = _num(d.get("l")), _num(d.get("w")), _num(d.get("h"))
    return {"l": l_, "w": w_, "h": h_} if l_ and w_ and h_ else None


def default_size(item: Item) -> Optional[Dict[str, float]]:
    return _triple(item.dimensions_mm)


def presets(item: Item) -> Dict[str, Dict[str, float]]:
    """Valid S/M/L presets only, in S, M, L order."""
    raw = item.size_presets or {}
    out = {}
    for key in ("S", "M", "L"):
        t = _triple(raw.get(key))
        if t:
            out[key] = t
    return out


def resolve_size(
    item: Item,
    size: Optional[str] = None,
    l: Optional[float] = None,  # noqa: E741
    w: Optional[float] = None,
    h: Optional[float] = None,
) -> Tuple[Dict[str, float], str]:
    """The overall size to solve for, and a label for it."""
    if any(v is not None for v in (l, w, h)):
        if None in (l, w, h):
            raise HTTPException(status_code=422, detail="Give all of l, w and h")
        for name, v in (("l", l), ("w", w), ("h", h)):
            if not CUSTOM_MIN <= v <= CUSTOM_MAX:
                raise HTTPException(
                    status_code=422,
                    detail=f"{name} must be between {CUSTOM_MIN:g} and {CUSTOM_MAX:g} mm",
                )
        return {"l": l, "w": w, "h": h}, "Custom"
    if size:
        key = size.upper()
        found = presets(item).get(key)
        if not found:
            raise HTTPException(
                status_code=404, detail=f"No '{size}' size set for this piece"
            )
        return found, SIZE_LABELS[key]
    base = default_size(item)
    if not base:
        raise HTTPException(
            status_code=422, detail="Set the piece's overall size (l, w, h) first"
        )
    return base, "Standard"


def _axes(axis: str, length: float, width: float, thick: float) -> Dict[str, float]:
    """Box extents (x=left-right, y=up, z=front-back) for a piece whose length runs along `axis`."""
    if axis == "y":
        return {"x": width, "y": length, "z": thick}
    if axis == "z":
        return {"x": width, "y": thick, "z": length}
    return {"x": length, "y": thick, "z": width}


def solve_part(part: FurniturePart, size: Dict[str, float]) -> Dict[str, Any]:
    """Resolve one part's formulas. Raises FormulaError (with the part name) if unusable."""
    L, W, H = size["l"], size["w"], size["h"]

    def ev(expr: str, field: str, positive: bool) -> float:
        try:
            v = formula.evaluate(expr, L, W, H)
        except formula.FormulaError as e:
            raise formula.FormulaError(
                f"{part.name}: {field} formula '{expr}' {e}"
            ) from None
        if positive and v <= 0:
            raise formula.FormulaError(
                f"{part.name}: {field} formula '{expr}' gives {v:g} mm, it must be above 0"
            )
        return round(v, 2)

    length = ev(part.l_expr, "length", True)
    width = ev(part.w_expr, "width", True)
    thick = ev(part.t_expr, "thickness", True)
    axis = part.axis if part.axis in ("x", "y", "z") else "x"
    return {
        "id": str(part.id) if part.id else None,
        "name": part.name,
        "wood": part.wood_type,
        "l": length,
        "w": width,
        "t": thick,
        "axis": axis,
        "size": _axes(axis, length, width, thick),
        "pos": {
            "x": ev(part.x_expr, "x position", False),
            "y": ev(part.y_expr, "y position", False),
            "z": ev(part.z_expr, "z position", False),
        },
        "step": part.assembly_step,
        "joint_type": part.joint_type,
        "note": part.joinery_note,
        "has_model": part.model_asset_id is not None,
    }


def cutting_list(parts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Group identical pieces: wood, name and finished L x W x T. Volume is net (no waste)."""
    groups: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
    for p in parts:
        key = (
            p["wood"] or "Unspecified",
            p["name"],
            round(p["l"], 1),
            round(p["w"], 1),
            round(p["t"], 1),
        )
        g = groups.setdefault(
            key,
            {
                "name": p["name"],
                "qty": 0,
                "l": key[2],
                "w": key[3],
                "t": key[4],
                "volume_m3": 0.0,
            },
        )
        g["qty"] += 1
        g["volume_m3"] += p["l"] * p["w"] * p["t"] / 1e9
    by_wood: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for (wood, *_), g in groups.items():
        g["volume_m3"] = round(g["volume_m3"], 6)
        entry = by_wood.setdefault(
            wood, {"wood": wood, "items": [], "total_m3": 0.0, "pieces": 0}
        )
        entry["items"].append(g)
        entry["total_m3"] = round(entry["total_m3"] + g["volume_m3"], 6)
        entry["pieces"] += g["qty"]
    woods = list(by_wood.values())
    return {
        "by_wood": woods,
        "pieces": sum(w["pieces"] for w in woods),
        "total_m3": round(sum(w["total_m3"] for w in woods), 6),
    }


def solve_hardware_line(
    line: FurnitureHardware, size: Dict[str, float], steps_by_part: Dict[Any, int]
) -> Dict[str, Any]:
    """One hardware line for a size: whole-number quantity (rounded up). Raises FormulaError."""
    try:
        v = formula.evaluate(line.qty_expr, size["l"], size["w"], size["h"])
    except formula.FormulaError as e:
        raise formula.FormulaError(
            f"{line.name}: quantity formula '{line.qty_expr}' {e}"
        ) from None
    if v < 0:
        raise formula.FormulaError(
            f"{line.name}: quantity formula '{line.qty_expr}' gives {v:g}, it must be 0 or more"
        )
    step = line.assembly_step or steps_by_part.get(line.part_id)
    return {
        "id": str(line.id) if line.id else None,
        "kind": line.kind,
        "name": line.name,
        "qty": math.ceil(round(v, 6)),  # round() first: 4.0000000001 is 4, not 5
        "step": step,
        "part_id": str(line.part_id) if line.part_id else None,
        "note": line.note,
    }


def hardware_lists(lines: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Split solved lines: stock hardware grouped by kind, and clamps ("tools",
    not stock) grouped by build step. Lines needing none (qty 0) are left out.
    """
    stock: Dict[str, List[Dict[str, Any]]] = {k: [] for k in HARDWARE_KINDS}
    by_step: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for ln in lines:
        if ln["qty"] <= 0:
            continue
        if ln["kind"] == "clamp":
            by_step.setdefault(ln["step"], []).append(ln)
        else:
            stock[ln["kind"]].append(ln)
    return {
        "hardware": [{"kind": k, "items": v} for k, v in stock.items() if v],
        # build order; clamps with no step come last
        "tools": [
            {"step": s, "items": by_step[s]}
            for s in sorted(by_step, key=lambda s: (s is None, s or 0))
        ],
    }


def carving_area(size: Dict[str, float], face: str, margin: int) -> Dict[str, float]:
    """Room for the picture on a face: the face less the margin on every side (mm)."""
    across, up = FACE_SPANS[face]
    return {
        "w": round(size[across] - 2 * margin, 2),
        "h": round(size[up] - 2 * margin, 2),
    }


def solve_carvings(
    carvings: Iterable[FurnitureCarving],
    solved_parts: List[Dict[str, Any]],
    designs: Dict[Any, Design],
) -> Dict[str, Any]:
    """
    Placements for the viewer, plus each design ONCE with how many places it is
    carved. A carving whose part was removed is left out (it stays in the editor).
    """
    parts = {p["id"]: p for p in solved_parts}
    out: List[Dict[str, Any]] = []
    used: "OrderedDict[Any, Dict[str, Any]]" = OrderedDict()
    for c in carvings:
        part = parts.get(str(c.part_id)) if c.part_id else None
        design = designs.get(c.design_id)
        if not part or not design:
            continue
        url = f"/lab/designs/{design.id}/image"
        out.append(
            {
                "id": str(c.id) if c.id else None,
                "part_id": part["id"],
                "part": part["name"],
                "design_id": str(design.id),
                "sku": design.sku,
                "name": design.name,
                "face": c.face,
                "margin_mm": c.margin_mm,
                "mirror": bool(c.mirror),
                "note": c.note,
                "area_mm": carving_area(part["size"], c.face, c.margin_mm),
                "image_url": url,
            }
        )
        entry = used.setdefault(
            design.id,
            {
                "id": str(design.id),
                "sku": design.sku,
                "name": design.name,
                "placements": 0,
                "image_url": url,
            },
        )
        entry["placements"] += 1
    return {"carvings": out, "designs": list(used.values())}


def solve(
    item: Item,
    parts: List[FurniturePart],
    size: Optional[str] = None,
    l: Optional[float] = None,  # noqa: E741
    w: Optional[float] = None,
    h: Optional[float] = None,
    hardware: Optional[List[FurnitureHardware]] = None,
    carvings: Optional[List[FurnitureCarving]] = None,
    designs: Optional[Dict[Any, Design]] = None,
) -> Dict[str, Any]:
    dims, label = resolve_size(item, size, l, w, h)
    steps = {p.id: p.assembly_step for p in parts}
    try:
        solved = [solve_part(p, dims) for p in parts]
        lines = [solve_hardware_line(hw, dims, steps) for hw in hardware or []]
    except formula.FormulaError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {
        "item": {"id": str(item.id), "name": item.name},
        "size": {"label": label, **dims},
        "presets": presets(item),
        "parts": solved,
        "cutting_list": cutting_list(solved),
        "steps": max((p["step"] for p in solved), default=0),
        **hardware_lists(lines),
        **solve_carvings(carvings or [], solved, designs or {}),
    }


def _sizes(item: Item) -> List[Tuple[str, Dict[str, float]]]:
    """The standard size and every preset, with a label for error messages."""
    sizes: List[Tuple[str, Dict[str, float]]] = []
    base = default_size(item)
    if base:
        sizes.append(("the standard size", base))
    return sizes + [(f"size {k}", v) for k, v in presets(item).items()]


def check_definition(item: Item, parts: List[FurniturePart]) -> None:
    """
    Before saving parts or presets: every formula must be valid and give a
    usable size for the default size AND every preset. Fails with the part,
    the field and the size, so the owner fixes it now rather than the achari
    finding it at the workbench.
    """
    if len(parts) > MAX_PARTS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_PARTS} parts")
    sizes = _sizes(item)
    if parts and not sizes:
        raise HTTPException(
            status_code=422,
            detail="Set the piece's overall size (l, w, h) before adding parts",
        )
    for part in parts:
        for expr, field in (
            (part.l_expr, "length"),
            (part.w_expr, "width"),
            (part.t_expr, "thickness"),
            (part.x_expr, "x position"),
            (part.y_expr, "y position"),
            (part.z_expr, "z position"),
        ):
            try:
                formula.validate(expr)
            except formula.FormulaError as e:
                raise HTTPException(
                    status_code=422, detail=f"{part.name}: {field} formula '{expr}' {e}"
                ) from None
        if part.axis not in ("x", "y", "z"):
            raise HTTPException(
                status_code=422, detail=f"{part.name}: axis must be x, y or z"
            )
        for label, dims in sizes:
            try:
                solve_part(part, dims)
            except formula.FormulaError as e:
                raise HTTPException(
                    status_code=422, detail=f"For {label}: {e}"
                ) from None


def check_hardware(
    item: Item, hardware: List[FurnitureHardware], parts: List[FurniturePart]
) -> None:
    """
    Before saving hardware (or changing sizes): every quantity formula must be
    valid and give 0 or more for the standard size AND every preset. Fails with
    the item, the line number and the size.
    """
    if len(hardware) > MAX_HARDWARE:
        raise HTTPException(
            status_code=422, detail=f"At most {MAX_HARDWARE} hardware lines"
        )
    sizes = _sizes(item)
    if hardware and not sizes:
        raise HTTPException(
            status_code=422,
            detail="Set the piece's overall size (l, w, h) before adding hardware",
        )
    steps = {p.id: p.assembly_step for p in parts}
    for n, line in enumerate(hardware, 1):
        try:
            formula.validate(line.qty_expr)
        except formula.FormulaError as e:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name}: hardware line {n} ({line.name}): "
                f"quantity formula '{line.qty_expr}' {e}",
            ) from None
        for label, dims in sizes:
            try:
                solve_hardware_line(line, dims, steps)
            except formula.FormulaError as e:
                raise HTTPException(
                    status_code=422,
                    detail=f"{item.name}: hardware line {n}, for {label}: {e}",
                ) from None


def check_carvings(
    item: Item,
    carvings: List[FurnitureCarving],
    parts: List[FurniturePart],
    designs: Dict[Any, Design],
) -> None:
    """
    Before saving carvings (or changing sizes): the margin must leave room for
    the picture on its face at the standard size AND every preset.
    """
    if len(carvings) > MAX_CARVINGS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_CARVINGS} carvings")
    sizes = _sizes(item)
    by_id = {p.id: p for p in parts}
    for n, c in enumerate(carvings, 1):
        part = by_id.get(c.part_id)
        if part is None:
            continue
        design = designs.get(c.design_id)
        what = f"{design.sku if design else 'design'} on {part.name}"
        for label, dims in sizes:
            try:
                size = solve_part(part, dims)["size"]
            except formula.FormulaError as e:
                raise HTTPException(
                    status_code=422, detail=f"For {label}: {e}"
                ) from None
            area = carving_area(size, c.face, c.margin_mm)
            if area["w"] <= 0 or area["h"] <= 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"{item.name}: carving line {n} ({what}), for {label}: "
                    f"a {c.margin_mm} mm margin leaves no room on the {c.face} face "
                    f"({area['w']:g} x {area['h']:g} mm)",
                )
