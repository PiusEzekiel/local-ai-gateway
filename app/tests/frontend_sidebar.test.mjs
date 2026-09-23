// Execute the production sidebar toggle (no duplicate logic in this test).
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const code = readFileSync(new URL('../gateway/dashboard/dashboard.js', import.meta.url), 'utf8').replace(/\r\n?/g, '\n');
const fn = code.match(/const SIDEBAR_PREF = "gatewaySidebarCollapsed";[\s\S]*?\n\}(?=\n\/\/ The transport badge)/)?.[0];
assert.ok(fn, 'find actual setSidebarCollapsed implementation');
function fixture(saved = null) {
  const map = new Map(saved === null ? [] : [['gatewaySidebarCollapsed',saved]]);
  const shell = {names:new Set(), classList:{toggle(k,v) {if(v) shell.names.add(k); else shell.names.delete(k);},contains:k=>shell.names.has(k)}};
  const attrs = new Map();
  const toggle = {setAttribute:(k,v)=>attrs.set(k,v),title:''};
  const context = {sessionStorage:{getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v)}, $: id => id==='appShell' ? shell:toggle};
  vm.createContext(context);
  vm.runInContext(`${fn}\nthis.toggle=setSidebarCollapsed;`,context);
  return {context,shell,attrs,toggle,map};
}
test('collapsed sidebar updates label, aria state, and tab-only preference',()=>{
  const f=fixture();f.context.toggle(true);
  assert.equal(f.shell.names.has('sidebar-collapsed'),true);
  assert.equal(f.attrs.get('aria-expanded'),'false');
  assert.equal(f.attrs.get('aria-label'),'Expand sidebar');
  assert.equal(f.map.get('gatewaySidebarCollapsed'),'1');
  f.context.toggle(false);
  assert.equal(f.shell.names.has('sidebar-collapsed'),false);
  assert.equal(f.attrs.get('aria-expanded'),'true');
  assert.equal(f.toggle.title,'Collapse sidebar');
});
test('restored preference does not require a gateway token',()=>{
  const f=fixture('1');f.context.toggle(f.map.get('gatewaySidebarCollapsed')==='1');
  assert.equal(f.shell.names.has('sidebar-collapsed'),true);
});
test('repeated collapse and expansion are idempotent',()=>{
  const f=fixture();f.context.toggle(true);f.context.toggle(true);
  assert.equal(f.shell.names.has('sidebar-collapsed'),true);
  f.context.toggle(false);f.context.toggle(false);
  assert.equal(f.shell.names.has('sidebar-collapsed'),false);
});
