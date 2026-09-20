// Optional runtime verification: node --experimental-default-type=module --test tests/frontend_live.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

const store = new Map();
globalThis.sessionStorage = {
  getItem: k => store.get(k) || null,
  setItem: (k, v) => store.set(k, v),
  removeItem: k => store.delete(k),
};
const {setToken} = await import('../gateway/dashboard/api.js?v=9c2-20260920');
const {createSSEParser, createLiveClient} = await import('../gateway/dashboard/live.js?v=9c2-20260920');

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async fn => {
  for (let i = 0; i < 100; i++) { if (fn()) return; await pause(5); }
  throw new Error('Timed out waiting for live client');
};

test('parser splits frames and handles CRLF, comments, and data split over chunks', () => {
  const received = [];
  const parser = createSSEParser((name, payload) => received.push([name, payload]));
  parser.push(': connected\r\nevent: ready\r\ndata: {"snapshot_');
  parser.push('required":true}\r\n\r\nevent: job.updated\ndata: {"job_id":"abc"}\n\n');
  assert.deepEqual(received, [
    ['ready', {snapshot_required: true}], ['job.updated', {job_id: 'abc'}],
  ]);
});

test('parser ignores unknown events and malformed JSON', () => {
  const received = [];
  const parser = createSSEParser((...args) => received.push(args));
  parser.push('event: malicious\ndata: {"hello":1}\n\nevent: ready\ndata: not-json\n\n');
  assert.deepEqual(received, []);
});

test('parser enforces stream buffer bound', () => {
  const parser = createSSEParser(() => {});
  assert.throws(() => parser.push('a'.repeat(128 * 1024 + 1)), /size limit/);
});

test('client delivers ready and metadata then aborts cleanly', async () => {
  setToken('local-test-value');
  const status = [], seen = [];
  let controller;
  const fake = async signal => {
    const stream = new ReadableStream({
      start(c) {
        controller = c;
        c.enqueue(new TextEncoder().encode('event: ready\ndata: {"snapshot_required":true}\n\nevent: workers.changed\ndata: {"pending":1}\n\n'));
        signal.addEventListener('abort', () => { try { c.close(); } catch {} }, {once: true});
      },
    });
    return {status: 200, ok: true, headers: new Headers({'Content-Type': 'text/event-stream'}), body: stream};
  };
  const client = createLiveClient({onEvent: (...x) => seen.push(x), onStatus: s => status.push(s), fetchStream: fake});
  client.start();
  await until(() => seen.length === 2);
  assert.deepEqual(seen.map(x => x[0]), ['ready', 'workers.changed']);
  assert.equal(client.status, 'live');
  client.stop();
  assert.equal(client.status, 'stopped');
  assert.ok(status.includes('connecting'));
});

test('401 halts retry and invokes authentication callback once', async () => {
  setToken('local-test-value');
  let calls = 0, unauthorized = 0;
  const client = createLiveClient({onUnauthorized: () => unauthorized++, fetchStream: async () => {
    calls++; return {status:401};
  }, initialDelay: 2});
  client.start();
  await until(() => unauthorized > 0);
  await pause(20);
  assert.equal(calls, 1);
  assert.equal(client.status, 'unauthorized');
  client.stop();
});

test('503 reconnects with snapshot-required ready and stop clears delay', async () => {
  setToken('local-test-value');
  let calls = 0, ready = 0;
  const client = createLiveClient({onEvent: e => {if (e === 'ready') ready++;}, initialDelay: 2, maximumDelay: 4,
    fetchStream: async signal => {
      if (++calls === 1) return {status:503, ok:false};
      return {status:200, ok:true, headers:new Headers({'Content-Type':'text/event-stream'}),
        body:new ReadableStream({start(c){
          c.enqueue(new TextEncoder().encode('event: ready\ndata: {"snapshot_required":true}\n\n'));
          signal.addEventListener('abort',()=>{try{c.close();}catch{}},{once:true});
        }})};
    },
  });
  client.start();
  await until(() => ready === 1);
  assert.equal(calls, 2);
  client.stop();
  await pause(10);
  assert.equal(calls, 2);
});

test('rapid stop/start invalidates stale connection without duplicate fetch loops', async () => {
  setToken('local-test-value');
  let calls = 0;
  let oldResolve;
  const first = new Promise(resolve => { oldResolve = resolve; });
  const client = createLiveClient({initialDelay: 2, fetchStream: async signal => {
    calls++;
    if (calls === 1) return first;
    return {status: 503, ok: false};
  }});
  client.start();
  client.stop();
  client.start();
  oldResolve({status: 503, ok: false});
  await until(() => calls >= 2);
  client.stop();
  await pause(10);
  assert.equal(calls, 2);
});
