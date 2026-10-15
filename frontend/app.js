'use strict';

// The UI uses Nabu's stored reports verbatim. It never invents model output or evidence.
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ICONS = {
  overview:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  reviews:'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>',
  agents:'<circle cx="12" cy="5" r="2"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="18" r="2"/><path d="M12 7v5M5 16v-4h14v4"/>',
  outcomes:'<path d="M4 4v16h17M8 15l4-5 4 2 5-7"/>',
  evidence:'<path d="M4 6h6l2 2h8v12H4zM4 6V4h7l2 2h7v2"/>',
  workspace:'<path d="M3 21h18M6 21V5h12v16M9 8h1m4 0h1M9 12h1m4 0h1M10 21v-5h4v5"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6z"/><path d="m8 12 3 3 5-6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  historian:'<path d="M4 5h7v15H4zM13 5h7v15h-7M7 9h1m8 0h1"/>',
  estimator:'<path d="M4 20h16M7 20V11h3v9m4 0V4h3v16"/>',
  dependency_hunter:'<rect x="3" y="8" width="6" height="8" rx="2"/><rect x="15" y="8" width="6" height="8" rx="2"/><path d="M9 12h6"/>',
  gap_finder:'<circle cx="12" cy="12" r="8"/><path d="M12 8v8M8 12h8"/>',
  critic:'<path d="m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6z"/><path d="m8 12 3 3 5-6"/>',
  writer:'<path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15z"/>',
  scorekeeper:'<path d="m4 13 5 5L20 5M16 18h5"/>',
  replay:'<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/>',
  planner:'<path d="M9 5h11M9 12h11M9 19h11M3 5h1M3 12h1M3 19h1"/>',
  data_quality:'<path d="M8 3h8v5l4 11a1 1 0 0 1-1 2H5a1 1 0 0 1-1-2L8 8zM8 3h8M7 14h10"/>',
  close:'<path d="m6 6 12 12M18 6 6 18"/>'
};
const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.agents}</svg>`;
const AGENTS = {historian:'Historian',planner:'Planner',estimator:'Estimator',dependency_hunter:'Dependency hunter',gap_finder:'Gap finder',data_quality:'Data quality',critic:'Critic',writer:'Writer',scorekeeper:'Scorekeeper',replay:'Replay clock'};
const NAV = [['overview','Overview'],['reviews','Reviews'],['agents','Agent activity'],['outcomes','Outcomes'],['evidence','Evidence']];
const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : '—';
const date = (iso, year = false) => { if (!iso) return 'Not available'; const d = new Date(iso); return Number.isNaN(d.getTime()) ? 'Not available' : new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',...(year?{year:'numeric'}:{}),timeZone:'UTC'}).format(d); };
const params = new URLSearchParams(location.search);
const API = (params.get('api') || window.NABU_API || '').replace(/\/$/, '');
let data = null, fullSnapshot = null, logs = [], runList = [], runId = params.get('run'), mode = 'connecting';
let currentView = 'overview', selectedReview = null, after = 0, pollTimer, playbackTimer, generation = 0, refreshEnabled = true;
let tableScope = '', tableQuery = '', reviewQuery = '', evidenceQuery = '', agentFilter = '', logQuery = '', toastTimer;
let snapshotFailure = false, initialApiError = '', lastUpdated = null, pendingRender = false, runMenuKey = '', pollingToken = null;

function totals() {
  const warnings = data?.reviews.flatMap(r => r.report.warnings || []) || [];
  const ids = new Set(warnings.map(w => w.id));
  const scores = Object.values(data?.scores || {}).filter(s => ids.has(s.warning_id));
  const hits = scores.filter(s => s.hit === 1 || s.hit === true).length;
  return {warnings,scores,hits,rate:scores.length ? Math.round(hits / scores.length * 100) : null,
    withheld:data?.reviews.reduce((sum,r) => sum + (r.report.suppressed_by_ring_fence || 0), 0) || 0};
}
function scopeFor(review) { return data.items[review.epic_id]?.scope || (data.run.viewer_scopes.length === 1 ? data.run.viewer_scopes[0] : ''); }
function newestReviews() { return [...data.reviews].sort((a,b) => b.as_of.localeCompare(a.as_of)); }
function selected() { return data.reviews.find(r => r.epic_id === selectedReview) || newestReviews()[0]; }
function badge(text, tone = '') { return `<span class="pill ${tone}">${esc(text)}</span>`; }
function searchInput(id,value,placeholder) { return `<div class="search-wrap">${icon('search')}<input id="${id}" class="search" type="search" value="${esc(value)}" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}"></div>`; }
function pageHead(title,subtitle,action = '') { return `<div class="page-head"><div><h1>${title}</h1><p>${subtitle}</p></div>${action ? `<div class="page-actions">${action}</div>` : ''}</div>`; }
function button(label,action,primary=false,ic='arrow') { return `<button class="button ${primary?'primary':''}" data-action="${action}">${esc(label)}${icon(ic)}</button>`; }
function empty(title,body,action='') { return `<div class="empty-state">${icon('reviews')}<h2>${title}</h2><p>${body}</p>${action}</div>`; }
function safeScopeLabel(scope) { return scope ? scope.charAt(0).toUpperCase() + scope.slice(1) : 'Workspace'; }
function isSynthetic() { return (data?.inventory?.sources || data?.dataset?.sources)?.includes('synthetic') || ['demo','company-demo'].includes(data?.run.id) && data?.run.tenant === 'demo'; }

function renderNavigation() {
  $('navigation').innerHTML = NAV.map(([id,label]) => `<a href="#${id}" class="nav-item ${currentView===id?'active':''}" ${currentView===id?'aria-current="page"':''}>${icon(id)}<span>${label}</span>${id==='reviews'&&data?`<span class="count">${data.reviews.length}</span>`:''}</a>`).join('');
  $('breadcrumb').textContent = [...NAV,['workspace','Workspace details']].find(([id])=>id===currentView)?.[1] || 'Overview';
}
function updateChrome() {
  $('connection').textContent = mode === 'live' ? (refreshEnabled ? 'API connected' : 'Refresh paused') : 'Recorded replay';
  $('connection').classList.remove('error');
  const menuKey = JSON.stringify([runId,runList]);
  if (runMenuKey !== menuKey) { runMenuKey=menuKey; $('run-select').innerHTML = runList.map(r=>`<option value="${esc(r.id)}" ${r.id===runId?'selected':''}>${esc(r.id)}</option>`).join(''); }
  const banner = $('data-banner');
  banner.hidden = false;
  banner.innerHTML = `${icon('shield')}<strong>${isSynthetic()?'Synthetic demo data':mode==='snapshot'?'Recorded run':'Connected workspace'}</strong><span class="dot">·</span><span>${mode==='snapshot'?'Saved replay; no agents are running in this browser.':data.run.offline===true?'Deterministic agents; no model calls.':'Read-only reviews from your Nabu server.'}</span>${snapshotFailure?'<span>API unavailable. Showing the saved demo.</span>':''}`;
  $('footer-context').textContent = `Replay clock: ${data.run.clock ? date(data.run.clock,true) : 'Not started'}`;
  const p=coverage();
  const progressText=`${fraction(p.epics)} epics reviewed, ${percentLabel(p.epics)}. ${fraction(p.tickets)} tickets covered, ${percentLabel(p.tickets)}.`;
  if($('coverage-status').textContent!==progressText)$('coverage-status').textContent=progressText;
  renderNavigation();
}
function render() {
  if (!data) return;
  updateChrome();
  const views = {overview:renderOverview,reviews:renderReviews,agents:renderAgents,outcomes:renderOutcomes,evidence:renderEvidence,workspace:renderWorkspace};
  $('main').innerHTML = (views[currentView] || renderOverview)();
  bindInputs();
}
function route() {
  if (location.hash === '#main') { $('main').focus(); return; }
  const parts = location.hash.slice(1).split('/');
  currentView = [...NAV.map(n=>n[0]),'workspace'].includes(parts[0]) ? parts[0] : 'overview';
  if (parts[0] === 'reviews' && parts[1]) { try { selectedReview = decodeURIComponent(parts.slice(1).join('/')); } catch { selectedReview = null; } }
  closeNavigation();
  render();
  window.scrollTo({top:0,behavior:'instant'});
}
function go(view, id) { const hash = `#${view}${id?'/'+encodeURIComponent(id):''}`; if(location.hash===hash) route(); else location.hash=hash; }

function stat(label,value,note,ic,tag='') { return `<div class="stat"><div class="stat-head"><span>${label}</span>${icon(ic)}</div><div class="stat-value"><strong>${value}</strong>${tag?`<span class="stat-inline">${tag}</span>`:''}</div><p>${note}</p></div>`; }
// Unknown totals stay unknown in older exports; a review count is not a census.
function metric(value, fallbackDone = null) {
  const valid = n => Number.isSafeInteger(n) && n >= 0;
  const total = valid(value?.total) ? value.total : null;
  const done = valid(value?.done) ? value.done : fallbackDone;
  const percent = total > 0 && valid(done) ? (done >= total ? 100 : Math.min(99.9, Math.round(done / total * 1000) / 10)) : null;
  return {done, total, percent};
}
const count = n => Number.isSafeInteger(n) && n >= 0 ? n.toLocaleString('en-GB') : '—';
const fraction = m => `${count(m.done)} / ${count(m.total)}`;
const percentLabel = m => m.percent === null ? (m.total === 0 ? 'No items' : 'Total not recorded') : `${m.percent}%`;
function coverage() {
  return {epics:metric(data.progress?.epics, data.reviews.length), tickets:metric(data.progress?.tickets), steps:metric(data.progress?.steps)};
}
function progressBar(m, label) {
  if (m.total === null || m.done === null) return '';
  return `<progress class="coverage-bar" max="100" value="${m.percent ?? 0}" aria-label="${esc(label)}: ${fraction(m)}${m.percent === null ? '' : `, ${m.percent}%`}">${percentLabel(m)}</progress>`;
}
function progressStat(label, m, note, ic) {
  return `<div class="stat progress-stat"><div class="stat-head"><span>${esc(label)}</span>${icon(ic)}</div><div class="stat-value"><strong>${count(m.done)}<span class="stat-total"> / ${count(m.total)}</span></strong></div><div class="stat-meta"><b>${percentLabel(m)}</b><span>${m.total === null ? 'Coverage unavailable' : `${count(Math.max(0,m.total-(m.done||0)))} ${label==='Tickets covered'?'not covered':'remaining'}`}</span></div>${progressBar(m,label)}<p>${esc(note)}</p></div>`;
}
function coverageStrip(includeSteps = false) {
  const p=coverage(), cells=[['Epics reviewed',p.epics],['Tickets covered',p.tickets],...(includeSteps?[['Replay steps',p.steps]]:[])];
  return `<section class="coverage-strip" aria-label="Analysis coverage">${cells.map(([label,m])=>`<div class="coverage-item"><h3>${label}</h3><div class="coverage-figure"><b>${fraction(m)}</b><span>${percentLabel(m)}</span></div>${progressBar(m,label)}</div>`).join('')}</section>`;
}
function inventoryPanel() {
  const inv=data.inventory, p=coverage();
  if(!inv) return `<section class="panel info-panel inventory-panel"><h2>Dataset coverage unavailable</h2><p>This older export has no full dataset inventory. Start a new run and export it to see reliable totals and completion percentages.</p></section>`;
  const status=data.run.status==='finished'?'Replay complete':data.run.status==='paused'?'Replay paused':data.run.status==='recorded playback'?'Recorded playback':p.steps.done===0?'Ready to analyse':'Analysis in progress';
  const missing=p.tickets.total===null||p.tickets.done===null?null:Math.max(0,p.tickets.total-p.tickets.done);
  const legacy=inv.source==='legacy_reconstructed';
  return `<section class="panel inventory-panel" aria-label="Dataset coverage"><div class="panel-header"><div><h2>Dataset coverage</h2><p>${legacy?'Inventory reconstructed from the currently loaded dataset.':'Full inventory captured before the first review.'} Totals cover this run’s viewer scopes.</p></div>${badge(status,data.run.status==='finished'?'green':'gray')}</div><div class="inventory-summary"><span><strong>${count(inv.work_items)}</strong> work items</span><span><strong>${count(inv.epics)}</strong> epics</span><span><strong>${count(inv.tickets)}</strong> tickets</span><span><strong>${count(inv.by_scope?.length)}</strong> scopes</span></div><div class="run-progress"><div class="coverage-figure"><b>Replay steps · ${fraction(p.steps)}</b><span>${percentLabel(p.steps)}</span></div>${progressBar(p.steps,'Replay steps')}<p class="coverage-note">Reviews and outcome checks completed. Progress updates after each committed step.</p></div><div class="scope-coverage"><div class="scope-coverage-row scope-coverage-head"><span>Scope</span><span>Epics reviewed</span><span>Tickets covered</span></div>${(data.progress?.by_scope||[]).map(row=>{const epics=metric(row.epics), tickets=metric(row.tickets);return `<div class="scope-coverage-row"><strong>${esc(safeScopeLabel(row.scope))}</strong><div><small class="scope-mobile-label">Epics reviewed</small><div class="coverage-figure"><b>${fraction(epics)}</b><span>${percentLabel(epics)}</span></div>${progressBar(epics,`${row.scope} epics reviewed`)}</div><div><small class="scope-mobile-label">Tickets covered</small><div class="coverage-figure"><b>${fraction(tickets)}</b><span>${percentLabel(tickets)}</span></div>${progressBar(tickets,`${row.scope} tickets covered`)}</div></div>`;}).join('')}</div><p class="coverage-note">Ticket coverage counts unique tickets present in an epic’s plan at its review date, out of all tickets in the inventory. It does not mean each ticket received a separate review.</p>${missing?`<p class="coverage-alert"><strong>${count(missing)} tickets ${data.run.status==='finished'?'were not included':'not yet included'} in reviewed plans.</strong> ${count(inv.unlinked_tickets)} have no linked epic in this inventory. Tickets added after a review also remain uncovered.</p>`:''}${legacy?'<p class="coverage-note">This run predates saved inventories; its original starting totals cannot be verified. Start a new run for a fixed baseline.</p>':''}</section>`;
}
function renderOverview() {
  const t = totals(), latest = newestReviews()[0], scopes = data.run.viewer_scopes;
  const p = coverage();
  return pageHead('Delivery overview','A clearer view of your plans, grounded in your team’s history.',latest?button('Explore reviews','reviews',true):'')+
  `<div class="stats-grid">${progressStat('Epics reviewed',p.epics,'Reviewed / all epics in the inventory','reviews')}${progressStat('Tickets covered',p.tickets,'Included in reviewed plans / all tickets','evidence')}${progressStat('Findings scored',metric({done:t.scores.length,total:t.warnings.length}),'Outcome checked / published findings','shield')}${stat('Confirmation rate',t.rate===null?'—':t.rate+'%',t.scores.length?`${t.hits} of ${t.scores.length} scored findings confirmed`:'Waiting for completed work','outcomes',t.scores.length?'Replay result':'')}</div>`+inventoryPanel()+
  (latest?`<section class="hero" aria-labelledby="latest-title"><div><div class="eyebrow">LATEST REVIEW ${badge(safeScopeLabel(scopeFor(latest)))}</div><h2 id="latest-title">${esc(latest.report.title)}<span>${esc(latest.report.epic)}</span></h2><p>${latest.report.warnings.length?`${latest.report.warnings.length} evidence-backed findings to consider before work starts.`:'No findings passed the evidence checks for this plan.'} Reviewed using history available on ${date(latest.as_of,true)}.</p><button class="button" data-review="${esc(latest.epic_id)}">Open review ${icon('arrow')}</button></div><div class="hero-right"><div class="forecast-header"><span>Historical delivery forecast</span>${icon('clock')}</div><div class="forecast-values"><div>Typical duration · P50<strong>${number(latest.report.forecast?.p50_days)}<small>days</small></strong></div><div>Upper range · P90<strong>${number(latest.report.forecast?.p90_days)}<small>days</small></strong></div></div><div class="forecast-track" aria-hidden="true"></div><div class="track-labels"><span>Based on completed epics</span><span>Duration, not a deadline</span></div></div></section>`:'')+
  `<div class="two-col"><section class="panel"><div class="panel-header"><div><h2>Recent reviews</h2><p>See the context behind each plan.</p></div><a class="text-button" href="#reviews">View all ${icon('arrow')}</a></div><div class="filter-bar"><div class="tabs" role="group" aria-label="Filter by scope">${['',...scopes].map(s=>`<button class="tab" data-scope="${esc(s)}" aria-pressed="${tableScope===s}">${s?esc(safeScopeLabel(s)):'All scopes'}</button>`).join('')}</div>${searchInput('review-search',tableQuery,'Find a review')}</div><div id="overview-table">${reviewTable()}</div></section><section class="panel"><div class="panel-header"><div><h2>Agent activity</h2><p>Latest actions in this replay</p></div><a class="text-button" href="#agents" aria-label="View all agent activity">${icon('arrow')}</a></div><div class="activity-list">${recentActivity()}</div><div class="table-footer"><span>${mode==='live'?'Updates after each committed step':'Saved agent activity'}</span><span>${date(data.run.clock)}</span></div></section></div>`+
  `<div class="pipeline-strip"><span>Behind every review</span>${[['historian','Learn'],['planner','Plan'],['agents','Investigate'],['critic','Validate'],['writer','Review'],['scorekeeper','Learn again']].map(([id,label],i)=>`${i?'<span class="arrow" aria-hidden="true">›</span>':''}<span class="step">${label}</span>`).join('')}<button class="text-button" data-action="how">Meet the agents ${icon('arrow')}</button></div>`;
}
function reviewTable() {
  const list = newestReviews().filter(r=>(!tableScope||scopeFor(r)===tableScope) && `${r.report.title} ${r.report.epic}`.toLowerCase().includes(tableQuery.toLowerCase()));
  if(!list.length) return empty('No matching reviews','Try another name, ticket key, or scope.');
  return `<div class="table-scroll"><table class="reviews-table"><thead><tr><th scope="col">Epic</th><th scope="col">Findings</th><th scope="col">P50 / P90</th><th scope="col">Reviewed</th></tr></thead><tbody>${list.slice(0,5).map(r=>`<tr><td><button class="review-name" data-review="${esc(r.epic_id)}">${esc(r.report.title)}</button><span class="review-key">${esc(r.report.epic)} · ${esc(safeScopeLabel(scopeFor(r)))}</span></td><td>${badge(r.report.warnings.length?`${r.report.warnings.length} findings`:'No findings',r.report.warnings.length?'amber':'green')}</td><td>${number(r.report.forecast?.p50_days)} / ${number(r.report.forecast?.p90_days)} <span class="muted">d</span></td><td>${date(r.as_of)}</td></tr>`).join('')}</tbody></table></div><div class="table-footer"><span>Showing ${Math.min(list.length,5)} of ${list.length} reviews</span><span>Evidence checked</span></div>`;
}
function recentActivity() {
  const rows = [...logs].reverse().filter(e=>!['accuracy','finished','forecast'].includes(e.kind)).slice(0,4);
  return rows.length ? rows.map(e=>`<div class="activity-entry"><span class="agent-symbol">${icon(e.agent)}</span><div><h3>${esc(AGENTS[e.agent]||e.agent)}<time>${date(e.sim_at)}</time></h3><p>${esc(e.message)}</p></div></div>`).join('') : empty('Agents are getting started','Activity appears after the first replay step.');
}

function renderReviews() {
  const r = selected();
  return pageHead('Delivery reviews','What could affect delivery, and the history behind it.',button('How to run a review','run-help',false,'reviews'))+coverageStrip()+
    (data.reviews.length?`<div class="review-layout"><section class="panel review-selector" aria-label="Choose a review">${searchInput('review-list-search',reviewQuery,'Search reviews')}<div id="review-options" class="review-options">${reviewOptions()}</div></section><article class="panel review-document" id="review-document">${r?reviewDocument(r):empty('Review not found','Choose a review from the list.')}</article></div>`:empty('No reviews yet','Run the pipeline against your tracker history. Published reviews will appear here.',button('Run instructions','run-help')));
}
function reviewOptions() {
  return newestReviews().filter(r=>`${r.report.epic} ${r.report.title}`.toLowerCase().includes(reviewQuery.toLowerCase())).map(r=>`<button class="review-option ${selected()?.epic_id===r.epic_id?'selected':''}" data-review="${esc(r.epic_id)}" ${selected()?.epic_id===r.epic_id?'aria-current="true"':''}><strong>${esc(r.report.title)}</strong><span class="review-key">${esc(r.report.epic)}</span><small>${date(r.as_of)}</small>${badge(`${r.report.warnings.length} findings`,r.report.warnings.length?'amber':'gray')}</button>`).join('') || '<div class="empty-state">No matching reviews</div>';
}
function reviewDocument(r) {
  const report=r.report, f=report.forecast||{};
  return `<div class="document-head"><div><span class="mono">${esc(report.epic)}</span><h2>${esc(report.title)}</h2><p>${esc(safeScopeLabel(scopeFor(r)))} · Reviewed ${date(r.as_of,true)}</p></div>${button('Export review','export',false,'download')}</div><p class="report-summary">${esc(report.summary)}</p><div class="forecast-box"><div><small>Typical · P50</small><strong>${number(f.p50_days)} <small>days</small></strong></div><div><small>Upper range · P90</small><strong>${number(f.p90_days)} <small>days</small></strong></div><p>${f.withheld_by_viewer_scope?'Forecast withheld because its history is outside this viewer’s scope.':f.p50_days==null?'Not enough completed epics to produce a forecast.':`Historical durations from ${Array.isArray(f.basis)?f.basis.length:0} completed epics. These are estimates, not delivery commitments.`}</p></div>${report.suppressed_by_ring_fence?`<div class="notice">${icon('shield')}${number(report.suppressed_by_ring_fence)} findings withheld because their evidence is outside this viewer’s scope.</div>`:''}<div class="section-heading"><h3>Evidence-backed findings</h3><small>${report.warnings.length} published</small></div>${report.warnings.length?report.warnings.map(w=>finding(w)).join(''):empty('No published findings','No candidate findings met the Critic’s evidence checks. This does not establish that the plan is risk-free.')}${report.missing_tickets?.length?`<div class="section-heading"><h3>Suggested work</h3><small>For the team to consider</small></div><ul class="suggested-list">${report.missing_tickets.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p class="reduced-note">Suggestions only. No tickets are created in your tracker.</p>`:''}`;
}
function finding(w) {
  const score=data.scores[w.id];
  return `<section class="finding"><div class="finding-top"><span class="agent-symbol">${icon(w.agent)}</span><strong>${esc(AGENTS[w.agent]||w.agent)}</strong>${badge(`${w.confidence||'Unspecified'} confidence`,'gray')}</div><p>${esc(w.claim)}</p><div class="evidence-chips">${w.evidence.map(id=>`<button class="evidence-chip" data-evidence="${esc(id)}" title="Inspect ${esc(data.items[id]?.title||id)}">${icon('evidence')}${esc(data.items[id]?.key||id)}</button>`).join('')}</div><p class="outcome">${score ? `${score.hit?'Confirmed':'Not observed'} in replay · ${esc(score.detail)}`:'Outcome pending · scored after this epic finishes'}</p></section>`;
}

const STAGES = [
  ['historian','Learn from history','Find patterns in estimates, dependencies and missing work.',['historian']],
  ['planner','Inspect the plan','Plan the review, assess data quality and run three specialists.',['planner','data_quality','estimator','dependency_hunter','gap_finder']],
  ['critic','Check the evidence','Require cited tickets, enforce viewer scope and revise once.',['critic']],
  ['writer','Publish the review','Summarise findings, suggested work and historical durations.',['writer']],
  ['scorekeeper','Learn from outcomes','Score predictions and adjust the weight of their patterns.',['scorekeeper']]
];
function renderAgents() {
  return pageHead('Agent activity','A transparent view of the work behind every delivery review.',mode==='snapshot'?button(playbackTimer?'Pause replay':'Play replay','playback',true,'replay'):button(refreshEnabled?'Pause updates':'Resume updates','refresh',false,'replay'))+coverageStrip(true)+
  `<div class="flow-grid">${STAGES.map(([id,title,desc,agents],i)=>`<section class="flow-card"><span class="number">0${i+1}</span><div class="agent-symbol">${icon(id)}</div><h3>${title}</h3><p>${desc}</p><div class="flow-count">${logs.filter(l=>agents.includes(l.agent)).length} recorded actions</div></section>`).join('')}</div><section class="panel"><div class="panel-header"><div><h2>Activity log</h2><p>${mode==='snapshot'?'Recorded agent actions. Playback reveals the saved timeline; it does not run inference.':'Committed agent actions, refreshed every 1.5 seconds while running.'}</p></div>${mode==='snapshot'?'<button class="text-button" data-action="reset-playback">Show complete run</button>':''}</div><div class="log-tools"><select id="agent-filter" aria-label="Filter by agent"><option value="">All agents</option>${Object.entries(AGENTS).map(([id,name])=>`<option value="${id}" ${agentFilter===id?'selected':''}>${name}</option>`).join('')}</select>${searchInput('log-search',logQuery,'Search agent activity')}</div><div id="log-box" class="log-box" tabindex="0" aria-label="Agent activity entries">${logRows()}</div></section>`;
}
function logRows() {
  const rows = [...logs].reverse().filter(e=>(!agentFilter||e.agent===agentFilter) && `${e.message} ${e.kind} ${AGENTS[e.agent]}`.toLowerCase().includes(logQuery.toLowerCase()));
  return rows.length ? rows.slice(0,250).map(e=>`<div class="log-row"><time datetime="${esc(e.sim_at)}">${date(e.sim_at)}</time><strong>${esc(AGENTS[e.agent]||e.agent)}</strong><span>${badge(e.kind.replaceAll('_',' '),['hit','accepted'].includes(e.kind)?'green':['miss','suppressed','recovered'].includes(e.kind)?'amber':'gray')}</span><p>${esc(e.message)}</p></div>`).join('')+(rows.length>250?`<div class="table-footer">Showing the most recent 250 of ${rows.length} matching actions. Filter to narrow the timeline.</div>`:'') : empty('No matching activity','Choose another agent or search term.');
}
function scoreSeries() {
  const scores = totals().scores.sort((a,b)=>a.at.localeCompare(b.at));
  let hits=0; const result=[];
  scores.forEach((s,i)=>{hits+=s.hit?1:0; if(i===scores.length-1||scores[i+1].at!==s.at) result.push({at:s.at,value:hits/(i+1),count:i+1,hits});});
  return result;
}
function outcomeChart() {
  const series=scoreSeries();
  if(!series.length) return empty('Outcomes are still pending','The Scorekeeper evaluates findings when an epic finishes.');
  const w=Math.min(640, Math.max(280, window.innerWidth - (window.innerWidth > 760 ? 320 : 84))),h=230,left=42,right=16,top=18,bottom=32,pw=w-left-right,ph=h-top-bottom;
  const points=series.map((p,i)=>[left+(series.length===1?pw/2:i/(series.length-1)*pw),top+(1-p.value)*ph]);
  const path=points.map((p,i)=>`${i?'L':'M'}${p[0]},${p[1]}`).join(' ');
  return `<div class="outcome-chart"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Cumulative share of scored findings confirmed, ending at ${Math.round(series.at(-1).value*100)} percent across ${series.at(-1).count} findings"><defs><linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1"><stop stop-color="#c4d6bc" stop-opacity=".55"/><stop offset="1" stop-color="#c4d6bc" stop-opacity=".05"/></linearGradient></defs>${[0,.25,.5,.75,1].map(n=>`<line x1="${left}" x2="${w-right}" y1="${top+(1-n)*ph}" y2="${top+(1-n)*ph}" stroke="#e9eee4" stroke-dasharray="3 4"/><text x="${left-9}" y="${top+(1-n)*ph+4}" text-anchor="end" font-size="12" fill="#65725d">${n*100}%</text>`).join('')}<path d="${path} L${points.at(-1)[0]},${h-bottom} L${points[0][0]},${h-bottom} Z" fill="url(#chart-fill)"/><path d="${path}" fill="none" stroke="#53794b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>${points.map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="#53794b"><title>${date(series[i].at,true)}: ${series[i].hits} of ${series[i].count} findings confirmed</title></circle>`).join('')}<text x="${left}" y="${h-6}" font-size="12" fill="#65725d">${date(series[0].at)}</text><text x="${w-right}" y="${h-6}" text-anchor="end" font-size="12" fill="#65725d">${date(series.at(-1).at,true)}</text></svg></div><p class="chart-caption">Cumulative confirmed findings ÷ scored findings. ${isSynthetic()?'Results are from synthetic demo history.':'Historical replay results are not a guarantee of future accuracy.'}</p>`;
}
function renderOutcomes() {
  const t=totals();
  return pageHead('Learning from outcomes','Check what happened after the review, then improve the next one.')+coverageStrip()+`<div class="stats-grid">${stat('Confirmed',t.hits,'Predictions observed in completed work','check')}${stat('Not observed',t.scores.length-t.hits,'Predictions not borne out in replay','outcomes')}${progressStat('Findings scored',metric({done:t.scores.length,total:t.warnings.length}),'Of findings published so far','clock')}${stat('Confirmation rate',t.rate===null?'—':t.rate+'%',`${t.scores.length} scored findings · visible scope`,'shield')}</div><div class="two-col"><section class="panel"><div class="panel-header"><div><h2>Confirmation over time</h2><p>How published findings compare with later outcomes</p></div></div>${outcomeChart()}</section><section class="panel"><div class="panel-header"><div><h2>By specialist</h2><p>Confirmed / scored findings</p></div></div><div class="breakdown">${['estimator','dependency_hunter','gap_finder'].map(a=>{const ids=new Set(t.warnings.filter(w=>w.agent===a).map(w=>w.id)); const scores=t.scores.filter(s=>ids.has(s.warning_id));const hits=scores.filter(s=>s.hit).length;return `<div class="breakdown-row"><span>${AGENTS[a]}</span><b>${hits} / ${scores.length}</b><div class="meter"><span style="width:${scores.length?hits/scores.length*100:0}%"></span></div></div>`;}).join('')}</div></section></div><section class="panel info-panel mt"><h2>How the learning loop works</h2><p>Every confirmed prediction adds 0.2 to its pattern’s weight. A miss subtracts 0.3. Specialists stop using patterns below 0.5. These updates influence subsequent reviews within the replay; they are not model fine-tuning. Confidence labels describe a finding’s evidence and are not calibrated probabilities.</p></section>`;
}
function evidenceItems() {
  const counts=new Map(); data.reviews.forEach(r=>r.report.warnings.forEach(w=>w.evidence.forEach(id=>counts.set(id,(counts.get(id)||0)+1))));
  return [...counts].map(([id,count])=>({id,count,...data.items[id]})).sort((a,b)=>(a.key||a.id).localeCompare(b.key||b.id)).filter(i=>`${i.key} ${i.title} ${i.scope}`.toLowerCase().includes(evidenceQuery.toLowerCase()));
}
function renderEvidence() {
  return pageHead('Evidence library','The historical tickets cited in published findings. Citations are a subset of the work covered.')+coverageStrip()+`<div class="evidence-toolbar">${searchInput('evidence-search',evidenceQuery,'Search title, ticket key or scope')}<small>Only evidence available to this viewer is shown.</small></div><div id="evidence-grid" class="evidence-grid">${evidenceCards()}</div>`;
}
function evidenceCards() {
  const items=evidenceItems();
  return items.length?items.map(i=>`<button class="evidence-card" data-evidence="${esc(i.id)}"><span class="mono">${esc(i.key||i.id)}</span><strong>${esc(i.title||'Cited historical ticket')}</strong><span class="scope-label">${esc(i.scope||'Visible scope')} · Cited ${i.count} ${i.count===1?'time':'times'}</span></button>`).join(''):empty('No matching evidence','Try another ticket key or title.');
}
function renderWorkspace() {
  const ds=data.dataset||{}, t=totals();
  const def=(label,value)=>`<div class="definition"><span>${label}</span><strong>${esc(value)}</strong></div>`;
  return pageHead('Workspace details','Understand the data and boundaries behind this workspace.')+inventoryPanel()+`<div class="info-grid"><section class="panel info-panel"><h2>Current run</h2>${def('Run',data.run.id)}${def('Source',mode==='live'?'Connected Nabu API':'Recorded snapshot')}${def('Status',playbackTimer?'Playback in progress':data.run.status)}${def('Replay clock',date(data.run.clock,true))}${def('Tenant',data.run.tenant)}${def('Inference',data.run.offline===true?'Deterministic (offline)':data.run.offline===false?'Model-assisted':'Not recorded in this run')}<div class="help-actions">${button('Export visible data','export-all',false,'download')}${button('Run instructions','run-help',false,'reviews')}</div></section><section class="panel info-panel"><h2>Data visibility</h2>${def('Viewer scopes',data.run.viewer_scopes.join(', '))}${def('Source types',ds.sources?.join(', ')||'Not recorded')}${def('Work items known at replay date',ds.work_items??'Not recorded')}${def('Epics known at replay date',ds.epics??'Not recorded')}${def('Withheld findings',t.withheld)}<p>Each review uses only the history available at its review date. Findings require cited evidence within the recorded viewer scope.</p></section><section class="panel info-panel"><h2>Connect your team’s history</h2><p>Load canonical JSONL into Nabu, then start a replay. The TAWOS adapter is available today. Direct Jira, Linear, GitHub and Azure DevOps connections are not implemented in this repository.</p><div class="help-actions">${button('View setup steps','run-help',false,'arrow')}</div></section><section class="panel info-panel"><h2>Company deployment</h2><p>This interface is a read-only client. It does not provide sign-in, role administration, tracker writeback or production tenant authentication. Deploy the API behind company identity and server-enforced access before connecting private company data.</p><p class="reduced-note">The recorded preview contains synthetic data. Browser filters are navigation tools, not access controls.</p></section></div>`;
}

function openDialog(title,subtitle,body) {
  $('dialog-body').innerHTML=`<div class="dialog-head"><div><h2 id="dialog-title">${esc(title)}</h2><p>${esc(subtitle)}</p></div><button class="icon-button" data-action="close-dialog" aria-label="Close dialog">${icon('close')}</button></div><div class="dialog-content">${body}</div>`;
  $('detail-dialog').showModal();
}
function showEvidence(id) {
  const item=data.items[id];
  if(!item) { toast('This evidence is not available in the current viewer scope.'); return; }
  const refs=data.reviews.filter(r=>r.report.warnings.some(w=>w.evidence.includes(id)));
  openDialog(item.key,item.title,`<span class="pill green">${esc(safeScopeLabel(item.scope))}</span><h3>Used in ${refs.length} ${refs.length===1?'review':'reviews'}</h3><ul class="suggested-list">${refs.map(r=>`<li><button class="text-button" data-review="${esc(r.epic_id)}">${esc(r.report.title)} · ${esc(r.report.epic)}</button></li>`).join('')}</ul><p class="mt">Nabu exposes the ticket’s key, title and scope here. Original ticket text and a tracker URL are not available in this API.</p>`);
}
function showHow() { openDialog('A review with a memory','How Nabu’s agents work together',STAGES.map(([,title,desc],i)=>`<div class="mini-step"><span>${i+1}</span><div><strong>${title}</strong><p>${desc}</p></div></div>`).join('')+`<h3>Evidence comes first</h3><p>Specialists create candidate findings from stored ticket history. The Critic requires at least two cited tickets known at review time. Findings outside the reader’s scope are withheld. Specialists execute sequentially, and a finding can be revised once before publication.</p><div class="dialog-actions">${button('Explore agent activity','agents',true)}</div>`); }
function showRunHelp() {
  openDialog('Run a delivery review','From tracker history to an evidence-backed review',`<p>Run these commands from the Nabu-2 checkout. This example generates synthetic data and uses deterministic agents.</p><pre>python -m nabu demo-data
python -m nabu load data/demo.jsonl
python -m nabu replay --run demo1 --tenant demo \\\n  --scopes payments,platform --offline --pace 2
python -m nabu.ui --db data/nabu.sqlite --port 8000</pre><p>Open <code>http://127.0.0.1:8000/?run=demo1</code>. For existing company history, load canonical JSONL and substitute its tenant and scopes.</p><h3>Hosted frontend</h3><p>Use <code>config.js</code> or <code>?api=https://your-protected-api</code> to connect an API. Export a saved run with:</p><pre>python -m nabu.ui --db data/nabu.sqlite --export demo1</pre><p>Keep private data behind company authentication. The local helper itself does not authenticate viewers.</p>`);
}
function download(filename,payload) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download=filename.replace(/[^a-z0-9._-]/gi,'-');document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('JSON export downloaded.');
}
function toast(message) { $('toast').textContent=message;$('toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),3500); }
function bindInputs() {
  const bind=(id,callback)=>{const node=$(id);if(node)node.addEventListener('input',callback);};
  bind('review-search',e=>{tableQuery=e.target.value;$('overview-table').innerHTML=reviewTable();});
  bind('review-list-search',e=>{reviewQuery=e.target.value;$('review-options').innerHTML=reviewOptions();});
  bind('evidence-search',e=>{evidenceQuery=e.target.value;$('evidence-grid').innerHTML=evidenceCards();});
  bind('log-search',e=>{logQuery=e.target.value;$('log-box').innerHTML=logRows();});
  if($('agent-filter'))$('agent-filter').onchange=e=>{agentFilter=e.target.value;$('log-box').innerHTML=logRows();};
}
document.addEventListener('click',e=>{
  const review=e.target.closest('[data-review]');if(review){$('detail-dialog').close();selectedReview=review.dataset.review;go('reviews',selectedReview);return;}
  const evidence=e.target.closest('[data-evidence]');if(evidence){showEvidence(evidence.dataset.evidence);return;}
  const scope=e.target.closest('[data-scope]');if(scope){tableScope=scope.dataset.scope;document.querySelectorAll('[data-scope]').forEach(b=>{b.setAttribute('aria-pressed',String(b===scope));});$('overview-table').innerHTML=reviewTable();return;}
  const nav=e.target.closest('[data-nav]');if(nav){go(nav.dataset.nav);return;}
  const action=e.target.closest('[data-action]')?.dataset.action;
  if(!action)return;
  if(['reviews','agents','overview','workspace'].includes(action)){$('detail-dialog').close();go(action);}
  else if(action==='how')showHow();
  else if(action==='run-help')showRunHelp();
  else if(action==='close-dialog')$('detail-dialog').close();
  else if(action==='export'){const r=selected();if(r)download(`${r.report.epic}-review.json`,{run:data.run.id,review:r,items:Object.fromEntries(r.report.warnings.flatMap(w=>w.evidence).filter(id=>data.items[id]).map(id=>[id,data.items[id]]))});}
  else if(action==='export-all')download(`nabu-${runId}.json`,{...data,log:logs});
  else if(action==='playback')togglePlayback();
  else if(action==='reset-playback'){stopPlayback();data=structuredClone(fullSnapshot);logs=data.log;render();toast('Showing the complete recorded run.');}
  else if(action==='refresh'){refreshEnabled=!refreshEnabled;clearTimeout(pollTimer);if(refreshEnabled)poll(generation);render();}
  else if(action==='retry')init();
  else if(action==='load-demo'){params.delete('run');runId=null;initialApiError='';loadSnapshots();}
});
const narrowNavigation = window.matchMedia('(max-width: 760px)');
function syncNavigation(){ $('sidebar').inert=narrowNavigation.matches&&!$('sidebar').classList.contains('open'); }
function closeNavigation(){ $('sidebar').classList.remove('open');$('menu').setAttribute('aria-expanded','false');syncNavigation(); }
narrowNavigation.addEventListener('change',syncNavigation);syncNavigation();
$('menu').onclick=()=>{const open=$('sidebar').classList.toggle('open');$('menu').setAttribute('aria-expanded',String(open));syncNavigation();};
document.addEventListener('click',e=>{if($('sidebar').classList.contains('open')&&!$('sidebar').contains(e.target)&&!$('menu').contains(e.target)){closeNavigation();}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeNavigation();}});
$('detail-dialog').addEventListener('click',e=>{if(e.target===$('detail-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
window.addEventListener('hashchange',route);
$('run-select').onchange=()=>{runId=$('run-select').value;selectedReview=null;tableScope='';tableQuery='';const url=new URL(location.href);url.searchParams.set('run',runId);history.replaceState(null,'',url);loadRun();};
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(pollTimer);if(playbackTimer){stopPlayback();render();}}else if(mode==='live'&&refreshEnabled){clearTimeout(pollTimer);poll(generation);}});

async function getJSON(url) {
  const response=await fetch(url,{signal:AbortSignal.timeout(7000),cache:'no-store'});
  if(!response.ok)throw new Error(`Request failed (${response.status}).`);
  return response.json();
}
function validateState(s) { if(!s?.run||!Array.isArray(s.reviews)||!Array.isArray(s.log)||!s.scores||!s.items||!Array.isArray(s.run.viewer_scopes))throw new Error('The server did not return a valid Nabu run.');return s; }
function showError(title,message,allowDemo=false) {
  $('connection').textContent='Connection unavailable';$('connection').classList.add('error');
  $('main').innerHTML=empty(esc(title),esc(message),button('Retry connection','retry',true)+(allowDemo?' '+button('Open recorded demo','load-demo'):''));
}
async function init() {
  clearTimeout(pollTimer);stopPlayback();generation++;snapshotFailure=false;
  if(params.get('source')==='snapshot'||document.documentElement.dataset.mode==='snapshot')return loadSnapshots();
  try {
    runList=await getJSON(`${API}/api/runs`);
    if(!Array.isArray(runList))throw new Error('Invalid run list.');
    mode='live';
    if(!runList.length){data=null;$('connection').textContent='API connected';$('main').innerHTML=empty('Your workspace is ready','No replay runs are stored yet. Run a review to see your team’s delivery history here.',button('Run instructions','run-help',true));renderNavigation();return;}
    if(runId&&!runList.some(r=>r.id===runId)){showError('Run not found',`The connected API has no run named “${runId}”. Select another run.`,true);$('run-select').innerHTML='<option value="">Select a run</option>'+runList.map(r=>`<option value="${esc(r.id)}">${esc(r.id)}</option>`).join('');return;}
    runId=runId||runList[0].id;await loadRun();
  } catch(error) {
    initialApiError=error.message;snapshotFailure=true;
    if(API){showError('The Nabu API could not be reached','Check the configured API address and its access settings, then retry. You can also open the recorded demo.',true);return;}
    await loadSnapshots();
  }
}
async function loadSnapshots() {
  generation++;clearTimeout(pollTimer);stopPlayback();mode='snapshot';
  try {
    runList=await getJSON('runs/index.json');if(!Array.isArray(runList)||!runList.length)throw new Error('No recorded runs are available.');
    if(runId&&!runList.some(r=>r.id===runId)){showError('Recorded run not found',`No saved run named “${runId}” is available.`,true);return;}
    runId=runId||runList[0].id;await loadRun();
  } catch(error){showError('Could not open the workspace',error.message);}
}
async function loadRun() {
  const token=++generation;clearTimeout(pollTimer);stopPlayback();after=0;logs=[];data=null;fullSnapshot=null;
  $('main').innerHTML='<div class="loading"><span class="loading-ring"></span><h1>Opening the review history</h1></div>';
  try {
    if(mode==='snapshot') {
      const row=runList.find(r=>r.id===runId);
      const filename=row?.file||`${encodeURIComponent(runId)}.json`;
      // Snapshot manifests are same-origin relative files, never arbitrary API destinations.
      if(filename.includes('..')||filename.includes(':')||filename.startsWith('/'))throw new Error('Invalid recorded run filename.');
      const s=validateState(await getJSON(`runs/${filename}`));if(token!==generation)return;
      fullSnapshot=structuredClone(s);data=s;logs=s.log;after=logs.at(-1)?.seq||0;route();
    } else await poll(token,true);
  } catch(error) {if(token===generation)showError('Could not load this run',error.message,mode==='live');}
}
async function poll(token,initial=false) {
  if(token!==generation||mode!=='live'||(!initial && (document.hidden||!refreshEnabled))||pollingToken===token)return;
  pollingToken=token;
  try {
    const s=validateState(await getJSON(`${API}/api/state?run=${encodeURIComponent(runId)}&after=${after}`));
    if(token!==generation)return;
    const changed=pendingRender||initial||s.log.length||!data||s.run.cursor!==data.run.cursor||s.run.status!==data.run.status;
    const seen=new Set(logs.map(l=>l.seq));logs.push(...s.log.filter(l=>!seen.has(l.seq)));logs.sort((a,b)=>a.seq-b.seq);after=logs.at(-1)?.seq||0;data=s;lastUpdated=new Date();
    if(changed){const active=document.activeElement;if(active?.matches('input,select')&&$('main').contains(active)){pendingRender=true;updateChrome();}else {pendingRender=false;if(initial)route();else render();}}else updateChrome();
    $('connection').title=`Last successful update ${lastUpdated.toLocaleTimeString()}`;
    if(refreshEnabled&&!document.hidden)pollTimer=setTimeout(()=>poll(token),s.log.length===1000?0:data.run.status==='finished'?10000:1500);
  } catch(error) {
    if(token!==generation)return;
    if(initial)throw error;
    $('connection').textContent='Connection lost · retrying';$('connection').classList.add('error');$('connection').title=error.message;
    if(refreshEnabled&&!document.hidden)pollTimer=setTimeout(()=>poll(token),5000);
  } finally { if(pollingToken===token)pollingToken=null; }
}
$('main').addEventListener('focusout',()=>requestAnimationFrame(()=>{if(pendingRender&&!$('main').contains(document.activeElement)){pendingRender=false;render();}}));
function snapshotAt(snapshot, at) {
  const checkpoint=[...(snapshot.checkpoints||[])].reverse().find(c=>at && c.at<=at);
  const zero = p => ({...p,epics:{...p.epics,done:0,percent:p.epics?.total?0:null},tickets:{...p.tickets,done:0,percent:p.tickets?.total?0:null},steps:{...p.steps,done:0,percent:p.steps?.total?0:null},by_scope:(p.by_scope||[]).map(zero)});
  // Older recordings do not contain reliable ticket/step checkpoints.
  const progress=checkpoint?.progress || (snapshot.checkpoints && snapshot.progress ? zero(snapshot.progress) : undefined);
  return {...snapshot,progress,
    run:{...snapshot.run,clock:at,status:'recorded playback',cursor:progress?.steps?.done??0},
    reviews:snapshot.reviews.filter(r=>at && r.as_of<=at),
    scores:Object.fromEntries(Object.entries(snapshot.scores).filter(([,s])=>at && s.at<=at)),
    log:snapshot.log.filter(l=>at && l.sim_at && l.sim_at<=at)};
}
let playbackIndex=0;
function stopPlayback(){clearInterval(playbackTimer);playbackTimer=null;}
function togglePlayback(){
  if(playbackTimer){stopPlayback();render();return;}
  if(!fullSnapshot)return;
  const moments=[...new Set([...fullSnapshot.log.map(l=>l.sim_at),...fullSnapshot.reviews.map(r=>r.as_of),...Object.values(fullSnapshot.scores).map(s=>s.at),...(fullSnapshot.checkpoints||[]).map(c=>c.at)])].filter(Boolean).sort();
  if(!moments.length){toast('This snapshot has no timeline to replay.');return;}
  const timeline=[null,...moments];
  if(data.run.clock===fullSnapshot.run.clock)playbackIndex=0;
  const tick=()=>{
    if(playbackIndex>=timeline.length){stopPlayback();data=structuredClone(fullSnapshot);logs=data.log;render();toast('Recorded replay complete.');return;}
    data=snapshotAt(fullSnapshot,timeline[playbackIndex++]);logs=data.log;render();
  };
  playbackTimer=setInterval(tick,1000);tick();
}
init();
