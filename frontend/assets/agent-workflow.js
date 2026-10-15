(() => {
  'use strict';
  const root = document.getElementById('workflow-reference');
  if (!root) return;
  const svg = root.querySelector('.workflow-svg');
  const viewport = document.getElementById('workflow-viewport');
  const name = document.getElementById('workflow-agent-name');
  const detail = document.getElementById('workflow-agent-detail');
  const zoomOutput = document.getElementById('workflow-zoom');
  const zoomIn = document.getElementById('workflow-zoom-in');
  const zoomOut = document.getElementById('workflow-zoom-out');
  const nodes = [...root.querySelectorAll('[data-agent]')];
  const edges = [...root.querySelectorAll('.wf-edge')];
  const roles = {
    replay: ['Review clock', 'Sets the historical cutoff for a review. Specialists use the ticket history available at that point, while the dashboard displays the saved snapshot immediately.'],
    historian: ['Historian', 'Reads completed tickets available at the review cutoff. Mines estimate, dependency and missing-work patterns with evidence; learned pattern weights persist.'],
    planner: ['Planner', 'Reads the new epic and the tickets known at review time. Deterministic routing assigns estimate, dependency and missing-work checks to specialists.'],
    estimator: ['Estimator', 'Compares planned estimates with historical time per point and component patterns. Python supplies cited candidate warnings and checkable overrun predictions.'],
    dependency: ['Dependency hunter', 'Finds dependencies that repeatedly blocked comparable work and checks whether the new plan includes that kind of prerequisite.'],
    gaps: ['Gap finder', 'Looks for work categories missing from the plan that comparable completed epics had to add after their initial review.'],
    quality: ['Data quality', 'Assesses historical coverage, estimate availability and recorded start dates. Attaches heuristic confidence to warnings; confidence is not a calibrated probability.'],
    critic: ['Critic', 'Code requires at least two known cited tickets and suppresses inaccessible evidence. A fresh model request can accept, drop or request one revision of eligible warnings.'],
    writer: ['Writer', 'Saves the accepted risks, evidence IDs and suggested tickets. It may add historical P50/P90 durations when enough finished epics exist. A human decides whether to change the plan.'],
    scorekeeper: ['Scorekeeper', 'When an epic finishes, checks each published prediction against outcomes. Hits and misses change pattern weights for later reviews, not the underlying model weights.'],
  };
  let zoom = 1;
  function setZoom(value) {
    zoom = Math.max(1, Math.min(3, value));
    svg.style.width = `${zoom * 100}%`;
    zoomOutput.textContent = `${Math.round(zoom * 100)}%`;
    zoomOut.disabled = zoom <= 1;
    zoomIn.disabled = zoom >= 3;
  }
  function select(agent) {
    if (!roles[agent]) return;
    nodes.forEach(node => node.setAttribute('aria-pressed', String(node.dataset.agent === agent)));
    edges.forEach(edge => edge.classList.toggle('active', edge.dataset.from === agent || edge.dataset.to === agent));
    name.textContent = roles[agent][0];
    detail.textContent = roles[agent][1];
  }
  nodes.forEach(node => {
    node.addEventListener('click', () => select(node.dataset.agent));
    node.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        select(node.dataset.agent);
      }
    });
  });
  zoomIn.addEventListener('click', () => setZoom(zoom + 0.25));
  zoomOut.addEventListener('click', () => setZoom(zoom - 0.25));
  document.getElementById('workflow-fit').addEventListener('click', () => {
    setZoom(1);
    viewport.scrollLeft = 0;
    viewport.scrollTop = 0;
  });
  document.getElementById('workflow-reset').addEventListener('click', () => {
    nodes.forEach(node => node.setAttribute('aria-pressed', 'false'));
    edges.forEach(edge => edge.classList.remove('active'));
    name.textContent = 'Review workflow';
    detail.textContent = 'Select a node to see its purpose, inputs and output.';
  });
  // The How it works view starts hidden, so measure after it is opened.
  function ensureReadable() {
    const width = viewport.clientWidth;
    if (width > 0 && width < 650 && zoom === 1) setZoom(Math.min(3, 980 / width));
  }
  setZoom(1);
  ensureReadable();
  window.addEventListener('hashchange', ensureReadable);
})();
