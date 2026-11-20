const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../../app/dashboard/static/recruitment.js'), 'utf8');

const defaults = () => ({name: 'Recruitment demo', training_location: 'Demo room', training_date: '2026-11-27',
  session_one: '08:30', session_two: '13:30', clock_convention: '24h', contact_source: 'Consented demo list',
  timezone: 'Africa/Addis_Ababa', prompt: 'Explain full-time and part-time work and invite an interested recipient.'});

function boot() {
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
  for (const [id, names] of [
    ['demo-config', ['name', 'training_location', 'training_date', 'session_one', 'session_two', 'clock_convention', 'contact_source', 'prompt']],
    ['demo-lead', ['name', 'phone_number', 'contact_source', 'contact_permission']],
  ]) {
    get(id).elements = Object.fromEntries(names.map(name => [name, new Element('input')]));
    get(id).button = new Element('button');
  }
  const requests = [];
  let campaign = null, leads = [], calls = [], failDial = false, confirmation = true, uuidCalls = 0;
  let interval;
  const context = vm.createContext({
    document: {getElementById: get, createElement: tag => new Element(tag),
      querySelectorAll: () => get('demo-leads').children.flatMap(label => label.children.filter(node => node.tagName === 'input'))},
    window: {confirm: () => confirmation, location: {reload() {}}},
    // Exercise the getRandomValues UUID fallback used on trusted HTTP LANs.
    crypto: {getRandomValues: array => { uuidCalls++; return webcrypto.getRandomValues(array); }},
    setInterval: callback => { interval = callback; },
    fetch: async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({url, options, body});
      assert.ok(options.headers.Authorization.startsWith('Bearer '));
      let data;
      if (url.endsWith('/defaults')) data = {config: defaults(), connection_configured: true, webhook_configured: false};
      else if (url.endsWith('/campaigns') && options.method === 'GET') data = {campaigns: campaign ? [campaign] : []};
      else if (url.endsWith('/campaigns') && options.method === 'POST') {
        campaign = {campaign_id: 'c1', revision: 1, config: body, assistant_id: null}; data = campaign;
      } else if (url.endsWith('/campaigns/c1') && options.method === 'PUT') {
        const {expected_revision, ...config} = body;
        assert.equal(expected_revision, campaign.revision);
        campaign = {...campaign, config, revision: campaign.revision + 1, assistant_id: null}; data = campaign;
      } else if (url.endsWith('/campaigns/c1') && options.method === 'GET') data = {campaign, leads, calls};
      else if (url.endsWith('/prepare')) { campaign = {...campaign, assistant_id: 'asst_demo', assistant_version: 1}; data = campaign; }
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
