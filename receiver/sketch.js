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
const CTRL_H = 70; // height reserved for control panel at bottom (was 50)

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
let ackStatus = { status: 'idle', receivedAt: 0 };

// Per-address send rate (Hz), keyed by full OSC address; updated via /stats/rate
let rateByAddr = {};

// Discovered Pis (from bridge /hello registry). Array of {addr,device_id,pi_id,mic_id,hostname,version,ageMs}.
let piList = [];
// Currently selected device_id (mirrors the <select> dropdown). null = auto.
let selectedDeviceId = null;

// ── WebSocket ────────────────────────────────────────────────────
function wsConnect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    connected = true;
    document.getElementById('status').textContent = 'connected ✓';
    document.getElementById('status').style.color = '#6f6';
    // Ask the Pi for current control flags so the UI reflects reality
    // (the streamer may have been launched with --osc-autostart).
    sendCmd('query_state');
  };

  ws.onclose = () => {
    connected = false;
    document.getElementById('status').textContent = 'disconnected — retrying…';
    document.getElementById('status').style.color = '#f66';
    setTimeout(wsConnect, RECONNECT_MS);
  };

  ws.onerror = () => ws.close();

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
      }
      // Pi discovery list from bridge
      if (msg.type === 'pi_list') {
        piList = msg.pis || [];
        updateDevicePicker();
      }
    } catch (_) {}
  };
}

function sendCmd(cmd, args = []) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'cmd', cmd, args, target_device: selectedDeviceId || undefined }));
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

// Wire the dropdown's change event once the DOM is ready.
window.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('device-picker');
  if (!sel) return;
  sel.addEventListener('change', (e) => {
    selectedDeviceId = e.target.value || null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'select_device', target: selectedDeviceId }));
    }
    // Reset transient view state so we don't show stale data from previous device.
    vadHistory = [];
    sampleIndex = 0;
    rateByAddr = {};
    logRunning = false;
    logPaused = false;
    logSessionOpen = false;
    emotionLoaded = true;
    for (const k of channelKeys) channels[k].data = [];
    sendCmd('query_state');
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
    return;
  }
  if (address === '/state/log_session_open') {
    logSessionOpen = !!args[0];
    return;
  }
  if (address === '/state/log_paused') {
    logPaused = !!args[0];
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

  // Per-address rate stats: /stats/rate <addr> <hz>
  if (address === '/stats/rate') {
    if (args.length >= 2) rateByAddr[args[0]] = args[1];
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
  const target = ackStatus.target_device ? ` ${ackStatus.target_device}` : '';
  const elapsed = ackStatus.elapsed_ms !== undefined ? `${ackStatus.elapsed_ms}ms` : '';
  if (ackStatus.status === 'pending') {
    return { text: `cmd:${target} ${cmd} waiting ACK`, color: [245, 205, 90] };
  }
  if (ackStatus.status === 'ok') {
    return { text: `cmd:${target} ${cmd} ACK ${elapsed}`, color: [110, 245, 130] };
  }
  if (ackStatus.status === 'timeout') {
    return { text: `cmd:${target} ${cmd} ACK TIMEOUT ${elapsed}`, color: [255, 95, 80] };
  }
  if (ackStatus.status === 'late') {
    return { text: `cmd:${target} ${cmd} ACK LATE`, color: [255, 170, 80] };
  }
  return { text: `cmd:${target} ${cmd} ACK ERROR`, color: [255, 95, 80] };
}

// Button layout (computed each frame)
function btnLayout() {
  let y = height - CTRL_H + 16;
  let bw = 110,
    bh = 38,
    gap = 10,
    bx = 135;
  const mkProc = (label, on, onCmd, offCmd, colorOn, enabled = true) => {
    if (!enabled) {
      return {
        x: 0,
        y,
        w: bw,
        h: bh,
        label: `× ${label}`,
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
      action: () => sendCmd(on ? offCmd : onCmd),
    };
  };
  const btns = [
    {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: oscRunning ? '■ STOP OSC' : '● OSC',
      bg: oscRunning ? [180, 120, 0] : [60, 60, 80],
      action: () => sendCmd(oscRunning ? 'osc_stop' : 'osc_start'),
    },
    {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: '● NEW LOG',
      bg: [150, 50, 50],
      action: () => sendCmd('log_start'),
    },
    {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: logPaused ? '▶ RESUME' : '⏸ PAUSE',
      bg: logSessionOpen ? (logPaused ? [60, 140, 60] : [80, 120, 180]) : [45, 45, 55],
      action: () => {
        if (!logSessionOpen) return;
        sendCmd(logPaused ? 'log_resume' : 'log_pause');
      },
    },
    {
      x: 0,
      y,
      w: bw,
      h: bh,
      label: '■ STOP LOG',
      bg: logSessionOpen ? [180, 50, 50] : [45, 45, 55],
      action: () => {
        if (logSessionOpen) sendCmd('log_stop');
      },
    },
    mkProc('VAD', vadRunning, 'vad_on', 'vad_off', [50, 140, 50]),
    mkProc('EMO', emotionRunning, 'emotion_on', 'emotion_off', [140, 90, 30], emotionLoaded),
    mkProc('PROS', prosodyRunning, 'prosody_on', 'prosody_off', [50, 100, 160]),
  ];
  // Assign x positions left→right
  let x = bx;
  for (const b of btns) {
    b.x = x;
    x += b.w + gap;
  }
  return btns;
}

// ── p5.js ────────────────────────────────────────────────────────
function setup() {
  createCanvas(1080, 780);
  textFont('monospace');
  wsConnect();
}

function windowResized() {
  resizeCanvas(windowWidth, windowHeight);
}

function mousePressed() {
  for (let b of btnLayout()) {
    if (inRect(mouseX, mouseY, b.x, b.y, b.w, b.h)) {
      b.action();
      return;
    }
  }
}

function draw() {
  background(26, 26, 46);

  const nCh = channelKeys.length;
  const topMargin = 195; // extra room for emotion bars (was 130)
  const botMargin = CTRL_H + 15;
  const stripH = (height - topMargin - botMargin - BAR_H - 15) / nCh;
  const lm = 135; // left margin for labels (was 90)
  const rm = 30; // right margin (was 20)
  const plotW = width - lm - rm;

  // ── Sample counter (top-left) ────────────────────────────────
  fill(100);
  noStroke();
  textAlign(LEFT, TOP);
  textSize(13);
  text(`sample: ${sampleIndex}`, 12, 12);
  // Per-address rates (small, under the sample counter)
  const rateAddrs = Object.keys(rateByAddr).sort();
  let stateY = 30;
  if (rateAddrs.length) {
    textSize(11);
    fill(140);
    let ry = 30;
    for (const a of rateAddrs) {
      const short = a.split('/').pop();
      text(`${short}: ${rateByAddr[a].toFixed(1)} Hz`, 12, ry);
      ry += 13;
    }
    stateY = ry + 2;
  }
  textSize(11);
  fill(140);
  const logStatus = logRunning
    ? 'log: active'
    : logPaused
      ? 'log: paused'
      : logSessionOpen
        ? 'log: ready'
        : 'log: idle';
  const emoStatus = emotionLoaded ? (emotionRunning ? 'emo: active' : 'emo: ready') : 'emo: not loaded';
  text(logStatus, 12, stateY);
  text(emoStatus, 12, stateY + 13);
  const ack = ackDisplay();
  fill(ack.color[0], ack.color[1], ack.color[2]);
  text(ack.text, 12, stateY + 26);
  // ── Emotion label (top center) ───────────────────────────────
  if (emotionLabel) {
    let ec = EMO_COLORS[emotionLabel] || [200, 200, 200];
    fill(ec[0], ec[1], ec[2]);
    noStroke();
    textAlign(CENTER, CENTER);
    textSize(min(42, width / 12));
    text(emotionLabel.toUpperCase() + '  ' + nf(emotionConf * 100, 0, 0) + '%', width / 2, 36);

    // Bar chart spanning full width
    let emoGap = 6;
    let totalGaps = (EMOTION_DIMS.length - 1) * emoGap;
    let barW = (plotW - totalGaps) / EMOTION_DIMS.length;
    let barMaxH = 68;
    let bx = lm;
    let by = 70;
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

  // ── Control panel ────────────────────────────────────────────
  stroke(50);
  strokeWeight(1);
  line(0, height - CTRL_H, width, height - CTRL_H);
  noStroke();

  let btns = btnLayout();
  for (let b of btns) {
    let hover = inRect(mouseX, mouseY, b.x, b.y, b.w, b.h);
    drawBtn(b.x, b.y, b.w, b.h, b.label, b.bg, hover);
  }

  // ── Connection indicator (labeled) ──────────────────────────
  let connColor = connected ? [100, 255, 100] : [255, 80, 80];
  let connText = connected ? 'connected' : 'disconnected';
  fill(connColor[0], connColor[1], connColor[2]);
  noStroke();
  circle(width - 16, height - CTRL_H / 2, 12);
  textSize(13);
  textAlign(RIGHT, CENTER);
  text(connText, width - 28, height - CTRL_H / 2);

  // ── Pi registry (top-right) ─────────────────────────────────
  textAlign(RIGHT, TOP);
  textSize(12);
  if (piList.length === 0) {
    fill(120);
    text('no Pi detected', width - 12, 12);
  } else {
    fill(180);
    text(`${piList.length} Pi${piList.length > 1 ? 's' : ''} online`, width - 12, 12);
    let py = 28;
    textSize(11);
    for (const p of piList) {
      // Green if heard from recently, dim if stale
      let fresh = (p.ageMs || 0) < 4000;
      fill(fresh ? 140 : 90, fresh ? 220 : 140, fresh ? 140 : 90);
      const label = p.device_id ? `${p.device_id} ${p.hostname || ''}` : p.hostname || '?';
      text(`${label} (${p.addr})`, width - 12, py);
      py += 13;
    }
  }
}
