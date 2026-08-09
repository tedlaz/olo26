import os
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import CellBordersLayout
from fpdf.fonts import FontFace

from .services import allocation_report, expense_report, receipt_report


def money(value):
    formatted = f"{Decimal(value):,.2f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def period_reference(period):
    issue_date = period.issue_date
    label = period.comments.strip() or "Χωρίς περιγραφή"
    number = period.id
    return f"Περίοδος: {label} {issue_date.day}/{issue_date.month}/{issue_date.year} (No: {number})"


def _font_paths():
    configured = os.environ.get("PDF_FONT_PATH")
    configured_bold = os.environ.get("PDF_FONT_BOLD_PATH")
    candidates = [
        (
            Path(configured) if configured else None,
            Path(configured_bold) if configured_bold else None,
        ),
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular and regular.is_file():
            return regular, bold if bold and bold.is_file() else regular
    raise RuntimeError("Δεν βρέθηκε Unicode PDF font. Ορίστε PDF_FONT_PATH και PDF_FONT_BOLD_PATH.")


class CommonExpensesPDF(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("App", size=8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, f"Σελίδα {self.page_no()}/{{nb}}", align="C")


def _new_pdf(orientation="P"):
    regular, bold = _font_paths()
    pdf = CommonExpensesPDF(orientation=orientation, format="A4")
    pdf.set_margins(10, 12, 10)
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_font("App", fname=regular)
    pdf.add_font("App", style="B", fname=bold)
    pdf.set_font("App", size=10)
    pdf.alias_nb_pages()
    return pdf


def _heading(pdf, title, period):
    pdf.set_font("App", style="B", size=18)
    pdf.cell(0, 9, title, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("App", size=10)
    pdf.cell(
        0,
        6,
        f"{period.building.name} · {period.building.address} · {period.building.postal_code}",
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        0,
        6,
        period_reference(period),
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)


def _section_title(pdf, title):
    pdf.set_font("App", style="B", size=12)
    pdf.cell(0, 8, title, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("App", size=8)


def create_period_report(period):
    ledger = expense_report(period)
    distribution = allocation_report(period)
    pdf = _new_pdf("P")
    pdf.set_title(f"Κοινόχρηστα {period.issue_date}")
    pdf.set_author(period.manager_name_snapshot)
    pdf.add_page()
    _heading(pdf, "Αναφορά κοινοχρήστων", period)

    _section_title(pdf, "Δαπάνες")
    expense_widths = [15, 18, 44] + [18] * len(ledger["categories"])
    with pdf.table(
        col_widths=expense_widths,
        line_height=6,
        text_align=["CENTER", "CENTER", "LEFT"] + ["RIGHT"] * len(ledger["categories"]),
        headings_style=FontFace(emphasis="B", fill_color=(232, 232, 232)),
    ) as table:
        heading = table.row()
        for label in ["Ημ/νία", "Παρ/κό", "Περιγραφή"] + [
            category.name for category in ledger["categories"]
        ]:
            heading.cell(label)
        for item in ledger["rows"]:
            row = table.row()
            expense = item["expense"]
            row.cell(expense.invoice_date.strftime("%d/%m/%Y"))
            row.cell(expense.invoice_number)
            row.cell(expense.description)
            for amount in item["values"]:
                row.cell(f"{money(amount)} €" if amount is not None else "")
        total_style = FontFace(emphasis="B", fill_color=(240, 240, 240))
        row = table.row()
        row.cell("Σύνολα", colspan=3, align="CENTER", style=total_style)
        for total, has_expenses in zip(ledger["totals"], ledger["has_expenses"], strict=True):
            row.cell(
                f"{money(total)} €" if has_expenses else "",
                align="RIGHT",
                style=total_style,
            )
        row = table.row()
        row.cell("Γενικό Σύνολο", colspan=3, align="CENTER", style=total_style)
        row.cell(
            f"{money(ledger['grand_total'])} €",
            colspan=max(1, len(ledger["categories"])),
            align="RIGHT",
            style=total_style,
        )

    pdf.ln(7)
    _section_title(pdf, "Κατανομή ανά διαμέρισμα")
    allocation_widths = [30] + [8, 15] * len(distribution["categories"]) + [17]
    allocation_alignments = ["LEFT"]
    for _category in distribution["categories"]:
        allocation_alignments.extend(["LEFT", "RIGHT"])
    allocation_alignments.append("RIGHT")
    with pdf.table(
        col_widths=allocation_widths,
        line_height=6,
        text_align=allocation_alignments,
        headings_style=FontFace(emphasis="B", fill_color=(232, 232, 232)),
    ) as table:
        heading = table.row()
        heading.cell("Διαμέρισμα")
        for category in distribution["categories"]:
            heading.cell(category.name, colspan=2, align="CENTER")
        heading.cell("Σύνολο", align="RIGHT")
        left_half_border = CellBordersLayout.LEFT | CellBordersLayout.TOP | CellBordersLayout.BOTTOM
        right_half_border = (
            CellBordersLayout.RIGHT | CellBordersLayout.TOP | CellBordersLayout.BOTTOM
        )
        for item in distribution["rows"]:
            row = table.row()
            row.cell(item["label"])
            for millesimal, amount, has_expenses in zip(
                item["millesimals"],
                item["values"],
                distribution["has_expenses"],
                strict=True,
            ):
                row.cell(
                    str(millesimal) if has_expenses else "",
                    align="LEFT",
                    border=left_half_border,
                )
                row.cell(
                    f"{money(amount)} €" if has_expenses else "",
                    border=right_half_border,
                )
            row.cell(f"{money(item['total'])} €", style=FontFace(emphasis="B"))
        total_style = FontFace(emphasis="B", fill_color=(240, 240, 240))
        row = table.row()
        row.cell("Σύνολα", style=total_style)
        for millesimal_total, total, has_expenses in zip(
            distribution["millesimal_totals"],
            distribution["totals"],
            distribution["has_expenses"],
            strict=True,
        ):
            row.cell(
                str(millesimal_total) if has_expenses else "",
                align="LEFT",
                style=total_style,
                border=left_half_border,
            )
            row.cell(
                f"{money(total)} €" if has_expenses else "",
                style=total_style,
                border=right_half_border,
            )
        row.cell(f"{money(distribution['grand_total'])} €", style=total_style)

    return bytes(pdf.output())


def create_receipts_report(period):
    receipts = receipt_report(period)
    pdf = _new_pdf("P")
    pdf.set_title(f"Αποδείξεις κοινοχρήστων {period.issue_date}")
    pdf.set_author(period.manager_name_snapshot)
    if not receipts:
        pdf.add_page()
        pdf.set_font("App", style="B", size=16)
        pdf.cell(
            0,
            12,
            "Δεν υπάρχουν αποδείξεις προς έκδοση",
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_font("App", size=11)
        pdf.cell(
            0,
            8,
            "Όλα τα διαμερίσματα έχουν μηδενικό πληρωτέο ποσό.",
            align="C",
        )
    for receipt in receipts:
        apartment = receipt["apartment"]
        pdf.add_page()
        pdf.set_font("App", style="B", size=18)
        pdf.cell(
            0,
            12,
            "Απόδειξη εξόφλησης κοινοχρήστων",
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(5)
        pdf.set_font("App", size=11)
        receipt_number = period.id
        pdf.cell(
            0,
            8,
            f"Ημερομηνία: {period.issue_date.strftime('%d/%m/%Y')} (No: {receipt_number})",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.cell(0, 8, f"Ένοικος: {apartment.occupant or '-'}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 8, f"Ιδιοκτήτης: {apartment.owner or '-'}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 8, f"Όροφος: {apartment.floor}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        _section_title(pdf, "Δαπάνες και αναλογία διαμερίσματος")
        with pdf.table(
            col_widths=[18, 20, 46, 16, 14, 18],
            line_height=7,
            text_align=["CENTER", "CENTER", "LEFT", "RIGHT", "RIGHT", "RIGHT"],
            headings_style=FontFace(emphasis="B", fill_color=(232, 232, 232)),
        ) as table:
            heading = table.row()
            for label in ["Ημ/νία", "Παρ/κό", "Περιγραφή", "Ποσό", "Χιλιοστά", "Αναλογούν"]:
                heading.cell(label)
            for item in receipt["rows"]:
                expense = item["expense"]
                row = table.row()
                row.cell(expense.invoice_date.strftime("%d/%m/%Y"))
                row.cell(expense.invoice_number)
                row.cell(expense.description)
                row.cell(f"{money(expense.amount)} €")
                row.cell(str(item["millesimals"]))
                row.cell(f"{money(item['amount'])} €")
            total_style = FontFace(emphasis="B", fill_color=(240, 240, 240))
            row = table.row()
            row.cell("Πληρωτέο ποσό", colspan=5, align="CENTER", style=total_style)
            row.cell(f"{money(receipt['payable'])} €", align="RIGHT", style=total_style)

        pdf.ln(18)
        pdf.set_font("App", size=11)
        pdf.cell(
            0,
            7,
            f"Ο διαχειριστής ({period.manager_name_snapshot})",
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(5)
        pdf.cell(0, 7, "(Υπογραφή)", align="C")
    return bytes(pdf.output())
