"""FastAPI application entry point."""

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.enricher import enrich_jats

app = FastAPI(title="jats-ref-refinery", version="0.1.0")


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
    """Accept a JATS XML package and return it enriched with DOIs."""
    body = await request.body()
    enriched_xml = await enrich_jats(body)
    return Response(
        content=enriched_xml,
        media_type="application/xml",
        status_code=status.HTTP_200_OK,
    )
