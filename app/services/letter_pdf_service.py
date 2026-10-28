"""
Draws an official letter (quotation, order confirmation, purchase receipt, completion
certificate) as an A4 PDF, entirely from the snapshot stored in letter.payload, so a
letter prints the same every time. Letterhead on every page, the number and a "system
generated" line in the footer, and a VOID watermark on a letter that was voided.
"""

import io
import os
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "logo.jpg"
)
LOGO_RATIO = 456 / 600  # width / height of the cropped logo

BROWN = colors.HexColor("#5A2A06")
GOLD = colors.HexColor("#BF830B")
WARM = colors.HexColor("#FBF6EC")
TEXT = colors.HexColor("#3A1B04")
MUTED = colors.HexColor("#7A5A3C")

TITLES = {
    "quotation": "QUOTATION",
    "order_confirmation": "ORDER CONFIRMATION AND ADVANCE RECEIPT",
    "purchase_receipt": "PURCHASE RECEIPT",
    "completion_certificate": "CERTIFICATE OF COMPLETION",
}


# ------------------------------------------------------------ text helpers
def safe(text, fallback="-") -> str:
    """Escape user text (reportlab reads <tags>) and keep its line breaks."""
    text = (text or "").strip()
    return escape(text).replace("\n", "<br/>") if text else fallback


def method_word(method) -> str:
    return "UPI" if method == "upi" else safe((method or "").title())


def day(iso) -> str:
    return date.fromisoformat(str(iso)[:10]).strftime("%d %b %Y") if iso else "-"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]  # fmt: skip
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]  # fmt: skip


def _below_hundred(n: int) -> str:
    return _ONES[n] if n < 20 else " ".join(w for w in (_TENS[n // 10], _ONES[n % 10]) if w)  # fmt: skip


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    words = [f"{_ONES[hundreds]} Hundred"] if hundreds else []
    if rest:
        words.append(_below_hundred(rest))
    return " ".join(words)


def _indian_words(n: int) -> str:
    """12,34,567 -> Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven."""
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1_000)
    if crore:
        parts.append(f"{_indian_words(crore)} Crore")
    if lakh:
        parts.append(f"{_below_hundred(lakh)} Lakh")
    if thousand:
        parts.append(f"{_below_hundred(thousand)} Thousand")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(amount) -> str:
    """1250.50 -> "Rupees One Thousand Two Hundred Fifty and Fifty Paise Only"."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rupees, paise = divmod(int(value * 100), 100)
    words = f"Rupees {_indian_words(rupees)}"
    if paise:
        words += f" and {_below_hundred(paise)} Paise"
    return f"{words} Only"


def money(amount) -> str:
    """Indian digit grouping: 123456.5 -> Rs. 1,23,456.50 (Helvetica has no rupee sign)."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, frac = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{'-' if value < 0 else ''}Rs. {whole}.{frac}"


# ------------------------------------------------------------------ styles
def _style(name, **kw) -> ParagraphStyle:
    base = dict(fontName="Helvetica", fontSize=10, leading=14, textColor=TEXT)
    base.update(kw)
    return ParagraphStyle(name, **base)


S = {
    "body": _style("body"),
    "small": _style("small", fontSize=8.5, leading=12, textColor=MUTED),
    "title": _style("title", fontName="Helvetica-Bold", fontSize=14, leading=18, alignment=TA_CENTER, textColor=BROWN),  # fmt: skip
    "label": _style("label", fontName="Helvetica-Bold", fontSize=9, textColor=MUTED),
    "head": _style(
        "head", fontName="Helvetica-Bold", fontSize=9, textColor=colors.white
    ),
    "head_right": _style(
        "head_right",
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.white,
        alignment=TA_RIGHT,
    ),
    "right": _style("right", alignment=TA_RIGHT),
    "small_right": _style(
        "small_right", fontSize=8.5, leading=12, textColor=MUTED, alignment=TA_RIGHT
    ),
    "bold": _style("bold", fontName="Helvetica-Bold"),
    "words": _style("words", fontName="Helvetica-Oblique"),
    "void": _style(
        "void", fontName="Helvetica-Bold", textColor=colors.HexColor("#B3261E")
    ),
    "centre": _style("centre", alignment=TA_CENTER, textColor=BROWN),
}


def P(text, style="body") -> Paragraph:
    return Paragraph(text, S[style])


def _kv(rows) -> Table:
    """Label / value pairs, two columns."""
    t = Table(
        [[P(k, "label"), P(v)] for k, v in rows],
        colWidths=[38 * mm, 128 * mm],
        hAlign="LEFT",
    )
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2)]))  # fmt: skip
    return t


def _grid(header, rows, widths, right_cols=()) -> Table:
    data = [
        [
            P(h, "head_right" if i in right_cols else "head")
            for i, h in enumerate(header)
        ]
    ] + [
        [P(c, "right" if i in right_cols else "body") for i, c in enumerate(r)]
        for r in rows
    ]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BROWN),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, WARM]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E6D9C2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def _totals(rows) -> Table:
    """Right-aligned summary lines; the last row is drawn bold."""
    last = len(rows) - 1
    t = Table(
        [
            [P(k, "bold" if i == last else "body"), P(v, "right")]
            for i, (k, v) in enumerate(rows)
        ],  # fmt: skip
        colWidths=[126 * mm, 40 * mm],
        hAlign="LEFT",
    )
    t.setStyle(TableStyle([("LINEABOVE", (0, last), (-1, last), 0.8, GOLD), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))  # fmt: skip
    return t


def _signature(left, right_name, right_title) -> Table:
    """A line to sign on. left is who signs on the left, right_* the shop's signatory."""
    line = "_" * 34
    cells = [[P(line if left else ""), P(line, "right")], [P(left, "small"), P(f"{safe(right_name, '')}<br/>{safe(right_title, '')}", "small_right")]]  # fmt: skip
    t = Table(cells, colWidths=[83 * mm, 83 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, 0), 22)]))
    return t


# ------------------------------------------------------------------ bodies
def _lines_table(p) -> list:
    rows = [
        [
            str(i),
            safe(line["description"]),
            str(line["quantity"]),
            money(line["unit_price"]),
            money(line["amount"]),
        ]  # fmt: skip
        for i, line in enumerate(p["lines"], 1)
    ]
    return [
        _grid(
            ["#", "Description", "Qty", "Rate", "Amount"],
            rows,
            [10 * mm, 86 * mm, 14 * mm, 28 * mm, 28 * mm],
            right_cols=(2, 3, 4),
        ),  # fmt: skip
        Spacer(1, 4),
    ]


def _party(p, heading="To") -> list:
    party = p["party"]
    lines = [f"<b>{safe(party.get('name'))}</b>"]
    for key in ("address", "phone"):
        if party.get(key):
            lines.append(safe(party[key]))
    return [P(heading, "label"), P("<br/>".join(lines)), Spacer(1, 8)]


def _notes(p) -> list:
    out = []
    if p.get("notes"):
        out += [Spacer(1, 6), P("Notes", "label"), P(safe(p["notes"]))]
    if p.get("terms"):
        out += [Spacer(1, 6), P("Terms", "label")]
        out += [P(f"{i}. {safe(t)}", "small") for i, t in enumerate(p["terms"], 1)]
    return out


def _quotation(letter, p) -> list:
    return [
        _kv(
            [
                ("Quotation no.", safe(letter.number)),
                ("Date", day(letter.issued_on)),
                ("Valid until", day(p.get("valid_until"))),
            ]
        ),  # fmt: skip
        Spacer(1, 8),
        *_party(p),
        P(f"<b>Subject:</b> {safe(letter.subject)}"),
        Spacer(1, 6),
        *_lines_table(p),
        _totals([("Total", money(p["total"]))]),
        Spacer(1, 4),
        P(amount_in_words(p["total"]), "words"),
        *_notes(p),
    ]


def _order_confirmation(letter, p) -> list:
    out = [
        _kv(
            [
                ("Confirmation no.", safe(letter.number)),
                ("Date", day(letter.issued_on)),
                ("Order no.", safe(p.get("order_number"))),
                ("Expected delivery", day(p.get("expected_delivery"))),
            ]
        ),  # fmt: skip
        Spacer(1, 8),
        *_party(p),
        P(f"<b>Subject:</b> {safe(letter.subject)}"),
        Spacer(1, 6),
        *_lines_table(p),
        _totals(
            [
                ("Order total", money(p["total"])),
                ("Advance received", money(p["advance"])),
                ("Balance due", money(p["balance"])),
            ]
        ),  # fmt: skip
        Spacer(1, 8),
    ]
    if p.get("payments"):
        rows = [[day(x["paid_on"]), method_word(x["method"]), safe(x.get("reference")), money(x["amount"])] for x in p["payments"]]  # fmt: skip
        out += [P("Payments received", "label"), Spacer(1, 2), _grid(["Date", "Method", "Reference", "Amount"], rows, [30 * mm, 36 * mm, 70 * mm, 30 * mm], right_cols=(3,)), Spacer(1, 8)]  # fmt: skip
    if Decimal(p["advance"]) > 0:
        out.append(P(f"Received with thanks from <b>{safe(p['party'].get('name'))}</b> an advance of {money(p['advance'])} ({amount_in_words(p['advance'])}) against this order.", "body"))  # fmt: skip
    else:
        out.append(P("No advance has been received against this order yet."))
    out += _notes(p)
    return out


def _purchase_receipt(letter, p) -> list:
    pay = p["payment"]
    rows = [[safe(p["description"]), str(p["quantity"]), money(p["amount"])]]
    out = [
        _kv(
            [
                ("Receipt no.", safe(letter.number)),
                ("Date", day(letter.issued_on)),
                ("Piece code", safe(p.get("item_sku"))),
            ]
        ),  # fmt: skip
        Spacer(1, 8),
        *_party(p, "Bought from"),
        _grid(
            ["Description", "Qty", "Amount"],
            rows,
            [110 * mm, 16 * mm, 40 * mm],
            right_cols=(1, 2),
        ),  # fmt: skip
        Spacer(1, 4),
        _totals([("Amount paid", money(p["amount"]))]),
        Spacer(1, 4),
        P(amount_in_words(p["amount"]), "words"),
        Spacer(1, 6),
        _kv(
            [
                ("Paid on", day(pay["paid_on"])),
                ("Method", method_word(pay["method"])),
                ("Reference", safe(pay.get("reference"))),
            ]
        ),  # fmt: skip
        *_notes(p),
        Spacer(1, 10),
        KeepTogether(
            [
                P(f"I, <b>{safe(p['party'].get('name'))}</b>, confirm that I have sold the piece described above to {safe(p['letterhead']['name'])} and received the amount in full."),  # fmt: skip
                _signature("Seller's signature", p["letterhead"].get("signatory_name"), p["letterhead"].get("signatory_title")),  # fmt: skip
            ]
        ),
    ]
    return out


def _completion_certificate(letter, p) -> list:
    r = p["restoration"]
    place = r.get("location")
    head = p["letterhead"]["name"]
    return [
        _kv([("Certificate no.", safe(letter.number)), ("Date", day(letter.issued_on))]),
        Spacer(1, 10),
        P(
            f"This is to certify that the restoration of <b>{safe(r['piece'], safe(r['title']))}</b> "
            f"belonging to <b>{safe(p['party'].get('name'))}</b>"
            f"{' (' + safe(place) + ')' if place else ''} has been completed by {safe(head)}.",
        ),
        Spacer(1, 10),
        _kv(
            [
                ("Project", safe(r["title"])),
                ("Condition found", safe(r.get("condition_notes"))),
                ("Work done", safe(p.get("work_done"))),
                ("Work began", day(r.get("started_on"))),
                ("Completed on", day(p.get("completed_on"))),
                ("Lead artisan", safe(r.get("lead_artisan"))),
            ]
        ),
        *_notes(p),
        Spacer(1, 14),
        KeepTogether([_signature("", p["letterhead"].get("signatory_name"), p["letterhead"].get("signatory_title"))]),  # fmt: skip
    ]


BODIES = {
    "quotation": _quotation,
    "order_confirmation": _order_confirmation,
    "purchase_receipt": _purchase_receipt,
    "completion_certificate": _completion_certificate,
}


# -------------------------------------------------------------------- page
def _page(letter):
    head = letter.payload["letterhead"]
    voided = letter.status == "void"

    def draw(canvas, doc):
        width, height = A4
        canvas.saveState()
        top = height - 12 * mm
        logo_h = 26 * mm
        text_x = 18 * mm
        if os.path.exists(LOGO_PATH):
            canvas.drawImage(LOGO_PATH, 18 * mm, top - logo_h, width=logo_h * LOGO_RATIO, height=logo_h, mask="auto")  # fmt: skip
            text_x += logo_h * LOGO_RATIO + 6 * mm
        y = top - 7 * mm
        canvas.setFillColor(BROWN)
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(text_x, y, head["name"])
        canvas.setFont("Helvetica-Oblique", 9)
        canvas.setFillColor(GOLD)
        if head.get("tagline"):
            y -= 5.5 * mm
            canvas.drawString(text_x, y, head["tagline"])
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8.5)
        contact = [head.get("address"), "   ".join(x for x in (head.get("phone"), head.get("email")) if x)]  # fmt: skip
        if head.get("gstin"):
            contact.append(f"GSTIN: {head['gstin']}")
        for line in contact:
            for part in (line or "").splitlines():
                if part.strip():
                    y -= 4.2 * mm
                    canvas.drawString(text_x, y, part.strip())
        canvas.setStrokeColor(GOLD)
        canvas.setLineWidth(1.2)
        canvas.line(18 * mm, height - 42 * mm, width - 18 * mm, height - 42 * mm)

        canvas.setStrokeColor(colors.HexColor("#E6D9C2"))
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, 18 * mm, width - 18 * mm, 18 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7.5)
        note = head.get("footer_note") or ""
        canvas.drawString(18 * mm, 13.5 * mm, note[:120])
        canvas.drawString(18 * mm, 9.5 * mm, "System generated document, no signature required unless a signature line is shown.")  # fmt: skip
        canvas.drawRightString(width - 18 * mm, 13.5 * mm, letter.number)
        canvas.drawRightString(width - 18 * mm, 9.5 * mm, f"Page {doc.page}")
        if voided:
            canvas.translate(width / 2, height / 2)
            canvas.rotate(40)
            canvas.setFillColor(colors.Color(0.7, 0.15, 0.12, alpha=0.18))
            canvas.setFont("Helvetica-Bold", 120)
            canvas.drawCentredString(0, 0, "VOID")
        canvas.restoreState()

    return draw


def render(letter) -> bytes:
    """The PDF of a letter, from its stored snapshot."""
    p = letter.payload
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=48 * mm,
        bottomMargin=24 * mm,
        title=f"{letter.number} {letter.subject}",
        author=p["letterhead"]["name"],
    )
    story = [P(TITLES[letter.kind], "title"), Spacer(1, 10)]
    if letter.status == "void":
        reason = safe(letter.void_reason, "no reason given")
        story += [P(f"VOID: this letter was cancelled. Reason: {reason}", "void"), Spacer(1, 8)]  # fmt: skip
    story += BODIES[letter.kind](letter, p)
    page = _page(letter)
    doc.build(story, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
