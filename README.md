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

## Production με Docker Compose

Το production image βασίζεται σε `python:3.12-alpine`, χρησιμοποιεί Gunicorn και
τρέχει ως non-root χρήστης. Η βάση και τα τοπικά backups αποθηκεύονται στο named
volume `app_data` και δεν περιλαμβάνονται ποτέ στο image.

Χρησιμοποιείται ένας Gunicorn worker, ώστε οι λειτουργίες SQLite backup/restore
και οι εγγραφές να μην εκτελούνται ταυτόχρονα από διαφορετικές διεργασίες.

1. Προαιρετικά αντιγράψτε το `.env.example` σε `.env` και αλλάξτε port/bind.

```powershell
Copy-Item .env.example .env
```

2. Δημιουργήστε και εκκινήστε το container.

```powershell
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs -f app
```

Στην πρώτη εκκίνηση δημιουργείται αυτόματα ισχυρό `flask_secret_key` στο
persistent volume, στη διαδρομή `/app/instance/flask_secret_key`, με permissions
`0600`. Το ίδιο secret επαναχρησιμοποιείται σε restart ή rebuild και διαγράφεται
μόνο αν αφαιρεθεί το volume με `docker compose down -v`.

Η εφαρμογή είναι διαθέσιμη στο `http://127.0.0.1:8076`. Σε νέα εγκατάσταση
συνδεθείτε με `admin@admin.app` / `changeme`, αλλάξτε τον κωδικό και χρησιμοποιήστε
το **Backup → Restore** για επαναφορά υπάρχουσου backup.

Για αρχική εισαγωγή της παλιάς `koinoxrista.db` αντί για restore, ενεργοποιήστε
το σχολιασμένο read-only bind mount στο `compose.yml` πριν από το πρώτο `up`.

Χρήσιμες εντολές:

```powershell
docker compose restart app
docker compose down
docker compose down --remove-orphans
```

Το `docker compose down` δεν διαγράφει τη βάση. Μην χρησιμοποιήσετε
`docker compose down -v` εκτός αν θέλετε να διαγράψετε οριστικά το volume.

### Ρύθμιση `COOKIE_SECURE`

Η μεταβλητή `COOKIE_SECURE` καθορίζει αν το cookie σύνδεσης αποστέλλεται μόνο
μέσω HTTPS:

- `COOKIE_SECURE=false`: χρησιμοποιήστε το όταν η εφαρμογή ανοίγει απευθείας με
  HTTP, π.χ. `http://127.0.0.1:8076`.
- `COOKIE_SECURE=true`: χρησιμοποιήστε το όταν η εφαρμογή εξυπηρετείται μέσω
  HTTPS/reverse proxy. Το cookie δεν αποστέλλεται ποτέ μέσω απλού HTTP.

Αν οριστεί `COOKIE_SECURE=true` χωρίς να υπάρχει HTTPS, η σύνδεση του χρήστη δεν
θα διατηρείται. Για δημόσια έκθεση της εφαρμογής συνιστώνται οπωσδήποτε HTTPS
και `COOKIE_SECURE=true`.

Από προεπιλογή το port δεσμεύεται μόνο στο `127.0.0.1`, ώστε η εφαρμογή να μην
εκτίθεται απευθείας στο δίκτυο.
