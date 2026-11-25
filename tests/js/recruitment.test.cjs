const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../../app/dashboard/static/recruitment.js'), 'utf8');

const defaults = () => ({name: 'Recruitment demo', company_name: 'BeSingularity', purpose: 'recruitment', questions: [], training_location: 'Demo room', training_date: '2026-11-27',
  session_one: '08:30', session_two: '13:30', clock_convention: '24h', contact_source: 'Consented demo list',
  timezone: 'Africa/Addis_Ababa', greeting: 'Hello, this is the recruitment assistant.', language: 'Amharic', voice: '', prompt: 'Explain full-time and part-time work and invite an interested recipient.'});

function boot({companyId = ''} = {}) {
  class Element {
    constructor(tag='div') { this.tagName = tag; this.children = []; this.value = ''; this.checked = false; this.disabled = false; this.dataset = {}; this.textContent = ''; this.hidden = false; }
    append(child) { this.children.push(child); }
    replaceChildren(...children) { this.children = children; }
    querySelector() { return this.button; }
    querySelectorAll() { return Object.values(this.elements || {}); }
    reset() { for (const value of Object.values(this.elements || {})) { value.value = ''; value.checked = false; } }
  }
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  get('company-workspace').dataset.companyId = companyId;
  for (const [id, names] of [
    ['demo-config', ['name', 'company_name', 'purpose', 'training_location', 'training_date', 'session_one', 'session_two', 'clock_convention', 'contact_source', 'prompt', 'greeting', 'language', 'voice']],
    ['demo-lead', ['name', 'phone_number', 'contact_source', 'contact_permission']],
  ]) {
    get(id).elements = Object.fromEntries(names.map(name => [name, new Element('input')]));
    get(id).button = new Element('button');
  }
  const requests = [];
  let campaign = null, leads = [], calls = [], failDial = false, confirmation = true, uuidCalls = 0;
  let interval, phoneStatus = 'ready';
  const context = vm.createContext({
    document: {getElementById: get, createElement: tag => new Element(tag),
      querySelectorAll: () => get('demo-leads').children.flatMap(label => label.children.filter(node => node.tagName === 'input'))},
    window: {confirm: () => confirmation, location: {reload() {}}},
    // Exercise the getRandomValues UUID fallback used on trusted HTTP LANs.
    crypto: {getRandomValues: array => { uuidCalls++; return webcrypto.getRandomValues(array); }},
    setInterval: callback => { interval = callback; },
    fetch: async (url, options) => {
      const body = options.body ? (options.headers['Content-Type'] === 'application/octet-stream' ? options.body : JSON.parse(options.body)) : undefined;
      requests.push({url, options, body});
      if (url === '/api/company-auth/session') return {ok: true, json: async () => ({company_id: companyId, company_name: 'Acme', must_change_password: false, csrf_token: 'FAKE_CSRF'})};
      if (companyId) {
        assert.ok(url.startsWith('/api/workspaces/' + companyId + '/recruitment'));
        assert.equal(options.headers['X-CSRF-Token'], 'FAKE_CSRF');
        assert.equal(options.headers.Authorization, undefined);
      } else assert.ok(options.headers.Authorization.startsWith('Bearer '));
      let data;
      if (url.endsWith('/connection')) data = {status: phoneStatus, provider: 'gemini_live', voices: ['Kore', 'Puck'], default_voice: 'Kore'};
      else if (url.endsWith('/defaults')) data = {config: defaults(), connection_configured: true, webhook_configured: false};
      else if (url.endsWith('/campaigns') && options.method === 'GET') data = {campaigns: campaign ? [campaign] : []};
      else if (url.endsWith('/campaigns') && options.method === 'POST') {
        campaign = {campaign_id: 'c1', revision: 1, config: body, assistant_id: null}; data = campaign;
      } else if (url.endsWith('/campaigns/c1') && options.method === 'PUT') {
        const {expected_revision, ...config} = body;
        assert.equal(expected_revision, campaign.revision);
        campaign = {...campaign, config, revision: campaign.revision + 1, assistant_id: null}; data = campaign;
      } else if (url.endsWith('/campaigns/c1') && options.method === 'GET') data = {campaign, leads, calls};
      else if (url.endsWith('/prepare')) { campaign = {...campaign, assistant_id: 'asst_demo', assistant_version: 1}; data = campaign; }
      else if (url.endsWith('/leads/import/preview')) data = {sheet: 'Sheet1', total: 2, valid: 1, rows: [
        {row: 2, name: '<Beza>', phone_number: '+251911222333', status: 'valid', message: 'Ready to import'},
        {row: 3, name: 'Invalid', phone_number: '', status: 'invalid', message: 'Invalid phone'},
      ]};
      else if (url.endsWith('/leads/import')) {
        for (const contact of body.contacts) leads.push({...contact, lead_id: 'imported', do_not_contact: false});
        data = {imported: body.contacts.length, skipped: 0};
      }
      else if (url.endsWith('/leads')) { leads.push({...body, lead_id: 'l1', do_not_contact: false}); data = {lead_id: 'l1'}; }
      else if (url.endsWith('/call')) { if (failDial) { failDial = false; throw Error('Lost browser response'); } data = {status: 'queued'}; }
      else throw Error('Unexpected request ' + url);
      return {ok: true, json: async () => JSON.parse(JSON.stringify(data))};
    },
  });
  vm.runInContext(source, context);
  const submit = id => get(id).onsubmit({preventDefault() {}});
  const tick = () => new Promise(resolve => setImmediate(resolve));
  return {get, requests, submit, tick,
    setPhoneStatus(value) { phoneStatus = value; },
    async login() { get('demo-token').value = 'FAKE_OPERATOR_TOKEN'; await submit('demo-access'); },
    async save() { submit('demo-config'); await tick(); },
    async prepare() { get('demo-prepare').onclick(); await tick(); },
    async addLead() {
      const form = get('demo-lead'); form.elements.name.value = 'Demo recipient';
      form.elements.phone_number.value = '+251900000001'; form.elements.contact_permission.checked = true;
      submit('demo-lead'); await tick();
    },
    async dial() { get('demo-call').onclick(); await tick(); },
    failNextDial() { failDial = true; },
    declineConfirmation() { confirmation = false; },
    uuidCalls: () => uuidCalls,
    setCalls(value) { calls = value; },
    poll: () => interval(),
  };
}

test('the editable campaign must be saved and prepared before a contact can be called', async () => {
  const page = boot(); await page.login();
  assert.equal(page.get('demo-token').value, '');
  assert.equal(page.get('demo-call').disabled, true);
  await page.save(); await page.prepare(); await page.addLead();
  assert.equal(page.get('demo-call').disabled, false);
  page.get('demo-config').elements.prompt.value += ' Speak slowly.';
  page.get('demo-config').oninput();
  assert.equal(page.get('demo-call').disabled, true);
  assert.equal(page.get('demo-prepare').disabled, true);
  await page.save();
  assert.equal(page.get('demo-call').disabled, true);
  await page.prepare();
  assert.equal(page.get('demo-call').disabled, false);
});

test('declining the real-call confirmation sends no call request', async () => {
  const page = boot(); await page.login(); await page.save(); await page.prepare(); await page.addLead();
  page.declineConfirmation(); await page.dial();
  assert.equal(page.requests.filter(item => item.url.endsWith('/call')).length, 0);
});

test('a lost browser response reuses the same intended-call UUID on the next attempt', async () => {
  const page = boot(); await page.login(); await page.save(); await page.prepare(); await page.addLead();
  page.failNextDial(); await page.dial(); await page.dial();
  const submissions = page.requests.filter(item => item.url.endsWith('/call'));
  assert.equal(submissions.length, 2);
  assert.equal(submissions[0].body.request_id, submissions[1].body.request_id);
  assert.match(submissions[0].body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(page.uuidCalls(), 1);
  assert.equal(submissions[0].body.confirmed, true);
});

test('polling refreshes the workspace without replacing an unsaved prompt', async () => {
  const page = boot(); await page.login(); await page.save(); await page.prepare();
  page.get('demo-config').elements.prompt.value = 'Unsaved prompt that the operator is editing now.';
  await page.poll();
  assert.equal(page.get('demo-config').elements.prompt.value, 'Unsaved prompt that the operator is editing now.');
});

test('results use the call snapshot, preserve callback wording and render summaries as text', async () => {
  const page = boot(); await page.login(); await page.save(); await page.prepare(); await page.addLead();
  const summary = '<img src=x onerror=alert(1)> needs a human answer';
  page.setCalls([{
    request_id: 'request-1', lead_id: 'l1', provider_call_id: 'call-1', status: 'completed',
    result_state: 'ready', outstanding: false, campaign_revision: 1, created_at: '2026-11-20T10:00:00Z',
    training: {training_location: 'Original room', training_date: '2026-11-27', session_one: 'Original first session', session_two: 'Original second session', clock_convention: 'ethiopian'},
    result: {outcome: 'callback_requested', interest: 'unknown', work_preference: 'not_asked', attendance_intent: 'unknown',
      preferred_session: 'second', callback_request: 'ነገ ከሰዓት በኋላ', do_not_contact: false,
      summary, unresolved_questions: ['Is there a training fee?']},
  }]);
  await page.poll();
  function all(node) { return [node, ...node.children.flatMap(all)]; }
  const nodes = all(page.get('demo-calls'));
  const values = nodes.map(node => node.textContent);
  assert.ok(values.includes('Original second session'));
  assert.ok(values.includes('ነገ ከሰዓት በኋላ'));
  assert.ok(values.includes(summary));
  assert.ok(values.includes('Review the requested time and arrange a callback.'));
  assert.ok(!nodes.some(node => node.tagName === 'img'));
});

 test('offline or busy phone blocks dialing without creating a submission', async () => {
  const page = boot(); await page.login(); await page.save(); await page.prepare(); await page.addLead();
  for (const status of ['offline', 'busy']) {
    page.setPhoneStatus(status); await page.poll();
    assert.equal(page.get('demo-call').disabled, true);
    assert.match(page.get('demo-call-reason').textContent, status === 'offline' ? /offline/ : /busy/);
    await page.dial();
  }
  assert.equal(page.requests.filter(item => item.url.endsWith('/call')).length, 0);
  page.setPhoneStatus('ready'); await page.poll();
  assert.equal(page.get('demo-call').disabled, false);
});

test('voice selection is saved and requires preparing the changed campaign', async () => {
  const page = boot(); await page.login(); await page.save(); await page.prepare(); await page.addLead();
  assert.equal(page.get('demo-call').disabled, false);
  const voice = page.get('demo-config').elements.voice;
  assert.ok(voice.children.some(option => option.value === 'Puck' && option.textContent === 'Puck — Male'));
  assert.ok(voice.children.some(option => option.value === 'Kore' && option.textContent === 'Kore — Female'));
  voice.value = 'Puck'; page.get('demo-config').oninput();
  assert.equal(page.get('demo-call').disabled, true);
  await page.save();
  assert.equal(page.get('demo-call').disabled, true);
  await page.prepare();
  assert.equal(page.get('demo-call').disabled, false);
  const saved = page.requests.filter(r => r.options.method === 'PUT').at(-1);
  assert.equal(saved.body.voice, 'Puck');
});

test('a completed call awaiting analysis permits a new confirmed call with a fresh UUID', async () => {
  const page = boot(); await page.login(); await page.save(); await page.prepare(); await page.addLead();
  await page.dial();
  page.setCalls([{
    request_id: 'old-request', lead_id: 'l1', provider_call_id: 'provider-old', status: 'completed',
    result_state: 'needs_review', outstanding: false, campaign_revision: 1, created_at: '2026-11-23T10:00:00Z',
    training: {training_location: 'Demo room', training_date: '2026-11-27', session_one: '08:30', session_two: '13:30', clock_convention: '24h'},
  }]);
  await page.poll();
  assert.equal(page.get('demo-call').disabled, false);
  assert.equal(page.requests.filter(r => r.url.endsWith('/call')).length, 1);
  await page.dial();
  const submissions = page.requests.filter(r => r.url.endsWith('/call'));
  assert.equal(submissions.length, 2);
  assert.notEqual(submissions[0].body.request_id, submissions[1].body.request_id);
});

test('Excel import previews numbers, requires permission and never dials', async () => {
  const page = boot(); await page.login(); await page.save();
  page.get('demo-import-file').files = [{name: 'people.xlsx', size: 123}];
  await page.submit('demo-import'); await page.tick();
  assert.equal(page.get('demo-import-confirm').disabled, true);
  const table = page.get('demo-import-results').children[1];
  assert.equal(table.children[1].children[1].textContent, '<Beza>');
  page.get('demo-import-permission').checked = true;
  page.get('demo-import-permission').onchange();
  assert.equal(page.get('demo-import-confirm').disabled, false);
  await page.get('demo-import-confirm').onclick(); await page.tick();
  const imported = page.requests.find(r => r.url.endsWith('/leads/import'));
  assert.equal(imported.body.contacts.length, 1);
  assert.equal(imported.body.contacts[0].phone_number, '+251911222333');
  assert.equal(imported.body.contact_permission, true);
  assert.equal(page.requests.filter(r => r.url.endsWith('/call')).length, 0);
  assert.equal(page.get('demo-import-confirm').disabled, true);
});

test('changing the spreadsheet clears the previous import preview', async () => {
  const page = boot(); await page.login(); await page.save();
  page.get('demo-import-file').files = [{name: 'people.xlsx', size: 123}];
  await page.submit('demo-import'); await page.tick();
  page.get('demo-import-permission').checked = true;
  page.get('demo-import-permission').onchange();
  assert.equal(page.get('demo-import-confirm').disabled, false);
  page.get('demo-import-file').onchange();
  assert.equal(page.get('demo-import-confirm').disabled, true);
  assert.equal(page.get('demo-import-results').children.length, 0);
});

test('company workspace uses scoped APIs and session CSRF without operator credentials', async () => {
  const page = boot({companyId: 'company_a'});
  await page.tick(); await page.tick();
  assert.equal(page.get('workspace-title').textContent, 'Acme · Campaigns');
  await page.save();
  const saved = page.requests.find(r => r.url.endsWith('/campaigns') && r.options.method === 'POST');
  assert.equal(saved.url, '/api/workspaces/company_a/recruitment/campaigns');
  assert.equal(saved.options.headers.Authorization, undefined);
  assert.equal(saved.options.headers['X-CSRF-Token'], 'FAKE_CSRF');
});

test('company questionnaire sends editable questions and renders answer evidence', async () => {
  const page = boot(); await page.login();
  const form = page.get('demo-config');
  form.elements.company_name.value = 'Acme'; form.elements.purpose.value = 'questionnaire';
  page.get('campaign-questions').value = 'What went well?\nWhat should we improve?';
  page.get('generic-contact-source').value = 'Customer opt-in';
  await page.save();
  const body = page.requests.find(r => r.url.endsWith('/campaigns') && r.options.method === 'POST').body;
  assert.deepEqual(body.questions, ['What went well?', 'What should we improve?']);
  assert.equal(body.contact_source, 'Customer opt-in');
  assert.equal(page.get('recruitment-settings').hidden, true);
  page.setCalls([{
    request_id: 'q-call', lead_id: 'l1', status: 'completed', outstanding: false,
    purpose: 'questionnaire', company_name: 'Acme', questions: body.questions,
    result_state: 'ready', campaign_revision: 1, created_at: '2026-11-25T10:00:00Z',
    result: {conversation_complete: false, summary: 'One answer captured.', callback_request: '', do_not_contact: false,
      answers: {q1: {answer: 'Delivery', evidence: 'Delivery was good', state: 'stated'}, q2: {answer: '', evidence: '', state: 'not_asked'}}},
  }]);
  await page.poll();
  const values = [];
  function visit(node) { values.push(node.textContent); for (const child of node.children) visit(child); }
  visit(page.get('demo-calls'));
  assert.ok(values.includes('Evidence: Delivery was good'));
  assert.ok(values.includes('not_asked: No answer recorded'));
});
