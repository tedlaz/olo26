import json
import re

from koinoxrista.extensions import db
from koinoxrista.models import (
    Apartment,
    AuditLog,
    Building,
    BuildingMembership,
    ExpenseCategory,
    Millesimal,
    User,
)


def login_admin(app, client):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@admin.app"))
        admin.must_change_password = False
        db.session.commit()
    client.post("/auth/login", data={"email": "admin@admin.app", "password": "changeme"})


def wizard_payload():
    return {
        "building": {
            "name": "Κτίριο Δοκιμής",
            "address": "Οδός Δοκιμής 1",
            "postal_code": "12345",
        },
        "apartments": [
            {
                "key": "apartment-1",
                "number": "1",
                "name": "Α1",
                "floor": "1",
                "square_meters": "80.5",
                "owner": "Ιδιοκτήτης Α",
                "occupant": "Ένοικος Α",
            },
            {
                "key": "apartment-2",
                "number": "2",
                "name": "Α2",
                "floor": "1",
                "square_meters": "70",
                "owner": "Ιδιοκτήτης Β",
                "occupant": "",
            },
        ],
        "categories": [
            {"key": "category-1", "name": "Θέρμανση"},
            {"key": "category-2", "name": "Ανελκυστήρας"},
        ],
        "millesimals": {
            "category-1": {"apartment-1": "600", "apartment-2": "400"},
            "category-2": {"apartment-1": "500", "apartment-2": "500"},
        },
    }


def test_building_wizard_creates_complete_graph_atomically(app, client):
    login_admin(app, client)

    response = client.post("/buildings/new", data={"wizard_payload": json.dumps(wizard_payload())})

    assert response.status_code == 302
    with app.app_context():
        building = db.session.scalar(db.select(Building))
        assert response.headers["Location"].endswith(f"/buildings/{building.id}")
        assert building.name == "Κτίριο Δοκιμής"
        assert len(building.apartments) == 2
        assert len(building.categories) == 2
        assert db.session.scalar(db.select(db.func.count(Millesimal.id))) == 4
        assert db.session.scalar(db.select(db.func.sum(Millesimal.value))) == 2000
        membership = db.session.scalar(db.select(BuildingMembership))
        assert membership.building_id == building.id
        assert membership.role == "building_admin"
        assert db.session.scalar(db.select(AuditLog)).action == "building_created"


def test_building_wizard_uses_normal_form_submission(app, client):
    login_admin(app, client)

    response = client.get("/buildings/new")

    assert response.status_code == 200
    assert 'data-wizard-form hx-boost="false"' in response.text


def test_building_wizard_rolls_back_and_preserves_payload_on_late_error(app, client):
    login_admin(app, client)
    payload = wizard_payload()
    payload["millesimals"]["category-2"]["apartment-2"] = "400"

    response = client.post(
        "/buildings/new",
        data={"wizard_payload": json.dumps(payload)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'data-initial-step="3"' in response.text
    assert "έχει 900 αντί για 1000 χιλιοστά" in response.text
    serialized_payload = re.search(
        r'<script type="application/json" data-wizard-initial>(.*?)</script>', response.text
    ).group(1)
    assert json.loads(serialized_payload) == payload
    with app.app_context():
        for model in (
            Building,
            BuildingMembership,
            Apartment,
            ExpenseCategory,
            Millesimal,
            AuditLog,
        ):
            assert db.session.scalar(db.select(db.func.count(model.id))) == 0


def test_wizard_accepts_empty_floor_as_ground_floor(app, client):
    login_admin(app, client)
    payload = wizard_payload()
    payload["apartments"][0]["floor"] = ""

    response = client.post("/buildings/new", data={"wizard_payload": json.dumps(payload)})

    assert response.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Apartment.floor).where(Apartment.name == "Α1")) == 0


def test_wizard_script_is_loaded_once_from_base_layout(app, client):
    login_admin(app, client)
    page = client.get("/buildings/new").text
    assert page.count("building_wizard.js") == 1
    assert "building_wizard.js" in client.get("/").text


def test_millesimal_matrix_footer_shows_total_and_remainder(app, client):
    login_admin(app, client)
    client.post("/buildings/new", data={"wizard_payload": json.dumps(wizard_payload())})
    with app.app_context():
        building = db.session.scalar(db.select(Building))
        apartment = db.session.scalar(db.select(Apartment).where(Apartment.name == "Α1"))
        heating = db.session.scalar(
            db.select(ExpenseCategory).where(ExpenseCategory.name == "Θέρμανση")
        )
        url = f"/buildings/{building.id}/millesimals"
        field = f"m_{apartment.id}_{heating.id}"

    page = client.get(url).text
    assert page.count("✓ σωστό") == 2

    # a rejected save re-renders the typed values: 450 + 400 → 850, 150 left
    form = {key: value for key, value in re.findall(r'name="(m_\d+_\d+)"[^>]*value="(\d+)"', page)}
    form[field] = "450"
    page = client.post(url, data=form).text
    assert "<strong>850</strong><small>Υπόλοιπο 150</small>" in page


def test_form_errors_never_show_database_or_parser_internals(app, client):
    from decimal import InvalidOperation

    from sqlalchemy.exc import IntegrityError

    from koinoxrista.main import form_error

    assert form_error(IntegrityError("stmt", {}, Exception("UNIQUE")), "διπλό") == "διπλό"
    assert form_error(ValueError("invalid literal for int() with base 10: 'x'"), "-") == (
        "Κάποιο αριθμητικό πεδίο δεν είναι έγκυρο."
    )
    assert form_error(InvalidOperation(), "-") == "Κάποιο αριθμητικό πεδίο δεν είναι έγκυρο."
    assert form_error(ValueError("Η ονομασία είναι υποχρεωτική."), "-") == (
        "Η ονομασία είναι υποχρεωτική."
    )

    login_admin(app, client)
    client.post("/buildings/new", data={"wizard_payload": json.dumps(wizard_payload())})
    with app.app_context():
        apartment = db.session.scalar(db.select(Apartment).where(Apartment.name == "Α2"))
        apartment_id = apartment.id
    page = client.post(
        f"/apartments/{apartment_id}/edit",
        data={"name": "Α2", "number": "1", "floor": "0", "square_meters": "70"},
    ).text
    assert "Υπάρχει ήδη διαμέρισμα με αυτόν τον αριθμό." in page
    assert "IntegrityError" not in page
