# jats-ref-refinery

A Python microservice that enriches bibliographic references in JATS XML with PIDs.

It accepts a JATS XML file, resolves each `<ref>` element against CrossRef and DataCite, and injects `<pub-id>` elements where a confident match is found.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) or Docker

## Configuration

(Optional) Populate `.env` to make use of higher API rate limits.

When running locally without Docker, export the variables directly:

```bash
export CROSSREF_MAILTO=you@example.com
export OPENALEX_API_KEY=your-key-here
```

---

## Using it as an app / API

### Installation

#### With Docker (recommended)

```bash
docker compose up --build
```

The service starts at `http://localhost:8000` with hot-reload enabled.

#### Locally

```bash
uv sync --extra serve
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### HTTP API

#### `POST /enrich`

Submit a JATS XML file. Returns the enriched XML with DOIs injected.

```bash
curl -X POST http://localhost:8000/enrich \
  --data-binary @your-article.xml \
  -o enriched.xml
```

A resolved reference gets a `<pub-id>` element added inside the `<mixed-citation>` or `<element-citation>`:

```xml
<mixed-citation>
  ...
  <pub-id pub-id-type="doi">10.7554/eLife.105209</pub-id>
  <pub-id pub-id-type="pmid">41416774</pub-id>
</mixed-citation>
```

#### `GET /health`

Kubernetes liveness probe. Returns `{"status": "ok"}`.

#### `GET /ready`

Kubernetes readiness probe. Returns `{"status": "ready"}`.

---

## Importing it as a library

Install the core package (no FastAPI/uvicorn) directly from GitHub:

```bash
pip install "jats-ref-refinery @ git+https://github.com/elifesciences/jats-ref-refinery.git"
```

Or with `uv`:

```bash
uv add "jats-ref-refinery @ git+https://github.com/elifesciences/jats-ref-refinery.git"
```

Then call `enrich_jats` directly:

```python
import asyncio
from app.enricher import enrich_jats

with open("article.xml", "rb") as f:
    enriched = asyncio.run(enrich_jats(f.read()))

with open("enriched.xml", "wb") as f:
    f.write(enriched)
```

---

## Running tests

```bash
uv run pytest
```
