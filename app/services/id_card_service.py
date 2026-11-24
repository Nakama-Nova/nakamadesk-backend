"""
A staff ID card as a two-page PDF, credit-card size (85.6 x 54 mm, portrait): the front has
the letterhead name, the person's photo (the newest confirmed "photo" document, or their
initials), name, position and staff number; the back has a QR code of the staff number, the
business address and phone, and a line for the holder's signature. Everything on the card
comes from the database (letterhead, staff record, shift), nothing is typed into the code.
"""

import io
import os
from typing import Optional

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.models.hr import UserDocument
from app.models.user import User
from app.services import hr_service, letter_service
from app.services.letter_pdf_service import (
    BROWN,
    GOLD,
    LOGO_PATH,
    MUTED,
    TEXT,
    WARM,
    day,
)

W, H = 54 * mm, 85.6 * mm  # a CR80 card, portrait
MAX_PHOTO = 4 * 1024 * 1024


def _photo(db: Session, user: User, store) -> Optional[ImageReader]:
    """The person's newest photo document, if the store has one reportlab can read."""
    if store is None:
        return None
    row = (
        db.query(UserDocument)
        .filter(
            UserDocument.user_id == user.id,
            UserDocument.kind == "photo",
            UserDocument.confirmed.is_(True),
        )
        .order_by(UserDocument.uploaded_at.desc())
        .first()
    )
    if not row or row.size > MAX_PHOTO:
        return None
    try:
        return ImageReader(io.BytesIO(store.get_bytes(row.storage_key, MAX_PHOTO)))
    except Exception:  # a missing or unreadable image falls back to initials
        return None


def _initials(name: str) -> str:
    return "".join(w[0] for w in name.split()[:2]).upper() or "?"


def _fit(c, text, font, size, width, x, y, centre=True):
    while size > 5 and c.stringWidth(text, font, size) > width:
        size -= 0.5
    c.setFont(font, size)
    (c.drawCentredString if centre else c.drawString)(x, y, text)


def render(db: Session, user: User, store=None) -> bytes:
    code = hr_service.assign_staff_code(db, user)
    head = letter_service.get_profile(db)
    name = user.full_name or user.username
    position = str(user.role).replace("_", " ").title()
    photo = _photo(db, user, store)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.setTitle(f"ID card {code}")

    # ---- front
    c.setFillColor(BROWN)
    c.rect(0, H - 17 * mm, W, 17 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    _fit(c, head["name"], "Helvetica-Bold", 11, W - 8 * mm, W / 2, H - 8 * mm)
    if head.get("tagline"):
        c.setFillColor(colors.HexColor("#F0D9A0"))
        _fit(
            c, head["tagline"], "Helvetica-Oblique", 5.5, W - 8 * mm, W / 2, H - 13 * mm
        )
    cx, cy, r = W / 2, H - 31 * mm, 12 * mm
    if photo:
        c.saveState()
        p = c.beginPath()
        p.circle(cx, cy, r)
        c.clipPath(p, stroke=0)
        c.drawImage(photo, cx - r, cy - r, 2 * r, 2 * r, preserveAspectRatio=True, anchor="c", mask="auto")  # fmt: skip
        c.restoreState()
    else:
        c.setFillColor(WARM)
        c.circle(cx, cy, r, stroke=0, fill=1)
        c.setFillColor(BROWN)
        c.setFont("Helvetica-Bold", 20)
        c.drawCentredString(cx, cy - 2.5 * mm, _initials(name))
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.2)
    c.circle(cx, cy, r, stroke=1, fill=0)
    c.setFillColor(TEXT)
    _fit(c, name, "Helvetica-Bold", 11, W - 8 * mm, W / 2, H - 50 * mm)
    c.setFillColor(GOLD)
    _fit(c, position, "Helvetica-Bold", 8, W - 8 * mm, W / 2, H - 55 * mm)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7)
    c.drawCentredString(W / 2, H - 62 * mm, f"Staff no. {code}")
    if user.joining_date:
        c.drawCentredString(W / 2, H - 66 * mm, f"Joined {day(user.joining_date)}")
    c.setFillColor(BROWN)
    c.rect(0, 0, W, 4 * mm, stroke=0, fill=1)
    c.showPage()

    # ---- back
    if os.path.exists(LOGO_PATH):
        c.drawImage(LOGO_PATH, W / 2 - 7 * mm, H - 17 * mm, 14 * mm, 14 * mm, preserveAspectRatio=True, mask="auto")  # fmt: skip
    c.setFillColor(BROWN)
    _fit(c, head["name"], "Helvetica-Bold", 9, W - 8 * mm, W / 2, H - 21 * mm)
    code_qr = qr.QrCodeWidget(f"{head['name']}|{code}")
    x0, y0, x1, y1 = code_qr.getBounds()
    size = 24 * mm
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(code_qr)
    renderPDF.draw(d, c, W / 2 - size / 2, H - 50 * mm)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.5)
    y = H - 55 * mm
    for line in (head.get("address") or "").splitlines()[:3]:
        _fit(c, line.strip(), "Helvetica", 6.5, W - 8 * mm, W / 2, y)
        y -= 3 * mm
    for part in (head.get("phone"), head.get("email")):
        if part:
            _fit(c, part, "Helvetica", 6.5, W - 8 * mm, W / 2, y)
            y -= 3 * mm
    c.setFont("Helvetica-Oblique", 5.5)
    c.drawCentredString(W / 2, 11 * mm, "If found, please return to the address above.")
    c.setStrokeColor(MUTED)
    c.setLineWidth(0.4)
    c.line(8 * mm, 7.5 * mm, W - 8 * mm, 7.5 * mm)
    c.setFont("Helvetica", 5)
    c.drawCentredString(W / 2, 5 * mm, "Holder's signature")
    c.setFillColor(BROWN)
    c.rect(0, 0, W, 2 * mm, stroke=0, fill=1)
    c.showPage()
    c.save()
    return buf.getvalue()
