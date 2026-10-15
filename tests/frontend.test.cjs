'use strict';

// Behavioral regressions for the dependency-free frontend. Real rendering
// functions and HTTP polling run in a VM; DOM and network boundaries are stubbed.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function fixture(id = 'first', title = 'Original plan', cursor = 1) {
  return {
    run: {id, tenant: 'test', viewer_scopes: ['payments'], scopes: ['payments'],
      clock: '2026-05-01T00:00:00Z', status: 'running', cursor, stats: {}, offline: true},
    reviews: [{epic_id: `${id}-epic`, as_of: '2026-04-01T00:00:00Z', report: {
      epic: 'PAY-1', title, summary: 'A stored review.', warnings: [],
      forecast: {p50_days: 12.5, p90_days: 20, basis: []}, suppressed_by_ring_fence: 0,
    }}],
    items: {}, scores: {}, log: [], dataset: {sources: ['synthetic']},
  };
}

function harness() {
  const elements = new Map(), timers = new Map(), frames = [], requests = [];
  let timerId = 0, responder = async () => { throw new Error('Unexpected request'); };
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set(), listeners = new Map();
      elements.set(id, {
        id, innerHTML: '', textContent: '', value: '', dataset: {}, listeners,
        classList: {
          add: (...names) => names.forEach(name => classes.add(name)),
          remove: (...names) => names.forEach(name => classes.delete(name)),
          contains: name => classes.has(name),
          toggle: name => { if (classes.has(name)) { classes.delete(name); return false; } classes.add(name); return true; },
        },
        addEventListener: (name, callback) => listeners.set(name, callback),
        setAttribute() {},
        contains: node => node === elements.get(id) || node?.parentId === id,
        matches: () => false,
      });
    }
    return elements.get(id);
  }
  const document = {
    hidden: false, activeElement: null, documentElement: {dataset: {}},
    getElementById: element, addEventListener() {}, querySelectorAll: () => [],
  };
  const context = vm.createContext({
    document,
    window: {NABU_API: '', innerWidth: 1440, addEventListener() {}, scrollTo() {},
      matchMedia: () => ({matches: false, addEventListener() {}})},
    location: {search: '', hash: '#overview', href: 'http://localhost/'},
    history: {replaceState() {}},
    URL, URLSearchParams, structuredClone,
    AbortSignal: {timeout: () => ({})},
    fetch: async url => { requests.push(url); return {ok: true, json: async () => structuredClone(await responder(url))}; },
    setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
    clearTimeout: id => timers.delete(id),
    setInterval: () => ++timerId, clearInterval() {},
    requestAnimationFrame: callback => frames.push(callback),
  });
  const source = readFileSync(path.join(__dirname, '../frontend/app.js'), 'utf8');
  assert.match(source, /\ninit\(\);\s*$/);
  // Disable automatic boot so each test chooses its starting run. The bridge
  // controls initial state; assertions observe rendered HTML and HTTP requests.
  vm.runInContext(source.replace(/\ninit\(\);\s*$/, '') + `
    globalThis.app = {
      prepare(state, paused = false) {
        data = state; logs = [...state.log]; mode = 'live'; runId = state.run.id;
        runList = [{id: state.run.id}, {id: 'second'}]; generation = 1;
        refreshEnabled = !paused; render();
      },
      poll: () => poll(generation),
      renderOverview, reviewTable, renderReviews, renderAgents, renderOutcomes, renderEvidence, renderWorkspace,
      snapshotAt, metric,
      reviewDocument: () => reviewDocument(data.reviews[0]),
    };
  `, context, {filename: 'frontend/app.js'});
  return {
    app: context.app, document, elements, requests, timers, frames,
    respond: fn => { responder = fn; },
    main: () => element('main').innerHTML,
    blur() {
      document.activeElement = null;
      element('main').listeners.get('focusout')();
      while (frames.length) frames.shift()();
    },
  };
}

const drain = () => new Promise(resolve => setImmediate(resolve));

test('changing runs while refresh is paused still loads the selected run once', async () => {
  const h = harness();
  h.app.prepare(fixture(), true);
  h.respond(() => fixture('second', 'Second plan'));
  const select = h.elements.get('run-select');
  select.value = 'second';
  select.onchange();
  await drain();
  assert.equal(h.requests.length, 1);
  assert.match(h.requests[0], /api\/state\?run=second&after=0$/);
  assert.match(h.main(), /Second plan/);
  assert.doesNotMatch(h.main(), /Opening the review history/);
  assert.equal(h.timers.size, 0, 'paused refresh must not start a polling loop');
});

test('forecast HTML and malformed basis cannot create markup in any review view', () => {
  const h = harness(), state = fixture();
  const payload = '<img src=x onerror="globalThis.compromised=true">';
  state.reviews[0].report.forecast = {p50_days: payload, p90_days: payload, basis: {length: payload}};
  state.reviews[0].report.suppressed_by_ring_fence = payload;
  h.app.prepare(state);
  for (const html of [h.app.renderOverview(), h.app.reviewTable(), h.app.reviewDocument()]) {
    assert.doesNotMatch(html, /<img|onerror|compromised/);
    assert.match(html, /—/, 'invalid durations should have an explicit unavailable marker');
  }
  const valid = fixture();
  h.app.prepare(valid);
  assert.match(h.app.reviewDocument(), /12\.5/);
  assert.match(h.app.reviewDocument(), /20/);
});

test('a response received during typing renders after blur without another response', async () => {
  const h = harness();
  h.app.prepare(fixture());
  h.document.activeElement = {parentId: 'main', matches: selector => selector === 'input,select'};
  h.respond(() => fixture('first', 'Fresh final plan', 2));
  await h.app.poll();
  assert.match(h.main(), /Original plan/);
  assert.doesNotMatch(h.main(), /Fresh final plan/);
  h.blur();
  assert.match(h.main(), /Fresh final plan/);
  assert.equal(h.requests.length, 1, 'blur must flush the already received state');
});

test('concurrent polling calls issue one request and schedule one continuation', async () => {
  const h = harness();
  h.app.prepare(fixture());
  let release;
  h.respond(() => new Promise(resolve => { release = resolve; }));
  const first = h.app.poll();
  await h.app.poll();
  assert.equal(h.requests.length, 1);
  release(fixture('first', 'Fresh plan', 2));
  await first;
  assert.match(h.main(), /Fresh plan/);
  assert.equal(h.timers.size, 1);
  h.respond(() => fixture('first', 'Next plan', 3));
  const [id, timer] = [...h.timers][0];
  h.timers.delete(id);
  await timer.callback();
  assert.equal(h.requests.length, 2, 'the in-flight guard must release after completion');
  assert.equal(h.timers.size, 1);
  assert.match(h.main(), /Next plan/);
});

function progressFixture() {
  const s=fixture();
  const m=(done,total)=>({done,total,percent:total?done/total*100:null});
  const progress=(epics,tickets,steps)=>({epics:m(epics,10),tickets:m(tickets,50),steps:m(steps,20),by_scope:[{scope:'payments',epics:m(epics,10),tickets:m(tickets,50),steps:m(steps,20)}]});
  s.inventory={work_items:60,epics:10,tickets:50,linked_tickets:47,unlinked_tickets:3,source:'run_start',by_scope:[{scope:'payments',work_items:60,epics:10,tickets:50}]};
  s.progress=progress(10,32,20);
  s.checkpoints=[{at:'2026-04-01T00:00:00Z',progress:progress(1,3,1)},{at:'2026-05-01T00:00:00Z',progress:s.progress}];
  s.run.status='finished';
  return s;
}

test('every dashboard view exposes completed and total epic and ticket coverage',()=>{
  const h=harness();h.app.prepare(progressFixture());
  for(const render of ['renderOverview','renderReviews','renderAgents','renderOutcomes','renderEvidence','renderWorkspace']){
    const html=h.app[render]();
    assert.match(html,/Epics reviewed/);
    assert.match(html,/Tickets covered/);
    assert.match(html,/64%/);
    assert.match(html,/100%/);
    assert.match(html,/aria-label="[^"]*10 \/ 10/);
    assert.match(html,/aria-label="[^"]*32 \/ 50/);
  }
  assert.match(h.app.renderOverview(),/18 tickets were not included/);
  assert.match(h.elements.get('coverage-status').textContent,/10 \/ 10 epics reviewed/);
});

test('recorded playback starts at zero and restores committed progress without changing totals',()=>{
  const h=harness(), snapshot=progressFixture();
  const before=h.app.snapshotAt(snapshot,null);
  assert.equal(before.progress.epics.done,0);
  assert.equal(before.progress.epics.total,10);
  assert.equal(before.progress.tickets.done,0);
  assert.equal(before.reviews.length,0);
  const partial=h.app.snapshotAt(snapshot,'2026-04-15T00:00:00Z');
  assert.equal(partial.progress.epics.done,1);
  assert.equal(partial.progress.tickets.done,3);
  assert.equal(partial.progress.tickets.total,50);
  assert.equal(partial.run.cursor,1);
  assert.equal(partial.inventory.work_items,60);
  assert.equal(partial.reviews.length,1);
  const complete=h.app.snapshotAt(snapshot,snapshot.run.clock);
  assert.equal(complete.progress.epics.done,10);
  assert.equal(complete.progress.tickets.done,32,'finishing the replay must not imply all tickets were covered');
  assert.equal(snapshot.progress.epics.done,10,'playback must not mutate the recording');
});

test('legacy exports and empty datasets never report invented completion',()=>{
  const h=harness();h.app.prepare(fixture());
  assert.match(h.app.renderOverview(),/Dataset coverage unavailable/);
  assert.match(h.app.renderReviews(),/1 \/ —/);
  assert.doesNotMatch(h.app.renderOverview(),/NaN|Infinity|100%/);
  const s=progressFixture();
  s.inventory={...s.inventory,work_items:0,epics:0,tickets:0,by_scope:[]};
  s.progress={epics:{done:0,total:0},tickets:{done:0,total:0},steps:{done:0,total:0},by_scope:[]};
  h.app.prepare(s);
  assert.match(h.main(),/No items/);
  assert.doesNotMatch(h.main(),/NaN|Infinity/);
  assert.equal(h.app.metric({done:9999,total:10000}).percent,99.9,'incomplete coverage cannot round to 100%');
});
