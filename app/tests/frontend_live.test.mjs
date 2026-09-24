// Optional runtime verification:
// node --test tests/frontend_live.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';

// ============================================================
// MOCK BROWSER STORAGE
//
// Dashboard authentication now persists in localStorage.
//
// sessionStorage remains available for temporary UI state
// and cleanup of credentials from the previous implementation.
//
// Both mocks must exist BEFORE importing the production API.
// ============================================================

const localStore = new Map();
const sessionStore = new Map();

globalThis.localStorage = {
  getItem: key => localStore.get(key) ?? null,

  setItem: (key, value) => {
    localStore.set(key, String(value));
  },

  removeItem: key => {
    localStore.delete(key);
  },
};

globalThis.sessionStorage = {
  getItem: key => sessionStore.get(key) ?? null,

  setItem: (key, value) => {
    sessionStore.set(key, String(value));
  },

  removeItem: key => {
    sessionStore.delete(key);
  },
};

// ============================================================
// IMPORT PRODUCTION MODULES
// ============================================================

const {setToken, hasToken} = await import(
  '../gateway/dashboard/api.js?v=ui-refresh-phase-a-20260923'
);

const {createSSEParser, createLiveClient} = await import(
  '../gateway/dashboard/live.js?v=ui-refresh-phase-a-20260923'
);

// ============================================================
// TEST HELPERS
// ============================================================

const pause = ms =>
  new Promise(resolve => setTimeout(resolve, ms));

const until = async fn => {
  for (let i = 0; i < 100; i++) {
    if (fn()) return;

    await pause(5);
  }

  throw new Error('Timed out waiting for live client');
};

// ============================================================
// AUTHENTICATION PERSISTENCE
// ============================================================

test('dashboard token persists in localStorage and clears on disconnect', () => {
  // Simulate a credential left by the old sessionStorage implementation.
  sessionStore.set('gatewayToken', 'old-session-token');

  // A successful login must persist the new credential.
  setToken('local-test-value');

  assert.equal(hasToken(), true);
  assert.equal(localStore.get('gatewayToken'), 'local-test-value');

  // The old session-scoped credential must be removed.
  assert.equal(sessionStore.has('gatewayToken'), false);

  // Disconnect must remove the persistent credential.
  setToken('');

  assert.equal(hasToken(), false);
  assert.equal(localStore.has('gatewayToken'), false);
  assert.equal(sessionStore.has('gatewayToken'), false);
});

// ============================================================
// SSE PARSER TESTS
// ============================================================

test('parser splits frames and handles CRLF, comments, and data split over chunks', () => {
  const received = [];

  const parser = createSSEParser((name, payload) => {
    received.push([name, payload]);
  });

  parser.push(
    ': connected\r\nevent: ready\r\ndata: {"snapshot_'
  );

  parser.push(
    'required":true}\r\n\r\nevent: job.updated\ndata: {"job_id":"abc"}\n\n'
  );

  assert.deepEqual(received, [
    ['ready', {snapshot_required: true}],
    ['job.updated', {job_id: 'abc'}],
  ]);
});

test('parser ignores unknown events and malformed JSON', () => {
  const received = [];

  const parser = createSSEParser((...args) => {
    received.push(args);
  });

  parser.push(
    'event: malicious\ndata: {"hello":1}\n\nevent: ready\ndata: not-json\n\n'
  );

  assert.deepEqual(received, []);
});

test('parser enforces stream buffer bound', () => {
  const parser = createSSEParser(() => {});

  assert.throws(
    () => parser.push('a'.repeat(128 * 1024 + 1)),
    /size limit/
  );
});

// ============================================================
// LIVE CLIENT TESTS
// ============================================================

test('client delivers ready and metadata then aborts cleanly', async () => {
  setToken('local-test-value');

  const status = [];
  const seen = [];

  const fake = async signal => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: ready\ndata: {"snapshot_required":true}\n\n' +
            'event: workers.changed\ndata: {"pending":1}\n\n'
          )
        );

        signal.addEventListener(
          'abort',
          () => {
            try {
              controller.close();
            } catch {
              // The stream may already be closed.
            }
          },
          {once: true}
        );
      },
    });

    return {
      status: 200,
      ok: true,
      headers: new Headers({
        'Content-Type': 'text/event-stream',
      }),
      body: stream,
    };
  };

  const client = createLiveClient({
    onEvent: (...args) => seen.push(args),
    onStatus: value => status.push(value),
    fetchStream: fake,
  });

  client.start();

  await until(() => seen.length === 2);

  assert.deepEqual(
    seen.map(item => item[0]),
    ['ready', 'workers.changed']
  );

  assert.equal(client.status, 'live');

  client.stop();

  assert.equal(client.status, 'stopped');
  assert.ok(status.includes('connecting'));
});

test('401 halts retry and invokes authentication callback once', async () => {
  setToken('local-test-value');

  let calls = 0;
  let unauthorized = 0;

  const client = createLiveClient({
    onUnauthorized: () => {
      unauthorized++;
    },

    fetchStream: async () => {
      calls++;

      return {
        status: 401,
      };
    },

    initialDelay: 2,
  });

  client.start();

  await until(() => unauthorized > 0);

  await pause(20);

  assert.equal(calls, 1);
  assert.equal(client.status, 'unauthorized');

  client.stop();
});

test('503 reconnects with snapshot-required ready and stop clears delay', async () => {
  setToken('local-test-value');

  let calls = 0;
  let ready = 0;

  const client = createLiveClient({
    onEvent: event => {
      if (event === 'ready') ready++;
    },

    initialDelay: 2,
    maximumDelay: 4,

    fetchStream: async signal => {
      if (++calls === 1) {
        return {
          status: 503,
          ok: false,
        };
      }

      return {
        status: 200,
        ok: true,

        headers: new Headers({
          'Content-Type': 'text/event-stream',
        }),

        body: new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                'event: ready\ndata: {"snapshot_required":true}\n\n'
              )
            );

            signal.addEventListener(
              'abort',
              () => {
                try {
                  controller.close();
                } catch {
                  // The stream may already be closed.
                }
              },
              {once: true}
            );
          },
        }),
      };
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

  const first = new Promise(resolve => {
    oldResolve = resolve;
  });

  const client = createLiveClient({
    initialDelay: 2,

    fetchStream: async () => {
      calls++;

      if (calls === 1) {
        return first;
      }

      return {
        status: 503,
        ok: false,
      };
    },
  });

  client.start();
  client.stop();
  client.start();

  oldResolve({
    status: 503,
    ok: false,
  });

  await until(() => calls >= 2);

  client.stop();

  await pause(10);

  assert.equal(calls, 2);
});