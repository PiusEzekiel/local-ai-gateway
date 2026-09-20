// Module 9B.4 — execute the production logout function with explicit spies.
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const script = readFileSync(new URL('../gateway/dashboard/dashboard.js', import.meta.url), 'utf8');
const sessionFn = script.match(/let sessionClosing = false;[\s\S]*?\n\}(?=\n\nasync function refresh)/)?.[0];
assert.ok(sessionFn, 'extract the actual disconnectSession implementation');

function harness({dirty = false, confirm = true} = {}) {
  const calls = [];
  const data = new Map([['gatewayToken', 'not-a-real-credential']]);
  const elements = Object.fromEntries(['token','appShell','authScreen','authError'].map(key => [key, {value:'not-a-real-credential',hidden:key==='authScreen',textContent:''}]));
  const ctx = {
    sessionStorage: {getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)},
    window: {confirm:() => {calls.push('confirm');return confirm;}, location:{reload:()=>calls.push('reload')}},
    settingsHasUnsavedChanges:()=>dirty,
    clearTimeout:id=>calls.push(`clear:${id}`), searchTimer:1, gallerySearchTimer:2, changeTimer:3,
    liveClient:{stop:()=>calls.push('stop')},syncMonitor:{hide:()=>calls.push('hide')},
    closeLightbox:()=>calls.push('close'),cancelGalleryList:()=>calls.push('cancel'),abortGalleryMedia:()=>calls.push('abort'),
    setToken:v => {calls.push(`token:${v}`);if(v)data.set('gatewayToken',v);else data.delete('gatewayToken');},
    $: id => elements[id], summaryGeneration:7,
  };
  vm.createContext(ctx);
  vm.runInContext(`${sessionFn}\nthis.disconnectSession=disconnectSession;`,ctx);
  return {ctx,calls,data,elements};
}

test('manual disconnect stops streaming, clears token, hides private view and reloads', () => {
  const {ctx,calls,data,elements}=harness();
  ctx.disconnectSession();
  assert.equal(data.has('gatewayToken'),false);
  assert.equal(elements.token.value,'');
  assert.equal(elements.appShell.hidden,true);
  assert.equal(elements.authScreen.hidden,false);
  assert.equal(ctx.summaryGeneration,8);
  assert.deepEqual(calls.filter(c=>['stop','hide','close','cancel','abort','token:','reload'].includes(c)),
    ['stop','hide','close','cancel','abort','token:','reload']);
});

test('disconnect with unsaved settings requires confirmation and allows cancel',()=>{
  const h=harness({dirty:true,confirm:false});
  h.ctx.disconnectSession();
  assert.deepEqual(h.calls,['confirm']);
  assert.ok(h.data.has('gatewayToken'));
  h.ctx.disconnectSession({expired:true});
  assert.equal(h.data.has('gatewayToken'),false);
  assert.equal(h.calls.filter(c=>c==='confirm').length,1);
});

test('expired bearer leaves only a fixed notice key and no token',()=>{
  const h=harness();h.ctx.disconnectSession({expired:true});
  assert.equal(h.data.get('gatewayAuthNotice'),'expired');
  assert.equal(h.data.has('gatewayToken'),false);
  assert.match(h.elements.authError.textContent,/no longer valid/);
  assert.deepEqual([...h.data.keys()],['gatewayAuthNotice']);
});

test('repeated 401 or double-click disconnect is idempotent',()=>{
  const h=harness();h.ctx.disconnectSession();h.ctx.disconnectSession({expired:true});
  assert.equal(h.calls.filter(c=>c==='reload').length,1);
  assert.equal(h.calls.filter(c=>c==='stop').length,1);
  assert.equal(h.data.has('gatewayAuthNotice'),false);
});

test('auth API notifies for JSON and blob 401 but not other errors',async()=>{
  const data = new Map([['gatewayToken','fake-value']]);
  globalThis.sessionStorage={getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};
  const api=await import('../gateway/dashboard/api.js?v=9b5-20260920');
  let expired=0;
  api.setUnauthorizedHandler(()=>expired++);
  const oldFetch=globalThis.fetch;
  try {
    globalThis.fetch=async()=>({ok:false,status:401,json:async()=>({error:{message:'Unauthorized'}})});
    await assert.rejects(api.request('/dashboard/api/summary'),{status:401});
    await assert.rejects(api.requestBlob('/dashboard/api/artifacts/opaque'),{status:401});
    assert.equal(expired,2);
    globalThis.fetch=async()=>({ok:false,status:503,json:async()=>({error:{message:'Unavailable'}})});
    await assert.rejects(api.request('/dashboard/api/summary'),{status:503});
    assert.equal(expired,2);
  }finally{globalThis.fetch=oldFetch;api.setUnauthorizedHandler(null);}
});
