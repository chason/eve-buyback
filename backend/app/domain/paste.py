"""Parse a pasted EVE item list into (name, quantity) pairs. Pure — name→type_id
resolution happens in the application layer against the SDE (ADR-0021)."""

import re
from dataclasses import dataclass

# Ceiling for a single line's quantity — rejects absurd input while staying well
# under BIGINT. Mirrored by `AppraisalItemIn.quantity` (le=) for structured items.
MAX_QUANTITY = 1_000_000_000_000

# A trailing quantity: whitespace, an optional `x`, then a run of digits that may
# carry thousands separators (`,` `.` space / non-breaking space — EVE's number
# format is locale-dependent). Item names ending in letters (e.g. "Warp Disruptor
# II") simply don't match, so they fall through to quantity 1.
_TRAILING_QTY = re.compile(r"^(.*?)\s+(?:x\s*)?(\d[\d.,\s]*)$", re.IGNORECASE)
_SEPARATORS = re.compile(r"[.,\s]")


@dataclass(frozen=True)
class ParsedLine:
    name: str
    quantity: int


def parse_paste(text: str) -> list[ParsedLine]:
    """Parse a multi-line paste. Supports the in-game inventory copy (tab-separated
    `name<TAB>qty<TAB>…`) and the multibuy form (`name qty` / `name xqty` / bare
    `name`). Blank lines are skipped; an unparseable quantity defaults to 1."""
    result: list[ParsedLine] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        name, quantity = _parse_line(line)
        if name:
            result.append(
                ParsedLine(name=name, quantity=min(quantity, MAX_QUANTITY))
            )
    return result


def _parse_line(line: str) -> tuple[str, int]:
    if "\t" in line:
        parts = line.split("\t")
        qty = _to_int(parts[1]) if len(parts) > 1 else None
        return parts[0].strip(), (qty if qty and qty > 0 else 1)

    match = _TRAILING_QTY.match(line)
    if match:
        name, qty = match.group(1).strip(), _to_int(match.group(2))
        if name and qty and qty > 0:
            return name, qty
    return line, 1


def _to_int(value: str) -> int | None:
    digits = _SEPARATORS.sub("", value)
    return int(digits) if digits.isdigit() else None
