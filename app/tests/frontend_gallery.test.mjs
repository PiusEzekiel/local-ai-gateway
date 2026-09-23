// Module 9B.3 — intentionally adversarial async-order tests without a browser or API.
// Evaluates the exact functions in dashboard.js against controllable mock promises.
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const code = readFileSync(new URL('../gateway/dashboard/dashboard.js', import.meta.url), 'utf8').replace(/\r\n?/g, '\n');
const cancelFn = code.match(/function cancelGalleryList\(\) \{[\s\S]*?\n\}/)?.[0];
const loadFn = code.match(/async function loadGallery\(reset = false\) \{[\s\S]*?\n\}(?=\n\n\/\/ The SSE stream)/)?.[0];
assert.ok(cancelFn && loadFn, 'extract real Gallery request handlers');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
function harness() {
  const calls = [], paints = [];
  const nodes = {galleryLoadMore: {disabled:false, hidden:false}, galleryGrid: {textContent:''},
    gallerySearch:{value:''}, galleryCount:{textContent:''}};
  const ctx = {
    AbortController,
    state:{page:'gallery',galleryCategory:'all',galleryCursor:'',galleryItems:[]},
    hasToken:()=>true,
    $: id=> nodes[id],
    update: values=> Object.assign(ctx.state,values),
    getGallery: (query,options)=> {const item=deferred(); calls.push({...item,query,signal:options.signal}); return item.promise;},
    renderGallery: (_target, items)=>paints.push(items.map(i=>i.request_id)),
  };
  vm.createContext(ctx);
  vm.runInContext(`let galleryListController=null; let galleryRequestGeneration=0; let galleryLoadMoreInFlight=false;\n${cancelFn}\n${loadFn}\nthis.loadGallery=loadGallery;this.cancelGalleryList=cancelGalleryList;`,ctx);
  return {ctx,calls,paints,nodes};
}
const item = request_id=>({request_id});
const flush = ()=>new Promise(resolve=>setImmediate(resolve));

test('slower old search cannot overwrite newer filtered Gallery', async()=>{
  const h=harness();
  const old=h.ctx.loadGallery(true);
  h.nodes.gallerySearch.value='woman';
  const newer=h.ctx.loadGallery(true);
  assert.equal(h.calls.length,2);
  assert.equal(h.calls[0].signal.aborted,true);
  h.calls[1].resolve({items:[item('new')],next_cursor:''});await newer;
  h.calls[0].resolve({items:[item('old')],next_cursor:''});await old;
  assert.deepEqual(h.paints,[['new']]);
  assert.equal(h.ctx.state.galleryItems[0].request_id,'new');
});

test('repeated Load more click cannot append the same cursor page twice', async()=>{
  const h=harness();h.ctx.state.galleryCursor='cursor1';h.ctx.state.galleryItems=[item('first')];
  const first=h.ctx.loadGallery(false);
  const ignored=h.ctx.loadGallery(false);
  assert.equal(h.calls.length,1);
  assert.equal(h.nodes.galleryLoadMore.disabled,true);
  h.calls[0].resolve({items:[item('second')],next_cursor:'cursor2'});
  await Promise.all([first, ignored]);
  assert.equal(JSON.stringify(h.paints),JSON.stringify([['first','second']]));
  assert.equal(h.nodes.galleryLoadMore.disabled,false);
});

test('navigation away cancels list and ignores late response', async()=>{
  const h=harness();const request=h.ctx.loadGallery(true);
  h.ctx.state.page='jobs';h.ctx.cancelGalleryList();
  assert.equal(h.calls[0].signal.aborted,true);
  h.calls[0].resolve({items:[item('late')],next_cursor:''});await request;
  assert.equal(h.paints.length,0);
  assert.equal(h.ctx.state.galleryItems.length,0);
});

test('aborted error does not replace Gallery with an error message', async()=>{
  const h=harness();const old=h.ctx.loadGallery(true);
  h.ctx.cancelGalleryList();
  h.calls[0].reject(new Error('Request aborted'));await old;
  assert.equal(h.nodes.galleryGrid.textContent,'');
});

test('current fetch error is displayed and load-more control is restored', async()=>{
  const h=harness();const work=h.ctx.loadGallery(true);
  h.calls[0].reject(new Error('Gateway temporarily offline'));await work;
  assert.equal(h.nodes.galleryGrid.textContent,'Gateway temporarily offline');
  assert.equal(h.nodes.galleryLoadMore.disabled,false);
});
