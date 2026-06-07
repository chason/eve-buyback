from app.domain.paste import MAX_QUANTITY, ParsedLine, parse_paste


def test_multibuy_name_space_qty():
    assert parse_paste("Tritanium 1000") == [ParsedLine("Tritanium", 1000)]


def test_thousands_separators():
    assert parse_paste("Tritanium 1,000,000") == [ParsedLine("Tritanium", 1000000)]
    assert parse_paste("Tritanium 1 000") == [ParsedLine("Tritanium", 1000)]


def test_x_prefixed_quantity():
    assert parse_paste("Pyerite x500") == [ParsedLine("Pyerite", 500)]
    assert parse_paste("Pyerite x 500") == [ParsedLine("Pyerite", 500)]


def test_bare_name_defaults_to_one():
    assert parse_paste("Warp Disruptor II") == [ParsedLine("Warp Disruptor II", 1)]


def test_name_with_trailing_qty_keeps_full_name():
    # The name has internal spaces and ends in a roman numeral, then a quantity.
    assert parse_paste("Medium Shield Extender II 5") == [
        ParsedLine("Medium Shield Extender II", 5)
    ]


def test_inventory_copy_tab_separated():
    # In-game copy: name<TAB>qty<TAB>group<TAB>… with a thousands separator.
    line = "Tritanium\t1,000\tMineral\tMaterial\t0.01 m3"
    assert parse_paste(line) == [ParsedLine("Tritanium", 1000)]


def test_tab_separated_without_quantity_defaults_to_one():
    assert parse_paste("Tritanium\t") == [ParsedLine("Tritanium", 1)]


def test_blank_lines_skipped_and_multiline():
    text = "Tritanium 1000\n\n   \nPyerite x2\n"
    assert parse_paste(text) == [
        ParsedLine("Tritanium", 1000),
        ParsedLine("Pyerite", 2),
    ]


def test_empty_input():
    assert parse_paste("") == []
    assert parse_paste("   \n  \n") == []


def test_quantity_clamped_to_max():
    # Absurd quantities are clamped so they stay well under BIGINT (ADR-0024).
    [line] = parse_paste("Tritanium 99999999999999999999999")
    assert line.quantity == MAX_QUANTITY
