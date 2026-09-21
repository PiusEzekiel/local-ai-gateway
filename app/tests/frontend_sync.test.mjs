// Optional: node --experimental-default-type=module --test tests/frontend_sync.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

const listeners = {};
globalThis.document = {
  addEventListener(type, handler) { (listeners[type] ??= []).push(handler); },
};
const {ageLabel, createSyncStatus} = await import('../gateway/dashboard/sync_status.js?v=9c3-20260920');
function element() {
  const attrs = new Map();
  const handlers = {};
  return {
    textContent: '', className: '', hidden: true, title: '', attrs, handlers, focused: false,
    addEventListener(type, handler) { handlers[type] = handler; },
    setAttribute(k,v) { attrs.set(k,v); },
    contains(target) { return target === this; },
    focus() {this.focused = true;},
  };
}
function fixture() {
  const e = Object.fromEntries(['badge','toggle','panel','description','snapshot','event',
    'transport','close','refresh'].map(key => [key,element()]));
  let now = 1000000;
  let refreshCount = 0;
  const monitor = createSyncStatus({...e,now:()=>now,onRefresh(){refreshCount++;}});
  return {e, monitor, advance:ms=>{now+=ms;monitor.tick();}, refreshCount:()=>refreshCount};
}

test('relative sync time is bounded and human-readable',()=>{
  assert.equal(ageLabel(null), 'Not yet');
  assert.equal(ageLabel(1000,1000),'Just now');
  assert.equal(ageLabel(1000,7000),'6s ago');
  assert.equal(ageLabel(1000,121000),'2m ago');
  assert.equal(ageLabel(1000,3601000),'1h ago');
});

test('live transport does not invent a successful API snapshot',()=>{
  const {e,monitor}=fixture();
  monitor.setTransport('live');
  assert.equal(e.badge.textContent,'Live');
  assert.equal(e.snapshot.textContent,'Waiting for data');
  monitor.recordEvent('job.updated');
  assert.match(e.event.textContent,/Job updated/);
  assert.equal(e.snapshot.textContent,'Waiting for data');
});

test('successful snapshot ages and warns when live data is stale',()=>{
  const {e,monitor,advance}=fixture();
  monitor.setTransport('live');
  monitor.recordSnapshot();
  assert.equal(e.snapshot.textContent,'Just now');
  advance(120001);
  assert.equal(e.badge.textContent,'Live · stale data');
  assert.match(e.description.textContent,/stale/);
  monitor.recordSnapshot();
  assert.equal(e.badge.textContent,'Live');
});

test('last good data stays visible when gateway API loses connectivity',()=>{
  const {e,monitor}=fixture();
  monitor.setTransport('live');
  monitor.recordSnapshot();
  monitor.setGateway(false);
  assert.match(e.description.textContent,/last gateway API request failed/);
  assert.match(e.transport.textContent,/API unavailable/);
});

test('details toggle, outside click and Escape never trigger destructive actions',()=>{
  const {e,monitor}=fixture();
  e.toggle.handlers.click();
  assert.equal(e.panel.hidden,false);
  assert.equal(e.toggle.attrs.get('aria-expanded'),'true');
  listeners.pointerdown.at(-1)({target:element()});
  assert.equal(e.panel.hidden,true);
  e.toggle.handlers.click();
  listeners.keydown.at(-1)({key:'Escape'});
  assert.equal(e.panel.hidden,true);
  assert.equal(e.toggle.focused,true);
  monitor.hide();
});

test('refresh action is explicit and does not run when opening the panel',()=>{
  const {e,refreshCount}=fixture();
  e.toggle.handlers.click();
  assert.equal(refreshCount(),0);
  e.refresh.handlers.click();
  assert.equal(refreshCount(),1);
  assert.equal(e.panel.hidden,true);
});
