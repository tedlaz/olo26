# Κοινόχρηστα

Flask/HTMX εφαρμογή διαχείρισης κοινοχρήστων για πολλαπλά κτίρια. Η αρχική
`koinoxrista.db` χρησιμοποιείται μόνο ως read-only πηγή εισαγωγής. Η εφαρμογή
γράφει αποκλειστικά στη νέα `instance/koinoxrista_app.db`.

## Εκκίνηση

```powershell
uv sync
$env:SECRET_KEY = "replace-with-a-long-random-value"
uv run flask --app run.py init-app
uv run flask --app run.py run --debug
```

Ανοίξτε το `http://127.0.0.1:5000`. Ο αρχικός λογαριασμός είναι
`admin@admin.app` / `changeme` και απαιτεί άμεση αλλαγή password. Στη συνέχεια,
η οθόνη αρχικού setup ζητά διεύθυνση και ΤΚ πριν εισαγάγει τα ιστορικά δεδομένα.

## Έλεγχοι

```powershell
uv run ruff check .
uv run pytest
```

Για production απαιτείται ισχυρό `SECRET_KEY`, HTTPS και απενεργοποίηση του
Flask debug server.

Τα PDF χρησιμοποιούν Unicode TrueType font. Σε Windows χρησιμοποιείται αυτόματα
η Arial. Σε άλλο σύστημα, αν δεν υπάρχει DejaVu Sans, ορίστε `PDF_FONT_PATH` και
`PDF_FONT_BOLD_PATH` με τις διαδρομές των κανονικών και bold `.ttf` αρχείων.
