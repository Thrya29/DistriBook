import pytest

from distribook.services.catalog_service import row_label, seat_labels


@pytest.mark.parametrize(
    ("index", "label"), [(0, "A"), (25, "Z"), (26, "AA"), (27, "AB"), (701, "ZZ"), (702, "AAA")]
)
def test_row_label(index: int, label: str) -> None:
    assert row_label(index) == label


def test_row_label_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        row_label(-1)


def test_seat_labels_are_row_major_and_unique() -> None:
    labels = seat_labels(rows=2, seats_per_row=3)
    assert labels == ["A1", "A2", "A3", "B1", "B2", "B3"]
    assert len(set(seat_labels(rows=100, seats_per_row=100))) == 10_000
