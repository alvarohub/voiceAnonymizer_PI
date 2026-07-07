/**
 * OSC → WebSocket bridge
 *
 * Receives UDP OSC messages from the Python app and forwards them
 * as JSON over WebSocket to the p5.js browser client.
 *
 * Usage:
 *   cd receiver && npm install && npm start
 *
 * Env vars:
 *   OSC_PORT    – UDP port to listen on (default 9000)
 *   WS_PORT     – WebSocket port for browser (default 8765)
 *   HTTP_PORT   – HTTP port for serving index.html (default 3000)
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const osc = require('osc');
const WebSocket = require('ws');

const OSC_PORT = parseInt(process.env.OSC_PORT || '9000', 10);
const WS_PORT = parseInt(process.env.WS_PORT || '8765', 10);
const HTTP_PORT = parseInt(process.env.HTTP_PORT || '3000', 10);
const CTRL_PORT_FALLBACK = parseInt(process.env.CTRL_PORT || '9001', 10); // default when registry has no entry
const ACK_TIMEOUT_MS = parseInt(process.env.ACK_TIMEOUT_MS || '750', 10);
const ACK_SENTINEL = '__ack__';
// Fallback CTRL target if no OSC has been received yet (env override or localhost).
const CTRL_HOST_FALLBACK = process.env.CTRL_HOST || '127.0.0.1';

// Last sender of incoming OSC; used only as a last-ditch fallback for CTRL routing.
let lastSenderAddress = null;

// ── Device registry (populated by /hello v2 broadcasts) ──────────
// device_id -> { ip, ctrl_port, pi_id, mic_id, hostname, version, lastSeen }
// device_id is "<pi_id>-<mic_id>" (e.g. "5-2"). For legacy v1 /hello
// (3 args) we synthesize device_id = "<pi_id>-1".
const devices = new Map();
const DEV_TTL_MS = 8000; // drop a device if no /hello for this long

function deviceListSnapshot() {
  const now = Date.now();
  return Array.from(devices.entries()).map(([device_id, d]) => ({
    device_id,
    pi_id: d.pi_id,
    mic_id: d.mic_id,
    addr: d.ip,
    ctrl_port: d.ctrl_port,
    hostname: d.hostname,
    version: d.version,
    ageMs: now - d.lastSeen,
  }));
}
// Back-compat alias for any older browser code expecting piListSnapshot().
const piListSnapshot = deviceListSnapshot;

function broadcastPiList() {
  broadcast({ type: 'pi_list', pis: deviceListSnapshot() });
}

// Periodic prune: drop stale devices, push update to browser if anything changed.
setInterval(() => {
  const now = Date.now();
  let changed = false;
  for (const [device_id, d] of devices) {
    if (now - d.lastSeen > DEV_TTL_MS) {
      devices.delete(device_id);
      console.log(`[HELLO] lost ${device_id} (${d.hostname || d.ip})`);
      changed = true;
    }
  }
  if (changed) broadcastPiList();
}, 2000);

// ── HTTP server (serves index.html + sketch.js) ──────────────────
const MIME = {
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
};

const httpServer = http.createServer((req, res) => {
  let filePath = req.url === '/' ? '/index.html' : req.url;
  filePath = path.join(__dirname, filePath);
  const ext = path.extname(filePath);
  const contentType = MIME[ext] || 'application/octet-stream';

  // Prevent path traversal
  const resolved = path.resolve(filePath);
  if (!resolved.startsWith(path.resolve(__dirname))) {
    res.writeHead(403);
    res.end('Forbidden');
    return;
  }

  fs.readFile(resolved, (err, data) => {
    if (err) {
      res.writeHead(404);
      res.end('Not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(data);
  });
});

httpServer.listen(HTTP_PORT, () => {
  console.log(`[HTTP] http://localhost:${HTTP_PORT}`);
});

// ── WebSocket server ─────────────────────────────────────────────
const wss = new WebSocket.Server({ port: WS_PORT });
const clients = new Set();

// Per-WS-client state: which device the user has currently selected.
// All non-state OSC messages are filtered to that device before forwarding,
// so the existing p5.js sketch (which expects un-prefixed addresses like
// /speech/F0... and /state/vad_active) keeps working unchanged.
const wsSelected = new WeakMap(); // ws -> device_id (string) | null
const pendingAcks = new Map(); // cmd_id -> { cmd, targetId, sentAt, timer }
let nextCmdSeq = 1;

function oscArg(value) {
  return { type: 's', value: String(value) };
}

function broadcastAckStatus(status) {
  broadcast({ type: 'ack_status', ...status });
}

function sendCtrl(targetHost, targetPort, cmd, args = [], targetId = null) {
  const cmdId = `${Date.now().toString(36)}-${nextCmdSeq++}`;
  const sentAt = Date.now();
  const cmdArgs = [...args, ACK_SENTINEL, cmdId, String(OSC_PORT)];
  const timer = setTimeout(() => {
    const pending = pendingAcks.get(cmdId);
    if (!pending) return;
    pendingAcks.delete(cmdId);
    broadcastAckStatus({
      status: 'timeout',
      cmd,
      cmd_id: cmdId,
      target_device: targetId,
      elapsed_ms: Date.now() - sentAt,
      timeout_ms: ACK_TIMEOUT_MS,
      message: 'no ACK received',
    });
    console.warn(`[ACK] timeout /ctrl/${cmd} ${cmdId} target=${targetId || `${targetHost}:${targetPort}`}`);
  }, ACK_TIMEOUT_MS);
  pendingAcks.set(cmdId, { cmd, targetId, sentAt, timer });

  const cmdPort = new osc.UDPPort({
    localAddress: '0.0.0.0',
    localPort: 0,
    remoteAddress: targetHost,
    remotePort: targetPort,
    metadata: true,
  });
  cmdPort.open();
  cmdPort.on('ready', () => {
    broadcastAckStatus({
      status: 'pending',
      cmd,
      cmd_id: cmdId,
      target_device: targetId,
      timeout_ms: ACK_TIMEOUT_MS,
      message: 'waiting for ACK',
    });
    cmdPort.send({ address: '/ctrl/' + cmd, args: cmdArgs.map(oscArg) });
    const suffix = args.length ? ` ${JSON.stringify(args)}` : '';
    console.log(`[CTRL] → /ctrl/${cmd}${suffix} ack=${cmdId} → ${targetHost}:${targetPort}`);
    setTimeout(() => cmdPort.close(), 100);
  });
}

function handleCommandAck(deviceId, args) {
  const [cmd, cmdId, okRaw, message] = args;
  if (!cmdId) return false;
  const pending = pendingAcks.get(String(cmdId));
  if (!pending) {
    broadcastAckStatus({
      status: 'late',
      cmd: cmd || '?',
      cmd_id: String(cmdId),
      target_device: deviceId,
      message: message || 'ACK arrived after timeout or for unknown command',
    });
    console.warn(`[ACK] late/unknown ${cmd || '?'} ${cmdId} from ${deviceId || '?'}`);
    return true;
  }
  clearTimeout(pending.timer);
  pendingAcks.delete(String(cmdId));
  const ok = okRaw === true || okRaw === 1 || okRaw === '1' || String(okRaw).toLowerCase() === 'true';
  const elapsedMs = Date.now() - pending.sentAt;
  broadcastAckStatus({
    status: ok ? 'ok' : 'error',
    cmd: cmd || pending.cmd,
    cmd_id: String(cmdId),
    target_device: deviceId || pending.targetId,
    elapsed_ms: elapsedMs,
    timeout_ms: ACK_TIMEOUT_MS,
    message: message || (ok ? 'ACK received' : 'command failed'),
  });
  console.log(`[ACK] ${ok ? 'ok' : 'error'} /ctrl/${cmd || pending.cmd} ${cmdId} ${elapsedMs}ms from ${deviceId || '?'}`);
  return true;
}

wss.on('connection', (ws) => {
  clients.add(ws);
  wsSelected.set(ws, null);
  console.log(`[WS] client connected (${clients.size} total)`);
  // Send a snapshot of currently-known devices so a fresh browser tab
  // immediately knows what's out there.
  ws.send(JSON.stringify({ type: 'pi_list', pis: deviceListSnapshot() }));

  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data);

      // Device selection from the browser dropdown.
      if (msg.type === 'select_device') {
        const target = msg.target || null;
        wsSelected.set(ws, target);
        console.log(`[WS] client selected device: ${target || '(none)'}`);
        return;
      }

      if (msg.type === 'cmd' && msg.cmd) {
        // Resolve target: explicit target_device > selected device > legacy fallback.
        const targetId = msg.target_device || wsSelected.get(ws) || null;
        let host = CTRL_HOST_FALLBACK;
        let port = CTRL_PORT_FALLBACK;
        let cmdArgs = Array.isArray(msg.args) ? [...msg.args] : [];
        if (targetId && devices.has(targetId)) {
          const d = devices.get(targetId);
          host = d.ip;
          port = d.ctrl_port || CTRL_PORT_FALLBACK;
        } else if (lastSenderAddress) {
          host = lastSenderAddress;
        }
        if (msg.cmd === 'log_start' && cmdArgs.length === 0) {
          const startMs = String(Date.now());
          cmdArgs = [startMs, new Date(Number(startMs)).toISOString()];
        }
        sendCtrl(host, port, msg.cmd, cmdArgs, targetId);
      }
    } catch (_) {}
  });

  ws.on('close', () => {
    clients.delete(ws);
    wsSelected.delete(ws);
    console.log(`[WS] client disconnected (${clients.size} total)`);
  });
});

function broadcast(obj) {
  const msg = JSON.stringify(obj);
  for (const ws of clients) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(msg);
    }
  }
}

console.log(`[WS] ws://localhost:${WS_PORT}`);

// ── OSC UDP receiver ─────────────────────────────────────────────
const udpPort = new osc.UDPPort({
  localAddress: '0.0.0.0',
  localPort: OSC_PORT,
  metadata: true,
});

udpPort.on('message', (oscMsg, timeTag, info) => {
  const addr = oscMsg.address;
  const args = (oscMsg.args || []).map((a) => a.value);
  const senderIp = info && info.address ? info.address : null;

  // Remember last sender IP as a last-ditch CTRL fallback only.
  if (senderIp && senderIp !== lastSenderAddress) {
    const prev = lastSenderAddress;
    lastSenderAddress = senderIp;
    console.log(`[OSC] sender ${prev ? 'changed' : 'first seen'}: ${senderIp}`);
  }

  // /hello — discovery heartbeat from a Pi. v2 payload (preferred):
  //   [device_id, pi_id, mic_id, hostname, ctrl_port, version]
  // v1 payload (legacy):
  //   [pi_id, hostname, version]
  if (addr === '/hello' && senderIp) {
    let device_id, pi_id, mic_id, hostname, ctrl_port, version;
    if (args.length >= 6) {
      [device_id, pi_id, mic_id, hostname, ctrl_port, version] = args;
      ctrl_port = parseInt(ctrl_port, 10) || CTRL_PORT_FALLBACK;
    } else {
      // Legacy: synthesize device_id from pi_id (assume mic 1, default ctrl port).
      [pi_id, hostname, version] = args;
      mic_id = 1;
      device_id = `${pi_id}-1`;
      ctrl_port = CTRL_PORT_FALLBACK;
    }
    device_id = String(device_id);
    const prev = devices.get(device_id);
    const isNew = !prev;
    devices.set(device_id, {
      ip: senderIp,
      ctrl_port,
      pi_id,
      mic_id,
      hostname: hostname || senderIp,
      version: version || '?',
      lastSeen: Date.now(),
    });
    if (isNew) {
      console.log(`[HELLO] new device ${device_id} (${hostname || pi_id}) @ ${senderIp}:${ctrl_port} v${version}`);
      broadcastPiList();
    }
    return;
  }

  // Parse /dev/<device_id>/<rest> namespacing. If a message arrives without
  // a /dev/ prefix we treat it as un-namespaced (legacy) and forward to all.
  let msgDeviceId = null;
  let strippedAddr = addr;
  const m = addr.match(/^\/dev\/([^\/]+)(\/.*)$/);
  if (m) {
    msgDeviceId = m[1];
    strippedAddr = m[2]; // e.g. /speech/F0... or /state/vad_active
  }

  if (strippedAddr === '/ack') {
    handleCommandAck(msgDeviceId, args);
    return;
  }

  // Forward to each WS client, filtered by their selected device.
  // Clients with no selection see only legacy (un-namespaced) messages
  // until they pick one — keeps the UI quiet when many devices are present.
  const payload = JSON.stringify({
    address: strippedAddr,
    args,
    device_id: msgDeviceId,
    address_full: addr,
  });
  for (const ws of clients) {
    if (ws.readyState !== WebSocket.OPEN) continue;
    const sel = wsSelected.get(ws);
    if (msgDeviceId === null) {
      // Legacy / un-namespaced — always forward.
      ws.send(payload);
    } else if (sel === null) {
      // No selection yet: auto-select the first device we hear from so the
      // single-device case Just Works without the user touching the dropdown.
      wsSelected.set(ws, msgDeviceId);
      console.log(`[WS] auto-selected device ${msgDeviceId} for new client`);
      ws.send(payload);
    } else if (sel === msgDeviceId) {
      ws.send(payload);
    }
    // else: message is for a non-selected device — drop.
  }
});

udpPort.on('error', (err) => {
  console.error('[OSC error]', err);
});

udpPort.open();
console.log(`[OSC] listening on UDP :${OSC_PORT}`);
console.log(`Ready. Open http://localhost:${HTTP_PORT} in your browser.`);
