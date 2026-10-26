import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.sale import Sale

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INVOICES_DIR = os.getenv("INVOICES_DIR") or os.path.join(BASE_DIR, "invoices")


def generate_invoice_pdf(sale: Sale) -> str:
    """
    Generate a professional PDF invoice for a completed sale transaction.

    The PDF includes company branding, customer details, line items with taxes,
    and a summary of totals. Files are stored in the `invoices/` directory.

    Args:
        sale (Sale): The sale model instance containing transaction data and items.

    Returns:
        str: Absolute file path to the generated PDF document.
    """
    # Ensure directory exists
    os.makedirs(INVOICES_DIR, exist_ok=True)

    # Define file path
    filename = f"{sale.invoice_number}.pdf"
    filepath = os.path.join(INVOICES_DIR, filename)

    doc = SimpleDocTemplate(filepath, pagesize=A4)
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
            item_name = "Unknown Item"
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
    elements.append(
        Paragraph(f"<b>Grand Total:</b> Rs. {grand_total:.2f}", right_align_style)
    )

    # Build PDF
    doc.build(elements)

    return filepath
