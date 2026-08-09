from decimal import Decimal

import pytest

from koinoxrista.models import Apartment, Building
from koinoxrista.services import allocate_amount, validate_millesimal_values


class Item:
    def __init__(self, item_id, name=""):
        self.id = item_id
        self.name = name


def test_allocation_is_exact_and_deterministic():
    result = allocate_amount(Decimal("10.00"), [(1, 333), (2, 333), (3, 334)])
    assert result == {1: Decimal("3.33"), 2: Decimal("3.33"), 3: Decimal("3.34")}
    assert sum(result.values()) == Decimal("10.00")


def test_negative_allocation_is_exact():
    result = allocate_amount(Decimal("-1.01"), [(1, 500), (2, 500)])
    assert result == {1: Decimal("-0.51"), 2: Decimal("-0.50")}
    assert sum(result.values()) == Decimal("-1.01")


def test_millesimals_require_exactly_one_thousand():
    apartments = [Item(1), Item(2)]
    categories = [Item(10, "Θέρμανση")]
    assert validate_millesimal_values(apartments, categories, {(1, 10): 600, (2, 10): 400})
    with pytest.raises(ValueError, match="999"):
        validate_millesimal_values(apartments, categories, {(1, 10): 600, (2, 10): 399})


def test_apartment_display_label_uses_building_preference_and_fallback():
    building = Building(apartment_display_mode="occupant")
    apartment = Apartment(
        building=building,
        number=6,
        name="Διαμέρισμα 5",
        owner="Ιδιοκτήτης",
        occupant="Λάζαρος Θεόδωρος",
    )

    assert apartment.display_label() == "6. Λάζαρος Θεόδωρος"
    assert apartment.display_label("owner") == "6. Ιδιοκτήτης"
    assert apartment.display_label("name") == "6. Διαμέρισμα 5"
    apartment.occupant = ""
    assert apartment.display_label() == "6. Διαμέρισμα 5"
