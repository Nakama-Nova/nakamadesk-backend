"""Where the joints sit: found from the solved parts, never typed in.

Two parts touch where their boxes meet face to face (or one is sunk into the other, like a tenon).
That contact is where the joint is. A contact is only a joint when one of the two parts says what
joint it has; the declaring part is the smaller one (the tenon, the slat), the bigger one takes it.
"""

from typing import Any, Dict, List, Optional

TOUCH = 1.0  # mm: parts closer than this count as touching
# mm between fasteners along the joint. A guess for drawing only; the shop's own drawing decides.
SPACING = {"dowel": 150.0, "pocket_screw": 150.0, "biscuit": 100.0}
AXES = ("x", "y", "z")


def _box(p: Dict[str, Any]):
    lo = [p["pos"][a] for a in AXES]
    return lo, [lo[i] + p["size"][a] for i, a in enumerate(AXES)]


def _volume(p: Dict[str, Any]) -> float:
    return p["size"]["x"] * p["size"]["y"] * p["size"]["z"]


def _contact(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The touching region of two parts as {lo, hi}, or None if they only meet at an edge or not at all."""
    (alo, ahi), (blo, bhi) = _box(a), _box(b)
    lo, hi, shared = [], [], 0
    for i in range(3):
        o = min(ahi[i], bhi[i]) - max(alo[i], blo[i])
        if o < -TOUCH:
            return None
        if o > TOUCH:
            shared += 1
            lo.append(max(alo[i], blo[i]))
            hi.append(min(ahi[i], bhi[i]))
        else:  # they just meet along this axis: the contact is the plane between them
            mid = (min(ahi[i], bhi[i]) + max(alo[i], blo[i])) / 2
            lo.append(mid)
            hi.append(mid)
    return {"lo": lo, "hi": hi} if shared >= 2 else None


def _points(kind: str, lo: List[float], hi: List[float]) -> List[Dict[str, float]]:
    """Fastener positions: one per SPACING along the longest side of the contact, else the middle."""
    mid = [(lo[i] + hi[i]) / 2 for i in range(3)]
    n, along = 1, 0
    if kind in SPACING:
        along = max(range(3), key=lambda i: hi[i] - lo[i])
        n = max(1, int((hi[along] - lo[along]) // SPACING[kind]))
    out = []
    for k in range(n):
        p = list(mid)
        p[along] = lo[along] + (hi[along] - lo[along]) * (k + 0.5) / n
        out.append({a: round(p[i], 1) for i, a in enumerate(AXES)})
    return out


def find_joints(
    parts: List[Dict[str, Any]], hardware: List[Dict[str, Any]] = ()
) -> List[Dict[str, Any]]:
    """One entry per joint: parts a and b (indexes into `parts`), kind, note, the middle of the
    contact, its size, the build step (when both parts are in), the fastener points, and the
    hardware lines tied to either part."""
    joints = []
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            owner = min(
                (p for p in (a, b) if p["joint_type"]), key=_volume, default=None
            )
            if owner is None:
                continue
            c = _contact(a, b)
            if c is None:
                continue
            lo, hi = c["lo"], c["hi"]
            ids = {a["id"], b["id"]} - {None}
            joints.append(
                {
                    "a": i,
                    "b": j,
                    "kind": owner["joint_type"],
                    "note": owner["note"],
                    "pos": {
                        k: round((lo[n] + hi[n]) / 2, 1) for n, k in enumerate(AXES)
                    },
                    "size": {k: round(hi[n] - lo[n], 1) for n, k in enumerate(AXES)},
                    "step": max(a["step"], b["step"]),
                    "points": _points(owner["joint_type"], lo, hi),
                    "hardware": [
                        h["name"]
                        for h in hardware
                        if h["part_id"] in ids and h["qty"] > 0
                    ],
                }
            )
    return joints
