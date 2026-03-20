"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.enricher import enrich_jats

_STATIC = Path(__file__).parent / "static"
_UI_PAGE = (_STATIC / "index.html").read_text()

app = FastAPI(title="jats-ref-refinery", version="0.1.0")
app.mount("/static", StaticFiles(directory=_STATIC, html=False), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def ui():
    """Browser upload UI."""
    return _UI_PAGE


@app.get("/health")
async def health():
    """Kubernetes liveness probe."""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Kubernetes readiness probe."""
    return {"status": "ready"}


@app.post(
    "/enrich",
    response_class=PlainTextResponse,
    responses={
        200: {
            "content": {"application/xml": {}},
            "description": "Enriched JATS XML",
        },
        422: {"description": "Invalid or unparseable XML"},
    },
)
async def enrich(request: Request) -> Response:
    """Accept a JATS XML package and return it enriched with DOIs and PMIDs."""
    body = await request.body()
    enriched_xml = await enrich_jats(body)
    return Response(
        content=enriched_xml,
        media_type="application/xml",
        status_code=status.HTTP_200_OK,
    )
