"""Open Graph link-preview helpers (ADR-0040): ISK/copy formatting (domain) and the
<meta>-tag assembly + index.html injection (interface). No DB — the /a/{public_id} route
is exercised end-to-end in test_appraisals.py."""

from decimal import Decimal

from app.domain.og import appraisal_preview_copy, format_isk
from app.interface.og import _index_html, _meta_tags, _render

# --- domain: format_isk ---


def test_format_isk_thousands_separated_whole_isk():
    assert format_isk(Decimal("4500.00")) == "4,500 ISK"
    assert format_isk(Decimal("1230000")) == "1,230,000 ISK"


def test_format_isk_rounds_bankers_to_whole():
    assert format_isk(Decimal("1230000.60")) == "1,230,001 ISK"  # rounds up
    assert format_isk(Decimal("0.5")) == "0 ISK"  # half-even → nearest even (0)
    assert format_isk(Decimal("1.5")) == "2 ISK"  # half-even → nearest even (2)


# --- domain: appraisal_preview_copy ---


def test_copy_includes_value_and_location():
    title, description = appraisal_preview_copy(
        Decimal("1230000.00"), "Jita IV - Moon 4 - CNAP"
    )
    assert title == "1,230,000 ISK · Buyback appraisal"
    assert "Jita IV - Moon 4 - CNAP" in description
    assert description.startswith("Drop-off at ")


def test_copy_without_location_omits_drop_off():
    title, description = appraisal_preview_copy(Decimal("100"), None)
    assert title == "100 ISK · Buyback appraisal"
    assert "Drop-off" not in description


# --- interface: meta-tag escaping ---


def test_meta_tags_escape_user_content():
    meta = _meta_tags('Evil "</title>" <script>', "desc & more")
    assert "<script>" not in meta
    assert "&lt;script&gt;" in meta
    assert "&quot;" in meta
    assert "desc &amp; more" in meta
    # The OG/Twitter properties are present.
    assert 'property="og:title"' in meta
    assert 'property="og:description"' in meta
    assert 'name="twitter:card"' in meta


# --- interface: injection into index.html ---


def test_render_injects_before_head_close(tmp_path):
    (tmp_path / "index.html").write_text(
        "<html><head><title>x</title></head><body>app</body></html>", encoding="utf-8"
    )
    _index_html.cache_clear()
    out = _render("<meta data-og />", str(tmp_path))
    assert "<meta data-og /></head>" in out
    assert "<body>app</body>" in out  # the SPA shell is preserved


def test_render_falls_back_to_minimal_shell_without_build():
    _index_html.cache_clear()
    out = _render("<meta data-og />", "")  # no static_dir → no index.html on disk
    assert out.startswith("<!doctype html>")
    assert "<meta data-og />" in out
