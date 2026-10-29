(() => {
  'use strict';

  const DEFAULT_BASE = 'http://localhost:8000';
  const LS_KEY = 'mydeskmate_backend';

  const STYLE = `
    #panel {
      position: fixed; right: 16px; top: 76px; z-index: 2147483647;
      width: min(440px, 38vw); max-height: 76vh;
      display: flex; flex-direction: column;

      overflow: hidden;
      background: #1a1a1a; color: #e0e0e0;
      border: 1px solid #333; border-radius: 10px;
      box-shadow: 0 8px 28px rgba(0,0,0,.55);
      box-sizing: border-box;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 13px; line-height: 1.5;
    }

    #panel.ball { width: 52px; height: 52px; border-radius: 50%; cursor: grab; user-select: none; }
    #panel.ball #header, #panel.ball #body { display: none; }
    #panel.dragging, #panel.dragging #header, #panel.dragging #ball { cursor: grabbing; }

    #panel.ball.busy { animation: ball-pulse 1.8s ease-in-out infinite; }
    @keyframes ball-pulse {
      0%, 100% { box-shadow: 0 8px 28px rgba(0,0,0,.55), 0 0 0 0 rgba(251,191,36,.5); }
      50% { box-shadow: 0 8px 28px rgba(0,0,0,.55), 0 0 0 9px rgba(251,191,36,0); }
    }

    #ball { display: none; flex: 1; align-items: center; justify-content: center; position: relative; cursor: grab; touch-action: none; }
    #panel.ball #ball { display: flex; }
    #ball-glyph { font-size: 18px; color: #9ca3af; line-height: 1; }
    #panel.ball.busy #ball-glyph { color: #fbbf24; }
    #ball-dot { position: absolute; right: 6px; bottom: 6px; }
    #header { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid #2a2a2a; flex: none; cursor: grab; touch-action: none; user-select: none; }
    #title { font-weight: 600; font-size: 13px; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #555; flex: none; }
    .dot.amber { background: #fbbf24; }
    .dot.green { background: #4ade80; }
    .dot.red { background: #f87171; }
    #min-btn { background: none; border: none; color: #888; cursor: pointer; font-size: 14px; line-height: 1; padding: 2px 6px; }
    #min-btn:hover { color: #ddd; }

    #body { display: flex; flex-direction: row; flex: 1; min-height: 0; }


    #rail { width: 140px; flex: none; border-right: 1px solid #2a2a2a; display: flex; flex-direction: column; min-height: 0; }
    #rail-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; font-size: 12px; color: #888; flex: none; }
    #new-conv-btn { background: none; border: 1px solid #3a3a3a; color: #aaa; border-radius: 5px; cursor: pointer; font-size: 12px; line-height: 1; padding: 3px 7px; }
    #new-conv-btn:hover { color: #fff; border-color: #555; background: #262626; }
    #conv-list { flex: 1; overflow-y: auto; min-height: 0; padding-bottom: 4px; }
    .conv-item { padding: 7px 10px; cursor: pointer; border-left: 3px solid transparent; }
    .conv-item:hover { background: #222; }
    .conv-item.active { background: #1e2a3f; border-left-color: #3b82f6; }
    .conv-row { display: flex; align-items: center; gap: 4px; }
    .conv-title { flex: 1; font-size: 12px; color: #e0e0e0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .conv-del { display: none; background: none; border: none; color: #777; cursor: pointer; font-size: 11px; padding: 0 2px; flex: none; }
    .conv-item:hover .conv-del { display: block; }
    .conv-del:hover { color: #f87171; }
    .conv-meta { font-size: 11px; color: #777; margin-top: 1px; }
    #conv-empty { padding: 10px; font-size: 11px; color: #666; line-height: 1.6; }


    #chat { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
    #chat-head { display: flex; align-items: center; gap: 6px; padding: 7px 12px; border-bottom: 1px solid #242424; flex: none; }
    #conv-title { flex: 1; font-size: 12px; color: #bbb; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    #agent-btn { background: none; border: 1px solid #3a3a3a; color: #777; border-radius: 5px; cursor: pointer; font-size: 11px; line-height: 1; padding: 3px 6px; white-space: nowrap; }
    #agent-btn:hover { color: #bbb; border-color: #555; }
    #agent-btn.on { background: #1e3a5f; border-color: #3b82f6; color: #93c5fd; }


    #chapter-bar { display: flex; align-items: center; gap: 6px; padding: 7px 12px; cursor: pointer; border-bottom: 1px solid #242424; font-size: 12px; color: #999; flex: none; }
    #chapter-bar:hover { background: #222; }
    #chapter-bar .caret { color: #666; font-size: 10px; }
    #chapter-now { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #bbb; }
    #chapter-list { display: none; overflow-y: auto; max-height: 45%; border-bottom: 1px solid #242424; flex: none; }
    #chapter-list.open { display: block; }
    .chapter-item { padding: 6px 12px; cursor: pointer; border-left: 3px solid transparent; }
    .chapter-item:hover { background: #222; }
    .chapter-item.active { background: #1e2a3f; border-left-color: #3b82f6; }
    .chapter-title { font-size: 12px; color: #e0e0e0; }
    .chapter-time { font-size: 11px; color: #888; }
    .chapter-summary { font-size: 11px; color: #aaa; margin-top: 2px; }


    #prep { display: none; padding: 7px 12px; border-bottom: 1px solid #242424; flex: none; }
    #prep.show { display: block; }
    #prep-text { font-size: 12px; color: #999; margin-bottom: 5px; }
    #prep-text .detail { display: block; color: #666; font-size: 11px; margin-top: 2px; word-break: break-all; }
    #pbar { height: 3px; background: #333; border-radius: 2px; overflow: hidden; }
    #pfill { height: 100%; width: 0%; background: #3b82f6; transition: width .3s; }

    #messages { flex: 1; min-height: 70px; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
    .bubble { padding: 6px 10px; border-radius: 8px; max-width: 92%; word-wrap: break-word; }
    .bubble.user { align-self: flex-end; background: #3b82f6; color: #fff; }
    .bubble.assistant { align-self: flex-start; background: #333; color: #e0e0e0; white-space: pre-wrap; }
    .bubble.warning { align-self: flex-start; background: #4a3f00; color: #fbbf24; font-size: 12px; }
    .bubble.warning .detail { display: block; color: #8a7a3a; font-size: 11px; margin-top: 3px; word-break: break-all; }

    .bubble.thinking { color: #888; font-style: italic; }
    .bubble.thinking::after { content: ''; animation: dots 1.4s steps(4, end) infinite; }
    @keyframes dots { 0% { content: ''; } 25% { content: '·'; } 50% { content: '··'; } 75% { content: '···'; } }
    .cite { color: #60a5fa; text-decoration: underline; cursor: pointer; padding: 0 1px; border-radius: 3px; }
    .cite:hover { background: #1e3a5f; }

    #empty-hint { margin: auto; text-align: center; color: #666; font-size: 12px; line-height: 1.9; padding: 0 8px; }
    #empty-hint .tip { display: block; color: #555; font-size: 11px; }

    #input-row { display: flex; gap: 6px; align-items: center; padding: 10px 12px; border-top: 1px solid #333; flex: none; }
    #q-input { flex: 1; min-width: 0; padding: 6px 10px; font-size: 13px; border: 1px solid #444; border-radius: 6px; background: #262626; color: #e0e0e0; }
    #q-input:disabled { opacity: .5; }
    #send-btn { padding: 6px 12px; font-size: 13px; border: none; border-radius: 6px; background: #3b82f6; color: #fff; cursor: pointer; flex: none; }
    #send-btn:disabled { opacity: .5; cursor: default; }
  `;

  document.getElementById('bili-qa-companion-host')?.remove();
  const host = document.createElement('div');
  host.id = 'bili-qa-companion-host';
  const shadow = host.attachShadow({ mode: 'open' });
  shadow.innerHTML = `
    <style>${STYLE}</style>
    <div id="panel">
      <div id="header">
        <span id="title">MyDeskmate</span>
        <span id="dot" class="dot"></span>
        <button id="min-btn" title="收起成悬浮球">–</button>
      </div>
      <div id="ball">
        <span id="ball-glyph">▶</span>
        <span id="ball-dot" class="dot"></span>
      </div>
      <div id="body">
        <div id="rail">
          <div id="rail-head">
            <span>对话</span>
            <button id="new-conv-btn" title="开始新对话">＋</button>
          </div>
          <div id="conv-list"></div>
          <div id="conv-empty">还没有别的对话。<br>这一节聊过的内容会存在这里。</div>
        </div>
        <div id="chat">
          <div id="chat-head">
            <span id="conv-title">新对话</span>
            <button id="agent-btn" title="深入检索：更慢，但助手会自己翻找视频片段">深入</button>
          </div>
          <div id="chapter-bar" role="button" tabindex="0" aria-expanded="false">
            <span class="caret" id="chapter-caret">▸</span>
            <span>章节</span>
            <span id="chapter-now"></span>
          </div>
          <div id="chapter-list"></div>
          <div id="prep">
            <div id="prep-text"></div>
            <div id="pbar"><div id="pfill"></div></div>
          </div>
          <div id="messages"></div>
          <div id="input-row">
            <input id="q-input" type="text" placeholder="问点什么…（回车发送）" disabled>
            <button id="send-btn" disabled>发送</button>
          </div>
        </div>
      </div>
    </div>
  `;
  host.style.display = 'none';
  document.documentElement.appendChild(host);

  const $ = (sel) => shadow.querySelector(sel);
  const titleEl = $('#title'), dotEl = $('#dot'), prepText = $('#prep-text');
  const pfill = $('#pfill'), qInput = $('#q-input'), sendBtn = $('#send-btn');
  const agentBtn = $('#agent-btn');
  const messagesEl = $('#messages'), chapterBar = $('#chapter-bar');
  const chapterList = $('#chapter-list'), chapterNow = $('#chapter-now');
  const chapterCaret = $('#chapter-caret'), convListEl = $('#conv-list');
  const convEmptyEl = $('#conv-empty'), convTitleEl = $('#conv-title');
  const prepEl = $('#prep'), panelEl = $('#panel');
  const ballEl = $('#ball'), ballGlyphEl = $('#ball-glyph'), ballDotEl = $('#ball-dot');

  const POS_KEY = 'mydeskmate_pos';
  const BALL = 52;
  const PAD = 8;
  const DRAG_THRESHOLD = 4;
  const NO_ROUTE = Symbol('no-route');

  let backendBase = readBackendBase();
  const saved = loadPos();
  let pos = saved ? { left: saved.left, top: saved.top } : null;
  let anchor = saved ? { rx: saved.rx, by: saved.by } : { rx: true, by: false };
  let rendered = { left: 0, top: 0 };
  let mode = 'hidden';
  let drag = null;
  let connectToken = 0;

  let videoId = null;
  let ws = null;
  let reconnectTimer = null;
  let lastUrl = NO_ROUTE;
  let connecting = false;
  let chapters = [];
  let currentAnswerEl = null;
  let currentAnswerRaw = '';
  let conversationId = null;
  let conversations = [];
  let streaming = false;

  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const fmt = (sec) => {
    const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return m + ':' + String(s).padStart(2, '0');
  };
  const setDot = (cls) => {
    dotEl.className = 'dot ' + cls;
    ballDotEl.className = 'dot ' + cls;
    panelEl.classList.toggle('busy', cls === 'amber');
  };

  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

  function readBackendBase() {
    try { return (localStorage.getItem(LS_KEY) || DEFAULT_BASE).replace(/\/+$/, ''); }
    catch (e) { return DEFAULT_BASE; }
  }

  function savePos() {
    try {
      const w = panelEl.offsetWidth, h = panelEl.offsetHeight;
      localStorage.setItem(POS_KEY, JSON.stringify({
        v: 1,
        left: Math.round(anchor.rx ? pos.left + w - BALL : pos.left),
        top: Math.round(anchor.by ? pos.top + h - BALL : pos.top),
        rx: anchor.rx, by: anchor.by,
      }));
    } catch (e) { /* 位置记不住不是错误，静默降级成「下次回默认位置」 */ }
  }

  function loadPos() {
    try {
      const raw = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
      if (!raw || raw.v !== 1) return null;
      if (!Number.isFinite(raw.left) || !Number.isFinite(raw.top)) return null;
      return { left: raw.left, top: raw.top, rx: !!raw.rx, by: !!raw.by };
    } catch (e) { return null; }
  }

  function applyPos(clampBase) {
    if (mode === 'hidden' || !pos) return;
    const w = panelEl.offsetWidth, h = panelEl.offsetHeight;
    const hiL = Math.max(PAD, window.innerWidth - w - PAD);
    const hiT = Math.max(PAD, window.innerHeight - h - PAD);
    if (clampBase) {
      pos.left = clamp(pos.left, PAD, hiL);
      pos.top = clamp(pos.top, PAD, hiT);
    }
    rendered = { left: clamp(pos.left, PAD, hiL), top: clamp(pos.top, PAD, hiT) };
    panelEl.style.left = rendered.left + 'px';
    panelEl.style.top = rendered.top + 'px';
    panelEl.style.right = 'auto';
  }

  function renderBall() {
    ballGlyphEl.textContent = videoId ? '问' : '▶';
    ballEl.title = videoId ? '展开 MyDeskmate' : '点我开始准备这节课';
  }

  function setMode(next) {
    if (next === mode) return;
    const before = (mode === 'hidden' || !pos || !panelEl.offsetWidth) ? null : {
      left: pos.left, top: pos.top,
      right: pos.left + panelEl.offsetWidth,
      bottom: pos.top + panelEl.offsetHeight,
    };
    mode = next;
    panelEl.classList.toggle('ball', next === 'ball');
    host.style.display = next === 'hidden' ? 'none' : '';
    if (next === 'hidden') return;
    const w = panelEl.offsetWidth, h = panelEl.offsetHeight;
    if (before) {
      pos = {
        left: anchor.rx ? before.right - w : before.left,
        top: anchor.by ? before.bottom - h : before.top,
      };
    } else {
      const base = pos || { left: window.innerWidth - 16 - BALL, top: 76 };
      pos = {
        left: anchor.rx ? base.left + BALL - w : base.left,
        top: anchor.by ? base.top + BALL - h : base.top,
      };
    }
    applyPos(false);
    renderBall();
  }

  function parseUtc(s) {
    if (!s) return null;
    const d = new Date(s.replace(' ', 'T') + 'Z');
    return isNaN(d.getTime()) ? null : d;
  }

  function relTime(s) {
    const t = parseUtc(s);
    if (!t) return '';
    const now = new Date();
    const diff = (now - t) / 1000;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    const sameDay = (a, b) => a.getFullYear() === b.getFullYear()
      && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    const hhmm = String(t.getHours()).padStart(2, '0') + ':' + String(t.getMinutes()).padStart(2, '0');
    if (sameDay(t, now)) return '今天 ' + hhmm;
    const y = new Date(now); y.setDate(y.getDate() - 1);
    if (sameDay(t, y)) return '昨天 ' + hhmm;
    return (t.getMonth() + 1) + '-' + t.getDate();
  }

  function addBubble(role, text) {
    clearEmptyHint();
    const div = document.createElement('div');
    div.className = 'bubble ' + role;
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
  }

  function clearEmptyHint() {
    const hint = messagesEl.querySelector('#empty-hint');
    if (hint) hint.remove();
  }

  function renderEmptyHint() {
    messagesEl.innerHTML = '';
    const d = document.createElement('div');
    d.id = 'empty-hint';
    d.textContent = '这一节还没聊过 · 播放到不懂的地方直接问我';
    const tip = document.createElement('span');
    tip.className = 'tip';
    tip.textContent = '答案里的 [12:34] 可以点击跳到那个位置';
    d.appendChild(tip);
    messagesEl.appendChild(d);
  }

  function setBusy(on) {
    streaming = on;
    qInput.disabled = on || !videoId;
    sendBtn.disabled = on || !videoId;
  }

  function renderConvList() {
    convListEl.innerHTML = '';
    if (!conversations.length) {
      convEmptyEl.style.display = 'block';
      return;
    }
    convEmptyEl.style.display = 'none';
    for (const c of conversations) {
      const item = document.createElement('div');
      item.className = 'conv-item' + (c.id === conversationId ? ' active' : '');
      item.dataset.id = c.id;

      const row = document.createElement('div');
      row.className = 'conv-row';
      const title = document.createElement('span');
      title.className = 'conv-title';
      title.textContent = c.title || '未命名对话';
      title.title = c.title || '';
      const del = document.createElement('button');
      del.className = 'conv-del';
      del.textContent = '✕';
      del.title = '删除这条对话';
      row.appendChild(title);
      row.appendChild(del);

      const meta = document.createElement('div');
      meta.className = 'conv-meta';
      const when = relTime(c.updated_at);
      meta.textContent = (when ? when + ' · ' : '') + (c.message_count || 0) + ' 条';

      item.appendChild(row);
      item.appendChild(meta);
      item.addEventListener('click', () => openConversation(c.id));
      del.addEventListener('click', (e) => { e.stopPropagation(); removeConversation(c.id); });
      convListEl.appendChild(item);
    }
  }

  async function loadConversations() {
    if (!videoId) return;
    try {
      const data = await fetch(`${backendBase}/api/videos/${videoId}/conversations`)
        .then((r) => r.json());
      conversations = data.conversations || [];
    } catch (e) {
      conversations = [];
    }
    renderConvList();
  }

  async function openConversation(cid) {
    if (!videoId || cid === conversationId) return;
    setBusy(false);
    currentAnswerEl = null;
    currentAnswerRaw = '';
    try {
      const detail = await fetch(
        `${backendBase}/api/videos/${videoId}/conversations/${cid}`
      ).then((r) => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
      conversationId = detail.id;
      renderMessages(detail.messages || []);
      convTitleEl.textContent = detail.title || '未命名对话';
      convTitleEl.title = detail.title || '';
    } catch (e) {
      conversationId = null;
      renderEmptyHint();
      convTitleEl.textContent = '新对话';
      addBubble('warning', '这条对话打不开了，可能已被删除。');
    }
    renderConvList();
  }

  function renderMessages(msgs) {
    messagesEl.innerHTML = '';
    if (!msgs.length) { renderEmptyHint(); return; }
    for (const m of msgs) {
      const el = addBubble(m.role === 'user' ? 'user' : 'assistant', m.content || '');
      if (m.role !== 'user') {
        el.innerHTML = '';
        el.appendChild(renderAnswerWithCitations(m.content || ''));
      }
    }
  }

  function newConversation() {
    setBusy(false);
    conversationId = null;
    currentAnswerEl = null;
    currentAnswerRaw = '';
    convTitleEl.textContent = '新对话';
    convTitleEl.title = '';
    renderEmptyHint();
    renderConvList();
  }

  async function removeConversation(cid) {
    const item = conversations.find((c) => c.id === cid);
    const label = (item && item.title) || '这条对话';
    if (!window.confirm(`删除「${label}」？删除后无法恢复。`)) return;
    try {
      const r = await fetch(`${backendBase}/api/videos/${videoId}/conversations/${cid}`,
        { method: 'DELETE' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
    } catch (e) {
      addBubble('warning', '删除失败，稍后再试。');
      return;
    }
    if (cid === conversationId) newConversation();
    await loadConversations();
  }

  function findMainVideo() {
    const vids = Array.from(document.querySelectorAll('video'));
    const candidates = vids.filter((v) => Number.isFinite(v.duration) && v.duration > 1 && (v.currentSrc || v.src));
    if (!candidates.length) return null;
    const playing = candidates.filter((v) => !v.paused && v.currentTime > 0);
    const ranked = playing.length ? playing : candidates;
    return ranked.sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (br.width * br.height) - (ar.width * ar.height);
    })[0];
  }

  function canonicalVideoUrl() {
    const pathname = location.pathname;
    if (!/^\/(video|bangumi)\//.test(pathname)) return null;
    const p = new URLSearchParams(location.search).get('p');
    const base = location.origin + pathname.replace(/\/+$/, '');
    return p ? base + '?p=' + p : base;
  }

  async function connectToVideo(url) {
    setPrep('正在打开这节课…', 'amber', 0);
    try {
      const resp = await fetch(`${backendBase}/api/videos`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'url=' + encodeURIComponent(url),
      });
      if (!resp.ok) {
        let detail = '';
        try { detail = (await resp.json()).detail || ''; } catch (e) { /* noop */ }
        throw new Error(detail || ('后端返回 ' + resp.status));
      }
      const data = await resp.json();
      if (url !== lastUrl) return;
      videoId = data.video_id;
      renderBall();
      titleEl.textContent = (data.title || '').slice(0, 30) || 'MyDeskmate';
      connectWS(videoId);
      fetchChapters(videoId);
      loadConversations();
      setBusy(false);
    } catch (err) {
      if (url !== lastUrl) return;
      setPrep('连不上陪看服务。确认它在运行后刷新本页。', 'red', 0,
        '（' + backendBase + '：' + err.message + '）');
    }
  }

  function connectWS(vid) {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (ws) { const old = ws; ws = null; old.onclose = null; try { old.close(); } catch (e) { /* noop */ } }
    const wsBase = backendBase.replace(/^http/, 'ws');
    const sock = new WebSocket(`${wsBase}/ws/${vid}`);
    ws = sock;
    sock.onopen = () => { if (ws === sock) setDot('green'); };
    sock.onmessage = (ev) => { try { handleMessage(JSON.parse(ev.data)); } catch (e) { /* noop */ } };
    sock.onclose = () => {
      if (ws !== sock) return;
      ws = null;
      dropPlaceholder();
      setBusy(false);
      setPrep('连接断开了，正在重连…', 'amber');
      reconnectTimer = setTimeout(() => { if (videoId) connectWS(videoId); }, 3000);
    };
    sock.onerror = () => { try { sock.close(); } catch (e) { /* noop */ } };
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case 'progress': updateProgress(msg); break;
      case 'conversation': onConversation(msg); break;
      case 'qa_chunk': appendAnswer(msg.text); break;
      case 'qa_done': finalizeAnswer(); break;
      case 'qa_warning':
        dropPlaceholder();
        addWarning(msg.message);
        setBusy(false);
        break;
      case 'tool_call':
        if (isPlaceholder()) currentAnswerEl.textContent = '正在翻看视频内容';
        break;
      default: break;
    }
  }

  function onConversation(msg) {
    conversationId = msg.conversation_id;
    convTitleEl.textContent = msg.title || '新对话';
    convTitleEl.title = msg.title || '';
    if (msg.created) loadConversations();
    else renderConvList();
  }

  function addWarning(text) {
    const raw = String(text || '').trim();
    const firstLine = raw.split('\n')[0];
    const human = /^[[{]/.test(firstLine) || firstLine.length > 120
      ? '这次没答上来，再问一次试试？'
      : firstLine;
    const el = addBubble('warning', human);
    if (raw && raw !== human) {
      const d = document.createElement('span');
      d.className = 'detail';
      d.textContent = raw.slice(0, 200);
      el.appendChild(d);
    }
  }

  function setPrep(text, dotCls, pct, detail) {
    setDot(dotCls || '');
    if (text === null) { prepEl.classList.remove('show'); return; }
    prepEl.classList.add('show');
    prepText.textContent = text;
    if (detail) {
      const d = document.createElement('span');
      d.className = 'detail';
      d.textContent = detail.slice(0, 200);
      prepText.appendChild(d);
    }
    pfill.style.width = (pct || 0) + '%';
  }

  function updateProgress(msg) {
    const total = msg.total || 1;
    const pct = Math.min(100, Math.round((msg.processed_up_to / total) * 100));
    switch (msg.stage) {
      case 'starting':
        setPrep('正在准备这节课…', 'amber', 2); break;
      case 'downloading':
        setPrep('正在听这节课…', 'amber', 5); break;
      case 'audio_fallback':
        setPrep('音频流被拦截，改用视频音轨…', 'amber', 6); break;
      case 'audio_extraction':
        setPrep('正在听这节课…', 'amber', 8); break;
      case 'transcription':
        setPrep(`已经听懂前 ${fmt(msg.processed_up_to)}，可以开始问了`, 'green', pct); break;
      case 'keyframe_extraction':
        setPrep('正在看板书…', 'green', 95); break;
      case 'complete':
        setPrep(null, 'green'); break;
      case 'no_speech':
      case 'transcription_incomplete':
      case 'transcription_warning':
      case 'keyframes_skipped':
        setPrep(msg.message || '这节课没有讲解声，暂时没法根据内容回答。', 'amber', pct);
        break;
      case 'error':
        setPrep(msg.message || '这节课我没准备好，请稍后重新加载一次。', 'red', 0);
        break;
      default:
        if (msg.stage) setPrep('正在准备这节课…', 'amber', pct);
        break;
    }
  }

  function sendQuestion() {
    const question = qInput.value.trim();
    if (!question || streaming || !ws || ws.readyState !== WebSocket.OPEN) return;
    const v = findMainVideo();
    const position = v ? v.currentTime : 0;
    addBubble('user', question);
    currentAnswerRaw = '';
    currentAnswerEl = addBubble('assistant thinking', '思考中');
    setBusy(true);
    ws.send(JSON.stringify({
      type: 'qa_request', question, position,
      conversation_id: conversationId, agentic: agentBtn.classList.contains('on'),
    }));
    qInput.value = '';
  }

  function isPlaceholder() {
    return !!currentAnswerEl && currentAnswerEl.classList.contains('thinking');
  }

  function dropPlaceholder() {
    if (isPlaceholder()) {
      currentAnswerEl.remove();
      currentAnswerEl = null;
      currentAnswerRaw = '';
    }
  }

  function appendAnswer(text) {
    if (!currentAnswerEl) currentAnswerEl = addBubble('assistant', '');
    if (isPlaceholder()) {
      currentAnswerEl.classList.remove('thinking');
      currentAnswerEl.textContent = '';
    }
    currentAnswerRaw += text;
    currentAnswerEl.textContent += text;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function finalizeAnswer() {
    if (currentAnswerEl) {
      if (currentAnswerRaw) {
        currentAnswerEl.classList.remove('thinking');
        currentAnswerEl.innerHTML = '';
        currentAnswerEl.appendChild(renderAnswerWithCitations(currentAnswerRaw));
      } else {
        currentAnswerEl.classList.remove('thinking');
        currentAnswerEl.classList.add('warning');
        currentAnswerEl.textContent = '这次没答上来，再问一次试试？';
      }
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
    currentAnswerEl = null;
    currentAnswerRaw = '';
    setBusy(false);
    if (conversationId) loadConversations();
  }

  // 模型有时把多个时刻写进一个方括号（区间/列表），逐个时间点生成可点击的 .cite。
  function citeSpan(min, sec, label) {
    const seconds = parseInt(min, 10) * 60 + parseInt(sec, 10);
    return `<span class="cite" role="button" tabindex="0" aria-label="跳转到 ${label}" data-time="${seconds}">${label}</span>`;
  }

  function renderBracketCitations(inner) {
    let html = '', last = 0, m;
    const re = /(\d{1,3}):(\d{2})/g;
    while ((m = re.exec(inner))) {
      html += esc(inner.slice(last, m.index));
      html += citeSpan(m[1], m[2], m[0]);
      last = m.index + m[0].length;
    }
    html += esc(inner.slice(last));
    return html;
  }

  function renderAnswerWithCitations(raw) {
    // 只处理“方括号里至少有一个 MM:SS”的片段，避免误伤普通方括号文本。
    const bracketRe = /\[([^\[\]]*\d{1,3}:\d{2}[^\[\]]*)\]/g;
    const el = document.createElement('div');
    let html = '', last = 0, m;
    while ((m = bracketRe.exec(raw))) {
      html += esc(raw.slice(last, m.index));
      const inner = m[1];
      const times = inner.match(/(\d{1,3}):(\d{2})/g) || [];
      if (times.length === 1 && inner.trim() === times[0]) {
        // 单个 [MM:SS]：保持整对方括号可点，和旧行为一致。
        const [mm, ss] = times[0].split(':');
        html += '[' + citeSpan(mm, ss, times[0]) + ']';
      } else {
        html += '[' + renderBracketCitations(inner) + ']';
      }
      last = m.index + m[0].length;
    }
    html += esc(raw.slice(last));
    el.innerHTML = html;
    el.querySelectorAll('.cite').forEach((c) => {
      c.addEventListener('click', () => seekTo(parseFloat(c.dataset.time)));
      c.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        seekTo(parseFloat(c.dataset.time));
      });
    });
    return el;
  }

  function seekTo(sec) {
    const v = findMainVideo();
    if (v) { v.currentTime = sec; v.play(); }
  }

  async function fetchChapters(vid) {
    try {
      const data = await fetch(`${backendBase}/api/videos/${vid}/chapters`).then((r) => r.json());
      chapters = data.chapters || [];
      renderChapters();
    } catch (e) { /* 后端未就绪时静默 */ }
  }

  function renderChapters() {
    chapterList.innerHTML = '';
    if (!chapters.length) { chapterBar.style.display = 'none'; return; }
    chapterBar.style.display = 'flex';
    chapterNow.textContent = '共 ' + chapters.length + ' 段';
    chapters.forEach((c, i) => {
      const item = document.createElement('div');
      item.className = 'chapter-item';
      item.dataset.index = i;
      item.setAttribute('role', 'button');
      item.tabIndex = 0;
      const t = document.createElement('div');
      t.className = 'chapter-title';
      t.textContent = (i + 1) + '. ' + c.title;
      const tm = document.createElement('div');
      tm.className = 'chapter-time';
      tm.textContent = fmt(c.start_time) + ' – ' + fmt(c.end_time);
      item.appendChild(t);
      item.appendChild(tm);
      if (c.summary) {
        const s = document.createElement('div');
        s.className = 'chapter-summary';
        s.textContent = c.summary;
        item.appendChild(s);
      }
      item.addEventListener('click', () => seekTo(c.start_time));
      item.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        seekTo(c.start_time);
      });
      chapterList.appendChild(item);
    });
  }

  function highlightChapter(time) {
    const items = chapterList.querySelectorAll('.chapter-item');
    let idx = -1;
    for (let i = 0; i < chapters.length; i++) {
      const c = chapters[i];
      if (time >= c.start_time && (i === chapters.length - 1 || time < chapters[i + 1].start_time)) { idx = i; break; }
    }
    items.forEach((el, i) => el.classList.toggle('active', i === idx));
    if (idx >= 0) {
      chapterNow.textContent = `${idx + 1}/${chapters.length} · ${chapters[idx].title}`;
    }
  }

  function teardown() {
    connectToken++;
    connecting = false;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (ws) { const old = ws; ws = null; old.onclose = null; try { old.close(); } catch (e) { /* noop */ } }
    videoId = null;
    chapters = [];
    conversations = [];
    titleEl.textContent = 'MyDeskmate';
    titleEl.title = '';
    prepEl.classList.remove('show');
    chapterList.innerHTML = '';
    chapterList.classList.remove('open');
    chapterCaret.textContent = '▸';
    chapterBar.style.display = 'none';
    chapterNow.textContent = '';
    setDot('');
    newConversation();
    renderBall();
  }

  function applyRoute() {
    const url = canonicalVideoUrl();
    if (url === lastUrl) return;
    teardown();
    lastUrl = url;
    setMode(url ? 'ball' : 'hidden');
  }

  function activate() {
    if (!lastUrl) return;
    if (lastUrl !== canonicalVideoUrl()) { applyRoute(); return; }
    if (videoId || connecting) { setMode('panel'); return; }
    connecting = true;
    const token = connectToken;
    setMode('panel');
    connectToVideo(lastUrl).finally(() => {
      if (token === connectToken) connecting = false;
    });
  }

  function dragHandleAt(target) {
    if (mode === 'ball') return target.closest('#ball');
    if (mode === 'panel') {
      if (target.closest('#min-btn')) return null;
      return target.closest('#header');
    }
    return null;
  }

  function onPointerDown(e) {
    if (e.button !== 0) return;
    const handle = dragHandleAt(e.target);
    if (!handle) return;
    e.preventDefault();
    drag = {
      id: e.pointerId,
      sx: e.clientX, sy: e.clientY,
      ox: rendered.left, oy: rendered.top,
      moved: false,
    };
    try { handle.setPointerCapture(e.pointerId); } catch (err) { /* 拿不到捕获也能拖，只是拖出窗口会断 */ }
    panelEl.classList.add('dragging');
  }

  function onPointerMove(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
    if (!drag.moved && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
    drag.moved = true;
    pos = { left: drag.ox + dx, top: drag.oy + dy };
    applyPos(true);
  }

  function onPointerUp(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const d = drag;
    drag = null;
    panelEl.classList.remove('dragging');
    if (!d.moved) {
      if (mode === 'ball') activate();
      return;
    }
    anchor = {
      rx: rendered.left + panelEl.offsetWidth / 2 > window.innerWidth / 2,
      by: rendered.top + panelEl.offsetHeight / 2 > window.innerHeight / 2,
    };
    savePos();
  }

  function onPointerCancel(e) {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null;
    panelEl.classList.remove('dragging');
  }

  qInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendQuestion(); });
  sendBtn.addEventListener('click', sendQuestion);
  $('#min-btn').addEventListener('click', () => setMode('ball'));
  $('#new-conv-btn').addEventListener('click', newConversation);
  agentBtn.addEventListener('click', () => agentBtn.classList.toggle('on'));
  chapterBar.addEventListener('click', () => {
    const open = chapterList.classList.toggle('open');
    chapterCaret.textContent = open ? '▾' : '▸';
    chapterBar.setAttribute('aria-expanded', String(open));
  });
  chapterBar.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    chapterBar.click();
  });

  panelEl.addEventListener('pointerdown', onPointerDown);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
  window.addEventListener('pointercancel', onPointerCancel);
  window.addEventListener('resize', () => applyPos(false));

  setInterval(() => {
    if (!chapters.length || mode !== 'panel') return;
    const v = findMainVideo();
    if (v) highlightChapter(v.currentTime);
  }, 1000);

  window.addEventListener('popstate', applyRoute);
  window.addEventListener('hashchange', applyRoute);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) applyRoute(); });
  setInterval(() => { if (!document.hidden) applyRoute(); }, 500);

  renderEmptyHint();
  applyRoute();
})();
