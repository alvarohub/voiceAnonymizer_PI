#!/usr/bin/env node
/*
 * Demo OSC sender for central-receiver screenshots.
 *
 * Run the bridge first:
 *   ./run_web.sh
 *
 * Then in another terminal:
 *   node docs/demo_receiver_osc.js
 *
 * The script fakes two mic processes from one Pi. It sends the same /hello
 * discovery heartbeat and /dev/<device_id>/... OSC messages that strip_monitor.py
 * sends, so the browser UI can be documented without real Pis connected.
 */

const path = require('path');

let osc;
try {
  osc = require(path.join(__dirname, '..', 'receiver', 'node_modules', 'osc'));
} catch (error) {
  console.error('[demo] missing receiver/node_modules/osc');
  console.error('[demo] run: cd receiver && npm install');
  process.exit(1);
}

const HOST = process.env.DEMO_OSC_HOST || '127.0.0.1';
const PORT = Number(process.env.DEMO_OSC_PORT || 9000);
const TICK_MS = Number(process.env.DEMO_OSC_TICK_MS || 100);

const FEATURES = [
  'F0semitoneFrom27.5Hz_sma3nz',
  'Loudness_sma3',
  'jitterLocal_sma3nz',
  'shimmerLocaldB_sma3nz',
  'HNRdBACF_sma3nz',
];

const EMOTIONS = ['angry', 'disgusted', 'fearful', 'happy', 'neutral', 'other', 'sad', 'surprised', 'unknown'];
const DEVICES = [
  { deviceId: '5-1', piId: '5', micId: '1', hostname: 'pi-voice-05', ctrlPort: 9001, phase: 0 },
  { deviceId: '5-2', piId: '5', micId: '2', hostname: 'pi-voice-05', ctrlPort: 9002, phase: 1.7 },
];

const udp = new osc.UDPPort({
  localAddress: '0.0.0.0',
  localPort: 0,
  remoteAddress: HOST,
  remotePort: PORT,
  metadata: true,
});

function arg(value) {
  if (typeof value === 'number') return { type: 'f', value };
  return { type: 's', value: String(value) };
}

function send(address, values = []) {
  udp.send({ address, args: values.map(arg) });
}

function sendHello(device) {
  send('/hello', [device.deviceId, device.piId, device.micId, device.hostname, device.ctrlPort, '2']);
}

function sendState(device) {
  const root = `/dev/${device.deviceId}`;
  send(`${root}/state/osc_active`, [1]);
  send(`${root}/state/log_active`, [1]);
  send(`${root}/state/log_session_open`, [1]);
  send(`${root}/state/log_paused`, [0]);
  send(`${root}/state/vad_active`, [1]);
  send(`${root}/state/emotion_active`, [1]);
  send(`${root}/state/emotion_loaded`, [1]);
  send(`${root}/state/prosody_active`, [1]);
}

function sendSpeechFrame(device, tick) {
  const t = tick / 10 + device.phase;
  const root = `/dev/${device.deviceId}`;
  const speech = Math.sin(t * 0.8) > -0.15 ? 1 : 0;
  const f0 = speech ? 26 + 7 * Math.sin(t * 0.9) + 2 * Math.sin(t * 2.7) : 0;
  const loudness = 0.55 + 0.35 * Math.max(0, Math.sin(t * 1.15));
  const jitter = 0.06 + 0.05 * Math.max(0, Math.sin(t * 1.9));
  const shimmer = 5 + 7 * Math.max(0, Math.sin(t * 1.4 + 1));
  const hnr = 5 + 6 * Math.max(0, Math.sin(t * 1.1 + 0.5));
  const values = [f0, loudness, jitter, shimmer, hnr];

  send(`${root}/speech/vad`, [speech]);
  FEATURES.forEach((feature, index) => send(`${root}/speech/${feature}`, [values[index]]));

  const moodIndex = Math.floor(t / 3) % EMOTIONS.length;
  const label = EMOTIONS[moodIndex];
  const scores = EMOTIONS.map((_, index) => {
    const distance = Math.abs(index - moodIndex);
    return Math.max(0.03, 0.88 - distance * 0.16 + 0.04 * Math.sin(t + index));
  });
  send(`${root}/speech/emo/label`, [label, scores[moodIndex]]);
  send(`${root}/speech/emo/scores`, scores);

  send(`${root}/stats/rate`, [`/dev/${device.deviceId}/speech/vad`, 10]);
  send(`${root}/stats/rate`, [`/dev/${device.deviceId}/speech/F0semitoneFrom27.5Hz_sma3nz`, 10]);
  send(`${root}/stats/rate`, [`/dev/${device.deviceId}/speech/emo/label`, 2]);
}

udp.on('ready', () => {
  console.log(`[demo] sending fake receiver traffic to ${HOST}:${PORT}`);
  let tick = 0;
  setInterval(() => {
    for (const device of DEVICES) {
      if (tick % 10 === 0) sendHello(device);
      if (tick % 20 === 0) sendState(device);
      sendSpeechFrame(device, tick);
    }
    tick += 1;
  }, TICK_MS);
});

udp.open();

process.on('SIGINT', () => {
  udp.close();
  process.exit(0);
});
