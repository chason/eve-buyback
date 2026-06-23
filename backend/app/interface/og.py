"""Server-rendered Open Graph tags for shared appraisal links (ADR-0040).

Link-unfurlers (Discord, Slack, Twitter) don't run JavaScript — they read static
``<meta>`` tags from the HTML ``<head>``. This route intercepts the SPA's appraisal URL
(``/a/{public_id}``), injects the appraisal's value + drop-off location as OG tags into
the built ``index.html``, and still serves the whole SPA so a human visitor's browser
hydrates the real page. Registered **before** the ``/`` SPA mount so it takes priority.

Only the total value and drop-off location are exposed — never character or item details
(the public read is unauthenticated, so the link itself is the only capability; ADR-0040).
"""

import html
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from starlette.responses import HTMLResponse

from app.application import appraisals as appraisals_app
from app.config import get_settings
from app.domain.og import appraisal_preview_copy
from app.interface.deps import SessionDep

router = APIRouter()

_SITE_NAME = "BUYBACK"
_DEFAULT_TITLE = f"{_SITE_NAME} // Corp Logistics"
_DEFAULT_DESCRIPTION = (
    "An EVE Online corporation buyback console — instant priced quotes against live "
    "market data."
)

# Used only when the built SPA isn't on disk (e.g. backend-only dev, where Vite serves
# the real SPA, or tests). Crawlers still get valid OG tags; humans get an empty body
# they'd never normally reach.
_FALLBACK_SHELL = "<!doctype html><html><head>{meta}</head><body></body></html>"


@lru_cache(maxsize=1)
def _index_html(static_dir: str) -> str | None:
    """The built ``index.html`` text, read once and cached. None if there's no build on
    disk. ``static_dir`` is the cache key so a config change re-reads."""
    if not static_dir:
        return None
    path = Path(static_dir) / "index.html"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _meta_tags(title: str, description: str) -> str:
    """The escaped OG/Twitter ``<meta>`` block. index.html ships no OG tags, so injecting
    these before ``</head>`` can't create duplicates."""
    t = html.escape(title, quote=True)
    d = html.escape(description, quote=True)
    return (
        '<meta property="og:type" content="website" />'
        f'<meta property="og:site_name" content="{_SITE_NAME}" />'
        f'<meta property="og:title" content="{t}" />'
        f'<meta property="og:description" content="{d}" />'
        '<meta name="twitter:card" content="summary" />'
        f'<meta name="twitter:title" content="{t}" />'
        f'<meta name="twitter:description" content="{d}" />'
    )


def _render(meta: str, static_dir: str) -> str:
    shell = _index_html(static_dir)
    if shell is None:
        return _FALLBACK_SHELL.format(meta=meta)
    return shell.replace("</head>", f"{meta}</head>", 1)


@router.get("/a/{public_id}", response_class=HTMLResponse, include_in_schema=False)
async def appraisal_link_preview(public_id: str, session: SessionDep) -> HTMLResponse:
    """Serve the SPA shell for an appraisal URL with its preview ``<meta>`` tags injected.
    Unknown ids fall back to the generic site card (the client router shows its own 404)."""
    preview = await appraisals_app.get_appraisal_preview(session, public_id=public_id)
    if preview is None:
        title, description = _DEFAULT_TITLE, _DEFAULT_DESCRIPTION
    else:
        title, description = appraisal_preview_copy(
            preview.accepted_total, preview.delivery_location_name
        )
    return HTMLResponse(_render(_meta_tags(title, description), get_settings().static_dir))
