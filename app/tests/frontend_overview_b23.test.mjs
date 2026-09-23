import {test} from 'node:test';
import assert from 'node:assert/strict';
import {
  QUOTA_DETAILS_PREF, remainingQuotaLabel, kindPresentation,
  readQuotaExpanded, writeQuotaExpanded,
} from '../gateway/dashboard/overview-b23.mjs';

test('quota header uses remaining capacity percentage, not a token count', () => {
  assert.equal(remainingQuotaLabel('73% left · Resets Fri, Sep 25'), '73% left');
  assert.equal(remainingQuotaLabel('0% left'), '0% left');
  assert.equal(remainingQuotaLabel('100% left'), '100% left');
  assert.equal(remainingQuotaLabel('Unavailable'), '—');
  assert.equal(remainingQuotaLabel('350% left'), '—');
  assert.equal(remainingQuotaLabel('') , '—');
});

test('quota preference is closed by default and survives component remounts', () => {
  const entries = new Map();
  const storage = {getItem: key => entries.get(key) ?? null, setItem:(key,value)=>entries.set(key,value)};
  assert.equal(readQuotaExpanded(storage), false);
  writeQuotaExpanded(storage, true);
  assert.equal(entries.get(QUOTA_DETAILS_PREF), '1');
  assert.equal(readQuotaExpanded(storage), true);
  writeQuotaExpanded(storage, false);
  assert.equal(readQuotaExpanded(storage), false);
});

test('unavailable browser storage never disables quota toggle', () => {
  const blocked = {getItem() {throw new Error('blocked');}, setItem() {throw new Error('blocked');}};
  assert.equal(readQuotaExpanded(blocked), false);
  assert.doesNotThrow(() => writeQuotaExpanded(blocked,true));
});

test('image/text/research badges map exact gateway task values', () => {
  assert.deepEqual(
    kindPresentation('IMAGE'),
    {label:'Image',className:'kind-image'}
  );

  assert.deepEqual(
    kindPresentation('GENERATE'),
    {label:'Text',className:'kind-text'}
  );

  assert.deepEqual(
    kindPresentation('RESEARCH'),
    {label:'Research',className:'kind-research'}
  );
});

// Exercise the real controller with a minimal DOM so an on-disk CSS-only fix
// cannot accidentally satisfy tests while the toggle or live values break.
test('quota toggle, persistence, and live header mirror work without API requests', async () => {
  const {initializeOverviewPolish} = await import('../gateway/dashboard/overview-b23.mjs');
  const map = new Map();
  const storage = {getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v)};
  const observers = [];
  const previousObserver = globalThis.MutationObserver;
  globalThis.MutationObserver = class {
    constructor(callback) {this.callback=callback;observers.push(this);}
    observe(target,opts) {this.target=target;this.opts=opts;}
  };
  try {
    const attrs = new Map();
    const toggle = {firstChild:{textContent:'Show details '},setAttribute:(k,v)=>attrs.set(k,v),
      addEventListener:(_event,fn)=>toggle.click=fn};
    const buckets = {hidden:true};
    const source = {dataset:{quotaLeft:'73% left'},textContent:'73% left · Resets Friday'};
    const mini = {textContent:'',title:''};
    const brief = {textContent:''};
    const feeds = {recentJobs:{querySelectorAll:()=>[]},allJobs:{querySelectorAll:()=>[]}};
    const els = {quotaToggle:toggle,quotaBuckets:buckets,topQuota:source,quotaMiniValue:mini,
      quotaBrief:brief,...feeds};
    const doc = {getElementById:key=>els[key]??null};
    initializeOverviewPolish(doc,storage);
    assert.equal(buckets.hidden,true);
    assert.equal(attrs.get('aria-expanded'),'false');
    assert.equal(mini.textContent,'73% left');
    assert.match(mini.title,/not remaining token count/);
    toggle.click();
    assert.equal(buckets.hidden,false);
    assert.equal(map.get(QUOTA_DETAILS_PREF),'1');
    assert.equal(attrs.get('aria-expanded'),'true');
    source.dataset.quotaLeft='40% left';source.textContent='40% left · Resets tomorrow';
    observers.find(o=>o.target===source).callback();
    assert.equal(mini.textContent,'40% left');
    assert.match(brief.textContent,/40% left/);
    toggle.click();
    assert.equal(buckets.hidden,true);
    assert.equal(map.get(QUOTA_DETAILS_PREF),'0');
  } finally {globalThis.MutationObserver=previousObserver;}
});

test('both real feed targets receive vector icons exactly once per rendered row', async () => {
  const {initializeOverviewPolish} = await import('../gateway/dashboard/overview-b23.mjs');
  const oldObserver = globalThis.MutationObserver;
  const observers = [];
  globalThis.MutationObserver = class {
    constructor(cb){this.cb=cb;observers.push(this);}
    observe(target,opts){this.target=target;this.opts=opts;}
  };
  const mkRow = (task) => {
    const dot = {nodeType:1,nextSibling:{nodeType:3}};
    const icons = [];
    const textNode = {nodeType:3,textContent:task};

    const kind = {
      textContent:task,
      firstChild:dot,
      childNodes:[dot,textNode],

      querySelector:sel=>sel==='.status-icon'?dot:null,

      insertBefore:(icon,at)=>{
        icons.push(icon);
        assert.equal(at,dot.nextSibling);
      }
    };
    const classes = [];
    return {
      dataset:{},
      classList:{add:c=>classes.push(c)},
      querySelector:()=>kind,
      icons,
      classes,
      labelNode:textNode
    };
  };
  const image = mkRow('IMAGE'), text = mkRow('GENERATE');
  const feedFor = row=>({querySelectorAll:()=>row.dataset.kindDecorated ? [] : [row]});
  const recentJobs=feedFor(image), allJobs=feedFor(text);
  const btn={firstChild:{textContent:''},setAttribute(){},addEventListener(){}};
  const els={quotaToggle:btn,quotaBuckets:{hidden:true},topQuota:{dataset:{},textContent:'Unavailable'},
    quotaMiniValue:{},quotaBrief:{},recentJobs,allJobs};
  const doc={getElementById:id=>els[id]??null,createElement:()=>({setAttribute(){}})};
  try {
    initializeOverviewPolish(doc,null);
    assert.equal(image.icons[0].className,'job-type-icon');
    assert.equal(text.icons[0].className,'job-type-icon');

    assert.equal(image.icons[0].textContent,'');
    assert.equal(text.icons[0].textContent,'');

    assert.equal(text.labelNode.textContent,'TEXT');
    assert.ok(image.classes.includes('kind-image'));
    assert.ok(text.classes.includes('kind-text'));
    observers.find(x=>x.target===recentJobs).cb();
    assert.equal(image.icons.length,1);
  } finally {globalThis.MutationObserver=oldObserver;}
});
