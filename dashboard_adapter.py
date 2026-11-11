"""Owner-only Alice dashboard routes for the existing aiohttp server."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from aiohttp import web

from diagnostics import load_test_summary


PANEL_HTML = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Панель Алисы</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:#11111d;--card:#202032;--line:#393750;--purple:#8c5bea;--pink:#df8dff;--text:#f4efff;--muted:#b8b0ce;--green:#45d483;--red:#ff637d;--yellow:#ffcb6b}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0,#34204d 0,transparent 38%),var(--bg);color:var(--text);font:16px system-ui,-apple-system,"Segoe UI",sans-serif;padding:18px;max-width:1100px;margin:auto}
header{display:flex;justify-content:space-between;align-items:center;gap:14px;margin:8px 0 22px}h1{font-size:25px;margin:0;color:var(--pink)}h2{font-size:18px;margin:0 0 14px}small,.muted{color:var(--muted)}button{border:0;border-radius:12px;background:linear-gradient(130deg,#6b35bd,#8d4add);color:white;padding:12px 16px;font-weight:700;font-size:15px;cursor:pointer}button.secondary{background:#393752}button:disabled{opacity:.55;cursor:wait}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.card{background:linear-gradient(145deg,#25243a,#1d1c2e);border:1px solid var(--line);border-radius:18px;padding:17px;box-shadow:0 8px 25px #08081266}.wide{grid-column:span 2}.value{font-size:22px;font-weight:750;margin:8px 0}.ok{color:var(--green)}.warn{color:var(--yellow)}.error{color:var(--red)}.actions{display:flex;flex-wrap:wrap;gap:9px}.checks{display:grid;gap:8px}.check{display:flex;justify-content:space-between;gap:10px;border-bottom:1px solid #363449;padding:7px 0}.logs{white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto;background:#11111b;padding:12px;border-radius:10px;color:#d9c9ff;font:12px ui-monospace,monospace}.notice{padding:12px;border-radius:12px;background:#332943;color:#f4dfff;margin:12px 0}#message{min-height:22px;color:var(--muted);margin-top:10px}@media(max-width:760px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.wide{grid-column:span 2}}@media(max-width:420px){body{padding:12px}.grid{grid-template-columns:1fr}.wide{grid-column:span 1}header{align-items:flex-start;flex-direction:column}}
</style></head><body>
<header><div><h1>♥ Алиса</h1><small>Панель управления</small></div><button class="secondary" onclick="refreshAll()">↻ Обновить</button></header>
<div id="notice" class="notice">Проверяю безопасный вход Telegram…</div>
<section class="grid">
<article class="card"><small>Статус бота</small><div id="bot" class="value">—</div><div id="uptime" class="muted">—</div></article>
<article class="card"><small>Groq</small><div id="groq" class="value">—</div><div id="model" class="muted">—</div></article>
<article class="card"><small>Последний прогон тестов</small><div id="tests" class="value">—</div><div id="testtime" class="muted">—</div></article>
<article class="card"><small>Память</small><div id="memory" class="value">—</div><div id="memorydetail" class="muted">—</div></article>
<article class="card wide"><h2>Быстрые действия</h2><div class="actions"><button onclick="action('health')">Проверить здоровье</button><button onclick="action('tests')">Запустить тесты</button><button class="secondary" onclick="action('backup')">Создать резервную копию</button></div><div id="message"></div></article>
<article class="card wide"><h2>Диагностика</h2><div id="checks" class="checks muted">Нажми «Проверить здоровье»</div></article>
<article class="card wide"><h2>Системные логи</h2><pre id="logs" class="logs">Логи появятся после запуска бота.</pre></article>
<article class="card wide"><h2>Резервные копии</h2><div id="backup" class="muted">Создаются в папке backups на сервере. .env и ключи не включаются.</div></article>
</section>
<script>
const tg=window.Telegram?.WebApp;const initData=tg?.initData||'';const notice=document.getElementById('notice');if(tg){tg.ready();tg.expand()}
async function api(path,method='GET'){const r=await fetch(path,{method,headers:{'X-Telegram-Init-Data':initData}});const d=await r.json().catch(()=>({error:'Ошибка ответа сервера'}));if(!r.ok)throw Error(d.error||'Нет доступа');return d}
function stateClass(s){return s==='ok'?'ok':s==='warning'?'warn':s==='error'?'error':''}
async function refreshAll(){if(!initData){notice.textContent='Открой панель из личного чата с Алисой командой /panel.';return}try{const d=await api('/api/panel/status');notice.textContent='Система Алисы';document.getElementById('bot').textContent=d.bot.label;document.getElementById('bot').className='value '+stateClass(d.bot.state);document.getElementById('uptime').textContent='Работает: '+d.uptime;document.getElementById('groq').textContent=d.groq.label;document.getElementById('groq').className='value '+stateClass(d.groq.state);document.getElementById('model').textContent='Модель: '+(d.groq.model||'не выбрана');document.getElementById('tests').textContent=d.tests?`${d.tests.passed}/${d.tests.total} · ошибок ${d.tests.failed+d.tests.errors}`:'Нет сохранённого результата';document.getElementById('testtime').textContent=d.tests?.timestamp||'Запусти тесты для первого результата';document.getElementById('memory').textContent=`${d.memory.messages} сообщений`;document.getElementById('memorydetail').textContent=`${d.memory.chats} чатов · ${d.memory.users} пользователей`;document.getElementById('backup').textContent=d.backups.length?`Последняя копия: ${d.backups[0]}`:'Резервных копий пока нет';await refreshLogs()}catch(e){notice.textContent=e.message}}
async function refreshLogs(){try{const d=await api('/api/panel/logs');document.getElementById('logs').textContent=d.lines.join('\n')||'Системных записей пока нет.'}catch(e){document.getElementById('logs').textContent=e.message}}
async function action(name){const m=document.getElementById('message');m.textContent='Выполняю…';try{const d=await api('/api/panel/'+name,'POST');if(name==='health'){const box=document.getElementById('checks');box.textContent='';for(const c of d.checks){const row=document.createElement('div');row.className='check';const a=document.createElement('span');a.textContent=c.name;const b=document.createElement('span');b.className=stateClass(c.state);b.textContent=c.state.toUpperCase()+' · '+c.detail;row.append(a,b);box.append(row)}m.textContent='Проверка завершена: '+d.overall.toUpperCase()}else if(name==='tests'){m.textContent=`Тесты: ${d.passed}/${d.total}; провалено ${d.failed}; ошибок ${d.errors}; пропущено ${d.skipped}.`;if(d.exit_code!==0)m.textContent+=' Есть ошибки; смотри результат в тестах.'}else{m.textContent=`Резервная копия создана: ${d.name} (${d.files} файлов)`}await refreshAll()}catch(e){m.textContent=e.message}}
refreshAll();
</script></body></html>'''


class ConsoleLogBuffer:
    """Tee console output to a bounded, redacted in-memory ring for the owner panel."""

    def __init__(self, secrets: tuple[str, ...] = (), max_lines: int = 200):
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._secrets = tuple(secret for secret in secrets if secret)
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._partial = ""
        self._lock = threading.Lock()

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]{16,}", r"\1[REDACTED]", text)
        text = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)\S+", r"\1[REDACTED]", text)
        return text[:1000]

    def append(self, line: str):
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with self._lock:
            self._lines.append(f"[{stamp}] {self._redact(line.rstrip())}")

    def snapshot(self, limit: int = 100) -> list[str]:
        with self._lock:
            return list(self._lines)[-max(1, min(limit, 200)):]

    def install(self):
        if sys.stdout is not self:
            sys.stdout = self
            sys.stderr = self

    @property
    def encoding(self):
        return getattr(self._stdout, "encoding", "utf-8")

    def write(self, text: str) -> int:
        self._stdout.write(text)
        with self._lock:
            self._partial += text
            parts = self._partial.splitlines(keepends=True)
            self._partial = ""
            for part in parts:
                if part.endswith(("\n", "\r")):
                    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                    self._lines.append(f"[{stamp}] {self._redact(part.rstrip())}")
                else:
                    self._partial = part
        return len(text)

    def flush(self):
        self._stdout.flush()

    def isatty(self):
        return bool(getattr(self._stdout, "isatty", lambda: False)())


class DashboardAdapter:
    def __init__(
        self,
        *,
        project_root: str | Path,
        owner_id: int | None,
        auth_user_id: Callable,
        status_provider: Callable,
        health_provider: Callable,
        log_buffer: ConsoleLogBuffer,
        memory_file: str | Path,
        character_data_dir: str | Path,
    ):
        self.project_root = Path(project_root).resolve()
        self.owner_id = owner_id
        self.auth_user_id = auth_user_id
        self.status_provider = status_provider
        self.health_provider = health_provider
        self.log_buffer = log_buffer
        self.memory_file = Path(memory_file)
        self.character_data_dir = Path(character_data_dir)

    def register_routes(self, app: web.Application):
        app.router.add_get("/panel", self.panel)
        app.router.add_get("/api/panel/status", self.status)
        app.router.add_get("/api/panel/health", self.health)
        app.router.add_get("/api/panel/logs", self.logs)
        app.router.add_post("/api/panel/health", self.health)
        app.router.add_post("/api/panel/tests", self.tests)
        app.router.add_post("/api/panel/backup", self.backup)

    async def panel(self, request: web.Request) -> web.Response:
        return web.Response(
            text=PANEL_HTML,
            content_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://telegram.org; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:",
            },
        )

    def _authorize(self, request: web.Request) -> int:
        if self.owner_id is None:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "Панель отключена: задайте OWNER_USER_ID в окружении."}, ensure_ascii=False),
                content_type="application/json",
            )
        user_id = self.auth_user_id(request)
        if user_id != self.owner_id:
            raise web.HTTPForbidden(
                text=json.dumps({"error": "Доступ к панели разрешён только владельцу."}, ensure_ascii=False),
                content_type="application/json",
            )
        return user_id

    async def status(self, request: web.Request) -> web.Response:
        user_id = self._authorize(request)
        return web.json_response(await self.status_provider(user_id), headers={"Cache-Control": "no-store"})

    async def health(self, request: web.Request) -> web.Response:
        self._authorize(request)
        return web.json_response(await self.health_provider(), headers={"Cache-Control": "no-store"})

    async def logs(self, request: web.Request) -> web.Response:
        self._authorize(request)
        return web.json_response({"lines": self.log_buffer.snapshot()}, headers={"Cache-Control": "no-store"})

    async def tests(self, request: web.Request) -> web.Response:
        self._authorize(request)
        runner = self.project_root / "tests" / "run_tests.py"
        if not runner.is_file():
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "Локальный запуск тестов не настроен."}, ensure_ascii=False),
                content_type="application/json",
            )
        env = os.environ.copy()
        env.pop("BOT_TOKEN", None)
        env.pop("GROQ_API_KEY", None)
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-B",
                str(runner),
                cwd=str(self.project_root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(process.communicate(), timeout=240)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise web.HTTPGatewayTimeout(
                text=json.dumps({"error": "Проверка превысила лимит времени."}, ensure_ascii=False),
                content_type="application/json",
            )
        output_text = output.decode("utf-8", errors="replace")
        for line in output_text.splitlines()[-40:]:
            self.log_buffer.append(line)
        result = load_test_summary(self.project_root / "reports" / "test_results.json")
        if not result:
            raise web.HTTPInternalServerError(
                text=json.dumps({"error": "Тесты завершились без сохранённого итогового отчёта."}, ensure_ascii=False),
                content_type="application/json",
            )
        return web.json_response({**result, "exit_code": process.returncode})

    async def backup(self, request: web.Request) -> web.Response:
        self._authorize(request)
        backups_dir = self.project_root / "backups"
        backups_dir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        final_path = backups_dir / f"alice_backup_{stamp}.zip"
        temp_path = backups_dir / f".{final_path.name}.tmp"
        members = []
        fixed_files = (
            "bot.py", "activity_adapter.py", "character_adapter.py", "persona_adapter.py",
            "personality.py", "relationship_adapter.py", "dashboard_adapter.py", "diagnostics.py",
            "health_check.py", "requirements.txt", "relationships.json",
        )
        data_files = ("characters.json", "character_chats.json", "feedback.json")
        sources = [self.project_root / name for name in fixed_files]
        sources.append(self.memory_file)
        sources.extend(self.character_data_dir / name for name in data_files)
        sources.extend((self.project_root / "reports" / "test_results.json",))
        try:
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for source in sources:
                    if not source.is_file() or source.is_symlink():
                        continue
                    resolved = source.resolve()
                    if resolved == (self.project_root / ".env").resolve() or resolved.name.startswith(".env"):
                        continue
                    if resolved == self.memory_file.resolve():
                        archive.write(source, "memory.json")
                    elif self.character_data_dir in resolved.parents and resolved.name in data_files:
                        archive.write(source, f"character_data/{resolved.name}")
                    else:
                        try:
                            archive.write(source, str(resolved.relative_to(self.project_root)))
                        except ValueError:
                            continue
                    members.append(source.name)
            os.replace(temp_path, final_path)
        except OSError:
            temp_path.unlink(missing_ok=True)
            raise web.HTTPInternalServerError(
                text=json.dumps({"error": "Не удалось создать резервную копию."}, ensure_ascii=False),
                content_type="application/json",
            )
        self.log_buffer.append(f"Создана резервная копия {final_path.name}; файлов: {len(members)}")
        return web.json_response({"name": final_path.name, "files": len(members), "size_bytes": final_path.stat().st_size})
