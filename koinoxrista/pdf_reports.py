from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import CellBordersLayout
from fpdf.fonts import FontFace

from .services import allocation_report, expense_report, receipt_report

# Grayscale palette tuned for monochrome laser printers.
BLACK = 0
RULE_GRAY = 120
FILL_GRAY = 225
MUTED = 80

HEADINGS = FontFace(emphasis="B", fill_color=FILL_GRAY)
TOTALS = FontFace(emphasis="B", fill_color=FILL_GRAY)
MUTED_TEXT = FontFace(color=MUTED)
BOLD = FontFace(emphasis="B")
# Horizontal rules plus a vertical separator on the left: starts a column group.
GROUP_START = CellBordersLayout.LEFT | CellBordersLayout.TOP | CellBordersLayout.BOTTOM


def money(value):
    formatted = f"{Decimal(value):,.2f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def period_reference(period):
    label = period.comments.strip() or "Χωρίς περιγραφή"
    return f"{period.issue_date.strftime('%d/%m/%Y')} · {label}"


GREEK_MONTHS = [
    "Ιανουάριος",
    "Φεβρουάριος",
    "Μάρτιος",
    "Απρίλιος",
    "Μάιος",
    "Ιούνιος",
    "Ιούλιος",
    "Αύγουστος",
    "Σεπτέμβριος",
    "Οκτώβριος",
    "Νοέμβριος",
    "Δεκέμβριος",
]


def report_title(period):
    issue_date = period.issue_date
    return f"Κοινόχρηστα {period.number} {GREEK_MONTHS[issue_date.month - 1]} {issue_date.year}"


# Bundled so PDFs render identically on every host (Liberation Sans = Arial metrics, SIL OFL).
FONT_DIR = Path(__file__).parent / "fonts"


class CommonExpensesPDF(FPDF):
    footer_text = ""

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(BLACK)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_font("App", size=7)
        self.set_text_color(MUTED)
        self.cell(0, 5, self.footer_text)
        self.set_x(self.l_margin)
        self.cell(0, 5, f"Σελίδα {self.page_no()}/{{nb}}", align="R")
        self.set_text_color(BLACK)


def _new_pdf(period):
    pdf = CommonExpensesPDF(orientation="P", format="A4")
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_font("App", fname=FONT_DIR / "LiberationSans-Regular.ttf")
    pdf.add_font("App", style="B", fname=FONT_DIR / "LiberationSans-Bold.ttf")
    pdf.set_font("App", size=10)
    pdf.alias_nb_pages()
    pdf.footer_text = f"{period.building.name} · {report_title(period)}"
    pdf.set_author(period.manager_name_snapshot)
    return pdf


def _heading(pdf, title, period):
    building = period.building
    top = pdf.get_y()
    pdf.set_font("App", style="B", size=16)
    pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("App", size=9)
    pdf.cell(0, 5, period_reference(period), new_x="LMARGIN", new_y="NEXT")
    bottom = pdf.get_y()
    pdf.set_y(top)
    pdf.set_font("App", style="B", size=11)
    pdf.cell(0, 6, building.name, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("App", size=9)
    pdf.cell(0, 5, f"{building.address}, {building.postal_code}", align="R")
    pdf.set_y(max(bottom, pdf.get_y() + 5) + 2)
    pdf.set_draw_color(BLACK)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)


def _section_title(pdf, title, note="Ποσά σε €"):
    pdf.set_font("App", style="B", size=11)
    pdf.cell(0, 7, title)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("App", size=7)
    pdf.set_text_color(MUTED)
    pdf.cell(0, 7, note, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(BLACK)
    pdf.set_font("App", size=8)


@contextmanager
def _table(pdf, **kwargs):
    pdf.set_draw_color(RULE_GRAY)
    pdf.set_line_width(0.15)
    with pdf.table(
        borders_layout="HORIZONTAL_LINES",
        headings_style=HEADINGS,
        padding=(1.2, 1.5),
        **kwargs,
    ) as table:
        yield table
    # HORIZONTAL_LINES leaves the last row open; close it.
    pdf.set_draw_color(RULE_GRAY)
    pdf.set_line_width(0.15)
    pdf.line(pdf.l_margin, pdf.y, pdf.w - pdf.r_margin, pdf.y)


def _grand_total(pdf, label, amount):
    """Full-width total under a table, set off by a heavy rule like a ledger sum line."""
    height = 9
    if pdf.will_page_break(height + 1):
        pdf.add_page()
    pdf.set_draw_color(BLACK)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.set_font("App", style="B", size=11)
    pdf.cell(pdf.epw / 2, height, f"  {label}")
    pdf.set_font("App", style="B", size=14)
    pdf.cell(pdf.epw / 2, height, f"{money(amount)} €  ", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_line_width(0.2)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.set_font("App", size=8)


def create_period_report(period):
    ledger = expense_report(period)
    distribution = allocation_report(period)
    pdf = _new_pdf(period)
    pdf.set_title(report_title(period))
    pdf.add_page()
    _heading(pdf, report_title(period), period)

    _section_title(pdf, "Δαπάνες")
    categories = ledger["categories"]
    with _table(
        pdf,
        col_widths=[16, 15, 50] + [18] * len(categories),
        line_height=4.5,
        text_align=["LEFT", "LEFT", "LEFT"] + ["RIGHT"] * len(categories),
    ) as table:
        heading = table.row()
        for label in ["Ημ/νία", "Παρ/κό", "Περιγραφή"]:
            heading.cell(label)
        for category in categories:
            heading.cell(category.name, align="RIGHT", border=GROUP_START)
        for item in ledger["rows"]:
            row = table.row()
            expense = item["expense"]
            row.cell(expense.invoice_date.strftime("%d/%m/%Y"))
            row.cell(expense.invoice_number)
            row.cell(expense.description)
            for amount in item["values"]:
                row.cell(money(amount) if amount is not None else "", border=GROUP_START)
        row = table.row(style=TOTALS)
        row.cell("Σύνολα ανά κατηγορία", colspan=3)
        for total, has_expenses in zip(ledger["totals"], ledger["has_expenses"], strict=True):
            row.cell(
                money(total) if has_expenses else "",
                align="RIGHT",
                border=GROUP_START,
            )
    _grand_total(pdf, "Γενικό σύνολο (άθροισμα συνόλων)", ledger["grand_total"])

    pdf.ln(8)
    _section_title(pdf, "Κατανομή ανά διαμέρισμα", "Χιλ. = χιλιοστά · Ποσά σε €")
    categories = distribution["categories"]
    with _table(
        pdf,
        col_widths=[34] + [8, 15] * len(categories) + [18],
        line_height=4.5,
        text_align=["LEFT"] + ["RIGHT", "RIGHT"] * len(categories) + ["RIGHT"],
        num_heading_rows=2,
    ) as table:
        heading = table.row()
        heading.cell("Διαμέρισμα", rowspan=2, align="LEFT")
        for category in categories:
            heading.cell(category.name, colspan=2, align="CENTER", border=GROUP_START)
        heading.cell("Σύνολο", rowspan=2, align="RIGHT", border=GROUP_START)
        heading = table.row()
        for _category in categories:
            heading.cell("Χιλ.", align="RIGHT", border=GROUP_START)
            heading.cell("Ποσό", align="RIGHT")
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
                    style=MUTED_TEXT,
                    border=GROUP_START,
                )
                row.cell(money(amount) if has_expenses else "")
            row.cell(money(item["total"]), style=BOLD, border=GROUP_START)
        row = table.row(style=TOTALS)
        row.cell("Σύνολα")
        for millesimal_total, total, has_expenses in zip(
            distribution["millesimal_totals"],
            distribution["totals"],
            distribution["has_expenses"],
            strict=True,
        ):
            row.cell(str(millesimal_total) if has_expenses else "", border=GROUP_START)
            row.cell(money(total) if has_expenses else "")
        row.cell(f"{money(distribution['grand_total'])} €", border=GROUP_START)

    return bytes(pdf.output())


def _info_box(pdf, pairs):
    """Framed two-column label/value block."""
    width = pdf.epw / 2
    top = pdf.get_y()
    pdf.ln(2)
    for start in range(0, len(pairs), 2):
        for column, (label, value) in enumerate(pairs[start : start + 2]):
            pdf.set_x(pdf.l_margin + column * width + 3)
            pdf.set_font("App", size=8)
            pdf.set_text_color(MUTED)
            pdf.cell(24, 6.5, label)
            pdf.set_text_color(BLACK)
            pdf.set_font("App", style="B", size=10)
            pdf.cell(width - 30, 6.5, value)
        pdf.ln(6.5)
    pdf.ln(2)
    pdf.set_draw_color(BLACK)
    pdf.set_line_width(0.3)
    pdf.rect(pdf.l_margin, top, pdf.epw, pdf.get_y() - top)
    pdf.ln(6)


def _payable_box(pdf, amount):
    box_width = 80
    pdf.set_x(pdf.w - pdf.r_margin - box_width)
    pdf.set_draw_color(BLACK)
    pdf.set_line_width(0.6)
    pdf.set_font("App", size=10)
    pdf.cell(box_width / 2, 12, "  Πληρωτέο ποσό", border="LTB")
    pdf.set_font("App", style="B", size=15)
    pdf.cell(
        box_width / 2,
        12,
        f"{money(amount)} €  ",
        border="RTB",
        align="R",
        new_x="LMARGIN",
        new_y="NEXT",
    )


def _signature(pdf, name):
    width = 70
    x = pdf.w - pdf.r_margin - width
    pdf.set_draw_color(BLACK)
    pdf.set_line_width(0.2)
    pdf.line(x, pdf.get_y(), x + width, pdf.get_y())
    pdf.ln(1)
    pdf.set_x(x)
    pdf.set_font("App", size=8)
    pdf.set_text_color(MUTED)
    pdf.cell(width, 5, "Υπογραφή διαχειριστή", align="C", new_x="LEFT", new_y="NEXT")
    pdf.set_text_color(BLACK)
    pdf.set_font("App", style="B", size=10)
    pdf.cell(width, 5, name, align="C")


def create_receipts_report(period):
    receipts = receipt_report(period)
    pdf = _new_pdf(period)
    pdf.set_title(f"Αποδείξεις κοινοχρήστων {period.issue_date}")
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
        _heading(pdf, "Απόδειξη εξόφλησης κοινοχρήστων", period)
        _info_box(
            pdf,
            [
                ("Διαμέρισμα", apartment.name),
                ("Ημερομηνία", period.issue_date.strftime("%d/%m/%Y")),
                ("Ένοικος", apartment.occupant or "-"),
                ("Αρ. περιόδου", str(period.number)),
                ("Ιδιοκτήτης", apartment.owner or "-"),
                ("Όροφος", str(apartment.floor)),
            ],
        )
        _section_title(pdf, "Δαπάνες και αναλογία διαμερίσματος")
        with _table(
            pdf,
            col_widths=[18, 17, 55, 18, 14, 20],
            line_height=5,
            text_align=["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"],
        ) as table:
            heading = table.row()
            for label in ["Ημ/νία", "Παρ/κό", "Περιγραφή"]:
                heading.cell(label)
            for label in ["Ποσό", "Χιλιοστά", "Αναλογούν"]:
                heading.cell(label, align="RIGHT")
            for item in receipt["rows"]:
                expense = item["expense"]
                row = table.row()
                row.cell(expense.invoice_date.strftime("%d/%m/%Y"))
                row.cell(expense.invoice_number)
                row.cell(expense.description)
                row.cell(money(expense.amount))
                row.cell(str(item["millesimals"]), style=MUTED_TEXT)
                row.cell(money(item["amount"]), style=BOLD)

        pdf.ln(5)
        _payable_box(pdf, receipt["payable"])
        pdf.ln(22)
        _signature(pdf, period.manager_name_snapshot)
    return bytes(pdf.output())
