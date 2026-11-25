'use strict';

const $ = id => document.getElementById(id);
const config = window.INTELLIGENCE_CONFIG;
let session;
let view = 'active';
let state = {};

function element(tag, value, className) {
  const item = document.createElement(tag);
  if (value !== undefined) item.textContent = value;
  if (className) item.className = className;
  return item;
}

function link(url, title) {
  const item = element('a', title);
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:') item.href = parsed.href;
  } catch { /* Leave malformed source URLs inert. */ }
  item.target = '_blank';
  item.rel = 'noopener noreferrer';
  return item;
}

async function api(path, options = {}) {
  const response = await fetch(config.url + path, {
    method: options.method || 'GET',
    headers: {apikey: config.key, 'Content-Type': 'application/json',
      ...(session ? {Authorization: 'Bearer ' + session.access_token} : {}),
      ...options.headers},
    ...(options.body ? {body: JSON.stringify(options.body)} : {})
  });
  if (!response.ok) throw new Error(response.status === 401 ? 'Authentication failed or expired. Sign in again.' : 'Request failed (' + response.status + ').');
  return response.status === 204 ? null : response.json();
}

async function table(name, query = 'select=*', limit = Infinity) {
  const rows = [];
  const size = 300;
  while (rows.length < limit) {
    const batch = await api('/rest/v1/' + name + '?' + query + '&limit=' + Math.min(size, limit - rows.length) + '&offset=' + rows.length);
    rows.push(...batch);
    if (batch.length < size) break;
  }
  return rows;
}

async function load() {
  const [operators, employers, vacancies, runs, pipeline] = await Promise.all([
    table('operators'), table('employers', 'select=*&order=id'), table('vacancies', 'select=*&order=id'),
    table('runs', 'select=*&order=started.desc,id', 30), table('pipeline', 'select=*&order=vacancy_id')
  ]);
  if (!operators.length) throw new Error('This account has no dashboard access.');
  state = {employers, vacancies, runs, pipeline};
  render();
}

function metric(label, value) {
  const card = element('div', undefined, 'metric');
  card.append(element('strong', value), element('span', label));
  return card;
}

function employerCard(row) {
  const card = element('article', undefined, 'card');
  card.append(element('h3', row.name), element('p', [
    row.rank ? '#' + row.rank + ' · ' + row.ranking_year : 'Priority queue · ranked status unresolved',
    row.method || 'Adapter pending', row.status || 'No run'
  ].join(' · ')), element('p', 'Last successful check ' + (row.last_success || '—')));
  if (row.portal) card.append(link(row.portal, 'Career portal'));
  return card;
}

function coverageCard(run) {
  const audit = run.audit ? JSON.parse(run.audit) : null;
  const card = element('article', undefined, 'card');
  card.append(element('h3', run.status + ' · ' + run.started));
  if (!audit) return card;
  card.append(element('p', audit.top_successful + '/150 Top 150 · ' + audit.attempted +
    ' attempted · ' + audit.successful + ' complete · ' + audit.unidentified_rank_slots +
    ' roster gaps · ' + audit.retrieved + ' retrieved · ' + audit.eligible + ' eligible · ' + audit.seconds + ' seconds'));
  const details = element('details');
  details.append(element('summary', 'Employer checks and errors'));
  Object.entries(audit.employer_results).forEach(([id, row]) => {
    details.append(element('p', [row.name || id, row.status, row.finished, ...(row.errors || [])].join(' · ')));
  });
  card.append(details);
  return card;
}

function vacancyCard(row) {
  const detail = JSON.parse(row.details);
  const employer = state.employers.find(item => item.id === row.employer_id);
  const saved = state.pipeline.find(item => item.vacancy_id === row.id);
  const card = element('article', undefined, 'card');
  card.append(element('h3', row.title), element('p', [employer?.name, row.location,
    row.requisition, row.status].filter(Boolean).join(' · ')),
    element('p', 'Compensation ' + (detail.compensation ? JSON.stringify(detail.compensation) : 'unknown') +
      ' · Equity ' + (detail.equity || 'unknown')),
    element('p', 'Posted ' + (detail.published || 'unknown') + ' · Last verified ' + (row.last_verified || 'unknown')),
    link(row.url, 'Open original source'));
  const disclosure = element('details');
  disclosure.append(element('summary', 'Evidence and pipeline'), element('p', row.evidence || 'Evidence unavailable'));
  if (detail.scope?.length) disclosure.append(element('p', 'Scope ' + detail.scope.join(', ')));
  if (detail.description) disclosure.append(element('p', detail.description));
  const form = element('form');
  const bookmark = element('input');
  bookmark.type = 'checkbox';
  bookmark.checked = saved?.bookmarked || false;
  const bookmarkLabel = element('label', 'Bookmark');
  bookmarkLabel.append(bookmark);
  const stage = element('select');
  for (const status of ['watching', 'applied', 'interview', 'offer', 'closed']) stage.add(new Option(status, status));
  stage.value = saved?.application_status || detail.application_status || 'watching';
  const stageLabel = element('label', 'Application status');
  stageLabel.append(stage);
  const notes = element('textarea');
  notes.value = saved?.notes || '';
  notes.maxLength = 12000;
  const noteLabel = element('label', 'Notes');
  noteLabel.append(notes);
  form.append(bookmarkLabel, stageLabel, noteLabel, element('button', 'Save'));
  form.onsubmit = async event => {
    event.preventDefault();
    const record = {vacancy_id: row.id, bookmarked: bookmark.checked, application_status: stage.value, notes: notes.value};
    try {
      await api('/rest/v1/pipeline?on_conflict=user_id,vacancy_id', {
        method: 'POST', headers: {Prefer: 'resolution=merge-duplicates,return=minimal'}, body: record});
      state.pipeline = state.pipeline.filter(item => item.vacancy_id !== row.id).concat(record);
      $('message').textContent = 'Saved.';
    } catch (error) { $('message').textContent = error.message; }
  };
  disclosure.append(form);
  card.append(disclosure);
  return card;
}

function render() {
  const {employers, vacancies, runs, pipeline} = state;
  const active = vacancies.filter(row => row.status === 'active');
  const cutoff = new Date(Date.now() - 86400000).toISOString();
  const discovered = active.filter(row => row.first_seen && row.first_seen >= cutoff);
  const audit = runs[0]?.audit ? JSON.parse(runs[0].audit) : null;
  $('metrics').replaceChildren(metric('Active vacancies', active.length), metric('New in the last 24 hours', discovered.length),
    metric('Latest Top 150 coverage', audit ? audit.top_successful + '/150' : '—'),
    metric('Latest run status', runs[0]?.status || 'No runs'));
  const term = $('search').value.trim().toLowerCase();
  const employerName = row => employers.find(item => item.id === row.employer_id)?.name || '';
  const result = $('results');
  if (view === 'coverage') {
    result.replaceChildren(...runs.filter(row => JSON.stringify(row).toLowerCase().includes(term)).map(coverageCard));
  } else if (view === 'employers') {
    result.replaceChildren(...employers.filter(row => row.name.toLowerCase().includes(term))
      .sort((a, b) => (a.rank || 999) - (b.rank || 999) || a.name.localeCompare(b.name)).map(employerCard));
  } else {
    const selected = {active, new: discovered, historical: vacancies.filter(row => row.status !== 'active'),
      pipeline: vacancies.filter(row => pipeline.some(item => item.vacancy_id === row.id))}[view];
    const sort = $('sort').value;
    result.replaceChildren(...selected.filter(row => [employerName(row), row.title, row.location, row.requisition]
      .join(' ').toLowerCase().includes(term)).sort((a,b) => sort === 'latest' ? b.last_changed.localeCompare(a.last_changed) :
      sort === 'name' ? a.title.localeCompare(b.title) : b.score - a.score).map(vacancyCard));
  }
  if (!result.children.length) result.append(element('p', 'No matching records.', 'panel'));
}

$('views').onclick = event => {
  if (!event.target.dataset.view) return;
  view = event.target.dataset.view;
  $('views').querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button === event.target)));
  render();
};
$('search').oninput = render;
$('sort').onchange = render;
$('signout').onclick = () => location.reload();
$('signin').onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  $('message').textContent = 'Signing in…';
  try {
    session = await api('/auth/v1/token?grant_type=password', {
      method: 'POST', body: {email: form.get('email'), password: form.get('password')}
    });
    await load();
    event.target.reset();
    $('signin').hidden = true;
    $('workspace').hidden = $('signout').hidden = false;
    $('message').textContent = '';
  } catch (error) {
    session = undefined;
    $('message').textContent = error.message;
  }
};
if (config?.url && config?.key && !config.key.startsWith('YOUR-')) $('signin').hidden = false;
else $('message').textContent = 'Dashboard connection has not been configured.';
