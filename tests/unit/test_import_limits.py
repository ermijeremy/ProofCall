import pytest

from app.core.import_limits import MAX_CSV_BYTES, MAX_CSV_COLUMNS, MAX_CSV_FIELD_CHARS, MAX_CSV_ROWS
from app.services.import_service import _csv_rows


def test_bounded_csv_keeps_bom_quotes_newlines_and_optional_blanks():
    content = '\ufeffname,contact,notes\n"Test, Person",+251900000001,"two\nlines"\nOther,+251900000002,\n'
    rows = list(_csv_rows(content.encode()))
    assert rows[0] == {"name": "Test, Person", "contact": "+251900000001", "notes": "two\nlines"}
    assert rows[1]["notes"] == ""


@pytest.mark.parametrize("content,message", [
    (b"a" * (MAX_CSV_BYTES + 1), "1 MiB"),
    (b"name\n" + b"a\n" * (MAX_CSV_ROWS + 1), "data rows"),
    ((",".join(f"col{i}" for i in range(MAX_CSV_COLUMNS + 1)) + "\n").encode(), "columns"),
    (("x" * (MAX_CSV_FIELD_CHARS + 1) + "\n").encode(), "characters"),
    (("name\n" + "x" * (MAX_CSV_FIELD_CHARS + 1) + "\n").encode(), "characters"),
    (b'name\n"unterminated\n', "Invalid CSV"),
])
def test_csv_limits_raise_a_readable_error_before_returning_rows(content, message):
    with pytest.raises(ValueError, match=message):
        _csv_rows(content)


def test_row_and_field_limits_are_inclusive():
    assert len(list(_csv_rows(b"name\n" + b"a\n" * MAX_CSV_ROWS))) == MAX_CSV_ROWS
    assert list(_csv_rows(("name\n" + "a" * MAX_CSV_FIELD_CHARS).encode()))[0]["name"] == "a" * MAX_CSV_FIELD_CHARS
