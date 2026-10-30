import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import object_session

from app.models.order import Order
from app.models.sale import Sale


def render_invoice_pdf(sale: Sale) -> bytes:
    """
    Draw the PDF bill for a completed sale and return it.

    Drawn in memory and never written to disk, so it works wherever the server runs
    (a serverless disk is read-only) and two people printing at once cannot clash.

    Args:
        sale (Sale): The sale model instance containing transaction data and items.

    Returns:
        bytes: The PDF document.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # --- Header ---
    title_style = ParagraphStyle(
        "MainTitle",
        parent=styles["Heading1"],
        alignment=1,
        spaceAfter=20,  # Center
    )
    elements.append(Paragraph("Vriksha Furnitures", title_style))

    # --- Invoice Info ---
    # A shop that is not GST-registered charges no tax (tax_total == 0), so the
    # document is a plain "Bill" with no CGST/SGST columns. Once items carry a
    # GST rate, the same code prints the full tax invoice.
    has_tax = bool(sale.tax_total)
    doc_label = "Invoice" if has_tax else "Bill"
    info_style = styles["Normal"]
    elements.append(Paragraph(f"<b>{doc_label}:</b> {sale.invoice_number}", info_style))
    elements.append(
        Paragraph(f"<b>Date:</b> {sale.invoice_date.strftime('%Y-%m-%d')}", info_style)
    )

    customer_name = sale.customer.name if sale.customer else "Walk-in Customer"
    elements.append(Paragraph(f"<b>Customer:</b> {customer_name}", info_style))
    elements.append(Spacer(1, 20))

    # --- Table Data ---
    header = ["Item Name", "Qty", "Price", "CGST", "SGST", "Total"]
    data = [header if has_tax else ["Item Name", "Qty", "Price", "Total"]]

    from decimal import Decimal

    total_cgst = Decimal("0.0")
    total_sgst = Decimal("0.0")
    grand_total = Decimal("0.0")

    if sale.items:
        for item in sale.items:
            # Check for tax attributes, default to 0 if not present
            cgst = getattr(item, "cgst_amount", Decimal("0.0"))
            sgst = getattr(item, "sgst_amount", Decimal("0.0"))

            # Use price from item if possible, fallback to cost_price if named differently
            price = getattr(item, "price_at_sale", Decimal("0.0"))

            # Try to get item name
            item_name = getattr(item, "description", None) or "Unknown Item"
            if item.item:
                item_name = getattr(item.item, "name", item_name)

            qty = getattr(item, "quantity", 0)

            # Check how total is stored on item, recalculate if necessary
            line_total = getattr(
                item, "total_price", (price * Decimal(str(qty))) + cgst + sgst
            )

            row = [item_name, str(qty), f"Rs. {price:.2f}"]
            if has_tax:
                row += [f"Rs. {cgst:.2f}", f"Rs. {sgst:.2f}"]
            row.append(f"Rs. {line_total:.2f}")
            data.append(row)

            total_cgst += cgst
            total_sgst += sgst
            grand_total += line_total

    # Fallback to sale total amount if no items or line totals don't match
    if grand_total == Decimal("0.0") and sale.total_amount:
        grand_total = sale.total_amount

    # --- Table Styling ---
    col_widths = [150, 50, 80, 80, 80, 80] if has_tax else [230, 50, 100, 100]
    table = Table(data, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 20))

    # --- Footer (Totals) ---
    right_align_style = ParagraphStyle(
        "RightAlign",
        parent=styles["Normal"],
        alignment=2,  # Right
    )
    if has_tax:
        elements.append(
            Paragraph(f"<b>CGST Total:</b> Rs. {total_cgst:.2f}", right_align_style)
        )
        elements.append(
            Paragraph(f"<b>SGST Total:</b> Rs. {total_sgst:.2f}", right_align_style)
        )
    discount = sale.discount or Decimal("0.0")
    if discount > 0:
        elements.append(
            Paragraph(f"<b>Discount:</b> -Rs. {discount:.2f}", right_align_style)
        )
    # the bill's own total is authoritative (it already has the discount taken off)
    grand_total = sale.total_amount if sale.total_amount is not None else grand_total
    elements.append(
        Paragraph(f"<b>Grand Total:</b> Rs. {grand_total:.2f}", right_align_style)
    )

    # A bill made from an order says which order, and what is still to pay on it
    order = object_session(sale).query(Order).filter(Order.sale_id == sale.id).first()
    if order is not None:
        received = Decimal(order.advance_paid or 0)
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(f"<b>Order:</b> {order.order_number}", info_style))
        elements.append(
            Paragraph(f"<b>Received so far:</b> Rs. {received:.2f}", right_align_style)
        )
        elements.append(
            Paragraph(
                f"<b>Balance due:</b> Rs. {max(Decimal(grand_total) - received, Decimal('0')):.2f}",
                right_align_style,
            )
        )

    # Build PDF
    doc.build(elements)
    return buffer.getvalue()
