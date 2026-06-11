"""FastAPI app for the live re-agent web UI."""
# ruff: noqa: E501
from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Any

from re_agent.config.loader import load_config
from re_agent.core.models import FunctionTarget
from re_agent.core.session import Session
from re_agent.reports.formatter import format_result
from re_agent.runtime.events import (
    EventSink,
    JsonlEventSink,
    RuntimeEvent,
    TeeEventSink,
    emit_event,
    make_jsonable,
    reset_event_sink,
    set_event_sink,
)
from re_agent.runtime.tracing import TracedBackend, TracedLLMProvider


class WebEventHub(EventSink):
    """In-memory event broadcaster for WebSocket clients."""

    def __init__(self, max_history: int = 1000) -> None:
        self.max_history = max_history
        self.history: list[dict[str, Any]] = []
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
        normalized_payload = make_jsonable(payload or {})
        event = RuntimeEvent(
            id=uuid.uuid4().hex,
            run_id=normalized_payload.get("run_id"),
            type=event_type,
            timestamp=time.time(),
            payload=normalized_payload,
        )
        data = asdict(event)
        with self._lock:
            self.history.append(data)
            if len(self.history) > self.max_history:
                self.history = self.history[-self.max_history :]
            clients = list(self.clients)

        if self.loop and clients:
            for queue in clients:
                self.loop.call_soon_threadsafe(queue.put_nowait, data)
        return event

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self.clients.add(queue)
            history = list(self.history)
        for event in history:
            await queue.put(event)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self.clients.discard(queue)


class RunManager:
    """Owns background reversing runs launched from the web UI."""

    def __init__(self, config_path: str | Path, hub: WebEventHub) -> None:
        self.config_path = Path(config_path)
        self.hub = hub
        self.runs: dict[str, dict[str, Any]] = {}
        self._active_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self._lock:
            if self._active_thread and self._active_thread.is_alive():
                return 409, {"error": "A run is already active."}

            run_id = uuid.uuid4().hex
            run = {
                "id": run_id,
                "request": request,
                "status": "queued",
                "started_at": time.time(),
                "finished_at": None,
                "exit_code": None,
            }
            self.runs[run_id] = run
            thread = threading.Thread(target=self._run, args=(run_id, request), daemon=True)
            self._active_thread = thread
            thread.start()
            return 202, run

    def list_runs(self) -> list[dict[str, Any]]:
        return sorted(self.runs.values(), key=lambda item: item["started_at"], reverse=True)

    def reset(self) -> tuple[int, dict[str, Any]]:
        """Wipe session progress and delete all auto-generated outputs."""
        with self._lock:
            if self._active_thread and self._active_thread.is_alive():
                return 409, {"error": "A run is active; wait for it to finish before resetting."}

        import shutil

        from re_agent.orchestrator.single import reset_b5_outputs

        config = load_config(self.config_path)
        removed = reset_b5_outputs()

        session_file = Path(config.output.session_file)
        if session_file.exists():
            session_file.write_text('{"functions": {}, "runs": []}', encoding="utf-8")

        code_dir = Path(config.output.report_dir) / "code"
        if code_dir.exists():
            shutil.rmtree(code_dir, ignore_errors=True)

        self.hub.emit("session.reset", {"removed_files": removed, "session_cleared": True})
        return 200, {"removed_files": removed, "session_cleared": True}

    def _run(self, run_id: str, request: dict[str, Any]) -> None:
        run = self.runs[run_id]
        run["status"] = "running"
        config = load_config(self.config_path)
        if request.get("max_rounds") is not None:
            config.orchestrator.max_review_rounds = int(request["max_rounds"])
        if request.get("skip_parity"):
            config.parity.enabled = False

        report_dir = Path(config.output.report_dir) / "web-runs" / run_id
        sink = TeeEventSink(
            JsonlEventSink(report_dir / "events.jsonl", run_id=run_id),
            _RunScopedHub(self.hub, run_id),
        )
        token = set_event_sink(sink)
        exit_code = 1
        try:
            sink.emit("run.started", {"run_id": run_id, "request": request, "report_dir": str(report_dir)})
            exit_code = self._execute(config, request)
            run["status"] = "completed" if exit_code == 0 else "failed"
            sink.emit("run.completed", {"run_id": run_id, "exit_code": exit_code, "status": run["status"]})
        except Exception as exc:
            run["status"] = "failed"
            sink.emit(
                "run.failed",
                {
                    "run_id": run_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            )
        finally:
            run["finished_at"] = time.time()
            run["exit_code"] = exit_code
            reset_event_sink(token)

    def _execute(self, config: Any, request: dict[str, Any]) -> int:
        from re_agent.backend.registry import create_backend
        from re_agent.llm.registry import create_provider

        backend = TracedBackend(create_backend(config.backend))
        base_llm = create_provider(config.llm)
        llm = TracedLLMProvider(base_llm, "reverser")
        checker_base = create_provider(config.checker_llm) if config.checker_llm else base_llm
        checker_llm = TracedLLMProvider(checker_base, "checker")
        session = Session(config.output.session_file)

        # Infer the mode from the filled-in fields so a stale Mode dropdown can
        # never produce "address is required" when the user clearly typed a
        # class (and vice versa).
        mode = request.get("mode")
        address_in = str(request.get("address") or "").strip()
        class_in = str(request.get("class_name") or "").strip()
        if mode == "address" and not address_in and class_in:
            mode = "class"
        elif mode == "class" and not class_in and address_in:
            mode = "address"
        elif mode not in ("address", "class"):
            mode = "address" if address_in else "class" if class_in else mode

        if mode == "address":
            from re_agent.orchestrator.single import reverse_single

            address = str(request.get("address") or "").strip()
            if not address:
                raise ValueError("address is required")
            class_name = str(request.get("class_name") or "").strip()
            function_name = str(request.get("function_name") or "").strip()

            if not class_name or not function_name:
                try:
                    dec = backend.decompile(address)
                    if dec.name and "::" in dec.name:
                        class_name, _, function_name = dec.name.rpartition("::")
                    elif dec.name and not function_name:
                        function_name = dec.name
                except Exception:
                    pass

            target = FunctionTarget(address=address, class_name=class_name, function_name=function_name)
            result = reverse_single(target, config, backend, llm, session, checker_llm=checker_llm)
            emit_event("run.result", {"result": format_result(result)})
            return 0 if result.success else 1

        if mode == "class":
            from re_agent.orchestrator.class_runner import reverse_class

            class_name = str(request.get("class_name") or "").strip()
            if not class_name:
                raise ValueError("class_name is required")
            max_functions = request.get("max_functions")
            results = reverse_class(
                class_name=class_name,
                config=config,
                backend=backend,
                llm=llm,
                session=session,
                max_functions=int(max_functions) if max_functions else None,
                checker_llm=checker_llm,
            )
            passed = sum(1 for result in results if result.success)
            emit_event(
                "run.result",
                {
                    "result": f"Results: {passed}/{len(results)} passed",
                    "passed": passed,
                    "total": len(results),
                },
            )
            return 0 if passed == len(results) else 1

        raise ValueError("mode must be 'address' or 'class'")


class _RunScopedHub(EventSink):
    """Adds a run_id to all events forwarded to the web hub."""

    def __init__(self, hub: WebEventHub, run_id: str) -> None:
        self.hub = hub
        self.run_id = run_id

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
        scoped = dict(payload or {})
        scoped.setdefault("run_id", self.run_id)
        return self.hub.emit(event_type, scoped)


def create_app(config_path: str | Path) -> Any:
    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    except ImportError as exc:  # pragma: no cover - exercised manually
        raise RuntimeError(
            "The web UI requires optional dependencies. Install with: pip install -e '.[web]'"
        ) from exc

    hub = WebEventHub()
    manager = RunManager(config_path, hub)
    app = FastAPI(title="re-agent live web UI")

    @app.on_event("startup")
    async def _startup() -> None:
        hub.attach_loop(asyncio.get_running_loop())

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @app.get("/api/runs")
    async def runs() -> JSONResponse:
        return JSONResponse(manager.list_runs())

    @app.get("/api/progress")
    async def progress() -> JSONResponse:
        config = load_config(Path(config_path))
        session = Session(config.output.session_file)
        return JSONResponse({"summary": session.get_summary(), "functions": session.get_all_functions()})

    @app.post("/api/runs")
    async def start_run(request: dict[str, Any]) -> JSONResponse:
        status, body = manager.start(request)
        return JSONResponse(body, status_code=status)

    @app.post("/api/reset")
    async def reset() -> JSONResponse:
        status, body = await asyncio.to_thread(manager.reset)
        return JSONResponse(body, status_code=status)

    @app.get("/api/search")
    async def search(q: str = "") -> JSONResponse:
        """Search the active backend for functions/classes matching ``q``."""
        q = q.strip()
        if len(q) < 2:
            return JSONResponse({"classes": [], "functions": []})

        def _search() -> dict[str, Any]:
            from re_agent.backend.registry import create_backend

            config = load_config(Path(config_path))
            backend = create_backend(config.backend)
            try:
                entries = backend.search(q)
            except Exception:
                return {"classes": [], "functions": []}
            classes = sorted({e.class_name for e in entries if e.class_name})[:30]
            functions = [
                {"address": e.address, "name": e.name, "class_name": e.class_name}
                for e in entries[:50]
            ]
            return {"classes": classes, "functions": functions}

        return JSONResponse(await asyncio.to_thread(_search))

    @app.get("/events")
    async def events() -> StreamingResponse:
        queue = await hub.subscribe()

        async def stream() -> Any:
            try:
                while True:
                    event = await queue.get()
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                hub.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        queue = await hub.subscribe()
        try:
            while True:
                event = await queue.get()
                await ws.send_text(json.dumps(event))
        except WebSocketDisconnect:
            hub.unsubscribe(queue)

    return app


def serve(config_path: str | Path, host: str, port: int, open_browser: bool = False) -> int:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - exercised manually
        raise RuntimeError(
            "The web UI requires optional dependencies. Install with: pip install -e '.[web]'"
        ) from exc

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(create_app(config_path), host=host, port=port)
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>re-agent live</title>
  <style>
    :root { color-scheme: dark; --bg:#101214; --panel:#181b1f; --panel2:#20242a; --text:#e8ecef; --muted:#9aa4ad; --line:#303640; --accent:#5cc8ff; --ok:#56d68a; --bad:#ff6b6b; --warn:#ffd166; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: ui-sans-serif, system-ui, Segoe UI, Arial, sans-serif; background: var(--bg); color: var(--text); font-size: 14px; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:12px 16px; border-bottom:1px solid var(--line); background:#121518; }
    h1 { font-size: 18px; margin:0; font-weight:700; letter-spacing:0; }
    main { height: calc(100vh - 57px); display:grid; grid-template-columns: 330px 1fr 420px; gap:1px; background:var(--line); }
    section { background:var(--panel); min-height:0; display:flex; flex-direction:column; }
    .panel-title { padding:10px 12px; border-bottom:1px solid var(--line); color:#dce4ea; font-weight:650; display:flex; justify-content:space-between; align-items:center; }
    .content { padding:10px; overflow:auto; min-height:0; flex:1; }
    .stack { display:flex; flex-direction:column; gap:10px; }
    label { display:block; color:var(--muted); font-size:12px; margin-bottom:4px; }
    input, select, button { width:100%; border:1px solid var(--line); background:var(--panel2); color:var(--text); border-radius:6px; padding:8px 9px; font:inherit; }
    button { cursor:pointer; background:#26313a; border-color:#3b4651; font-weight:650; }
    button:hover { border-color:var(--accent); }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .status { color:var(--muted); font-size:12px; }
    .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; background:var(--bad); }
    .dot.on { background:var(--ok); }
    pre { margin:0; white-space:pre-wrap; word-break:break-word; font:12px/1.45 ui-monospace, SFMono-Regular, Consolas, monospace; }
    .event { padding:8px; border:1px solid var(--line); border-radius:6px; background:#15181c; margin-bottom:8px; }
    .event b { color:#f2f5f7; }
    .time { color:var(--muted); font-size:11px; }
    .tabs { display:flex; gap:6px; padding:8px; border-bottom:1px solid var(--line); flex-wrap:wrap; }
    .tab { width:auto; padding:6px 9px; font-size:12px; }
    .tab.active { border-color:var(--accent); color:#fff; }
    .split { display:grid; grid-template-rows: 1fr 1fr; min-height:0; }
    .kv { display:grid; grid-template-columns:90px 1fr; gap:4px 8px; font-size:12px; }
    .kv span:nth-child(odd) { color:var(--muted); }
    .pill { font-size:11px; color:#061014; background:var(--accent); padding:2px 7px; border-radius:999px; }
    .ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); }
    @media (max-width: 1100px) { main { grid-template-columns: 300px 1fr; } .right { display:none; } }
  </style>
</head>
<body>
  <header>
    <h1>re-agent live</h1>
    <div class="status"><span id="connDot" class="dot"></span><span id="connText">connecting</span></div>
  </header>
  <main>
    <section>
      <div class="panel-title">Run Control</div>
      <div class="content stack">
        <div>
          <label>Mode</label>
          <select id="mode"><option value="class">Class</option><option value="address">Address</option></select>
        </div>
        <div id="addressField" style="display:none">
          <label>Address</label>
          <input id="address" placeholder="0x821f5bd8">
        </div>
        <div>
          <label>Class <span class="status">(type 2+ chars to search the binary)</span></label>
          <input id="className" placeholder="e.g. Attrib" autocomplete="off">
          <div id="searchResults" class="stack" style="gap:4px; margin-top:6px; max-height:180px; overflow:auto"></div>
        </div>
        <div>
          <label>Function</label>
          <input id="functionName" placeholder="optional">
        </div>
        <div class="row">
          <div><label>Max functions</label><input id="maxFunctions" type="number" min="1" placeholder="class only"></div>
          <div><label>Max rounds</label><input id="maxRounds" type="number" min="1" placeholder="config"></div>
        </div>
        <label><input id="skipParity" type="checkbox" style="width:auto; margin-right:6px">Skip parity</label>
        <button id="startBtn">Start Run</button>
        <button id="resetBtn" style="background:#3a2626; border-color:#5a3b3b">Reset progress &amp; outputs</button>
        <div class="panel-title" style="padding-left:0;border-bottom:0">Current</div>
        <div id="current" class="kv"></div>
        <div class="panel-title" style="padding-left:0;border-bottom:0">Progress</div>
        <div id="progress" class="kv"></div>
      </div>
    </section>

    <section>
      <div class="tabs">
        <button class="tab active" data-tab="timeline">Timeline</button>
        <button class="tab" data-tab="llm">Agent Calls</button>
        <button class="tab" data-tab="decompile">Decompile</button>
        <button class="tab" data-tab="code">Generated Code</button>
        <button class="tab" data-tab="verifier">Verifier</button>
      </div>
      <div id="timeline" class="content tabPanel"></div>
      <div id="llm" class="content tabPanel" style="display:none"></div>
      <div id="decompile" class="content tabPanel" style="display:none"><pre id="decompileText"></pre></div>
      <div id="code" class="content tabPanel" style="display:none"><pre id="codeText"></pre></div>
      <div id="verifier" class="content tabPanel" style="display:none"></div>
    </section>

    <section class="right">
      <div class="panel-title">Raw Event <span id="eventCount" class="pill">0</span></div>
      <div class="content"><pre id="rawEvent"></pre></div>
    </section>
  </main>
  <script>
    const state = { count: 0, latestRun: null };
    const $ = id => document.getElementById(id);
    const append = (id, html) => { const el=$(id); el.insertAdjacentHTML('afterbegin', html); };
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const ts = t => new Date(t * 1000).toLocaleTimeString();
    const kv = obj => Object.entries(obj).map(([k,v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join('');
    document.querySelectorAll('.tab').forEach(btn => btn.onclick = () => {
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tabPanel').forEach(p => p.style.display = 'none');
      btn.classList.add('active'); $(btn.dataset.tab).style.display = 'block';
    });
    $('mode').onchange = () => {
      const isAddr = $('mode').value === 'address';
      $('addressField').style.display = isAddr ? 'block' : 'none';
    };
    $('startBtn').onclick = async () => {
      const mode = $('mode').value;
      if (mode === 'class' && !$('className').value.trim()) { alert('Class mode needs a class name — type one (the search below the field shows what exists).'); return; }
      if (mode === 'address' && !$('address').value.trim()) { alert('Address mode needs an address (e.g. 0x821f5bd8).'); return; }
      const body = {
        mode,
        address: $('address').value,
        class_name: $('className').value,
        function_name: $('functionName').value,
        max_functions: $('maxFunctions').value || null,
        max_rounds: $('maxRounds').value || null,
        skip_parity: $('skipParity').checked
      };
      const res = await fetch('/api/runs', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) alert(data.error || 'failed to start');
    };
    $('resetBtn').onclick = async () => {
      if (!confirm('Delete ALL generated outputs in b5-decomp and wipe session progress?')) return;
      const res = await fetch('/api/reset', { method:'POST' });
      const data = await res.json();
      if (!res.ok) { alert(data.error || 'reset failed'); return; }
      alert('Reset done. Removed: ' + ((data.removed_files || []).join(', ') || 'no files') + '.');
      loadProgress();
    };
    let searchTimer = null;
    $('className').oninput = () => {
      clearTimeout(searchTimer);
      const q = $('className').value.trim();
      if (q.length < 2) { $('searchResults').innerHTML = ''; return; }
      searchTimer = setTimeout(async () => {
        $('searchResults').innerHTML = '<div class="status">searching…</div>';
        const res = await fetch('/api/search?q=' + encodeURIComponent(q));
        const data = await res.json();
        const classes = (data.classes || []).map(c =>
          `<button class="tab" style="text-align:left" onclick="document.getElementById('className').value='${esc(c)}';document.getElementById('searchResults').innerHTML=''">class ${esc(c)}</button>`).join('');
        const fns = (data.functions || []).slice(0, 15).map(f =>
          `<button class="tab" style="text-align:left" onclick="document.getElementById('mode').value='address';document.getElementById('mode').onchange();document.getElementById('address').value='${esc(f.address)}';document.getElementById('searchResults').innerHTML=''">${esc(f.address)} ${esc(f.class_name ? f.class_name + '::' : '')}${esc(f.name)}</button>`).join('');
        $('searchResults').innerHTML = (classes + fns) || '<div class="status">no matches in the binary</div>';
      }, 350);
    };
    async function loadProgress() {
      const res = await fetch('/api/progress');
      const data = await res.json();
      $('progress').innerHTML = kv(data.summary);
    }
    function targetName(t) {
      if (!t) return '';
      return `${t.class_name || ''}${t.class_name && t.function_name ? '::' : ''}${t.function_name || ''} ${t.address || ''}`.trim();
    }
    function handle(event) {
      state.count += 1; $('eventCount').textContent = state.count;
      $('rawEvent').textContent = JSON.stringify(event, null, 2);
      const p = event.payload || {};
      if (p.run_id) state.latestRun = p.run_id;
      append('timeline', `<div class="event"><div class="time">${esc(ts(event.timestamp))}</div><b>${esc(event.type)}</b><br>${esc(targetName(p.target) || p.class_name || p.status || p.error || '')}</div>`);
      if (event.type === 'run.started') $('current').innerHTML = kv({run: p.run_id, status:'running', report_dir:p.report_dir});
      if (event.type === 'run.completed' || event.type === 'run.failed') { $('current').innerHTML = kv({run:p.run_id, status:p.status || 'failed', exit_code:p.exit_code ?? '', error:p.error || ''}); loadProgress(); }
      if (event.type === 'llm.call.started') append('llm', `<div class="event"><div class="time">${esc(ts(event.timestamp))}</div><b>${esc(p.role)} ${esc(p.method)} prompt</b><pre>${esc((p.messages || []).map(m => '[' + m.role + ']\\n' + m.content).join('\\n\\n'))}</pre></div>`);
      if (event.type === 'llm.call.completed') append('llm', `<div class="event"><div class="time">${esc(ts(event.timestamp))}</div><b>${esc(p.role)} reply (${Number(p.duration_s || 0).toFixed(1)}s)</b><pre>${esc(p.response)}</pre></div>`);
      if (event.type === 'backend.decompile.completed') $('decompileText').textContent = (p.decompile && (p.decompile.raw_output || p.decompile.decompiled)) || '';
      if (event.type === 'reverser.completed') $('codeText').textContent = p.code || '';
      if (event.type === 'checker.completed') append('verifier', `<div class="event"><b>Checker: ${esc(p.verdict)}</b><pre>${esc(p.summary)}\\n\\nIssues:\\n${esc((p.issues || []).join('\\n'))}\\n\\nFixes:\\n${esc((p.fix_instructions || []).join('\\n'))}</pre></div>`);
      if (event.type === 'objective_verifier.completed') append('verifier', `<div class="event"><b>Objective: ${esc(p.verdict)}</b><pre>${esc(p.summary)}\\n${esc((p.findings || []).join('\\n'))}</pre></div>`);
      if (event.type === 'parity.completed') append('verifier', `<div class="event"><b>Parity: ${esc(p.status)}</b><pre>${esc(JSON.stringify(p.findings || [], null, 2))}</pre></div>`);
    }
    function connect() {
      const events = new EventSource('/events');
      events.onopen = () => { $('connDot').classList.add('on'); $('connText').textContent = 'connected'; };
      events.onerror = () => { $('connDot').classList.remove('on'); $('connText').textContent = 'reconnecting'; };
      events.onmessage = msg => handle(JSON.parse(msg.data));
    }
    connect(); loadProgress();
  </script>
</body>
</html>
"""
