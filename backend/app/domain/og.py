"""Pure helpers for the public appraisal link-unfurl preview (ADR-0040): format the ISK
value and compose the Open Graph title/description. No I/O and no HTML here — the
interface layer escapes these plain strings into <meta> tags."""

from decimal import ROUND_HALF_EVEN, Decimal

_ISK = Decimal(1)


def format_isk(value: Decimal) -> str:
    """A thousands-separated, whole-ISK amount with the ISK suffix, e.g. ``1,230,000 ISK``.
    Rounded to whole ISK (banker's rounding, ADR-0020) — a link preview doesn't need the
    decimals."""
    whole = value.quantize(_ISK, rounding=ROUND_HALF_EVEN)
    return f"{whole:,} ISK"


def appraisal_preview_copy(total: Decimal, location_name: str | None) -> tuple[str, str]:
    """The (title, description) for a shared appraisal's link preview. Deliberately limited
    to the total value and drop-off location — no character or item details (ADR-0040)."""
    title = f"{format_isk(total)} · Buyback appraisal"
    if location_name:
        description = (
            f"Drop-off at {location_name}. Open the link for the full itemized quote."
        )
    else:
        description = "Open the link for the full itemized quote."
    return title, description
