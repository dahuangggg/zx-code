# FastAPI Demo

A small FastAPI demo app with:
- health check (with server time)
- echo endpoint
- request-id + timing middleware
- enhanced in-memory CRUD (filter/sort/pagination, PATCH, DELETE)
- a protected admin endpoint (API key)

## Install

From repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install "fastapi>=0.110" "uvicorn[standard]>=0.27"
```

## Run

```bash
uvicorn demo.fastapi_demo.main:app --reload --port 8000
```

Open:
- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs

## Quick test

```bash
# health
curl -s http://127.0.0.1:8000/health | jq

# echo
curl -s -X POST http://127.0.0.1:8000/echo \
  -H 'content-type: application/json' \
  -d '{"message":"hello"}' | jq

# create items
curl -s -X POST http://127.0.0.1:8000/items \
  -H 'content-type: application/json' \
  -d '{"name":"apple","price":3.5,"tags":["fruit"],"in_stock":true}' | jq
curl -s -X POST http://127.0.0.1:8000/items \
  -H 'content-type: application/json' \
  -d '{"name":"banana","price":2.0,"tags":["fruit"],"in_stock":false}' | jq

# list with filter/sort/pagination
curl -s 'http://127.0.0.1:8000/items?q=app&sort=price&order=desc&offset=0&limit=10' | jq

# patch
curl -s -X PATCH http://127.0.0.1:8000/items/1 \
  -H 'content-type: application/json' \
  -d '{"price":4.2,"in_stock":false}' | jq

# delete
curl -i -X DELETE http://127.0.0.1:8000/items/2

# protected endpoint (API key: demo-key)
curl -s http://127.0.0.1:8000/admin/summary -H 'x-api-key: demo-key' | jq

# whoami (also shows x-request-id header echoed back)
curl -i http://127.0.0.1:8000/whoami
```

## Notes

- This demo uses an in-memory dict as a database; restarting the server resets data.
- The admin endpoint uses a hard-coded API key `demo-key` for demonstration only.
