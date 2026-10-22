// Qualification only. Never print the credential-bearing WebSocket URL.
import { readFileSync, appendFileSync } from 'node:fs';
const [fixture, port, output] = process.argv.slice(2);
const data = JSON.parse(readFileSync(fixture, 'utf8'));
const socket = new WebSocket(`ws://127.0.0.1:${port}/api/v1/socket/websocket?vsn=2.0.0&api_key=${encodeURIComponent(data.observer_key)}`);
const emit = value => appendFileSync(output, JSON.stringify(value) + '\n', { mode: 0o600 });
socket.onopen = () => socket.send(JSON.stringify(['1', '2', `chat_room:${data.room}`, 'phx_join', {}]));
socket.onmessage = event => {
  const frame = JSON.parse(event.data);
  if (frame[3] === 'phx_reply' && frame[1] === '2') emit({event: 'joined', status: frame[4].status});
  if (frame[3] === 'event_created') emit({event: 'event_created', at_us: Date.now() * 1000, payload: frame[4]});
};
socket.onerror = () => { emit({event: 'socket_error'}); process.exitCode = 1; };
socket.onclose = () => { emit({event: 'closed'}); process.exit(); };
const timer = setInterval(() => { if (socket.readyState === 1) socket.send(JSON.stringify([null, 'heartbeat', 'phoenix', 'heartbeat', {}])); }, 10000);
process.on('SIGTERM', () => { clearInterval(timer); socket.close(); setTimeout(() => process.exit(), 2000).unref(); });
