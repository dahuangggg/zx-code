from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parent

app = FastAPI(title="Python AST Visualizer Demo")


def _read_python_file(rel_path: str) -> str:
    # Restrict reads to demo directory for safety.
    demo_root = ROOT
    target = (demo_root / rel_path).resolve()
    if demo_root not in target.parents and target != demo_root:
        raise HTTPException(status_code=400, detail="path must stay within demo directory")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {rel_path}")
    if target.suffix != ".py":
        raise HTTPException(status_code=400, detail="only .py files are supported")
    return target.read_text(encoding="utf-8")


def _node_label(n: ast.AST) -> str:
    # A compact label showing node type + a few useful fields.
    t = type(n).__name__
    parts: list[str] = [t]
    for key in ("name", "id", "arg", "attr"):
        if hasattr(n, key):
            v = getattr(n, key)
            if isinstance(v, str) and v:
                parts.append(f"{key}={v}")
                break
    if hasattr(n, "lineno") and hasattr(n, "col_offset"):
        parts.append(f"@{getattr(n,'lineno')}:{getattr(n,'col_offset')}")
    return " ".join(parts)


def _ast_to_tree(n: Any) -> Any:
    # Convert AST nodes/lists/primitives into a JSON-serializable tree structure
    # suitable for UI rendering.
    if isinstance(n, ast.AST):
        children = []
        for field, value in ast.iter_fields(n):
            child = _ast_to_tree(value)
            if child is None:
                continue
            children.append({"field": field, "value": child})
        return {"type": type(n).__name__, "label": _node_label(n), "children": children}

    if isinstance(n, list):
        out = []
        for i, item in enumerate(n):
            child = _ast_to_tree(item)
            if child is None:
                continue
            out.append({"index": i, "value": child})
        return {"type": "list", "label": f"list[{len(n)}]", "children": out}

    if isinstance(n, (str, int, float, bool)) or n is None:
        return {"type": "value", "label": repr(n), "value": n}

    # Fallback for unexpected types
    return {"type": "value", "label": repr(n)}


@app.get("/", response_class=HTMLResponse)
def index(file: str | None = Query(default=None, description="Relative .py path under demo/")):
    # default file: a bundled example
    default = "example.py"
    file = file or default

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Python AST Visualizer</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system; margin: 0; }}
    header {{ padding: 12px 16px; background: #111827; color: #fff; display:flex; gap:12px; align-items:center; }}
    header input {{ flex: 1; padding: 8px 10px; border-radius: 6px; border: 1px solid #374151; background:#0b1220; color:#fff; }}
    header button {{ padding: 8px 10px; border-radius: 6px; border: 1px solid #374151; background:#1f2937; color:#fff; cursor:pointer; }}
    main {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0; height: calc(100vh - 52px); }}
    .pane {{ overflow: auto; padding: 12px 16px; }}
    .code {{ background:#0b1220; color:#e5e7eb; border-radius: 8px; padding: 12px; white-space: pre; font-family: ui-monospace, SFMono-Regular; font-size: 12px; }}
    details {{ margin-left: 12px; }}
    summary {{ cursor: pointer; }}
    .meta {{ color:#6b7280; font-size: 12px; margin-top: 8px; }}
    .err {{ color: #b91c1c; }}
  </style>
</head>
<body>
  <header>
    <div style="font-weight:600;">AST Visualizer</div>
    <input id="file" value="{file}" placeholder="example.py" />
    <button onclick="loadAll()">Load</button>
  </header>
  <main>
    <div class="pane">
      <div style="font-weight:600; margin-bottom: 8px;">Source</div>
      <div id="source" class="code">Loading...</div>
      <div class="meta">Reads only files under <code>demo/</code>. Try <code>example.py</code>.</div>
    </div>
    <div class="pane">
      <div style="font-weight:600; margin-bottom: 8px;">AST</div>
      <div id="tree"></div>
      <div id="error" class="meta err"></div>
    </div>
  </main>

<script>
async function fetchJSON(url) {{
  const res = await fetch(url);
  const text = await res.text();
  let data = null;
  try {{ data = JSON.parse(text); }} catch (e) {{}}
  if (!res.ok) {{
    const msg = data && data.detail ? data.detail : text;
    throw new Error(msg);
  }}
  return data;
}}

function el(tag, attrs={{}}, ...children) {{
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {{
    if (k === 'class') e.className = v;
    else e.setAttribute(k, v);
  }}
  for (const c of children) {{
    if (c == null) continue;
    if (typeof c === 'string') e.appendChild(document.createTextNode(c));
    else e.appendChild(c);
  }}
  return e;
}}

function renderNode(node) {{
  // node: {{type,label,children}} or list/value wrappers
  const label = node.label || node.type;

  if (!node.children || node.children.length === 0) {{
    return el('div', {{}}, label);
  }}

  const details = el('details', {{ open: false }});
  const summary = el('summary', {{}}, label);
  details.appendChild(summary);

  const container = el('div', {{}});
  for (const child of node.children) {{
    if (child.field !== undefined) {{
      const row = el('div', {{}},
        el('div', {{style:'color:#6b7280; font-size:12px;'}}, child.field),
        renderNode(child.value)
      );
      container.appendChild(row);
    }} else if (child.index !== undefined) {{
      const row = el('div', {{}},
        el('div', {{style:'color:#6b7280; font-size:12px;'}}, '['+child.index+']'),
        renderNode(child.value)
      );
      container.appendChild(row);
    }} else {{
      container.appendChild(renderNode(child));
    }}
  }}
  details.appendChild(container);
  return details;
}}

async function loadAll() {{
  const file = document.getElementById('file').value || 'example.py';
  document.getElementById('error').textContent = '';
  document.getElementById('source').textContent = 'Loading...';
  document.getElementById('tree').innerHTML = '';

  try {{
    const src = await fetchJSON(`/api/source?file=${encodeURIComponent(file)}`);
    document.getElementById('source').textContent = src.source;

    const astTree = await fetchJSON(`/api/ast?file=${encodeURIComponent(file)}`);
    document.getElementById('tree').appendChild(renderNode(astTree));
  }} catch (e) {{
    document.getElementById('error').textContent = String(e.message || e);
  }}
}}

loadAll();
</script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/api/source")
def api_source(file: str = Query(...)):
    return {"file": file, "source": _read_python_file(file)}


@app.get("/api/ast")
def api_ast(file: str = Query(...)):
    src = _read_python_file(file)
    try:
        tree = ast.parse(src, filename=file, type_comments=True)
    except SyntaxError as e:
        raise HTTPException(status_code=400, detail=f"SyntaxError: {e.msg} at line {e.lineno}:{e.offset}")
    return JSONResponse(_ast_to_tree(tree))


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=True)
