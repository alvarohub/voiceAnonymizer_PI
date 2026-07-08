/**
 * p5.js sketch — Real-time Speech Emotion/Prosody receiver
 *
 * Connects to the Node.js bridge via WebSocket.
 * Displays scrolling strips for prosody features, VAD bar,
 * emotion label + score bars, sample counter, and controls.
 */

// ── Config ───────────────────────────────────────────────────────
const WS_URL = `ws://${location.hostname || 'localhost'}:8765`;
const HISTORY = 200; // data points to keep (scrolling window)
const BAR_H = 18; // height of VAD bar (was 12)
const RECONNECT_MS = 2000;
const CTRL_H = 84; // desktop height reserved for control panel at bottom
const HEADER_H = 225; // desktop fixed status/inventory/rate panels at top
const MIN_CANVAS_H = 760;
const MAX_STRIP_H = 76;

// ── State ────────────────────────────────────────────────────────
let ws = null;
let connected = false;
let sampleIndex = 0; // increments on every VAD message

// Prosody channels — keyed by OSC address suffix
const channels = {
  'F0semitoneFrom27.5Hz_sma3nz': { label: 'F0 (st)', color: [0, 255, 255], hi: 50, data: [] },
  Loudness_sma3: { label: 'Loudness', color: [0, 200, 80], hi: 2.5, data: [] },
  jitterLocal_sma3nz: { label: 'Jitter', color: [255, 180, 200], hi: 0.35, data: [] },
  shimmerLocaldB_sma3nz: { label: 'Shimmer (dB)', color: [255, 165, 0], hi: 30, data: [] },
  HNRdBACF_sma3nz: { label: 'HNR (dB)', color: [200, 130, 255], hi: 15, data: [] },
};
const channelKeys = Object.keys(channels);

let vadHistory = [];
let emotionLabel = '';
let emotionConf = 0;
let emotionScores = {};
const EMOTION_DIMS = ['angry', 'disgusted', 'fearful', 'happy', 'neutral', 'other', 'sad', 'surprised', 'unknown'];
const EMO_COLORS = {
  angry: [255, 60, 60],
  disgusted: [120, 200, 60],
  fearful: [180, 100, 255],
  happy: [255, 220, 40],
  neutral: [160, 160, 160],
  other: [100, 100, 100],
  sad: [80, 140, 255],
  surprised: [255, 140, 200],
  unknown: [80, 80, 80],
};

// Controls state
let oscRunning = false;
let logRunning = false;
let logPaused = false;
let logSessionOpen = false;
let vadRunning = false;
let emotionRunning = false;
let emotionLoaded = true;
let prosodyRunning = false;
let audioOk = false;
let audioDevice = '';
let audioError = 'waiting for state';
let ackStatus = { status: 'idle', receivedAt: 0 };
let commandEvents = [];
let latestReplyByDevice = {};
let selfStats = null;
let oscSendHz = 4;
let oscMaxHz = 100;
let researchSyncHz = 100;
let researchSyncPeriodMs = 10;
let researchEmotionHz = 2;
let researchEmotionHopMs = 500;
let researchEmotionWindowS = 2;
let logRecordedSecs = 0;
let logSessionStartIso = '';
let logPath = '';
let logStateAt = 0;
let micHitAreas = [];
let headerButtons = [];
let logBroadcast = false;

// Per-address send rate (Hz), keyed by full OSC address; updated via /stats/rate
let rateByAddr = {};

// Device list from bridge (merged heartbeat + optional expected rig).
// Fields include {addr,device_id,pi_id,mic_id,hostname,version,ageMs,connected,expected}.
let piList = [];
// Currently selected device_id (mirrors the <select> dropdown). null = auto.
let selectedDeviceId = null;
let stateQueryAt = {};
let transportRightEdge = 0;
let commandLogBox = null;
let commandLogHtmlCache = '';
let streamPanelRect = null;
let streamListBottomY = 0;

// ── WebSocket ────────────────────────────────────────────────────
function wsConnect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    connected = true;
    document.getElementById('status').textContent = 'bridge connected';
    document.getElementById('status').style.color = '#6f6';
    // Ask the Pi for current control flags so the UI reflects reality
    // (the streamer may have been launched with --osc-autostart).
    sendCmd('query_state');
  };

  ws.onclose = () => {
    document.getElementById('status').textContent = 'bridge disconnected - retrying...';
    document.getElementById('status').style.color = '#f66';
    setTimeout(wsConnect, RECONNECT_MS);
  };

  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      // OSC data forwarded from bridge
      if (msg.address) {
        handleOSC(msg.address, msg.args);
      }
      // Control state feedback from bridge
      if (msg.type === 'state') {
        if (msg.osc !== undefined) oscRunning = msg.osc;
        if (msg.log !== undefined) logRunning = msg.log;
      }
      // Command acknowledgement status from bridge/Pi round trip.
      if (msg.type === 'ack_status') {
        ackStatus = { ...msg, receivedAt: Date.now() };
        rememberCommandEvent(ackStatus);
        applyAckToLocalState(ackStatus);
      }
      // Pi discovery list from bridge
      if (msg.type === 'pi_list') {
        piList = msg.pis || [];
        updateDevicePicker();
        queryDeviceStates();
      }
    } catch (_) {}
  };
}

function sendCmd(cmd, args = [], record = true) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    if (record && cmd !== 'query_state') rememberSentCommand(cmd, args, currentStreamSendLabel(cmd, args));
    ws.send(JSON.stringify({ type: 'cmd', cmd, args, target_device: selectedDeviceId || undefined }));
  }
}

function sendScopedCmd(cmd, args = []) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    rememberSentCommand(cmd, args, logBroadcast ? broadcastSendLabel(cmd, args) : currentStreamSendLabel(cmd, args));
    ws.send(
      JSON.stringify({
        type: 'cmd',
        cmd,
        args,
        target_device: logBroadcast ? undefined : selectedDeviceId || undefined,
        broadcast: logBroadcast,
      }),
    );
  }
}

function sendCmdToDevice(deviceId, cmd, args = [], record = false) {
  if (ws && ws.readyState === WebSocket.OPEN && deviceId) {
    if (record && cmd !== 'query_state')
      rememberSentCommand(cmd, args, deviceSendLabel(deviceInfoById(deviceId), cmd, args));
    ws.send(JSON.stringify({ type: 'cmd', cmd, args, target_device: deviceId }));
  }
}

function oscLineForCommand(cmd, args = []) {
  return `/ctrl/${cmd}${args.length ? ` ${args.map((a) => JSON.stringify(String(a))).join(' ')}` : ''}`;
}

function rememberSentCommand(cmd, args, targetLabel) {
  commandEvents.push({
    kind: 'sent',
    at: Date.now(),
    oscLine: oscLineForCommand(cmd, args),
    target: targetLabel || 'OSC SEND target unknown',
    userIntent: true,
  });
  if (commandEvents.length > 220) {
    commandEvents = commandEvents.slice(commandEvents.length - 220);
  }
}

function rememberCommandEvent(status) {
  if (!status) return;
  const at = Date.now();
  const oscLine = status.osc_line || `/ctrl/${status.cmd || 'unknown'}`;
  if (status.status === 'pending') {
    return;
  } else {
    commandEvents.push({
      kind: 'reply',
      at,
      oscLine,
      target: status.target_device || '',
      targetLabel: processLabel(deviceInfoById(status.target_device)) || status.target_device || '',
      state: status.status || 'unknown',
      message: String(status.message || ''),
      elapsedMs: status.elapsed_ms,
    });
    if (status.target_device) latestReplyByDevice[status.target_device] = commandEvents[commandEvents.length - 1];
  }
  if (commandEvents.length > 220) {
    commandEvents = commandEvents.slice(commandEvents.length - 220);
  }
}

function ensureCommandLogBox() {
  if (commandLogBox) return;
  commandLogBox = createDiv('');
  commandLogBox.style('position', 'absolute');
  commandLogBox.style('overflow', 'auto');
  commandLogBox.style('white-space', 'pre-wrap');
  commandLogBox.style('user-select', 'text');
  commandLogBox.style('-webkit-user-select', 'text');
  commandLogBox.style('cursor', 'text');
  commandLogBox.style('pointer-events', 'auto');
  commandLogBox.style('background', '#0f1322');
  commandLogBox.style('color', '#b8c4e8');
  commandLogBox.style('border', '1px solid #374261');
  commandLogBox.style('border-radius', '4px');
  commandLogBox.style('padding', '8px');
  commandLogBox.style(
    'font-family',
    'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
  );
  commandLogBox.style('font-size', '11px');
  commandLogBox.style('line-height', '1.35');
  commandLogBox.style('z-index', '20');
  commandLogBox.style('display', 'none');
  commandLogBox.elt.addEventListener('mousedown', (event) => event.stopPropagation());
  commandLogBox.elt.addEventListener('mousemove', (event) => event.stopPropagation());
  commandLogBox.elt.addEventListener('mouseup', (event) => event.stopPropagation());
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function eventLineForTerminal(ev) {
  if (!ev) return '';
  if (ev.kind === 'sent') {
    return `<span style="color:#8fb4ff">--&gt; ${escapeHtml(ev.target || `OSC SEND ${ev.oscLine}`)}</span>`;
  }
  const state = String(ev.state || 'unknown').toUpperCase();
  const elapsed = ev.elapsedMs !== undefined ? ` ${ev.elapsedMs}ms` : '';
  const msg = ev.message ? `: ${ev.message}` : '';
  const color = ev.state === 'ok' ? '#74f28a' : ev.state === 'late' ? '#ffb15a' : '#ff6f6f';
  return `<span style="color:${color}">&lt;-- ${escapeHtml(ev.targetLabel || ev.target || 'unknown')}  ${escapeHtml(state + elapsed + msg)}</span>`;
}

function updateCopyLogBox(x, y, w, h) {
  ensureCommandLogBox();
  if (!commandLogBox) return;
  if (w < 220 || h < 60) {
    commandLogBox.style('display', 'none');
    return;
  }
  const lines = commandEvents.slice(-180).map(eventLineForTerminal);
  const html = lines.join('\n');
  const changed = html !== commandLogHtmlCache;
  if (changed) {
    commandLogHtmlCache = html;
    commandLogBox.html(html);
  }
  commandLogBox.position(round(x), round(y));
  commandLogBox.size(round(w), round(h));
  commandLogBox.style('display', 'block');
  if (changed) commandLogBox.elt.scrollTop = commandLogBox.elt.scrollHeight;
}

function deviceInfoById(id) {
  if (!id) return null;
  for (const p of piList) {
    if (p && p.device_id === id) return p;
  }
  return null;
}

function processLabel(p) {
  if (!p) return '';
  const pi = p.pi_id || p.hostname || 'Pi';
  const mic = p.mic_id !== undefined && p.mic_id !== null ? `M${p.mic_id}` : 'mic';
  const ip = p.addr ? ` ${p.addr}` : '';
  return `${pi} / ${mic}${ip}`;
}

function ctrlPort(p) {
  return p && p.ctrl_port ? p.ctrl_port : 9001;
}

function deviceSendLabel(p, cmd, args = []) {
  const line = oscLineForCommand(cmd, args);
  if (!p || !p.addr) return `OSC SEND udp://selected-process ${line}`;
  const label = processLabel(p);
  return `OSC SEND udp://${p.addr}:${ctrlPort(p)} ${line}${label ? `    # ${label}` : ''}`;
}

function currentStreamSendLabel(cmd, args = []) {
  return deviceSendLabel(selectedDevice(), cmd, args);
}

function broadcastSendLabel(cmd, args = []) {
  const targets = piList.filter((p) => p && p.audio_ok === true && (p.ageMs || 0) <= 8000);
  const line = oscLineForCommand(cmd, args);
  if (targets.length === 0) return `OSC FANOUT ${line}    # no online audio-ok targets`;
  const routes = targets
    .map((p) => `udp://${p.addr}:${ctrlPort(p)}${processLabel(p) ? ` (${processLabel(p)})` : ''}`)
    .join(', ');
  return `OSC FANOUT ${line}  TO ${targets.length} process(es): ${routes}`;
}

function clearLocalLogState() {
  logRunning = false;
  logPaused = false;
  logSessionOpen = false;
  logRecordedSecs = 0;
  logStateAt = Date.now();
}

function ackAppliesToSelected(status) {
  return !status.target_device || status.target_device === selectedDeviceId;
}

function applyAckToLocalState(status) {
  if (!status || status.status !== 'ok' || !ackAppliesToSelected(status)) return;
  if (status.cmd === 'log_start') {
    logRunning = true;
    logPaused = false;
    logSessionOpen = true;
    logRecordedSecs = 0;
    logStateAt = Date.now();
  } else if (status.cmd === 'log_pause') {
    logRecordedSecs = currentLogSecs();
    logRunning = false;
    logPaused = true;
    logSessionOpen = true;
    logStateAt = Date.now();
  } else if (status.cmd === 'log_resume') {
    logRunning = true;
    logPaused = false;
    logSessionOpen = true;
    logStateAt = Date.now();
  } else if (status.cmd === 'log_save_stop' || status.cmd === 'log_discard_stop') {
    clearLocalLogState();
  }
}

function queryDeviceStates() {
  const now = Date.now();
  for (const p of piList) {
    if (!p.device_id) continue;
    const connectedNow = p.connected !== undefined ? !!p.connected : (p.ageMs || 0) <= 8000;
    if (!connectedNow) continue;
    if (now - (stateQueryAt[p.device_id] || 0) < 3000) continue;
    stateQueryAt[p.device_id] = now;
    sendCmdToDevice(p.device_id, 'query_state');
  }
}

// Populate / refresh the <select> dropdown from piList. Preserves the user's
// current choice if that device is still online; otherwise auto-picks the
// first available device so the single-device case Just Works.
function updateDevicePicker() {
  const sel = document.getElementById('device-picker');
  if (!sel) return;
  // Group entries by pi_id for a nicer label, then sort.
  const sorted = [...piList].sort((a, b) => {
    const ap = String(a.pi_id ?? ''),
      bp = String(b.pi_id ?? '');
    if (ap !== bp) return ap.localeCompare(bp, undefined, { numeric: true });
    return String(a.mic_id ?? '').localeCompare(String(b.mic_id ?? ''), undefined, { numeric: true });
  });
  const ids = sorted.map((p) => p.device_id).filter(Boolean);
  // Auto-select first device if nothing chosen yet (matches bridge behavior).
  if (!selectedDeviceId && ids.length > 0) {
    selectedDeviceId = ids[0];
    ws &&
      ws.readyState === WebSocket.OPEN &&
      ws.send(JSON.stringify({ type: 'select_device', target: selectedDeviceId }));
  }
  // If the selected device disappeared, fall back to the first available.
  if (selectedDeviceId && !ids.includes(selectedDeviceId)) {
    selectedDeviceId = ids[0] || null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'select_device', target: selectedDeviceId }));
    }
  }
  // Rebuild options.
  sel.innerHTML = '';
  if (ids.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '(no device)';
    sel.appendChild(opt);
    return;
  }
  for (const p of sorted) {
    if (!p.device_id) continue;
    const opt = document.createElement('option');
    opt.value = p.device_id;
    const host = p.hostname ? ` — ${p.hostname}` : '';
    opt.textContent = `Pi ${p.pi_id} / Mic ${p.mic_id} (${p.device_id})${host}`;
    if (p.device_id === selectedDeviceId) opt.selected = true;
    sel.appendChild(opt);
  }
}

function resetTransientViewState() {
  vadHistory = [];
  sampleIndex = 0;
  rateByAddr = {};
  logRunning = false;
  logPaused = false;
  logSessionOpen = false;
  emotionLoaded = true;
  audioOk = false;
  audioDevice = '';
  audioError = 'waiting for state';
  selfStats = null;
  logRecordedSecs = 0;
  logSessionStartIso = '';
  logPath = '';
  logStateAt = Date.now();
  for (const k of channelKeys) channels[k].data = [];
}

function selectDevice(deviceId) {
  selectedDeviceId = deviceId || null;
  const sel = document.getElementById('device-picker');
  if (sel) sel.value = selectedDeviceId || '';
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'select_device', target: selectedDeviceId }));
  }
  resetTransientViewState();
  sendCmd('query_state');
}

// Wire the dropdown's change event once the DOM is ready.
window.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('device-picker');
  if (!sel) return;
  sel.addEventListener('change', (e) => {
    selectDevice(e.target.value || null);
  });
});

function handleOSC(address, args) {
  if (!address) return;

  // State broadcasts from Pi (authoritative source for button visuals)
  if (address === '/state/osc_active') {
    oscRunning = !!args[0];
    return;
  }
  if (address === '/state/log_active') {
    logRunning = !!args[0];
    logStateAt = Date.now();
    return;
  }
  if (address === '/state/log_session_open') {
    logSessionOpen = !!args[0];
    logStateAt = Date.now();
    return;
  }
  if (address === '/state/log_paused') {
    logPaused = !!args[0];
    logStateAt = Date.now();
    return;
  }
  if (address === '/state/vad_active') {
    vadRunning = !!args[0];
    return;
  }
  if (address === '/state/emotion_active') {
    emotionRunning = !!args[0];
    return;
  }
  if (address === '/state/emotion_loaded') {
    emotionLoaded = !!args[0];
    if (!emotionLoaded) emotionRunning = false;
    return;
  }
  if (address === '/state/prosody_active') {
    prosodyRunning = !!args[0];
    return;
  }
  if (address === '/state/audio_ok') {
    audioOk = !!args[0];
    return;
  }
  if (address === '/state/audio_device') {
    audioDevice = String(args[0] || '');
    return;
  }
  if (address === '/state/audio_error') {
    audioError = String(args[0] || '');
    return;
  }
  if (address === '/state/osc_send_hz') {
    oscSendHz = Number(args[0] || oscSendHz);
    return;
  }
  if (address === '/state/osc_max_hz') {
    oscMaxHz = Number(args[0] || oscMaxHz);
    return;
  }
  if (address === '/state/research_sync_hz') {
    researchSyncHz = Number(args[0] || researchSyncHz);
    return;
  }
  if (address === '/state/research_sync_period_ms') {
    researchSyncPeriodMs = Number(args[0] || researchSyncPeriodMs);
    return;
  }
  if (address === '/state/research_emotion_hz') {
    researchEmotionHz = Number(args[0] || researchEmotionHz);
    return;
  }
  if (address === '/state/research_emotion_hop_ms') {
    researchEmotionHopMs = Number(args[0] || researchEmotionHopMs);
    return;
  }
  if (address === '/state/research_emotion_window_s') {
    researchEmotionWindowS = Number(args[0] || researchEmotionWindowS);
    return;
  }
  if (address === '/state/log_recorded_secs') {
    logRecordedSecs = Number(args[0] || 0);
    logStateAt = Date.now();
    return;
  }
  if (address === '/state/log_session_start_iso') {
    logSessionStartIso = String(args[0] || '');
    return;
  }
  if (address === '/state/log_path') {
    logPath = String(args[0] || '');
    return;
  }

  // /stats/self → [rss_mb, cpu_pct, temp_c, n_threads]
  if (address === '/stats/self') {
    selfStats = {
      rssMb: Number(args[0] || 0),
      cpuPct: Number(args[1] || 0),
      tempC: Number(args[2]),
      threads: Number(args[3] || 0),
      at: Date.now(),
    };
    return;
  }

  // Per-address rate stats: /stats/rate <addr> <hz>
  if (address === '/stats/rate') {
    if (args.length >= 2 && !String(args[0]).endsWith('/stats/rate') && !String(args[0]).endsWith('/stats/self')) {
      rateByAddr[args[0]] = args[1];
    }
    return;
  }

  // /speech/vad → [0.0 or 1.0]
  if (address.endsWith('/vad')) {
    vadHistory.push(args[0] || 0);
    if (vadHistory.length > HISTORY) vadHistory.shift();
    sampleIndex++;
    return;
  }

  // /speech/emo/label → [label, confidence]
  if (address.endsWith('/emo/label')) {
    emotionLabel = args[0] || '';
    emotionConf = args[1] || 0;
    return;
  }

  // /speech/emo/scores → [angry, disgusted, …]
  if (address.endsWith('/emo/scores')) {
    for (let i = 0; i < EMOTION_DIMS.length && i < args.length; i++) {
      emotionScores[EMOTION_DIMS[i]] = args[i];
    }
    return;
  }

  // Prosody features — /speech/<key>
  for (const key of channelKeys) {
    if (address.endsWith('/' + key)) {
      channels[key].data.push(args[0] || 0);
      if (channels[key].data.length > HISTORY) channels[key].data.shift();
      return;
    }
  }
}

// ── Button helper ────────────────────────────────────────────────
function drawBtn(x, y, w, h, label, bg, isHover) {
  let c = isHover ? bg.map((v) => min(255, v + 30)) : bg;
  fill(c[0], c[1], c[2]);
  noStroke();
  rect(x, y, w, h, 5);
  fill(255);
  textAlign(CENTER, CENTER);
  textSize(14);
  text(label, x + w / 2, y + h / 2);
}

function inRect(mx, my, x, y, w, h) {
  return mx >= x && mx <= x + w && my >= y && my <= y + h;
}

function ackDisplay() {
  if (!ackStatus || ackStatus.status === 'idle') {
    return { text: 'cmd: idle', color: [120, 120, 130] };
  }
  const cmd = ackStatus.cmd || 'cmd';
  const line = ackStatus.osc_line || `/ctrl/${cmd}`;
  const target = ackStatus.target_device ? ` [${ackStatus.target_device}]` : '';
  const elapsed = ackStatus.elapsed_ms !== undefined ? `${ackStatus.elapsed_ms}ms` : '';
  if (ackStatus.status === 'pending') {
    return { text: `${line}${target}  waiting ACK`, color: [245, 205, 90] };
  }
  const rawMessage = String(ackStatus.message || '');
  const usefulOkMessage =
    ackStatus.status !== 'ok' ||
    (rawMessage && rawMessage !== 'ok' && !rawMessage.startsWith('osc=') && cmd !== 'query_state');
  const message = usefulOkMessage ? `: ${rawMessage}` : '';
  if (ackStatus.status === 'ok') {
    return { text: `${line}${target}  OK ${elapsed}${message}`, color: [110, 245, 130] };
  }
  if (ackStatus.status === 'timeout') {
    return { text: `${line}${target}  TIMEOUT ${elapsed}${message}`, color: [255, 95, 80] };
  }
  if (ackStatus.status === 'late') {
    return { text: `${line}${target}  LATE${message}`, color: [255, 170, 80] };
  }
  return { text: `${line}${target}  ERROR${message}`, color: [255, 95, 80] };
}

function eventColor(ev) {
  if (!ev) return [120, 120, 130];
  if (ev.kind === 'sent') return [140, 180, 255];
  if (ev.state === 'ok') return [110, 245, 130];
  if (ev.state === 'timeout') return [255, 95, 80];
  if (ev.state === 'late') return [255, 170, 80];
  return [255, 95, 80];
}

function drawCommandBusPanel(x, y, w, h) {
  if (w < 220 || h < 110) return;
  drawPanel(x, y, w, h, 'Command Bus');
  const pad = 12;
  const left = x + pad;
  fill(150, 160, 185);
  textAlign(LEFT, TOP);
  textSize(10);
  text('OSC terminal log (select and copy):', left, y + 30);
  updateCopyLogBox(x + pad, y + 46, w - pad * 2, h - 58);
}

function fmtHz(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return 'n/a';
  return n >= 10 ? `${n.toFixed(0)} Hz` : `${n.toFixed(1)} Hz`;
}

function fmtMs(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return 'n/a';
  return n >= 100 ? `${n.toFixed(0)} ms` : `${n.toFixed(1)} ms`;
}

function currentLogSecs() {
  if (logRunning && logStateAt) return logRecordedSecs + (Date.now() - logStateAt) / 1000;
  return logRecordedSecs;
}

function hasLogSession() {
  return !!(logSessionOpen || logRunning || logPaused);
}

function displayLogName() {
  const path = logPath || proposedLogName();
  return String(path).split('/').pop() || 'not named yet';
}

function proposedLogName() {
  const d = logSessionStartIso ? new Date(logSessionStartIso) : new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  const dur = Math.max(0, Math.round(currentLogSecs()));
  const sel = selectedDevice();
  const device = sel ? `_${sel.pi_id}_mic${sel.mic_id}` : '';
  return `track${device}_${stamp}_${dur}s.csv`;
}

function observedOscHz() {
  const vals = Object.keys(rateByAddr)
    .filter((a) => !a.includes('/stats/'))
    .map((a) => Number(rateByAddr[a]))
    .filter((v) => Number.isFinite(v) && v > 0);
  if (vals.length === 0) return 0;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function promptOscRate() {
  const maxHz = Number.isFinite(oscMaxHz) && oscMaxHz > 0 ? oscMaxHz : researchSyncHz;
  const value = prompt(
    `OSC send rate in Hz (0.5 to ${fmtHz(maxHz)}, research timeline is ${fmtHz(researchSyncHz)})`,
    String(Number(oscSendHz || 4).toFixed(1)),
  );
  if (value === null) return;
  const hz = Number(value);
  if (!Number.isFinite(hz) || hz <= 0) return;
  sendCmd('osc_send_hz', [String(hz)]);
}

function addHeaderButton(x, y, w, h, label, bg, action) {
  const button = { x, y, w, h, label, bg, action };
  headerButtons.push(button);
  drawBtn(x, y, w, h, label, bg, inRect(mouseX, mouseY, x, y, w, h));
  return button;
}

function saveLog() {
  if (!logBroadcast && !hasLogSession()) {
    ackStatus = { status: 'error', cmd: 'log_save_stop', message: 'start a log before saving', receivedAt: Date.now() };
    rememberCommandEvent(ackStatus);
    return;
  }
  const name = displayLogName();
  sendScopedCmd('log_save_stop', [name]);
}

function discardLog() {
  if (!logBroadcast && !hasLogSession()) {
    ackStatus = {
      status: 'error',
      cmd: 'log_discard_stop',
      message: 'start a log before discarding',
      receivedAt: Date.now(),
    };
    rememberCommandEvent(ackStatus);
    return;
  }
  sendScopedCmd('log_discard_stop');
}

function startLog() {
  if (!logBroadcast && hasLogSession()) return;
  sendScopedCmd('log_start');
}

function pauseOrResumeLog() {
  if (!logBroadcast && !hasLogSession()) return;
  sendScopedCmd(logPaused ? 'log_resume' : 'log_pause');
}

// Button layout (computed each frame)
function btnLayout() {
  const ctrlH = controlHeight();
  let y = height - ctrlH + 28;
  let bw = 98,
    bh = 38,
    gap = 8,
    bx = 18;
  const mkProc = (label, on, onCmd, offCmd, colorOn, enabled = true, disabledLabel = null) => {
    if (!enabled) {
      return {
        x: 0,
        y,
        w: label === 'EMO' ? 140 : bw,
        h: bh,
        label: disabledLabel || `× ${label}`,
        bg: [45, 45, 55],
        action: () => {},
      };
    }
    return {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: (on ? '■ ' : '○ ') + label,
      bg: on ? colorOn : [60, 60, 80],
      action: () => sendScopedCmd(on ? offCmd : onCmd),
    };
  };
  const btns = [
    {
      x: 0,
      y,
      w: 168,
      h: bh,
      label: logBroadcast ? 'BROADCAST' : 'CURRENT STREAM',
      bg: logBroadcast ? [125, 95, 45] : [55, 65, 85],
      action: () => {
        logBroadcast = !logBroadcast;
      },
    },
    mkProc('VAD', vadRunning, 'vad_on', 'vad_off', [50, 140, 50]),
    mkProc('EMO', emotionRunning, 'emotion_on', 'emotion_off', [140, 90, 30], emotionLoaded, '× EMO NOT LOADED'),
    mkProc('PROS', prosodyRunning, 'prosody_on', 'prosody_off', [50, 100, 160]),
    {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: logRunning ? '■ LOGGING' : logPaused ? 'PAUSED' : logSessionOpen ? 'LOG OPEN' : '● START LOG',
      bg: logRunning ? [190, 45, 45] : logPaused || logSessionOpen ? [120, 90, 45] : [70, 65, 75],
      action: () => startLog(),
    },
    {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: logPaused ? '▶ RESUME' : '⏸ PAUSE',
      bg: hasLogSession() ? (logPaused ? [60, 140, 60] : [80, 120, 180]) : [45, 45, 55],
      action: () => pauseOrResumeLog(),
    },
    {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: '✓ SAVE LOG',
      bg: hasLogSession() ? [45, 125, 75] : [45, 45, 55],
      action: () => saveLog(),
    },
    {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: '× DISCARD',
      bg: hasLogSession() ? [125, 55, 55] : [45, 45, 55],
      action: () => discardLog(),
    },
  ];
  // Assign x positions left→right, wrapping on narrower browser windows.
  let x = bx;
  for (let i = 0; i < btns.length; i++) {
    const b = btns[i];
    if (i === 4) x += 26;
    if (x + b.w > width - 14) {
      x = bx;
      y += bh + 8;
    }
    b.x = x;
    b.y = y;
    x += b.w + gap;
  }
  return btns;
}

function controlHeight() {
  return width < 1100 ? 116 : CTRL_H;
}

function headerHeight() {
  return width < 1100 ? 330 : HEADER_H;
}

function canvasWidth() {
  return document.documentElement.clientWidth || windowWidth;
}

function selectedDevice() {
  return piList.find((p) => p.device_id === selectedDeviceId) || null;
}

function piGroups() {
  const groups = new Map();
  for (const p of piList) {
    const key = String(p.pi_id ?? p.hostname ?? p.addr ?? p.device_id ?? '?');
    if (!groups.has(key)) {
      groups.set(key, {
        pi_id: key,
        hostname: p.hostname || '',
        addr: p.addr || '',
        mics: [],
      });
    }
    const g = groups.get(key);
    if (!g.hostname && p.hostname) g.hostname = p.hostname;
    if (!g.addr && p.addr) g.addr = p.addr;
    g.mics.push(p);
  }
  return [...groups.values()].sort((a, b) =>
    String(a.pi_id).localeCompare(String(b.pi_id), undefined, { numeric: true }),
  );
}

function countPis() {
  return piGroups().length;
}

function micStatusText(p) {
  if (!p) return 'unknown';
  const connectedNow = p.connected !== undefined ? !!p.connected : (p.ageMs || 0) <= 8000;
  if (!connectedNow) return p.expected ? 'missing' : 'offline';
  if (p.audio_ok === true) return 'ok';
  if (p.audio_ok === false) return 'fail';
  return 'process';
}

function micStatusColor(p) {
  const s = micStatusText(p);
  if (s === 'ok') return [105, 245, 130];
  if (s === 'fail') return [255, 95, 80];
  if (s === 'missing') return [255, 95, 80];
  if (s === 'offline') return [120, 120, 130];
  return [245, 205, 90];
}

function drawPanel(x, y, w, h, title) {
  noStroke();
  fill(31, 31, 54);
  rect(x, y, w, h, 6);
  fill(47, 47, 76);
  rect(x, y, w, 22, 6, 6, 0, 0);
  fill(185);
  textAlign(LEFT, CENTER);
  textSize(11);
  text(title, x + 10, y + 11);
}

function ellipsize(value, maxW) {
  let s = String(value ?? '');
  if (textWidth(s) <= maxW) return s;
  while (s.length > 4 && textWidth(s + '...') > maxW) {
    s = s.slice(0, -1);
  }
  return s + '...';
}

function drawKeyValue(label, value, x, y, col = [210, 210, 220], maxW = 320) {
  textAlign(LEFT, TOP);
  textSize(11);
  fill(125, 125, 145);
  text(label, x, y);
  fill(col[0], col[1], col[2]);
  text(ellipsize(value, maxW), x + 82, y);
}

function drawHeader() {
  micHitAreas = [];
  headerButtons = [];
  const margin = 12;
  const y = 12;
  const gap = 10;
  const headerW = width - margin * 2;
  const compact = width < 1100;
  const streamW = compact ? headerW : min(560, max(430, headerW * 0.44));
  const selectedW = compact ? (headerW - gap) * 0.48 : min(340, max(260, headerW * 0.23));
  const transportW = compact ? headerW - selectedW - gap : max(320, headerW - streamW - selectedW - gap * 2);
  const streamH = compact ? 138 : HEADER_H - y - 12;
  const panelH = compact ? 160 : HEADER_H - y - 12;
  const x1 = margin;
  const x2 = compact ? margin : x1 + streamW + gap;
  const x3 = compact ? x2 + selectedW + gap : x2 + selectedW + gap;
  const y2 = compact ? y + streamH + gap : y;
  const sel = selectedDevice();
  const processOnline = !!sel && (sel.connected !== undefined ? !!sel.connected : (sel.ageMs || 0) < 8000);

  const piTitle = compact
    ? `Streams (${countPis()} Pi, ${piList.length} mics)`
    : `Pis And Microphones (${countPis()} Pi${countPis() === 1 ? '' : 's'}, ${piList.length} mic process${piList.length === 1 ? '' : 'es'})`;
  drawPanel(x1, y, streamW, streamH, piTitle);
  streamPanelRect = { x: x1, y, w: streamW, h: streamH };
  let gy = y + 32;
  const groups = piGroups();
  if (groups.length === 0) {
    fill(120);
    textAlign(LEFT, TOP);
    text('No processes discovered yet.', x1 + 12, gy);
  }
  for (const g of groups.slice(0, 7)) {
    fill(190);
    textAlign(LEFT, TOP);
    textSize(11);
    const piLabel = compact ? `${g.pi_id} ${g.addr || ''}` : `${g.pi_id}  ${g.hostname || ''}  ${g.addr || ''}`;
    text(ellipsize(piLabel, compact ? streamW - 24 : 250), x1 + 12, gy);
    let mx = compact ? x1 + 12 : x1 + 270;
    if (compact) gy += 14;
    for (const mic of g.mics
      .sort((a, b) => String(a.mic_id).localeCompare(String(b.mic_id), undefined, { numeric: true }))
      .slice(0, 4)) {
      const col = micStatusColor(mic);
      const label = `M${mic.mic_id}:${micStatusText(mic)}`;
      const chipW = textWidth(label) + 12;
      const selected = mic.device_id && mic.device_id === selectedDeviceId;
      fill(selected ? 58 : 34, selected ? 72 : 40, selected ? 92 : 58);
      stroke(selected ? 150 : 65, selected ? 180 : 75, selected ? 230 : 90);
      rect(mx - 5, gy - 4, chipW, 17, 4);
      noStroke();
      fill(col[0], col[1], col[2]);
      text(label, mx, gy);
      micHitAreas.push({ x: mx - 5, y: gy - 4, w: chipW, h: 17, deviceId: mic.device_id });
      mx += chipW + 10;
    }
    gy += 18;
  }
  streamListBottomY = gy;

  drawPanel(x2, y2, selectedW, panelH, 'Selected Stream');
  let ty = y2 + 34;
  const procCol = processOnline ? [110, 245, 130] : [255, 95, 80];
  drawKeyValue('process', processOnline ? 'online' : 'offline / waiting', x2 + 12, ty, procCol, selectedW - 104);
  drawKeyValue(
    'bridge',
    connected ? 'connected' : 'disconnected',
    x2 + 12,
    ty + 18,
    connected ? [110, 245, 130] : [255, 95, 80],
    selectedW - 104,
  );
  const micName = sel ? `MIC${sel.mic_id}` : 'mic';
  const audioStatus = audioOk ? `${micName} ok` : `${micName} failure${audioError ? ` - ${audioError}` : ''}`;
  drawKeyValue('mic', audioStatus, x2 + 12, ty + 36, audioOk ? [110, 245, 130] : [255, 95, 80], selectedW - 104);
  const logStatus = logRunning
    ? 'recording in RAM'
    : logPaused
      ? 'RAM paused'
      : logSessionOpen
        ? 'RAM session open'
        : 'idle';
  drawKeyValue('log', logStatus, x2 + 12, ty + 54, logRunning ? [255, 95, 80] : [150, 150, 165], selectedW - 104);
  drawKeyValue(
    'file',
    displayLogName(),
    x2 + 12,
    ty + 72,
    hasLogSession() ? [190, 205, 235] : [120, 120, 135],
    selectedW - 104,
  );
  if (!compact) drawKeyValue('input', audioDevice || 'configured', x2 + 12, ty + 90, [150, 150, 165], selectedW - 104);
  const selfAge = selfStats ? (Date.now() - selfStats.at) / 1000 : null;
  const tempText =
    selfStats && Number.isFinite(selfStats.tempC) && selfStats.tempC >= 0 ? ` temp ${selfStats.tempC.toFixed(1)}C` : '';
  if (!compact) {
    drawKeyValue(
      'process',
      selfStats
        ? `${selfStats.rssMb.toFixed(0)}MB  cpu ${selfStats.cpuPct.toFixed(0)}%  ${selfStats.threads} threads${tempText}  (${selfAge.toFixed(0)}s ago)`
        : 'waiting',
      x2 + 12,
      ty + 108,
      [160, 175, 210],
      selectedW - 104,
    );
  }
  addHeaderButton(
    max(x2 + 12, x2 + selectedW - 120),
    y2 + 30,
    min(108, selectedW - 24),
    24,
    'RETRY MIC',
    audioOk ? [65, 85, 95] : [170, 55, 55],
    () => sendCmd('audio_reconnect'),
  );

  drawPanel(x3, y2, transportW, panelH, 'Transport');
  textAlign(LEFT, TOP);
  textSize(10);
  const observed = observedOscHz();
  fill(215);
  text(
    `OSC: ${oscRunning ? 'sending' : 'stopped'} at ${fmtHz(oscSendHz)} (${fmtMs(1000 / max(oscSendHz, 0.001))})`,
    x3 + 12,
    y2 + 30,
  );
  fill(130);
  text(`observed packets: ${observed > 0 ? fmtHz(observed) : 'waiting'}   max ${fmtHz(oscMaxHz)}`, x3 + 12, y2 + 45);
  fill(190);
  text('Research CSV timeline', x3 + 12, y2 + 68);
  fill(130, 220, 185);
  text(`openSMILE + VAD: ${fmtHz(researchSyncHz)} / ${fmtMs(researchSyncPeriodMs)}`, x3 + 12, y2 + 84);
  fill(210, 170, 120);
  text(`emotion: ${fmtHz(researchEmotionHz)} hop / ${researchEmotionWindowS.toFixed(1)}s window`, x3 + 12, y2 + 100);
  const buttonY = y2 + panelH - 34;
  const buttonX = max(x3 + 12, x3 + transportW - 238);
  addHeaderButton(
    buttonX,
    buttonY,
    112,
    24,
    oscRunning ? '■ STOP OSC' : '● START OSC',
    oscRunning ? [180, 120, 0] : [60, 90, 130],
    () => sendCmd(oscRunning ? 'osc_stop' : 'osc_start'),
  );
  addHeaderButton(buttonX + 122, buttonY, 104, 24, `RATE ${fmtHz(oscSendHz)}`, [70, 75, 105], () => promptOscRate());
  transportRightEdge = x3 + transportW;
}

// ── p5.js ────────────────────────────────────────────────────────
function setup() {
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  window.scrollTo(0, 0);
  createCanvas(canvasWidth(), max(windowHeight, MIN_CANVAS_H));
  requestAnimationFrame(() => resizeCanvas(canvasWidth(), max(windowHeight, MIN_CANVAS_H)));
  textFont('monospace');
  ensureCommandLogBox();
  wsConnect();
}

function windowResized() {
  resizeCanvas(canvasWidth(), max(windowHeight, MIN_CANVAS_H));
  if (commandLogBox) commandLogBox.style('display', 'none');
}

function mousePressed() {
  for (const area of micHitAreas) {
    if (area.deviceId && inRect(mouseX, mouseY, area.x, area.y, area.w, area.h)) {
      selectDevice(area.deviceId);
      return;
    }
  }
  for (const b of headerButtons) {
    if (inRect(mouseX, mouseY, b.x, b.y, b.w, b.h)) {
      b.action();
      return;
    }
  }
  for (let b of btnLayout()) {
    if (inRect(mouseX, mouseY, b.x, b.y, b.w, b.h)) {
      b.action();
      return;
    }
  }
}

function draw() {
  background(26, 26, 46);
  drawHeader();

  const nCh = channelKeys.length;
  const emotionAreaH = emotionLabel ? 130 : 0;
  const topMargin = headerHeight() + 26 + emotionAreaH;
  const botMargin = controlHeight() + 15;
  const cmdGuessH = constrain(height * 0.22, 120, 220);
  const availableStripH = (height - topMargin - botMargin - BAR_H - 24 - cmdGuessH) / nCh;
  const stripH = min(MAX_STRIP_H, max(24, availableStripH));
  const lm = 145;
  const rm = 30; // right margin (was 20)
  const fullPlotW = width - lm - rm;
  const plotW = fullPlotW;

  // ── Emotion label (top center) ───────────────────────────────
  if (emotionLabel) {
    let ec = EMO_COLORS[emotionLabel] || [200, 200, 200];
    fill(ec[0], ec[1], ec[2]);
    noStroke();
    textAlign(CENTER, CENTER);
    textSize(min(42, width / 12));
    text(emotionLabel.toUpperCase() + '  ' + nf(emotionConf * 100, 0, 0) + '%', width / 2, headerHeight() + 26);

    // Bar chart spanning full width
    let emoGap = 6;
    let totalGaps = (EMOTION_DIMS.length - 1) * emoGap;
    let barW = (plotW - totalGaps) / EMOTION_DIMS.length;
    let barMaxH = 68;
    let bx = lm;
    let by = headerHeight() + 58;
    textSize(11);
    textAlign(CENTER, TOP);
    for (let i = 0; i < EMOTION_DIMS.length; i++) {
      let d = EMOTION_DIMS[i];
      let v = emotionScores[d] || 0;
      let c = EMO_COLORS[d] || [150, 150, 150];
      let h = v * barMaxH;
      fill(c[0], c[1], c[2], 180);
      noStroke();
      rect(bx, by + barMaxH - h, barW, h, 3);
      fill(150);
      text(d.substr(0, 4), bx + barW / 2, by + barMaxH + 3);
      bx += barW + emoGap;
    }
  }

  // ── VAD bar (tri-state: -1 = VAD off, 0 = silent, 1 = speech) ──
  let vadY = topMargin - BAR_H - 6;
  noStroke();

  // Always draw a background so the strip is visible even with no data
  fill(40, 40, 60);
  rect(lm, vadY, plotW, BAR_H, 3);

  // Draw VAD history on top
  for (let i = 0; i < vadHistory.length; i++) {
    let x = lm + map(i, 0, HISTORY, 0, plotW);
    let w = plotW / HISTORY + 1;
    let v = vadHistory[i];
    if (v > 0.5) {
      fill(100, 255, 100, 180); // speech detected — green
    } else if (v < -0.5) {
      fill(80, 200, 200, 120); // VAD OFF (gate open) — muted cyan
    } else {
      fill(60, 60, 80, 120); // silence — dark
    }
    rect(x, vadY, w, BAR_H);
  }

  // VAD label — always visible, shows current state
  let lastVad = vadHistory.length > 0 ? vadHistory[vadHistory.length - 1] : 0;
  let vadStateText, vadLabelColor;
  if (vadHistory.length === 0) {
    vadStateText = 'VAD';
    vadLabelColor = [120, 120, 120]; // grey — no data yet
  } else if (lastVad < -0.5) {
    vadStateText = 'VAD OFF';
    vadLabelColor = [80, 200, 200]; // cyan — gate always open
  } else if (lastVad > 0.5) {
    vadStateText = 'VAD ON';
    vadLabelColor = [100, 255, 100]; // green — speech
  } else {
    vadStateText = 'VAD ON';
    vadLabelColor = [140, 140, 160]; // dim — silence (VAD active but quiet)
  }
  fill(vadLabelColor[0], vadLabelColor[1], vadLabelColor[2]);
  textSize(14);
  textAlign(RIGHT, CENTER);
  text(vadStateText, lm - 8, vadY + BAR_H / 2);

  // ── Prosody strips ──────────────────────────────────────────
  for (let ci = 0; ci < nCh; ci++) {
    let key = channelKeys[ci];
    let ch = channels[key];
    let y0 = topMargin + ci * stripH;
    let y1 = y0 + stripH - 4;

    // Background
    fill(30, 30, 50);
    noStroke();
    rect(lm, y0, plotW, stripH - 4, 3);

    // Label
    fill(ch.color[0], ch.color[1], ch.color[2]);
    textSize(14);
    textAlign(RIGHT, CENTER);
    text(ch.label, lm - 8, y0 + (stripH - 4) / 2);

    // Determine if this is F0 (scatter/segment style for unvoiced gaps)
    let isF0 = key === 'F0semitoneFrom27.5Hz_sma3nz';

    // Line plot
    if (ch.data.length > 1) {
      if (isF0) {
        // F0: draw only segments where value > 0 (skip unvoiced)
        stroke(ch.color[0], ch.color[1], ch.color[2]);
        strokeWeight(2);
        noFill();
        let inSegment = false;
        for (let i = 0; i < ch.data.length; i++) {
          let x = lm + map(i, 0, HISTORY, 0, plotW);
          let raw = ch.data[i];
          let v = constrain(raw / ch.hi, 0, 1);
          let y = map(v, 0, 1, y1, y0);
          if (raw > 0.5) {
            if (!inSegment) {
              beginShape();
              inSegment = true;
            }
            vertex(x, y);
          } else {
            if (inSegment) {
              endShape();
              inSegment = false;
            }
          }
        }
        if (inSegment) endShape();
      } else {
        // Other features: continuous line + fill
        stroke(ch.color[0], ch.color[1], ch.color[2]);
        strokeWeight(2);
        noFill();
        beginShape();
        for (let i = 0; i < ch.data.length; i++) {
          let x = lm + map(i, 0, HISTORY, 0, plotW);
          let v = constrain(ch.data[i] / ch.hi, 0, 1);
          let y = map(v, 0, 1, y1, y0);
          vertex(x, y);
        }
        endShape();

        // Filled area
        fill(ch.color[0], ch.color[1], ch.color[2], 40);
        noStroke();
        beginShape();
        vertex(lm + map(0, 0, HISTORY, 0, plotW), y1);
        for (let i = 0; i < ch.data.length; i++) {
          let x = lm + map(i, 0, HISTORY, 0, plotW);
          let v = constrain(ch.data[i] / ch.hi, 0, 1);
          let y = map(v, 0, 1, y1, y0);
          vertex(x, y);
        }
        vertex(lm + map(ch.data.length - 1, 0, HISTORY, 0, plotW), y1);
        endShape(CLOSE);
      }
    }
  }

  // Command bus occupies the full graph width under the plots.
  const cmdPanelY = topMargin + nCh * stripH + 12;
  const cmdPanelH = max(110, height - botMargin - cmdPanelY - 6);
  drawCommandBusPanel(12, cmdPanelY, width - 24, cmdPanelH);

  // ── Control panel ────────────────────────────────────────────
  stroke(50);
  strokeWeight(1);
  line(0, height - controlHeight(), width, height - controlHeight());
  noStroke();

  let btns = btnLayout();
  if (btns.length >= 4) {
    textAlign(LEFT, TOP);
    textSize(10);
    fill(125, 125, 145);
    text('Command scope', btns[0].x, btns[0].y - 13);
    text('What to log', btns[1].x, btns[1].y - 13);
    text('Logging actions', btns[4].x, btns[4].y - 13);
  }
  for (let b of btns) {
    let hover = inRect(mouseX, mouseY, b.x, b.y, b.w, b.h);
    drawBtn(b.x, b.y, b.w, b.h, b.label, b.bg, hover);
  }
}
