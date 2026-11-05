"""Checks for a carved area marked on a photo (see ScanParams.crop)."""

from typing import List, Optional, Tuple

from fastapi import HTTPException

MIN_EDGE_PX = 30  # shorter than this and there is nothing to read


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def check_crop(
    quad: List[List[float]],
    outline: Optional[List[List[float]]],
    size: Tuple[int, int],
) -> None:
    """
    The four corners must run clockwise from the top-left, form a convex shape, and
    lie inside the photo (width, height) in pixels; the outline, too, if there is one.
    """
    width, height = size
    for name, points in (("corner", quad), ("outline point", outline or [])):
        for x, y in points:
            if not (0 <= x <= width and 0 <= y <= height):
                raise HTTPException(
                    status_code=422,
                    detail=f"A {name} is outside the photo ({width} x {height} px)",
                )
    turns = [_cross(quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]) for i in range(4)]
    if not all(t > 0 for t in turns):
        raise HTTPException(
            status_code=422,
            detail="Mark the four corners clockwise from the top-left, "
            "as a four-sided shape with no crossing",
        )
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        if ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 < MIN_EDGE_PX:
            raise HTTPException(
                status_code=422,
                detail=f"The marked area is too small (under {MIN_EDGE_PX} px a side)",
            )
