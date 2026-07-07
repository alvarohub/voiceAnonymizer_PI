#!/usr/bin/env node
/*
 * Capture central-receiver screenshots for README documentation.
 *
 * In two other terminals, run:
 *   ./run_web.sh
 *   node docs/demo_receiver_osc.js
 *
 * Then run:
 *   node docs/capture_receiver_screenshots.js
 */

const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

let WebSocket;
try {
  WebSocket = require(path.join(__dirname, '..', 'receiver', 'node_modules', 'ws'));
} catch (error) {
  console.error('[capture] missing receiver/node_modules/ws');
  console.error('[capture] run: cd receiver && npm install');
  process.exit(1);
}

const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const URL = process.env.RECEIVER_URL || 'http://localhost:3000';
const OUT_DIR = path.join(__dirname, 'images');
const DEBUG_PORT = Number(process.env.CHROME_DEBUG_PORT || 9223);

const SHOTS = [
  { file: 'central-receiver-live.png', width: 1280, height: 900 },
  { file: 'central-receiver-compact.png', width: 900, height: 700 },
];

function requestJson(method, requestPath) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port: DEBUG_PORT, path: requestPath, method }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => (body += chunk));
      res.on('end', () => {
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    req.on('error', reject);
    req.end();
  });
}

async function waitForDebugPort(deadlineMs = 8000) {
  const start = Date.now();
  while (Date.now() - start < deadlineMs) {
    try {
      return await requestJson('GET', '/json/version');
    } catch (_) {
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }
  throw new Error(`Chrome DevTools port ${DEBUG_PORT} did not open`);
}

function cdpClient(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let nextId = 1;
  const pending = new Map();
  ws.on('message', (data) => {
    const msg = JSON.parse(data.toString());
    if (!msg.id || !pending.has(msg.id)) return;
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) reject(new Error(msg.error.message || JSON.stringify(msg.error)));
    else resolve(msg.result || {});
  });
  return new Promise((resolve, reject) => {
    ws.once('open', () => {
      resolve({
        send(method, params = {}) {
          const id = nextId++;
          ws.send(JSON.stringify({ id, method, params }));
          return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
        },
        close() {
          ws.close();
        },
      });
    });
    ws.once('error', reject);
  });
}

async function waitForSamples(client, deadlineMs = 12000) {
  const expression = `(() => {
    const picker = document.getElementById('device-picker');
    return Boolean(typeof sampleIndex !== 'undefined' && sampleIndex > 25 && picker && picker.options.length > 1);
  })()`;
  const start = Date.now();
  while (Date.now() - start < deadlineMs) {
    const result = await client.send('Runtime.evaluate', { expression, returnByValue: true });
    if (result.result && result.result.value === true) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('Receiver UI did not receive demo samples in time');
}

async function capture(client, shot) {
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: shot.width,
    height: shot.height,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await client.send('Runtime.evaluate', { expression: 'window.dispatchEvent(new Event("resize"));' });
  await new Promise((resolve) => setTimeout(resolve, 700));
  const png = await client.send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  const outPath = path.join(OUT_DIR, shot.file);
  fs.writeFileSync(outPath, Buffer.from(png.data, 'base64'));
  console.log(`[capture] wrote ${outPath}`);
}

async function main() {
  if (!fs.existsSync(CHROME)) {
    throw new Error(`Chrome not found at ${CHROME}. Set CHROME_PATH to override.`);
  }
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'receiver-shot-'));
  const chrome = spawn(
    CHROME,
    [
      '--headless=new',
      '--disable-gpu',
      '--hide-scrollbars',
      `--remote-debugging-port=${DEBUG_PORT}`,
      `--user-data-dir=${userDataDir}`,
      'about:blank',
    ],
    { stdio: ['ignore', 'ignore', 'pipe'] },
  );
  chrome.stderr.on('data', () => {});

  try {
    await waitForDebugPort();
    const tab = await requestJson('PUT', `/json/new?${encodeURIComponent(URL)}`);
    const client = await cdpClient(tab.webSocketDebuggerUrl);
    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await waitForSamples(client);
    for (const shot of SHOTS) await capture(client, shot);
    client.close();
  } finally {
    chrome.kill('SIGTERM');
    try {
      fs.rmSync(userDataDir, { recursive: true, force: true, maxRetries: 3, retryDelay: 150 });
    } catch (_) {}
  }
}

main().catch((error) => {
  console.error(`[capture] ${error.message}`);
  process.exit(1);
});
