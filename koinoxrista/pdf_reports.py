from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import CellBordersLayout
from fpdf.fonts import FontFace

from .models import CashEntry
from .services import (
    GREEK_MONTHS,
    allocation_report,
    apartment_ledger,
    expense_report,
    manager_on,
    previous_balance,
    receipt_report,
)

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


_UNITS = {
    "n": ["", "ένα", "δύο", "τρία", "τέσσερα", "πέντε", "έξι", "επτά", "οκτώ", "εννέα"],
    "f": ["", "μία", "δύο", "τρεις", "τέσσερις", "πέντε", "έξι", "επτά", "οκτώ", "εννέα"],
}
_TEENS = {
    "n": ["δέκα", "έντεκα", "δώδεκα", "δεκατρία", "δεκατέσσερα"],
    "f": ["δέκα", "έντεκα", "δώδεκα", "δεκατρείς", "δεκατέσσερις"],
}
_TEENS_REST = ["δεκαπέντε", "δεκαέξι", "δεκαεπτά", "δεκαοκτώ", "δεκαεννέα"]
_TENS = [
    "",
    "",
    "είκοσι",
    "τριάντα",
    "σαράντα",
    "πενήντα",
    "εξήντα",
    "εβδομήντα",
    "ογδόντα",
    "ενενήντα",
]
_HUNDREDS = [
    "",
    "εκατό",
    "διακόσι",
    "τριακόσι",
    "τετρακόσι",
    "πεντακόσι",
    "εξακόσι",
    "επτακόσι",
    "οκτακόσι",
    "εννιακόσι",
]


def _below_thousand(number, gender):
    """Greek words for 1..999; gender 'n' (neuter) or 'f' (feminine, used before χιλιάδες)."""
    hundreds, rest = divmod(number, 100)
    words = []
    if hundreds == 1:
        words.append("εκατόν" if rest else "εκατό")
    elif hundreds:
        words.append(_HUNDREDS[hundreds] + ("α" if gender == "n" else "ες"))
    tens, units = divmod(rest, 10)
    if tens == 1:
        words.append(_TEENS[gender][units] if units < 5 else _TEENS_REST[units - 5])
    else:
        if tens:
            words.append(_TENS[tens])
        if units:
            words.append(_UNITS[gender][units])
    return " ".join(words)


def _integer_words(number):
    if number == 0:
        return "μηδέν"
    millions, rest = divmod(number, 1_000_000)
    thousands, units = divmod(rest, 1000)
    words = []
    if millions == 1:
        words.append("ένα εκατομμύριο")
    elif millions:
        words.append(f"{_below_thousand(millions, 'n')} εκατομμύρια")
    if thousands == 1:
        words.append("χίλια")
    elif thousands:
        words.append(f"{_below_thousand(thousands, 'f')} χιλιάδες")
    if units:
        words.append(_below_thousand(units, "n"))
    return " ".join(words)


def amount_in_words(amount):
    """'Εκατόν είκοσι ευρώ και πενήντα λεπτά' for Decimal('120.50')."""
    cents_total = int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    sign = "μείον " if cents_total < 0 else ""
    euros, cents = divmod(abs(cents_total), 100)
    if euros or not cents:
        text = f"{_integer_words(euros)} ευρώ"
        if cents:
            text += f" και {_integer_words(cents)} {'λεπτό' if cents == 1 else 'λεπτά'}"
    else:
        text = f"{_integer_words(cents)} {'λεπτό' if cents == 1 else 'λεπτά'}"
    text = sign + text
    return text[0].upper() + text[1:]


def period_reference(period):
    label = period.comments.strip() or "Χωρίς περιγραφή"
    return f"{period.issue_date.strftime('%d/%m/%Y')} · {label}"


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


def _new_pdf(building, footer, author=""):
    pdf = CommonExpensesPDF(orientation="P", format="A4")
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_font("App", fname=FONT_DIR / "LiberationSans-Regular.ttf")
    pdf.add_font("App", style="B", fname=FONT_DIR / "LiberationSans-Bold.ttf")
    pdf.set_font("App", size=10)
    pdf.alias_nb_pages()
    pdf.footer_text = f"{building.name} · {footer}"
    pdf.set_author(author)
    return pdf


def _heading(pdf, title, building, subtitle):
    top = pdf.get_y()
    pdf.set_font("App", style="B", size=16)
    pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("App", size=9)
    pdf.cell(0, 5, subtitle, new_x="LMARGIN", new_y="NEXT")
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
    pdf = _new_pdf(period.building, report_title(period), period.manager_name_snapshot)
    pdf.set_title(report_title(period))
    pdf.add_page()
    _heading(pdf, report_title(period), period.building, period_reference(period))

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


def _payable_box(pdf, amount, label="Πληρωτέο ποσό"):
    box_width = 80
    pdf.set_x(pdf.w - pdf.r_margin - box_width)
    pdf.set_draw_color(BLACK)
    pdf.set_line_width(0.6)
    pdf.set_font("App", size=10)
    pdf.cell(box_width / 2, 12, f"  {label}", border="LTB")
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


def _signature(pdf, name, caption="Υπογραφή διαχειριστή", x=None):
    width = 70
    x = pdf.w - pdf.r_margin - width if x is None else x
    pdf.set_draw_color(BLACK)
    pdf.set_line_width(0.2)
    pdf.line(x, pdf.get_y(), x + width, pdf.get_y())
    pdf.ln(1)
    pdf.set_x(x)
    pdf.set_font("App", size=8)
    pdf.set_text_color(MUTED)
    pdf.cell(width, 5, caption, align="C", new_x="LEFT", new_y="NEXT")
    pdf.set_text_color(BLACK)
    pdf.set_font("App", style="B", size=10)
    pdf.cell(width, 5, name, align="C")


def _summary_line(pdf, label, amount):
    width = 80
    pdf.set_x(pdf.w - pdf.r_margin - width)
    pdf.set_font("App", size=9)
    pdf.cell(width / 2, 6, f"  {label}")
    pdf.cell(width / 2, 6, f"{money(amount)} €  ", align="R", new_x="LMARGIN", new_y="NEXT")


def _apartment_pages(period, title, empty_title, finish):
    """One page per apartment with a charge: details, its share of every expense, then finish()."""
    items = receipt_report(period)
    pdf = _new_pdf(period.building, report_title(period), period.manager_name_snapshot)
    pdf.set_title(f"{title} {report_title(period)}")
    if not items:
        pdf.add_page()
        pdf.set_font("App", style="B", size=16)
        pdf.cell(0, 12, empty_title, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("App", size=11)
        pdf.cell(0, 8, "Όλα τα διαμερίσματα έχουν μηδενικό πληρωτέο ποσό.", align="C")
    for item in items:
        apartment = item["apartment"]
        pdf.add_page()
        _heading(pdf, title, period.building, period_reference(period))
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
            for line in item["rows"]:
                expense = line["expense"]
                row = table.row()
                row.cell(expense.invoice_date.strftime("%d/%m/%Y"))
                row.cell(expense.invoice_number)
                row.cell(expense.description)
                row.cell(money(expense.amount))
                row.cell(str(line["millesimals"]), style=MUTED_TEXT)
                row.cell(money(line["amount"]), style=BOLD)
        pdf.ln(5)
        finish(pdf, apartment, item["payable"])
    return bytes(pdf.output())


def create_payment_notices(period):
    """Notice with previous balance and total due; no signature."""

    def finish(pdf, apartment, payable):
        previous = previous_balance(apartment, period)
        if previous is None:
            _payable_box(pdf, payable)
            return
        total = previous + payable
        _summary_line(pdf, "Προηγούμενο υπόλοιπο", previous)
        _summary_line(pdf, "Τρέχοντα κοινόχρηστα", payable)
        pdf.ln(1)
        if total >= 0:
            _payable_box(pdf, total, "Σύνολο προς πληρωμή")
        else:
            _payable_box(pdf, -total, "Πιστωτικό υπόλοιπο")

    return _apartment_pages(
        period,
        "Ειδοποίηση πληρωμής κοινοχρήστων",
        "Δεν υπάρχουν ειδοποιήσεις προς έκδοση",
        finish,
    )


def create_receipts_report(period):
    """Pre-printed receipt for this period's amount, signed by hand when paid."""

    def finish(pdf, apartment, payable):
        _payable_box(pdf, payable)
        pdf.ln(2)
        pdf.set_font("App", size=8)
        pdf.set_text_color(MUTED)
        pdf.cell(20, 6, "Ολογράφως")
        pdf.set_text_color(BLACK)
        pdf.set_font("App", style="B", size=10)
        pdf.cell(0, 6, amount_in_words(payable), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(18)
        _signature(pdf, period.manager_name_snapshot)

    return _apartment_pages(
        period,
        "Απόδειξη εξόφλησης κοινοχρήστων",
        "Δεν υπάρχουν αποδείξεις προς έκδοση",
        finish,
    )


RECEIPT_TITLES = {
    "collection": "Απόδειξη είσπραξης κοινοχρήστων",
    "reserve_in": "Απόδειξη είσπραξης αποθεματικού",
    "reserve_out": "Απόδειξη επιστροφής αποθεματικού",
    "refund": "Απόδειξη επιστροφής πίστωσης",
}


def _amount_block(pdf, amount, side_label, side_value, voided_reason=None):
    """Side figure on the left, framed amount on the right, then the amount in words."""
    top = pdf.get_y()
    pdf.set_font("App", size=8)
    pdf.set_text_color(MUTED)
    pdf.cell(90, 6, side_label, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(BLACK)
    pdf.set_font("App", style="B", size=11)
    pdf.cell(90, 6, f"{money(side_value)} €")
    pdf.set_y(top)
    _payable_box(pdf, amount, "Ποσό")
    pdf.ln(2)
    pdf.set_font("App", size=8)
    pdf.set_text_color(MUTED)
    pdf.cell(20, 6, "Ολογράφως")
    pdf.set_text_color(BLACK)
    pdf.set_font("App", style="B", size=10)
    pdf.cell(0, 6, amount_in_words(amount), new_x="LMARGIN", new_y="NEXT")
    if voided_reason is not None:
        pdf.set_font("App", style="B", size=12)
        pdf.cell(0, 7, f"ΑΚΥΡΩΜΕΝΗ — {voided_reason}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(12)


def _cut_page(pdf, draw_copy, first_copy_label):
    """A4 with two copies, a dashed cut line in the middle."""
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    half = pdf.h / 2
    draw_copy(first_copy_label)
    with pdf.local_context(draw_color=RULE_GRAY, line_width=0.2):
        pdf.set_dash_pattern(dash=2, gap=2)
        pdf.line(pdf.l_margin, half, pdf.w - pdf.r_margin, half)
        pdf.set_dash_pattern()
    pdf.set_y(half + 10)
    draw_copy("Αντίγραφο διαχειριστή")
    return bytes(pdf.output())


def _manager_name(building_id, day):
    manager = manager_on(building_id, day)
    return manager.display_name if manager else ""


def create_cash_receipt(entry):
    after = next(row for row in apartment_ledger(entry.apartment) if row.entry is entry)
    manager_name = _manager_name(entry.building_id, entry.date)
    title = f"{RECEIPT_TITLES[entry.kind]} αρ. {entry.number}"
    pdf = _new_pdf(entry.building, title, manager_name)
    pdf.set_title(title)
    apartment = entry.apartment
    date_text = entry.date.strftime("%d/%m/%Y")
    if entry.kind == "collection":
        side = ("Υπόλοιπο οφειλής μετά την είσπραξη", after.balance)
    elif entry.kind == "refund":
        side = ("Υπόλοιπο διαμερίσματος μετά την επιστροφή", after.balance)
    else:
        side = ("Αποθεματικό διαμερίσματος μετά την κίνηση", after.reserve)

    def receipt(copy_label):
        _heading(
            pdf,
            RECEIPT_TITLES[entry.kind],
            entry.building,
            f"Αρ. {entry.number} · {date_text} · {copy_label}",
        )
        _info_box(
            pdf,
            [
                ("Διαμέρισμα", apartment.name),
                ("Αρ. απόδειξης", str(entry.number)),
                ("Ιδιοκτήτης", apartment.owner or "-"),
                ("Ημερομηνία", date_text),
                ("Ένοικος", apartment.occupant or "-"),
                ("Όροφος", str(apartment.floor)),
                ("Προς" if entry.kind in CashEntry.OUTFLOWS else "Από", entry.party_name or "-"),
                ("Αιτιολογία", entry.reason),
            ],
        )
        _amount_block(pdf, entry.amount, *side, entry.void_reason if entry.voided_at else None)
        if entry.kind in CashEntry.OUTFLOWS:
            _signature(pdf, entry.party_name, "Υπογραφή παραλήπτη")
        else:
            _signature(pdf, manager_name)

    return _cut_page(pdf, receipt, "Αντίγραφο διαμερίσματος")


def create_payment_receipt(payment):
    expense = payment.expense
    period = expense.period
    manager_name = _manager_name(payment.building_id, payment.date)
    title = f"Απόδειξη πληρωμής δαπάνης αρ. {payment.number}"
    pdf = _new_pdf(payment.building, title, manager_name)
    pdf.set_title(title)
    date_text = payment.date.strftime("%d/%m/%Y")
    paid_until = sum(
        (
            item.amount
            for item in expense.payments
            if not item.voided_at and (item.date, item.id) <= (payment.date, payment.id)
        ),
        Decimal("0"),
    )

    def voucher(copy_label):
        _heading(
            pdf,
            "Απόδειξη πληρωμής δαπάνης",
            payment.building,
            f"Αρ. {payment.number} · {date_text} · {copy_label}",
        )
        _info_box(
            pdf,
            [
                ("Δικαιούχος", payment.payee or "-"),
                ("Αρ. πληρωμής", str(payment.number)),
                ("Δαπάνη", expense.description),
                ("Ημερομηνία", date_text),
                ("Παραστατικό", f"{expense.invoice_number} / {expense.invoice_date:%d/%m/%Y}"),
                ("Ποσό δαπάνης", f"{money(expense.amount)} €"),
                ("Κατηγορία", expense.category.name),
                ("Περίοδος", f"No {period.number} · {period.issue_date:%d/%m/%Y}"),
                ("Αιτιολογία", payment.reason),
            ],
        )
        _amount_block(
            pdf,
            payment.amount,
            "Υπόλοιπο δαπάνης μετά την πληρωμή",
            expense.amount - paid_until,
            payment.void_reason if payment.voided_at else None,
        )
        _signature(pdf, manager_name, "Ο διαχειριστής", x=pdf.l_margin)
        pdf.set_y(pdf.get_y() - 6)
        _signature(pdf, payment.payee, "Ο δικαιούχος (υπογραφή)")

    return _cut_page(pdf, voucher, "Αντίγραφο δικαιούχου")
