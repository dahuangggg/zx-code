# Python AST Visualizer (demo)

A tiny FastAPI app that renders a collapsible AST tree for a Python file.

## Run

From repo root:

```bash
cd demo
python -m venv .venv && source .venv/bin/activate
pip install fastapi uvicorn
python app.py
```

Open: http://127.0.0.1:8000

## Notes

- The UI reads only files under `demo/` for safety.
- Try `example.py` (default).
