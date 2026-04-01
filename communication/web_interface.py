# Communication Layer — Web Interface — Слой 15
# Архитектура автономного AI-агента
#
# Веб-чат и REST API для агента.
# Запускается в фоновом потоке рядом с Telegram-ботом.
#
# Авторизация:
#   Токен доступа читается из переменной окружения WEB_TOKEN (или .env).
#   Если WEB_TOKEN не задан — генерируется случайный при старте и печатается в лог.
#   Браузер: форма входа, cookie "agent_token" на 7 дней.
#   API:     заголовок X-Agent-Token: <токен>
#
# Эндпоинты:
#   GET  /              — HTML-чат (браузер, требует токен)
#   GET  /login         — страница входа
#   POST /login         — проверка токена, устанавливает cookie
#   POST /chat          — JSON API: {"message": "текст"} → {"reply": "...", "ok": true}
#   GET  /status        — состояние агента (JSON)
#   POST /goal          — поставить цель: {"goal": "текст"}
#   GET  /history       — последние N сообщений чата (JSON)
#
# Для доступа из интернета: запусти ngrok
#   ngrok http 8000
# pylint: disable=broad-except,protected-access,redefined-builtin

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote_plus

# ── HTML-страница чата ────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Agent Chat</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f0f13; color: #e8e8f0; height: 100vh; display: flex; flex-direction: column; }
  #header { background: #1a1a2e; padding: 14px 20px; border-bottom: 1px solid #2a2a4a; display: flex; align-items: center; gap: 12px; }
  #header .dot { width: 10px; height: 10px; border-radius: 50%; background: #4ade80; }
  #header h1 { font-size: 18px; font-weight: 600; }
  #header span { font-size: 12px; color: #888; margin-left: auto; }
  #messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
  .msg { max-width: 75%; padding: 10px 14px; border-radius: 14px; line-height: 1.5; font-size: 14px; white-space: pre-wrap; word-break: break-word; }
  .msg.user { background: #2563eb; align-self: flex-end; border-bottom-right-radius: 4px; }
  .msg.agent { background: #1e1e2e; border: 1px solid #2a2a4a; align-self: flex-start; border-bottom-left-radius: 4px; }
  .msg.system { background: transparent; border: none; color: #666; font-size: 12px; align-self: center; }
  #input-area { padding: 12px 20px 16px; background: #1a1a2e; border-top: 1px solid #2a2a4a; display: flex; flex-direction: column; gap: 8px; }
  #input-row { display: flex; gap: 10px; align-items: flex-end; }
  #attach-btn { background: #1e1e2e; border: 1px solid #2a2a4a; color: #999; border-radius: 10px; padding: 10px 13px; cursor: pointer; font-size: 16px; flex-shrink: 0; line-height: 1; }
  #attach-btn:hover { border-color: #2563eb; color: #e8e8f0; }
  #file-badge { display: none; align-items: center; gap: 8px; background: #1e1e2e; border: 1px solid #2563eb44; border-radius: 8px; padding: 5px 10px; font-size: 12px; color: #aaa; }
  #file-badge span.icon { flex-shrink: 0; }
  #file-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 300px; color: #c8c8e8; }
  #file-size { color: #666; flex-shrink: 0; }
  #file-clear { background: none; border: none; color: #666; cursor: pointer; font-size: 15px; padding: 0 2px; line-height: 1; }
  #file-clear:hover { color: #f87171; }
  #msg-input { flex: 1; background: #0f0f20; border: 1px solid #2a2a4a; color: #e8e8f0; border-radius: 10px; padding: 10px 14px; font-size: 14px; outline: none; resize: none; max-height: 120px; }
  #msg-input:focus { border-color: #2563eb; }
  #send-btn { background: #2563eb; color: white; border: none; border-radius: 10px; padding: 10px 20px; cursor: pointer; font-size: 14px; font-weight: 600; white-space: nowrap; }
  #send-btn:hover { background: #1d4ed8; }
  #send-btn:disabled { background: #334; cursor: default; }
  #quick-btns { display: flex; gap: 6px; flex-wrap: wrap; padding: 0 0 4px 0; }
  .quick-cmd { background: #1e1e2e; border: 1px solid #2a2a4a; color: #aaa; border-radius: 8px; padding: 4px 10px; font-size: 12px; cursor: pointer; white-space: nowrap; }
  .quick-cmd:hover { border-color: #2563eb; color: #e8e8f0; }
  .typing { display: none; }
  .typing.visible { display: flex; align-self: flex-start; gap: 4px; padding: 10px 14px; background: #1e1e2e; border-radius: 14px; border: 1px solid #2a2a4a; }
  .typing span { width: 7px; height: 7px; background: #666; border-radius: 50%; animation: bounce 1.2s infinite; }
  .typing span:nth-child(2) { animation-delay: 0.2s; }
  .typing span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce { 0%,60%,100% { transform: translateY(0); } 30% { transform: translateY(-6px); } }
  #scroll-down-btn {
    display: none;
    position: fixed;
    bottom: 120px;
    right: 24px;
    z-index: 999;
    width: 44px; height: 44px;
    border-radius: 50%;
    background: #2563eb;
    color: #fff;
    border: none;
    font-size: 20px;
    cursor: pointer;
    box-shadow: 0 4px 16px #0008;
    align-items: center;
    justify-content: center;
    transition: opacity .2s, transform .2s;
  }
  #scroll-down-btn:hover { background: #1d4ed8; transform: translateY(2px); }
  #scroll-down-btn.visible { display: flex; }
</style>
</head>
<body>
<div id="header">
  <div class="dot" id="status-dot"></div>
  <h1>AI Agent</h1>
  <span id="status-text">подключение...</span>
</div>
<div id="messages">
  <div class="msg agent" style="max-width:90%;align-self:flex-start;">
    <b>Привет! Я автономный AI-агент.</b> Просто напиши задачу — я выполню её и верну результат.<br><br>
    Например:<br>
    📄 <i>«Создай PDF отчёт о состоянии системы»</i><br>
    📊 <i>«Сделай Excel таблицу с данными продаж»</i><br>
    🔍 <i>«Найди топ-5 статей про Python автоматизацию»</i><br>
    📷 <i>«Сделай скриншот экрана»</i><br>
    🌐 <i>«Переведи текст на английский: [текст]»</i><br>
    🖥 <i>«Проверь CPU и RAM сервера»</i>
  </div>
</div>
<div class="typing" id="typing"><span></span><span></span><span></span></div>
<button id="scroll-down-btn" title="Прокрутить вниз">↓</button>
<input type="file" id="file-input" style="display:none">
<div id="input-area">
  <div id="quick-btns">
    <button class="quick-cmd" data-cmd="/status">📊 Статус</button>
    <button class="quick-cmd" data-cmd="создай PDF отчёт о системе">📄 PDF</button>
    <button class="quick-cmd" data-cmd="сделай скриншот экрана">📷 Скриншот</button>
    <button class="quick-cmd" data-cmd="проверь состояние системы CPU и RAM">🖥 Система</button>
    <button class="quick-cmd" data-cmd="/help">❓ Помощь</button>
  </div>
  <div id="file-badge"><span class="icon">📎</span><span id="file-name"></span><span id="file-size"></span><button id="file-clear" title="Убрать файл">×</button></div>
  <div id="input-row">
    <button id="attach-btn" title="Прикрепить файл">📎</button>
    <textarea id="msg-input" rows="1" placeholder="Напишите задачу агенту..."></textarea>
    <button id="send-btn">Отправить</button>
  </div>
</div>
<script>
const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('msg-input');
const sendBtn = document.getElementById('send-btn');
const typingEl = document.getElementById('typing');
const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');

let attachedFile = null;

// ── Умный скрол: не мешать пользователю читать историю ───────────────────────
let userScrolledUp = false;
const scrollDownBtn = document.getElementById('scroll-down-btn');

messagesEl.addEventListener('scroll', () => {
  const threshold = 60;  // пикселей от низа — считается "внизу"
  const atBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < threshold;
  userScrolledUp = !atBottom;
  if (userScrolledUp) {
    scrollDownBtn.classList.add('visible');
  } else {
    scrollDownBtn.classList.remove('visible');
  }
});

scrollDownBtn.addEventListener('click', () => {
  userScrolledUp = false;
  scrollDownBtn.classList.remove('visible');
  smartScroll();
});

// Вызывать вместо прямого scrollTop — скролит только если пользователь внизу
function smartScroll() {
  if (!userScrolledUp) {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}
// ─────────────────────────────────────────────────────────────────────────────

document.getElementById('attach-btn').addEventListener('click', () => {
  document.getElementById('file-input').click();
});
document.getElementById('file-input').addEventListener('change', e => {
  const file = e.target.files[0];
  if (!file) return;
  attachedFile = file;
  document.getElementById('file-name').textContent = file.name;
  const kb = (file.size / 1024).toFixed(0);
  document.getElementById('file-size').textContent = kb < 1024 ? kb + ' KB' : (file.size/1048576).toFixed(1) + ' MB';
  document.getElementById('file-badge').style.display = 'flex';
});
document.getElementById('file-clear').addEventListener('click', () => {
  attachedFile = null;
  document.getElementById('file-input').value = '';
  document.getElementById('file-badge').style.display = 'none';
});

function fmtFileMsg(filename, content) {
  const d = document.createElement('div');
  d.className = 'msg user';
  const badge = document.createElement('div');
  badge.style.cssText = 'font-size:11px;opacity:.7;margin-bottom:4px;';
  badge.textContent = '📎 ' + filename;
  d.appendChild(badge);
  if (content) { const t = document.createElement('span'); t.textContent = content; d.appendChild(t); }
  messagesEl.appendChild(d);
  smartScroll();
}

function addMsg(text, role) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  // Агентские сообщения: конвертируем переносы и базовый markdown
  if (role === 'agent') {
    d.innerHTML = text
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/\*\*(.+?)\*\*/g,'<b>$1</b>')
      .replace(/\n/g,'<br>');
  } else {
    d.textContent = text;
  }
  messagesEl.appendChild(d);
  smartScroll();
}

function addLiveSystemMsg(text) {
    const d = document.createElement('div');
    d.className = 'msg system';
    d.textContent = text;
    messagesEl.appendChild(d);
    smartScroll();
    return d;
}

function startThinkingTicker(el, mode = 'single') {
    const singleStages = [
        'Размышляю над запросом...',
        'Планирую шаги решения...',
        'Проверяю инструменты...',
        'Выполняю задачу...',
        'Формирую ответ...',
    ];
    const queueStages = [
        'Разбираю текущий шаг...',
        'Планирую действие по шагу...',
        'Выполняю шаг...',
        'Проверяю результат шага...',
    ];
    const stages = mode === 'queue' ? queueStages : singleStages;
    let i = 0;
    el.textContent = '🧠 ' + stages[0];
    const timer = setInterval(() => {
        i = (i + 1) % stages.length;
        el.textContent = '🧠 ' + stages[i];
        smartScroll();
    }, 1600);
    return () => clearInterval(timer);
}

async function checkStatus() {
  try {
    const r = await fetch('/status');
    const d = await r.json();
    statusDot.style.background = '#4ade80';
    statusText.textContent = 'онлайн · цикл #' + (d.cycle || 0);
  } catch { statusDot.style.background = '#f87171'; statusText.textContent = 'офлайн'; }
}
checkStatus();
setInterval(checkStatus, 10000);

async function readFileText(file) {
  return new Promise((resolve) => {
    const r = new FileReader();
    r.onload = e => resolve(e.target.result || '');
    r.onerror = () => resolve('[не удалось прочитать файл]');
    r.readAsText(file, 'utf-8');
  });
}

function parseTaskQueue(text) {
    const lines = (text || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    if (lines.length < 2) return [];
    const items = [];
    // Паттерны заголовков разделов (не задачи):
    //   "Категория 1: ...", "**Раздел 2**", строки без глагола заканчивающиеся на ":"
    const headerPattern = /^(?:категори[яи]|category|раздел|section|блок|block|группа|group)[\s\d:.,]/i;
    const endsWithColon = /[:\—–-]\s*$/;
    // Глаголы-признак исполнимой задачи (рус + eng)
    // Для кириллицы \b не работает — используем lookahead/lookbehind на пробел/начало строки
    const actionVerb = /(?:^|[\s,.:;(])(?:создай|найди|поищи|провер|запусти|выполн|скачай|отправ|получи|напиши|сделай|открой|закрой|запиш|удали|переведи|переименуй|скопируй|вычисли|посчитай|покажи|собер|установ|обнов|протест|сохрани|прочитай|загрузи|конвертируй|\bcreate\b|\bfind\b|\bcheck\b|\brun\b|\bexecute\b|\bsearch\b|\bsend\b|\bget\b|\bwrite\b|\bopen\b|\bclose\b|\bdelete\b|\btranslate\b|\brename\b|\bcopy\b|\bcalculate\b|\bshow\b|\bcollect\b|\binstall\b|\bupdate\b|\btest\b|\bsave\b|\bread\b|\bdownload\b|\bconvert\b|\bping\b|\blist\b|\bscan\b|\banalyze\b|\bgenerate\b)/i;
    for (const line of lines) {
        const m = line.match(/^(?:\d+[\.)]|[-*•])\s+(.+)$/);
        if (!m || !m[1]) continue;
        const item = m[1].trim();
        // Пропускаем заголовки категорий
        if (headerPattern.test(item)) continue;
        if (endsWithColon.test(item) && !actionVerb.test(item)) continue;
        // Пропускаем очень короткие строки без глагола (скорее всего заголовок)
        if (item.length < 8 && !actionVerb.test(item)) continue;
        items.push(item);
    }
    // Режим очереди включаем только если действительно есть список из 2+ задач.
    if (items.length < 2) return [];
    return items.slice(0, 40);
}

async function requestChat(payload) {
    const token = document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('agent_token='));
    const tok = token ? token.split('=')[1] : '';
    const r = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Agent-Token': tok },
        body: JSON.stringify(payload),
    });
    if (r.status === 401) {
        window.location.href = '/login';
        throw new Error('unauthorized');
    }
    return await r.json();
}

async function runQueue(tasks) {
    sendBtn.disabled = true;
    addMsg(`Запускаю очередь задач: ${tasks.length} пункт(ов).`, 'system');

    for (let i = 0; i < tasks.length; i++) {
        const step = i + 1;
        const t = tasks[i];
        addMsg(`[${step}/${tasks.length}] ${t}`, 'user');
        const live = addLiveSystemMsg(`⏳ Шаг ${step}/${tasks.length}: запуск...`);
        const stopTicker = startThinkingTicker(live, 'queue');

        typingEl.classList.add('visible');
        messagesEl.appendChild(typingEl);
        smartScroll();

        try {
            const d = await requestChat({ message: t });
            stopTicker();
            typingEl.classList.remove('visible');
            live.textContent = `✅ Шаг ${step}/${tasks.length} завершён`;
            addMsg(d.reply || '(нет ответа)', 'agent');
        } catch {
            stopTicker();
            typingEl.classList.remove('visible');
            live.textContent = `❌ Шаг ${step}/${tasks.length}: ошибка соединения`;
            break;
        }
    }

    addMsg('Очередь задач завершена.', 'system');
    sendBtn.disabled = false;
    inputEl.focus();
}

async function send() {
  const text = inputEl.value.trim();
  const hasFile = !!attachedFile;
  if (!text && !hasFile) return;
  inputEl.value = '';
  inputEl.style.height = 'auto';

    // Если пользователь отправил список задач — выполняем их по очереди
    // с промежуточным прогрессом, а не одним длинным запросом.
    if (!hasFile) {
        const queue = parseTaskQueue(text);
        if (queue.length >= 2) {
            await runQueue(queue);
            return;
        }
    }

  let fileContent = null;
  let fileName = null;
  if (hasFile) {
    fileName = attachedFile.name;
    fileContent = await readFileText(attachedFile);
    // Trim very large files to 200KB of text
    if (fileContent.length > 200000) fileContent = fileContent.slice(0, 200000) + '...[обрезано]';
    fmtFileMsg(fileName, text);
    attachedFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-badge').style.display = 'none';
  } else {
    addMsg(text, 'user');
  }

  sendBtn.disabled = true;
    const live = addLiveSystemMsg('⏳ Обработка запроса...');
    const stopTicker = startThinkingTicker(live, 'single');
  typingEl.classList.add('visible');
  messagesEl.appendChild(typingEl);
  smartScroll();
    try {
        const d = await requestChat({ message: text, filename: fileName, file_content: fileContent });
        stopTicker();
    typingEl.classList.remove('visible');
        live.textContent = '✅ Готово';
    addMsg(d.reply || '(нет ответа)', 'agent');
  } catch (e) {
        stopTicker();
    typingEl.classList.remove('visible');
        live.textContent = '❌ Ошибка выполнения';
    addMsg('Ошибка соединения с агентом.', 'system');
  }
  sendBtn.disabled = false;
  inputEl.focus();
}

sendBtn.addEventListener('click', send);
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

// Быстрые команды: кнопки-подсказки
document.querySelectorAll('.quick-cmd').forEach(btn => {
  btn.addEventListener('click', () => {
    const cmd = btn.dataset.cmd;
    if (!cmd) return;
    if (cmd === '/status') {
      // Статус — вызываем API напрямую, не через LLM
      fetch('/status').then(r => r.json()).then(d => {
        const loop = d.cycle || 0;
        const goal = (d.goal || '—').slice(0, 120);
        const mem = (d.persistent_memory_text || '');
        let txt = `📊 Статус агента\n━━━━━━━━━━━━━━━━━━━━\nЦиклов: ${loop}\nЦель: ${goal}`;
        if (mem) txt += `\n\n🧠 Память:\n${mem}`;
        addMsg('/status', 'user');
        addMsg(txt, 'agent');
      }).catch(() => addMsg('Ошибка получения статуса.', 'system'));
    } else {
      inputEl.value = cmd + ' ';
      inputEl.focus();
    }
  });
});
</script>
</body>
</html>"""

_LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Agent — Вход</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f0f13; color: #e8e8f0;
         height: 100vh; display: flex; align-items: center; justify-content: center; }
  .card { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 16px;
          padding: 40px 36px; width: 360px; display: flex; flex-direction: column; gap: 20px; }
  h1 { font-size: 20px; font-weight: 700; text-align: center; }
  p  { font-size: 13px; color: #888; text-align: center; }
  input { background: #0f0f20; border: 1px solid #2a2a4a; color: #e8e8f0;
          border-radius: 10px; padding: 12px 14px; font-size: 15px; width: 100%; outline: none; }
  input:focus { border-color: #2563eb; }
  button { background: #2563eb; color: white; border: none; border-radius: 10px;
           padding: 12px; font-size: 15px; font-weight: 600; cursor: pointer; width: 100%; }
  button:hover { background: #1d4ed8; }
  .err { color: #f87171; font-size: 13px; text-align: center; display: none; }
  .err.show { display: block; }
</style>
</head>
<body>
<div class="card">
  <h1>AI Agent</h1>
  <p>Введите токен доступа</p>
  <form method="POST" action="/login" id="form">
    <input type="password" name="token" id="tok" placeholder="Токен..." autocomplete="current-password" autofocus>
    <br><br>
    <button type="submit">Войти</button>
  </form>
  <div class="err" id="err">__ERROR__</div>
</div>
<script>
  const err = document.getElementById('err');
  if (err.textContent.trim() === '__ERROR__') err.classList.remove('show');
  else if (err.textContent.trim()) err.classList.add('show');
</script>
</body>
</html>"""


class WebInterface:
    """
    Communication Layer — Web Interface (Слой 15).

    Лёгкий HTTP-сервер на stdlib (без зависимостей).
    Запускается в фоновом потоке, не блокирует основной процесс.

    Параметры:
        host            — адрес привязки (по умолчанию '0.0.0.0' — все интерфейсы)
        port            — порт (по умолчанию 8000)
        cognitive_core  — для ответов в чате (метод converse)
        autonomous_loop — для статуса и управления
        goal_manager    — для постановки целей
        monitoring      — для логирования
        max_history     — сколько сообщений хранить в памяти сессии
    """

    def __init__(
        self,
        host: str = '127.0.0.1',
        port: int = 8000,
        cognitive_core=None,
        autonomous_loop=None,
        goal_manager=None,
        monitoring=None,
        max_history: int = 200,
        persistent_brain=None,
    ):
        self.host = host
        self.port = port
        self.cognitive_core = cognitive_core
        self.loop = autonomous_loop
        self.goal_manager = goal_manager
        self.monitoring = monitoring
        self.max_history = max_history
        self.persistent_brain = persistent_brain

        self._history: list[dict] = []   # {"role": "user"|"agent", "text": ..., "ts": ...}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._chat_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='web_chat')
        try:
            # Длинные multi-step задачи через web-чат нередко занимают >90с.
            # Увеличенный дефолт снижает ложные обрывы при нормальной работе агента.
            self._chat_timeout_sec = max(10.0, float(os.environ.get('WEB_CHAT_TIMEOUT_SEC', '240') or 240.0))
        except Exception:
            self._chat_timeout_sec = 240.0

        # ── Токен доступа ─────────────────────────────────────────────────────
        # Читаем из env; если не задан — генерируем случайный.
        env_token = os.environ.get('WEB_TOKEN', '').strip()
        if env_token:
            self._token = env_token
        else:
            self._token = secrets.token_urlsafe(24)
            token_preview = f"{self._token[:6]}...{self._token[-4:]}"
            self._log(
                f"WEB_TOKEN не задан. Сгенерирован временный токен: {token_preview}\n"
                "  Добавьте WEB_TOKEN=<ваш_токен> в .env чтобы он сохранился между перезапусками.",
                level='warning',
            )

        # Хэш токена для константного времени сравнения
        self._token_hash = hashlib.sha256(self._token.encode()).digest()
        self.max_body_bytes = 12 * 1024 * 1024  # 12 MB — поддержка загрузки файлов

    # ── Проверка токена ───────────────────────────────────────────────────────

    def _check_token(self, provided: str) -> bool:
        """Безопасное (constant-time) сравнение токенов."""
        if not provided:
            return False
        provided_hash = hashlib.sha256(provided.encode()).digest()
        return secrets.compare_digest(self._token_hash, provided_hash)

    def _token_from_request(self, handler) -> str:
        """Извлекает токен из заголовка X-Agent-Token или cookie agent_token."""
        # 1. Заголовок X-Agent-Token (для API)
        header_tok = handler.headers.get('X-Agent-Token', '').strip()
        if header_tok:
            return header_tok
        # 2. Cookie agent_token (для браузера)
        cookie_raw = handler.headers.get('Cookie', '')
        for part in cookie_raw.split(';'):
            part = part.strip()
            if part.startswith('agent_token='):
                return part[len('agent_token='):]
        return ''

    # ── Запуск / остановка ────────────────────────────────────────────────────

    def start(self) -> None:
        """Запускает HTTP-сервер в фоновом потоке."""
        handler = self._make_handler()

        class _SilentHTTPServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                import sys
                exc = sys.exc_info()[1]
                # Подавляем ошибки разрыва соединения — браузер закрыл сокет
                # до завершения ответа (типично для Windows после редиректов)
                if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
                    return
                super().handle_error(request, client_address)

        self._server = _SilentHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name='web_interface',
        )
        self._thread.start()
        self._log(f"Web Interface запущен: http://localhost:{self.port}")

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._log("Web Interface остановлен.")
        if self._chat_executor:
            try:
                self._chat_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                self._chat_executor.shutdown(wait=False)

    # ── Обработчик запросов ───────────────────────────────────────────────────

    def _make_handler(self):
        """Создаёт класс-обработчик с замыканием на self."""
        interface = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # заглушаем стандартный вывод
                _ = (format, args)
                return

            def _allowed_origin(self) -> str:
                origin = self.headers.get('Origin', '').strip()
                allowed = (
                    'http://localhost',
                    'http://127.0.0.1',
                    'http://[::1]',
                    'https://localhost',
                    'https://127.0.0.1',
                    'https://[::1]',
                )
                if any(origin.startswith(prefix) for prefix in allowed):
                    return origin
                return ''

            def _set_security_headers(self):
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
                self.send_header('Pragma', 'no-cache')
                self.send_header(
                    'Content-Security-Policy',
                    "default-src 'self'; img-src 'self' data:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                )

            def _is_same_origin_or_local(self) -> bool:
                origin = self.headers.get('Origin', '').strip()
                if not origin:
                    return True
                return bool(self._allowed_origin())

            def _send_json(self, data: dict, code: int = 200):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self._set_security_headers()
                allowed_origin = self._allowed_origin()
                if allowed_origin:
                    self.send_header('Access-Control-Allow-Origin', allowed_origin)
                    self.send_header('Vary', 'Origin')
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, html: str):
                body = html.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self._set_security_headers()
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict:
                try:
                    length = int(self.headers.get('Content-Length', 0))
                except (TypeError, ValueError):
                    return {}
                if length == 0:
                    return {}
                if length > interface.max_body_bytes:
                    return {}
                raw = self.rfile.read(length)
                try:
                    return json.loads(raw)
                except Exception:
                    return {}

            def do_OPTIONS(self):
                allowed_origin = self._allowed_origin()
                if not allowed_origin:
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin', allowed_origin)
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Agent-Token')
                self.end_headers()

            def _is_auth(self) -> bool:
                tok = interface._token_from_request(self)
                return interface._check_token(tok)

            def _redirect_login(self):
                self.send_response(302)
                self.send_header('Location', '/login')
                self._set_security_headers()
                self.end_headers()

            def do_GET(self):
                path = urlparse(self.path).path.rstrip('/')
                if path == '/login':
                    self._send_html(_LOGIN_HTML.replace('__ERROR__', ''))
                    return
                if not self._is_auth():
                    # Браузер → на страницу входа; API → 401
                    accept = self.headers.get('Accept', '')
                    if 'text/html' in accept:
                        self._redirect_login()
                    else:
                        self._send_json({'error': 'Unauthorized'}, 401)
                    return
                if path in ('', '/'):
                    self._send_html(_HTML)
                elif path == '/status':
                    self._send_json(interface._handle_status())
                elif path == '/history':
                    with interface._lock:
                        self._send_json({'history': interface._history[-50:]})
                else:
                    self._send_json({'error': 'not found'}, 404)

            def do_POST(self):
                path = urlparse(self.path).path.rstrip('/')

                # Логин — не требует токена
                if path == '/login':
                    self._handle_login_post()
                    return

                if not self._is_auth():
                    self._send_json({'error': 'Unauthorized'}, 401)
                    return

                if not self._is_same_origin_or_local():
                    self._send_json({'error': 'Forbidden origin'}, 403)
                    return

                if path == '/chat':
                    data = self._read_json()
                    result = interface._handle_chat(data)
                    self._send_json(result)
                elif path == '/goal':
                    data = self._read_json()
                    result = interface._handle_goal(data)
                    self._send_json(result)
                else:
                    self._send_json({'error': 'not found'}, 404)

            def _handle_login_post(self):
                """Обрабатывает форму входа (application/x-www-form-urlencoded)."""
                try:
                    length = int(self.headers.get('Content-Length', 0))
                except (TypeError, ValueError):
                    length = 0
                if length > interface.max_body_bytes:
                    self._send_json({'error': 'Payload too large'}, 413)
                    return
                raw = self.rfile.read(length).decode('utf-8', errors='replace') if length else ''
                # Парсим form-data
                token = ''
                for pair in raw.split('&'):
                    if '=' in pair:
                        k, _, v = pair.partition('=')
                        if unquote_plus(k) == 'token':
                            token = unquote_plus(v)
                            break

                if interface._check_token(token):
                    # Устанавливаем cookie на 7 дней и редиректим на чат
                    max_age = 7 * 24 * 3600
                    secure = ''
                    if self.headers.get('X-Forwarded-Proto', '').lower() == 'https':
                        secure = '; Secure'
                    self.send_response(302)
                    self.send_header(
                        'Set-Cookie',
                        f'agent_token={token}; Max-Age={max_age}; HttpOnly; SameSite=Strict; Path=/{secure}'
                    )
                    self.send_header('Location', '/')
                    self._set_security_headers()
                    self.end_headers()
                else:
                    html = _LOGIN_HTML.replace('__ERROR__', 'Неверный токен. Попробуйте снова.')
                    self._send_html(html)

        return Handler

    # ── Обработчики маршрутов ─────────────────────────────────────────────────

    def _get_tool_layer(self):
        loop = self.loop
        if loop is None:
            return None
        return getattr(loop, 'tool_layer', None)

    def _handle_quick_task(self, message: str) -> dict | None:
        """Детерминированные быстрые задачи для web-очереди (без LLM-кода)."""
        m = (message or '').strip()
        lower = m.lower()
        tool_layer = self._get_tool_layer()
        if tool_layer is None:
            return None
        try:
            return (
                self._qt_documents(lower, m, tool_layer) or
                self._qt_system_info(lower, m, tool_layer) or
                self._qt_git(lower, m, tool_layer) or
                self._qt_network(lower, m, tool_layer) or
                self._qt_filesystem(lower, m, tool_layer) or
                self._qt_execute_code(lower, m, tool_layer) or
                self._qt_misc(lower, m, tool_layer)
            )
        except Exception as e:
            return {'ok': False, 'reply': f"❌ Ошибка быстрого выполнения: {e}"}

    # ── _handle_quick_task helpers ────────────────────────────────────────────

    def _qt_documents(self, lower: str, m: str, tool_layer) -> dict | None:
        """PDF отчёт, статьи, скриншот, Excel, перевод, PowerPoint, PDF из текста."""
        # 1) PDF системный отчёт
        if ('pdf' in lower and ('отч' in lower or 'report' in lower)) and ('систем' in lower or 'system' in lower):
            sys_r = tool_layer.use('process_manager', action='system')
            if not isinstance(sys_r, dict) or not sys_r.get('success'):
                return {'ok': False, 'reply': f"❌ Не удалось собрать метрики системы: {sys_r}"}
            cpu = sys_r.get('cpu_percent', 'n/a')
            mem = sys_r.get('memory_percent', 'n/a')
            disk = sys_r.get('disk_percent', 'n/a')
            free = sys_r.get('disk_free_gb', 'n/a')
            now = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            text = (f"Отчёт о системе\nВремя: {now}\n\nCPU: {cpu}%\nRAM: {mem}%\n"
                    f"Disk: {disk}%\nСвободно на диске: {free} GB\n")
            out_path = os.path.join('outputs', f'system_report_{int(time.time())}.pdf')
            pdf_r = tool_layer.use('pdf_generator', action='from_text', output=out_path,
                                   title='Системный отчёт', text=text)
            if isinstance(pdf_r, dict) and pdf_r.get('success'):
                return {'ok': True, 'reply': f"✅ PDF создан: {pdf_r.get('path', out_path)}"}
            return {'ok': False, 'reply': f"❌ Ошибка при создании PDF: {pdf_r}"}

        # 2) Найти статьи
        if ('найди' in lower or 'find' in lower or 'поищи' in lower) and \
           ('стат' in lower or 'article' in lower or 'новост' in lower or 'news' in lower):
            num_m = re.search(r'\b(\d+)\b', lower)
            count = min(int(num_m.group(1)) if num_m else 3, 10)
            query = m
            for kw in ['python', 'ai', 'machine learning', 'django', 'fastapi',
                       'javascript', 'typescript', 'rust', 'go', 'docker',
                       'kubernetes', 'linux', 'windows', 'android', 'ios']:
                if kw in lower:
                    query = kw + ' programming tutorials articles'
                    break
            sr = tool_layer.use('search', query=query, num_results=count + 2)
            if not isinstance(sr, dict) or not sr.get('success'):
                return {'ok': False, 'reply': f"❌ Поиск не удался: {sr}"}
            results = (sr.get('results') or [])[:count]
            if not results:
                return {'ok': False, 'reply': '❌ Не удалось найти статьи.'}
            lines = [f"✅ Найдено {len(results)} статей:"]
            for i, item in enumerate(results, 1):
                lines.append(f"{i}. {item.get('title', 'без названия')} — {item.get('url', '')}")
            return {'ok': True, 'reply': '\n'.join(lines)}

        # 3) Скриншот
        if 'скрин' in lower or 'screenshot' in lower:
            out_path = os.path.join('outputs', f'screenshot_{int(time.time())}.png')
            sc = tool_layer.use('screenshot', save_path=out_path)
            if isinstance(sc, dict) and sc.get('success'):
                return {'ok': True, 'reply': f"✅ Скриншот создан: {sc.get('path', out_path)}"}
            return {'ok': False, 'reply': f"❌ Ошибка при создании скриншота: {sc}"}

        # 4) Excel
        if ('excel' in lower or 'xlsx' in lower) and \
           ('запиш' in lower or 'сохрани' in lower or 'write' in lower or 'создай' in lower):
            now = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            rows = [{'metric': 'timestamp', 'value': now},
                    {'metric': 'status', 'value': 'ok'},
                    {'metric': 'source', 'value': 'web_queue'}]
            out_path = os.path.join('outputs', f'data_{int(time.time())}.xlsx')
            xr = tool_layer.use('spreadsheet', action='write', path=out_path, rows=rows, sheet='Data')
            if isinstance(xr, dict) and xr.get('success'):
                return {'ok': True, 'reply': f"✅ Excel-файл создан: {xr.get('path', out_path)}"}
            return {'ok': False, 'reply': f"❌ Ошибка при записи Excel: {xr}"}

        # 6) Перевести текст
        if 'перевед' in lower or 'переведи' in lower or 'translate' in lower:
            target_map = [
                (['английск', 'english', ' en'], 'english'),
                (['немецк', 'german'], 'german'),
                (['французск', 'french'], 'french'),
                (['испанск', 'spanish'], 'spanish'),
                (['японск', 'japanese'], 'japanese'),
                (['китайск', 'chinese'], 'chinese'),
            ]
            target = 'russian'
            for keywords, lang in target_map:
                if any(k in lower for k in keywords):
                    target = lang
                    break
            q_m = re.search(r'[«"\'"](.+?)[»"\'"]', m)
            colon_m = re.search(r'[:：]\s*(.+)$', m)
            text_to_translate = q_m.group(1) if q_m else (colon_m.group(1) if colon_m else m)
            tr = tool_layer.use('translate', text=text_to_translate, target=target)
            if isinstance(tr, dict) and tr.get('success'):
                return {'ok': True, 'reply': f"✅ Перевод ({target}):\n{tr.get('translated', tr.get('translation', tr.get('result', '')))}"}
            if isinstance(tr, str):
                return {'ok': True, 'reply': f"✅ Перевод: {tr}"}
            return {'ok': False, 'reply': f"❌ Ошибка перевода: {tr}"}

        # 21) PowerPoint презентация
        if 'презентац' in lower or 'powerpoint' in lower or 'pptx' in lower or '.ppt' in lower:
            now_s = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            out_path = os.path.join('outputs', f'presentation_{int(time.time())}.pptx')
            slides = [
                {'title': 'Автономный AI-агент', 'content': f'Создано: {now_s}'},
                {'title': 'Архитектура', 'content': '48 модулей, 28 инструментов'},
                {'title': 'Инструменты', 'content': 'PDF, Excel, поиск, перевод, скриншот...'},
            ]
            pr = tool_layer.use('powerpoint', action='create', output=out_path, slides=slides)
            if isinstance(pr, dict) and pr.get('success'):
                return {'ok': True, 'reply': f"✅ Презентация создана: {pr.get('path', out_path)}"}
            return {'ok': False, 'reply': f"❌ Ошибка PowerPoint: {pr}"}

        # 22) PDF из произвольного текста
        if 'pdf' in lower and ('создай' in lower or 'create' in lower or 'сделай' in lower or 'сгенери' in lower):
            title_m = re.search(r'(?:с заголовком|title)[:\s]+([\w\s]+)', m, re.IGNORECASE)
            title = title_m.group(1).strip() if title_m else 'Документ'
            content_m = re.search(r'(?:содержимым|текстом|content)[:\s]+(.+)', m, re.IGNORECASE)
            content = content_m.group(1).strip() if content_m else f'Документ создан: {time.strftime("%Y-%m-%d %H:%M:%S")}'
            out_path = os.path.join('outputs', f'doc_{int(time.time())}.pdf')
            pr = tool_layer.use('pdf_generator', action='from_text', output=out_path, title=title, text=content)
            if isinstance(pr, dict) and pr.get('success'):
                return {'ok': True, 'reply': f"✅ PDF создан: {pr.get('path', out_path)}"}
            return {'ok': False, 'reply': f"❌ Ошибка PDF: {pr}"}

        return None

    def _qt_system_info(self, lower: str, _m: str, tool_layer) -> dict | None:
        """Системные метрики: CPU/RAM/диск и список процессов."""
        if ('провер' in lower or 'check' in lower or 'покажи' in lower or 'show' in lower) and \
           ('cpu' in lower or 'ram' in lower or 'систем' in lower or
                'памят' in lower or 'диск' in lower or 'disk' in lower):
            sr = tool_layer.use('process_manager', action='system')
            if isinstance(sr, dict) and sr.get('success'):
                return {'ok': True, 'reply': (
                    "🖥 Состояние системы:\n"
                    f"CPU: {sr.get('cpu_percent', 'n/a')}%\n"
                    f"RAM: {sr.get('memory_percent', 'n/a')}% (свободно {sr.get('memory_available_mb', 'n/a')} MB)\n"
                    f"Диск: {sr.get('disk_percent', 'n/a')}% (свободно {sr.get('disk_free_gb', 'n/a')} GB)"
                )}
            return {'ok': False, 'reply': f"❌ Ошибка получения метрик: {sr}"}

        if ('список' in lower or 'list' in lower or 'покажи' in lower) and \
           ('процес' in lower or 'process' in lower or 'запущен' in lower or 'task' in lower):
            pr = tool_layer.use('process_manager', action='list')
            if isinstance(pr, dict) and pr.get('success'):
                procs = pr.get('processes', [])[:10]
                lines = [f"🔁 Топ-10 процессов (всего {pr.get('total', '?')}):\n"]
                for p in procs:
                    lines.append(
                        f"  {p.get('pid','?')} {p.get('name','?')} — "
                        f"cpu:{p.get('cpu_percent',0):.1f}% ram:{p.get('memory_percent',0):.1f}%"
                    )
                return {'ok': True, 'reply': '\n'.join(lines)}
            return {'ok': False, 'reply': f"❌ Ошибка: {pr}"}

        return None

    def _qt_git(self, lower: str, _m: str, tool_layer) -> dict | None:
        """Git: status, log, branches."""
        if 'git' not in lower:
            return None
        if 'status' in lower or 'статус' in lower or 'состояни' in lower:
            gr = tool_layer.use('git', action='status')
            if isinstance(gr, dict) and gr.get('success'):
                return {'ok': True, 'reply': f"✅ Git status:\n{gr.get('stdout', '').strip() or '(нет изменений)'}"}
            return {'ok': False, 'reply': f"❌ Ошибка git: {gr.get('stderr', gr)}"}
        if 'log' in lower or 'история' in lower or 'коммит' in lower:
            gr = tool_layer.use('git', action='log', n=10)
            if isinstance(gr, dict) and gr.get('success'):
                return {'ok': True, 'reply': f"📜 Git log (последние 10):\n{gr.get('stdout', '').strip() or '(коммитов нет)'}"}
            return {'ok': False, 'reply': f"❌ Ошибка git: {gr.get('stderr', gr)}"}
        if 'ветк' in lower or 'branch' in lower:
            gr = tool_layer.use('git', action='branch')
            if isinstance(gr, dict) and gr.get('success'):
                return {'ok': True, 'reply': f"🌿 Git branches:\n{gr.get('stdout', '').strip() or '(нет веток)'}"}
            return {'ok': False, 'reply': f"❌ Ошибка git: {gr.get('stderr', gr)}"}
        return None

    def _qt_network(self, lower: str, m: str, tool_layer) -> dict | None:
        """Ping, DNS, port check, HTTP GET."""
        if 'пинг' in lower or 'ping' in lower:
            host_m = re.search(r'(?:пинг|ping)\s+([\w\-\.]+)', lower)
            host = host_m.group(1) if host_m else 'google.com'
            nr = tool_layer.use('network', action='ping', host=host, count=3)
            if isinstance(nr, dict) and nr.get('success'):
                icon = '✅' if nr.get('reachable') else '❌'
                status = 'доступен' if nr.get('reachable') else 'недоступен'
                return {'ok': True, 'reply': f"{icon} Ping {host}: {status}\n{nr.get('stdout','')[:300]}"}
            return {'ok': False, 'reply': f"❌ Ошибка ping: {nr}"}

        if 'dns' in lower or ('ip' in lower and ('узнай' in lower or 'получи' in lower or 'resolve' in lower)):
            host_m = re.search(r'(?:dns|ip)\s+([\w\-\.]+)', lower)
            host = host_m.group(1) if host_m else 'google.com'
            nr = tool_layer.use('network', action='dns', hostname=host)
            if isinstance(nr, dict) and nr.get('success'):
                return {'ok': True, 'reply': f"✅ DNS: {nr.get('hostname')} → {nr.get('ip')}"}
            return {'ok': False, 'reply': f"❌ DNS ошибка: {nr}"}

        if 'порт' in lower or 'port' in lower:
            port_m = re.search(r'\b(\d{2,5})\b', lower)
            host_m = re.search(r'(?:порт|port)[^\d]*\d+[^\w]*([\w\-\.]+)', lower)
            port = int(port_m.group(1)) if port_m else 80
            host = host_m.group(1) if host_m else 'localhost'
            nr = tool_layer.use('network', action='port_check', host=host, port=port)
            if isinstance(nr, dict) and nr.get('success'):
                icon = '✅' if nr.get('open') else '🔒'
                state = 'открыт' if nr.get('open') else 'закрыт'
                return {'ok': True, 'reply': f"{icon} Порт {nr.get('port')} на {nr.get('host')}: {state}"}
            return {'ok': False, 'reply': f"❌ Ошибка: {nr}"}

        if ('http' in lower or 'запрос' in lower or 'request' in lower or 'url' in lower) and \
           ('get' in lower or 'сделай' in lower or 'отправ' in lower):
            url_m = re.search(r'https?://[\w\-\.\/\?\=\&\%\+\#]+', m)
            url = url_m.group(0) if url_m else 'https://httpbin.org/get'
            nr = tool_layer.use('network', action='http', url=url, method='GET')
            if isinstance(nr, dict) and nr.get('success'):
                content = str(nr.get('content', ''))[:300]
                return {'ok': True, 'reply': f"✅ HTTP GET {url}\nСтатус: {nr.get('status_code', '?')}\n{content}"}
            return {'ok': False, 'reply': f"❌ HTTP ошибка: {nr}"}

        return None

    def _qt_filesystem(self, lower: str, m: str, tool_layer) -> dict | None:
        """Создание, чтение, список файлов."""
        if ('создай' in lower or 'создать' in lower or 'create' in lower) and \
           ('файл' in lower or 'file' in lower or '.txt' in lower or '.md' in lower):
            fname_m = re.search(r'([\w\-]+\.\w+)', m)
            fname = fname_m.group(1) if fname_m else f'file_{int(time.time())}.txt'
            content_m = re.search(r'(?:со[дд]ержимым|content|текстом)[:\s]+(.+)', m, re.IGNORECASE)
            content = content_m.group(1).strip() if content_m else f'Файл создан агентом {time.strftime("%Y-%m-%d %H:%M:%S")}'
            out_path = os.path.join('outputs', fname)
            fr = tool_layer.use('filesystem', action='write', path=out_path, content=content)
            if isinstance(fr, dict) and fr.get('success'):
                return {'ok': True, 'reply': f"✅ Файл создан: {fr.get('written', out_path)}"}
            return {'ok': False, 'reply': f"❌ Ошибка создания файла: {fr}"}

        if ('прочитай' in lower or 'читай' in lower or 'read' in lower or 'открой' in lower) and \
           ('файл' in lower or 'file' in lower):
            path_m = re.search(r'([\w\-\/\\\.]+\.\w+)', m)
            if not path_m:
                return None
            fpath = path_m.group(1)
            fr = tool_layer.use('filesystem', action='read', path=fpath)
            if isinstance(fr, dict) and fr.get('success'):
                return {'ok': True, 'reply': f"📄 Содержимое файла {fpath}:\n{(fr.get('content') or '')[:1000]}"}
            return {'ok': False, 'reply': f"❌ Ошибка чтения файла: {fr}"}

        if ('список' in lower or 'list' in lower or 'покажи' in lower or 'что' in lower) and \
           ('файл' in lower or 'папк' in lower or 'директори' in lower or 'director' in lower):
            dir_m = re.search(r'(?:папк|директори|director|folder|dir)[уаеи]*\s+([\w\-\/\\\.]+)', lower)
            dpath = dir_m.group(1) if dir_m else 'outputs'
            fr = tool_layer.use('filesystem', action='list', path=dpath)
            if isinstance(fr, dict) and fr.get('success'):
                entries = fr.get('entries', [])
                return {'ok': True, 'reply': f"📁 {dpath} ({len(entries)} файлов):\n" + '\n'.join(f"  {e}" for e in entries[:30])}
            return {'ok': False, 'reply': f"❌ Ошибка: {fr}"}

        return None

    def _qt_execute_code(self, lower: str, m: str, tool_layer) -> dict | None:
        """Python код, терминальные команды, установка пакетов."""
        if ('выполни' in lower or 'запусти' in lower or 'выполн' in lower or
                'run' in lower or 'execute' in lower) and \
           ('python' in lower or 'код' in lower or 'скрипт' in lower or 'code' in lower):
            code_m = re.search(r'```(?:python)?\s*([\s\S]+?)```', m)
            inline_m = re.search(r'(?:код|code)[:\s]+(.+)', m, re.IGNORECASE)
            if code_m:
                code = code_m.group(1).strip()
            elif inline_m:
                code = inline_m.group(1).strip()
            else:
                code = 'import sys; print(f"Python {sys.version}")'
            pr = tool_layer.use('python', code=code)
            if isinstance(pr, dict) and pr.get('success'):
                out = str(pr.get('output') or pr.get('stdout') or pr.get('result') or '')[:500]
                return {'ok': True, 'reply': f"✅ Python результат:\n{out}"}
            return {'ok': False, 'reply': f"❌ Ошибка Python: {pr}"}

        if ('выполни' in lower or 'запусти' in lower or 'run' in lower) and \
           ('команд' in lower or 'terminal' in lower or 'shell' in lower or 'консол' in lower):
            cmd_m = re.search(r'["`\'«]([\w\-\.\s\/\\]+)["`\'»]', m)
            cmd_colon = re.search(r'(?:команд|terminal|shell)[:\s]+(.+)', m, re.IGNORECASE)
            if cmd_m:
                cmd = cmd_m.group(1).strip()
            elif cmd_colon:
                cmd = cmd_colon.group(1).strip()
            else:
                return None
            tr = tool_layer.use('terminal', command=cmd, timeout=15)
            if isinstance(tr, dict) and tr.get('success'):
                return {'ok': True, 'reply': f"✅ Команда '{cmd}':\n{(tr.get('stdout') or '').strip()[:500]}"}
            err = (tr.get('stderr') or tr.get('error') or '')[:300]
            return {'ok': False, 'reply': f"❌ Ошибка команды '{cmd}': {err}"}

        if ('установ' in lower or 'install' in lower or 'pip' in lower) and \
           ('пакет' in lower or 'package' in lower or 'библи' in lower or 'lib' in lower):
            pkg_m = re.search(r'(?:install|установ\w*)[:\s]+([\w\-\[\]\.>=<,\s]+)', lower)
            if not pkg_m:
                return None
            pkg_name = pkg_m.group(1).strip().split()[0]
            pkr = tool_layer.use('package_manager', action='install', package=pkg_name)
            if isinstance(pkr, dict) and pkr.get('success'):
                return {'ok': True, 'reply': f"✅ Пакет {pkg_name} установлен"}
            err = str(pkr.get('error') or pkr.get('stderr') or pkr)[:200]
            return {'ok': False, 'reply': f"❌ Ошибка установки {pkg_name}: {err}"}

        return None

    # Данные городов для _qt_misc (время/часовой пояс)
    _CITY_TZ: dict[str, str] = {
        'тель-ави': 'Asia/Jerusalem', 'тель ави': 'Asia/Jerusalem',
        'tel aviv': 'Asia/Jerusalem', 'telaviv': 'Asia/Jerusalem',
        'иерусали': 'Asia/Jerusalem', 'jerusalem': 'Asia/Jerusalem',
        'израил': 'Asia/Jerusalem', 'israel': 'Asia/Jerusalem',
        'нью-йорк': 'America/New_York', 'нью йорк': 'America/New_York',
        'new york': 'America/New_York', 'newyork': 'America/New_York',
        'москва': 'Europe/Moscow', 'moscow': 'Europe/Moscow',
        'лондон': 'Europe/London', 'london': 'Europe/London',
        'париж': 'Europe/Paris', 'paris': 'Europe/Paris',
        'берлин': 'Europe/Berlin', 'berlin': 'Europe/Berlin',
        'токио': 'Asia/Tokyo', 'tokyo': 'Asia/Tokyo',
        'пекин': 'Asia/Shanghai', 'beijing': 'Asia/Shanghai',
        'шанхай': 'Asia/Shanghai', 'shanghai': 'Asia/Shanghai',
        'дубай': 'Asia/Dubai', 'dubai': 'Asia/Dubai',
        'сингапур': 'Asia/Singapore', 'singapore': 'Asia/Singapore',
        'сидней': 'Australia/Sydney', 'sydney': 'Australia/Sydney',
        'лос-анджел': 'America/Los_Angeles', 'los angeles': 'America/Los_Angeles',
        'чикаго': 'America/Chicago', 'chicago': 'America/Chicago',
        'киев': 'Europe/Kiev', 'kyiv': 'Europe/Kiev',
        'минск': 'Europe/Minsk', 'minsk': 'Europe/Minsk',
        'варшав': 'Europe/Warsaw', 'warsaw': 'Europe/Warsaw',
        'стамбул': 'Europe/Istanbul', 'istanbul': 'Europe/Istanbul',
    }
    _CITY_DISPLAY: dict[str, str] = {
        'Asia/Jerusalem': 'Тель-Авив', 'America/New_York': 'Нью-Йорк',
        'Europe/Moscow': 'Москва', 'Europe/London': 'Лондон',
        'Europe/Paris': 'Париж', 'Europe/Berlin': 'Берлин',
        'Asia/Tokyo': 'Токио', 'Asia/Shanghai': 'Пекин/Шанхай',
        'Asia/Dubai': 'Дубай', 'Asia/Singapore': 'Сингапур',
        'Australia/Sydney': 'Сидней', 'America/Los_Angeles': 'Лос-Анджелес',
        'America/Chicago': 'Чикаго', 'Europe/Kiev': 'Киев',
        'Europe/Minsk': 'Минск', 'Europe/Warsaw': 'Варшава',
        'Europe/Istanbul': 'Стамбул',
    }

    @staticmethod
    def _city_datetime(tz_name: str):
        """Возвращает datetime для часового пояса или None."""
        try:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except ImportError:
                import pytz  # type: ignore[import-untyped]
                tz = pytz.timezone(tz_name)
            import datetime as _dt
            return _dt.datetime.now(tz)
        except (ImportError, KeyError, OSError, ValueError):
            return None

    def _qt_misc(self, lower: str, m: str, tool_layer) -> dict | None:
        """Время/часовой пояс, буфер обмена, общий поиск."""
        _time_kw = (
            'который час', 'сколько времени', 'текущее время', 'какое время',
            'локальное время', 'время в', 'время сейчас', 'время там',
            'часовой пояс', 'разница во времени', 'разница времени',
            'timezone', 'time zone', 'utc', 'what time', 'current time',
            'local time', 'time in', 'time difference',
        )
        if any(kw in lower for kw in _time_kw):
            import datetime as _dt
            found: dict[str, object] = {}
            for kw, tz_name in self._CITY_TZ.items():
                if kw in lower:
                    dt = self._city_datetime(tz_name)
                    if dt:
                        found[self._CITY_DISPLAY.get(tz_name, tz_name)] = dt
            lines: list[str] = []
            if found:
                for city, dt in found.items():
                    lines.append(f"🕐 {city}: {dt.strftime('%H:%M:%S %d.%m.%Y %Z')}")  # type: ignore[union-attr]
                if len(found) == 2:
                    dts = list(found.values())
                    try:
                        off0 = dts[0].utcoffset().total_seconds() / 3600  # type: ignore[union-attr]
                        off1 = dts[1].utcoffset().total_seconds() / 3600  # type: ignore[union-attr]
                        diff = abs(off0 - off1)
                        h, mins = int(diff), int((diff - int(diff)) * 60)
                        cities = list(found.keys())
                        diff_str = f"{h}ч" + (f" {mins}мин" if mins else "")
                        ahead = cities[0] if off0 > off1 else cities[1]
                        behind = cities[1] if off0 > off1 else cities[0]
                        lines += [f"\n⏱ Разница: {diff_str}",
                                  f"   {ahead} опережает {behind} на {diff_str}"]
                    except (AttributeError, TypeError):
                        pass
            else:
                now_local = _dt.datetime.now()
                lines = [
                    f"🕐 Местное время: {now_local.strftime('%H:%M:%S %d.%m.%Y')} ({time.strftime('%Z %z')})",
                    f"🌐 UTC: {_dt.datetime.utcnow().strftime('%H:%M:%S %d.%m.%Y')}",
                ]
            return {'ok': True, 'reply': '\n'.join(lines)}

        if 'буфер' in lower or 'clipboard' in lower:
            if 'запиш' in lower or 'скопируй' in lower or 'copy' in lower or 'set' in lower or 'write' in lower:
                text_m = re.search(r'[«"\'"](.+?)[»"\'"]', m) or re.search(r'[:：]\s*(.+)$', m)
                text = text_m.group(1) if text_m else 'Привет от агента!'
                cr = tool_layer.use('clipboard', action='write', text=text)
                if isinstance(cr, dict) and cr.get('success'):
                    return {'ok': True, 'reply': f"✅ В буфер скопировано: {text[:100]}"}
                return {'ok': False, 'reply': f"❌ Ошибка буфера: {cr}"}
            cr = tool_layer.use('clipboard', action='read')
            if isinstance(cr, dict) and cr.get('success'):
                return {'ok': True, 'reply': f"📋 Буфер обмена: {cr.get('text', '')[:500]}"}
            return {'ok': False, 'reply': f"❌ Ошибка чтения буфера: {cr}"}

        if 'поищи' in lower or 'найди' in lower or 'поиск' in lower or 'search' in lower:
            query = re.sub(
                r'^(?:поищи|найди|поиск|search|найдите|найти)\s+(?:мне\s+)?(?:информацию\s+(?:о|про|об)\s+)?',
                '', m, flags=re.IGNORECASE,
            ).strip()
            query = re.sub(r'\s+и\s+(?:скажи|покажи|расскажи|напиши|выведи|дай|объясни|опиши|сообщи).*$',
                           '', query, flags=re.IGNORECASE).strip()
            query = re.sub(r'\s+(?:в моем городе|для меня|пожалуйста|please|прямо сейчас|сейчас)$',
                           '', query, flags=re.IGNORECASE).strip()
            query = re.sub(r',?\s+(?:нужен ли|стоит ли взять|стоит брать|need umbrella|umbrella needed).*$',
                           '', query, flags=re.IGNORECASE).strip()
            if not query:
                return None
            sr = tool_layer.use('search', query=query, num_results=5)
            if isinstance(sr, dict) and sr.get('success'):
                results = (sr.get('results') or [])[:5]
                lines2 = [f"🔍 Результаты поиска: «{query[:60]}»"]
                for i, item in enumerate(results, 1):
                    lines2.append(f"{i}. {item.get('title', 'без названия')} — {item.get('url', '')}")
                return {'ok': True, 'reply': '\n'.join(lines2)}
            return {'ok': False, 'reply': f"❌ Поиск не удался: {sr}"}

        return None

    _CURRENCY_PAIRS = {
        # (from_code, to_code): [(ключевые слова в запросе)]
        ('USD', 'ILS'): ['usd.*ils', 'доллар.*шекел', 'шекел.*доллар', r'\$.*шекел',
                         'dollar.*shekel', 'shekel.*dollar', 'usd to ils', 'dollar to shekel'],
        ('USD', 'EUR'): ['usd.*eur', 'доллар.*евро', 'евро.*доллар', 'dollar.*euro'],
        ('USD', 'RUB'): ['usd.*rub', 'доллар.*рубл', 'рубл.*доллар', 'dollar.*ruble'],
        ('EUR', 'USD'): ['eur.*usd', 'евро.*доллар'],
        ('EUR', 'ILS'): ['eur.*ils', 'евро.*шекел', 'euro.*shekel'],
        ('BTC', 'USD'): ['btc.*usd', 'биткоин.*доллар', 'bitcoin.*dollar'],
    }

    def _handle_currency(self, message: str) -> dict | None:
        """
        Быстрый обработчик запросов курсов валют.
        Вызывает api.frankfurter.app напрямую без LLM.
        Также ищет сумму для конвертации ("250 долларов", "100 USD" и т.д.)
        """
        _re = re  # re импортирован на уровне модуля (line 31)
        lower = message.lower()

        # Определяем пару валют
        pair = None
        for (frm, to), patterns in self._CURRENCY_PAIRS.items():
            for pat in patterns:
                if re.search(pat, lower):
                    pair = (frm, to)
                    break
            if pair:
                break

        if not pair:
            return None

        from_code, to_code = pair
        # Ищем сумму в запросе: "250 долларов", "100 USD", "1000$" и т.д.
        amount = None
        amt_match = _re.search(r'(\d+(?:[.,]\d+)?)\s*(?:долларов?|шекелей|рублей|евро|'
                               r'usd|ils|eur|rub|btc|\$|₪|€|₽)', lower)
        if amt_match:
            try:
                amount = float(amt_match.group(1).replace(',', '.'))
            except ValueError:
                pass

        # Получаем курс — всегда urllib напрямую с явным таймаутом 8с
        # (tool_layer.http_client не имеет таймаута → может зависнуть на 240с)
        try:
            import urllib.request as _ur
            url = f'https://api.frankfurter.app/latest?from={from_code}&to={to_code}'
            with _ur.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            rate = data.get('rates', {}).get(to_code)
            date = data.get('date', '')
            if rate is None:
                return None
        except Exception:
            return None  # при любой сетевой ошибке — упасть на LLM

        lines = [f"💱 Курс на {date}:  1 {from_code} = {rate} {to_code}"]
        if amount is not None:
            converted = round(amount * rate, 2)
            sym = {'USD': '$', 'ILS': '₪', 'EUR': '€', 'RUB': '₽', 'BTC': '₿'}
            s_from = sym.get(from_code, from_code)
            s_to = sym.get(to_code, to_code)
            lines.append(f"📊 {amount} {s_from} = **{converted} {s_to}**")
        lines.append("_(источник: frankfurter.app)_")
        return {'ok': True, 'reply': '\n'.join(lines)}

    def _handle_chat(self, data: dict) -> dict:
        message = str(data.get('message', '')).strip()
        filename = str(data.get('filename') or '').strip()
        file_content = data.get('file_content')

        # ── Slash-команды: обрабатываем локально, не отправляем в LLM ──────────
        if message.startswith('/'):
            cmd = message.strip().lower()
            if cmd in ('/status', '/stat', '/статус'):
                s = self._handle_status()
                loop = s.get('cycle', '?')
                goal = s.get('goal', '') or '—'
                mem = s.get('persistent_memory_text', '')
                lines = ["📊 Статус агента\n━━━━━━━━━━━━━━━━━━━━",
                         f"Циклов выполнено: {loop}",
                         f"Цель: {goal[:120]}"]
                if mem:
                    lines.append(f"\n🧠 Память:\n{mem}")
                return {'ok': True, 'reply': '\n'.join(lines)}
            if cmd in ('/help', '/помощь'):
                return {'ok': True, 'reply': (
                    "Команды:\n"
                    "/status — состояние агента\n"
                    "/goal <текст> — поставить цель\n"
                    "/help — эта справка\n\n"
                    "Или просто напиши задачу — агент выполнит её."
                )}
            if cmd.startswith('/goal '):
                goal_text = message[6:].strip()
                result = self._handle_goal({'goal': goal_text})
                return {'ok': True, 'reply': f"✅ Цель установлена: {goal_text}" if result.get('ok') else f"❌ {result.get('error', 'Ошибка')}"}
            # Неизвестная команда — отвечаем сами, не трогаем LLM
            return {'ok': True, 'reply': f"Неизвестная команда: {message}\nВведи /help для справки."}

        if filename and file_content is not None:
            # Сохраняем файл на диск
            try:
                _uploads = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), '..', 'outputs', 'uploads'
                )
                os.makedirs(_uploads, exist_ok=True)
                _safe = ''.join(c for c in filename if c.isalnum() or c in '._- ')[:120]
                with open(os.path.join(_uploads, _safe), 'w', encoding='utf-8', errors='replace') as _f:
                    _f.write(str(file_content))
            except Exception:
                pass
            # Прикрепляем содержимое к сообщению
            _body = str(file_content)[:100_000]
            file_prefix = f"[Файл: {filename}]\n{'─' * 40}\n{_body}\n{'─' * 40}\n"
            message = (file_prefix + message) if message else file_prefix.rstrip()

        if not message:
            return {'ok': False, 'reply': 'Пустое сообщение.'}

        # ── Ранний выход: Gmail / Google Calendar не настроены ───────────────
        _msg_lower = message.lower()
        _google_svc_kw = (
            'gmail', 'отправь письмо', 'отправь email', 'напиши письмо',
            'google calendar', 'гугл календарь', 'добавь встречу в календарь',
            'запланируй встречу в google', 'google contacts', 'гугл контакты',
        )
        if any(kw in _msg_lower for kw in _google_svc_kw):
            _creds = os.environ.get('GOOGLE_CREDENTIALS_PATH', 'config/credentials.json')
            _token_file = os.path.join(os.path.dirname(_creds), 'token.json')
            if not os.path.exists(_creds) and not os.path.exists(_token_file):
                _svc = 'Gmail' if ('gmail' in _msg_lower or 'письм' in _msg_lower or 'email' in _msg_lower) else 'Google Calendar/Contacts'
                _reply = (
                    f"❌ {_svc} не настроен — отсутствуют OAuth-учётные данные.\n\n"
                    "Для настройки:\n"
                    "1. Google Cloud Console → создай проект\n"
                    "2. Включи Gmail API / Calendar API\n"
                    "3. Создай OAuth 2.0 Desktop credentials\n"
                    "4. Скачай credentials.json → положи в config/\n"
                    "5. Запусти: python -m tools.google_auth\n\n"
                    "После настройки задача выполнится автоматически."
                )
                self._add_history('user', message)
                self._add_history('agent', _reply)
                return {'ok': False, 'reply': _reply}

        # Быстрый путь: курсы валют (без LLM, прямой вызов API)
        currency = self._handle_currency(message)
        if currency is not None:
            self._add_history('user', message)
            self._add_history('agent', currency.get('reply', ''))
            return currency

        # ── Ранний выход: задача требует файл, но путь не указан ─────────────
        _file_op_kw = (
            'прочитай pdf', 'открой pdf', 'анализ pdf', 'работа с pdf',
            'извлеки из pdf', 'распарси pdf', 'читай pdf', 'разбери pdf',
            'конвертируй изображение', 'преобразуй изображение',
            'обработай изображение', 'конвертируй фото', 'resize изображение',
            'распарси json', 'прочитай json', 'обработай json файл',
            'распарси xml', 'прочитай xml', 'обработай xml файл',
            'прочитай csv', 'обработай csv', 'анализируй csv файл',
            'открой docx', 'прочитай docx', 'открой word',
        )
        _has_file_path = bool(re.search(r'[\w\-\/\\]+\.\w{2,5}', message))
        if not filename and any(kw in _msg_lower for kw in _file_op_kw) and not _has_file_path:
            _op_reply = (
                "❌ Для этой операции нужен конкретный файл, но путь не указан.\n\n"
                "Как указать файл:\n"
                "• Напиши путь прямо в сообщении: outputs/document.pdf\n"
                "• Или прикрепи файл через 📎 (кнопка слева от поля ввода)\n\n"
                "Пример: «прочитай pdf outputs/report.pdf»"
            )
            self._add_history('user', message)
            self._add_history('agent', _op_reply)
            return {'ok': False, 'reply': _op_reply}

        # Быстрый детерминированный путь для типовых web-задач
        quick = self._handle_quick_task(message)
        if quick is not None:
            self._add_history('user', message)
            self._add_history('agent', quick.get('reply', ''))
            return quick

        self._add_history('user', message)

        # Строим историю последних 10 обменов для LLM (исключаем текущее сообщение)
        history = []
        with self._lock:
            recent = list(self._history[-21:-1])
        for h in recent:  # последние 20 без текущего
            if h.get('role') == 'user':
                history.append({'role': 'user', 'content': h.get('text', '')})
            elif h.get('role') == 'agent':
                history.append({'role': 'assistant', 'content': h.get('text', '')})

        _web_system = (
            "Ты — автономный AI-агент Андрея. Ты УЖЕ существуешь и работаешь. "
            "У тебя 48 инструментов: PDF, Excel, скриншоты, поиск, git, docker, SSH и др. "
            "Говори от ПЕРВОГО ЛИЦА. НИКОГДА не говори 'агент сделает' — ТЫ и есть агент. "
            "НИКОГДА не предлагай 'создать агента' — ты уже создан и работаешь. "
            "Отвечай коротко и конкретно. Язык ответа = язык вопроса.\n"
            "КРИТИЧНО: НИКОГДА не говори 'я не могу предоставить актуальные данные', "
            "'у меня нет доступа к реальному времени', 'обратитесь к другим источникам'. "
            "Для любых реальных данных (курсы валют, погода, цены, новости) ты ИСПОЛЬЗУЕШЬ "
            "инструменты: http_client для API, search для поиска. "
            "Если не можешь выполнить сам — скажи КОНКРЕТНО что нужно сделать, а не отказывай."
        )

        reply = ''
        try:
            if self.cognitive_core:
                if self._chat_executor:
                    future = self._chat_executor.submit(
                        self.cognitive_core.converse,
                        message,
                        _web_system,
                        history,
                    )
                    reply = future.result(timeout=self._chat_timeout_sec)
                else:
                    reply = self.cognitive_core.converse(message, system=_web_system, history=history)
            else:
                reply = 'Cognitive Core не подключён.'
        except FuturesTimeoutError:
            self._log(
                f'Таймаут /chat после {self._chat_timeout_sec:.0f}с: запрос слишком долгий.',
                level='warning',
            )
            reply = (
                f'Ответ занял слишком много времени (>{self._chat_timeout_sec:.0f}с) и был прерван. '
                'Попробуй разбить задачу на 1-2 шага или увеличь WEB_CHAT_TIMEOUT_SEC в .env.'
            )
        except Exception:
            self._log('Ошибка обработки чата в cognitive_core.converse', level='error')
            reply = 'Внутренняя ошибка обработки запроса.'

        self._add_history('agent', reply)
        if self.persistent_brain:
            try:
                self.persistent_brain.record_conversation(
                    role='user', message=message, response=reply
                )
            except Exception:
                pass
        return {'ok': True, 'reply': reply}

    def _handle_status(self) -> dict:
        status: dict = {'ok': True}
        try:
            if self.loop:
                status['cycle'] = getattr(self.loop, '_cycle_count', 0)
                status['running'] = getattr(self.loop, '_running', False)
                status['goal'] = str(getattr(self.loop, '_goal', '') or '')[:100]
            if self.monitoring:
                if callable(getattr(self.monitoring, 'summary', None)):
                    monitoring_summary = self.monitoring.summary()  # type: ignore[union-attr]
                    status['monitoring'] = monitoring_summary
                    if isinstance(monitoring_summary, dict):
                        status['core_smoke'] = monitoring_summary.get('core_smoke', {})
                elif callable(getattr(self.monitoring, 'get_stats', None)):
                    status['monitoring'] = self.monitoring.get_stats()  # type: ignore[union-attr]
            if self.persistent_brain:
                status['persistent_memory'] = self.persistent_brain.summary(
                    max_solver_types=3,
                    max_challengers_per_solver=2,
                )
                status['persistent_memory_text'] = self.persistent_brain.compact_status_text(
                    max_solver_types=3,
                    max_challengers_per_solver=1,
                    max_chars=320,
                )
        except Exception as e:
            status['error'] = str(e)
        return status

    def _handle_goal(self, data: dict) -> dict:
        goal = str(data.get('goal', '')).strip()
        if not goal:
            return {'ok': False, 'error': 'Цель не указана.'}
        try:
            if self.loop:
                self.loop.set_goal(goal)
                return {'ok': True, 'message': f'Цель установлена: {goal[:80]}'}
            return {'ok': False, 'error': 'Autonomous Loop не подключён.'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ── Вспомогательные ───────────────────────────────────────────────────────

    def _add_history(self, role: str, text: str) -> None:
        with self._lock:
            self._history.append({
                'id':   str(uuid.uuid4())[:8],
                'role': role,
                'text': text,
                'ts':   time.time(),
            })
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history:]

    def _log(self, msg: str, level: str = 'info') -> None:
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(msg, source='web_interface')
        else:
            print(f"[web_interface] {msg}")
