# jats-ref-refinery

A Python microservice that enriches bibliographic references in JATS XML with PIDs.

It accepts a JATS XML package, resolves each `<ref>` element against CrossRef and DataCite, and injects `<pub-id>` elements where a confident match is found.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or Docker

## Installation

### Local (without Docker)

```bash
uv sync
```

### With Docker

```bash
docker compose build
```

## Configuration

(Optional) Set the `CROSSREF_MAILTO` environment variable before running (to use the CrossRef polite pool).

```bash
export CROSSREF_MAILTO=you@example.com
```

## Running the service

### With Docker Compose (recommended)

```bash
docker compose up
```

The service starts at `http://localhost:8000` with hot-reload enabled.

### Locally

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API

### `POST /enrich`

Submit a JATS XML file. Returns the enriched XML with DOIs injected.

**Example**

```bash
curl -X POST http://localhost:8000/enrich \
  --data-binary @your-article.xml \
  -o enriched.xml
```

A resolved reference gets a `<pub-id>` element added inside the `<mixed-citation>` or `<element-citation>`:

```xml
<mixed-citation>
  ...
  <pub-id pub-id-type="doi">10.1038/s41586-021-03819-2</pub-id>
</mixed-citation>
```

### `GET /health`

Kubernetes liveness probe. Returns `{"status": "ok"}`.

### `GET /ready`

Kubernetes readiness probe. Returns `{"status": "ready"}`.

## Running tests

```bash
uv run pytest
```
