// Execute the production inline script with a small DOM boundary and deferred
// fetch responses. No packages, browser installation or network are required.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../../app/dashboard/templates/round.html'), 'utf8');
const person = (worker_id, extra={}) => ({worker_id, name: worker_id, phone_number: '+251900000001', selected: false, ...extra});
const thread = (people, extra={}) => ({company_id: 'co_test', people, status: 'calling', round: {batch_id: 'b1'}, ...extra});
const response = (body, ok=true) => ({ok, status: ok ? 200 : 400, text: async () => JSON.stringify(body)});
const deferred = () => {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve};};

function boot(initial) {
  const elements = new Map();
  const listeners = {};
  let checkboxes = [];
  function element(id) {
    if (!elements.has(id)) {
      let html = '';
      elements.set(id, {
        hidden: false, disabled: false, value: '1', textContent: '',
        get innerHTML() { return html; },
        set innerHTML(value) {
          html = value;
          if (id === 'roster-list') {
            checkboxes = [...value.matchAll(/<input type="checkbox"([^>]*)>/g)].map(([, attrs]) => ({
              dataset: {worker: attrs.match(/data-worker="([^"]*)"/)[1], locked: attrs.match(/data-locked="([^"]*)"/)[1]},
              checked: /\schecked(?:\s|$)/.test(attrs),
              disabled: /\sdisabled(?:\s|$)/.test(attrs),
              matches: selector => selector === '[data-worker]',
            }));
          }
        },
      });
    }
    return elements.get(id);
  }
  const document = {
    getElementById: element,
    querySelectorAll: selector => selector === '[data-worker]' ? checkboxes : [],
    addEventListener: (name, callback) => {listeners[name] = callback;},
  };
  const requests = [];
  const context = vm.createContext({
    document, FormData: class {append() {}}, setInterval() {},
    fetch: (url, options) => {
      const pending = deferred(); requests.push({url, options, ...pending}); return pending.promise;
    },
  });
  const script = template.split('<script>')[1].split('</script>')[0]
    .replace('{{ thread.company_id|tojson }}', JSON.stringify(initial.company_id))
    .replace('{{ thread|tojson }}', JSON.stringify(initial));
  vm.runInContext(script, context);
  return {
    requests, element,
    run: code => vm.runInContext(code, context),
    box: id => checkboxes.find(box => box.dataset.worker === id),
    change(id, checked) {
      const box = checkboxes.find(box => box.dataset.worker === id);
      assert.ok(!box.disabled, 'a user cannot change a disabled checkbox');
      box.checked = checked; listeners.change({target: box});
    },
  };
}

test('polling preserves new checks and unchecks of server-selected contacts', async () => {
  const initial = thread([person('active', {call_status: 'dialing'}), person('a'), person('b', {selected: true})]);
  const page = boot(initial);
  page.change('a', true); page.change('b', false);
  const refresh = page.run('refresh()');
  page.requests[0].resolve(response(initial)); await refresh;
  assert.equal(page.box('a').checked, true);
  assert.equal(page.box('b').checked, false);
  assert.equal(page.box('active').disabled, true);
  assert.equal(page.element('selection-count').textContent, 1);
});

test('bulk and random selections survive roster repainting', () => {
  const page = boot(thread([person('a'), person('b'), person('c')]));
  page.run('setAll(true); paint()');
  assert.equal(page.run('chosen().length'), 3);
  page.run('setAll(false); paint()');
  assert.equal(page.run('chosen().length'), 0);
  page.element('random-size').value = '2';
  page.run('pickRandom(); paint()');
  assert.equal(page.run('chosen().length'), 2);
});

test('editing a reviewed selection requires review again before confirming calls', () => {
  const page = boot(thread([person('a', {selected: true})], {status: 'selected'}));
  assert.equal(page.element('confirm-calls').disabled, false);
  page.change('a', false);
  assert.equal(page.element('confirm-calls').disabled, true);
  page.change('a', true);
  assert.equal(page.element('confirm-calls').disabled, false);
});

test('removed, newly excluded and active contacts are deselected with an explanation', async () => {
  const page = boot(thread([person('removed'), person('excluded'), person('active'), person('keep')]));
  page.run('setAll(true)');
  const refresh = page.run('refresh()');
  page.requests[0].resolve(response(thread([
    person('excluded', {excluded: true}), person('active', {call_status: 'dialing'}), person('keep'),
  ])));
  await refresh;
  assert.equal(page.box('removed'), undefined);
  assert.equal(page.box('excluded').disabled, true);
  assert.equal(page.box('excluded').checked, false);
  assert.equal(page.box('active').checked, false);
  assert.equal(page.box('keep').checked, true);
  assert.match(page.element('notice').textContent, /3 selected people are no longer available/);
  assert.equal(page.element('notice').hidden, false);
});

test('a stale poll cannot overwrite a later successful selection', async () => {
  const initial = thread([person('a')]);
  const page = boot(initial);
  page.change('a', true);
  const poll = page.run('refresh()');
  const action = page.run("action('/api/companies/co_test/rounds/select', {worker_ids: ['a']})");
  assert.equal(page.box('a').disabled, true);
  const selected = thread([person('a', {selected: true})], {status: 'selected'});
  page.requests[1].resolve(response({thread: selected})); await action;
  page.requests[0].resolve(response(initial)); await poll;
  assert.equal(page.run('state.status'), 'selected');
  assert.equal(page.box('a').checked, true);
  assert.equal(page.box('a').disabled, false);
  assert.equal(page.run('selectionEdits.size'), 0);
});

test('out-of-order polling responses do not rewind the roster', async () => {
  const page = boot(thread([person('a')]));
  const first = page.run('refresh()');
  const second = page.run('refresh()');
  page.requests[1].resolve(response(thread([person('new')]))); await second;
  page.requests[0].resolve(response(thread([person('old')]))); await first;
  assert.ok(page.box('new'));
  assert.equal(page.box('old'), undefined);
});

test('failed actions preserve local choices and invalidate old poll errors', async () => {
  const page = boot(thread([person('a')]));
  page.change('a', true);
  const poll = page.run('refresh()');
  const action = page.run("action('/api/companies/co_test/rounds/select', {worker_ids: ['a']})");
  page.requests[1].resolve(response({detail: 'Please retry'}, false)); await action;
  page.requests[0].resolve(response({detail: 'Old failure'}, false)); await poll;
  assert.equal(page.box('a').checked, true);
  assert.equal(page.box('a').disabled, false);
  assert.equal(page.element('notice').textContent, 'Please retry');
  assert.equal(page.run('missed'), 0);
});

test('polls do not begin during mutations and newer user changes survive an in-flight poll', async () => {
  const page = boot(thread([person('a'), person('b')]));
  const poll = page.run('refresh()');
  page.change('b', true);
  page.requests[0].resolve(response(thread([person('a'), person('b')]))); await poll;
  assert.equal(page.box('b').checked, true);
  const action = page.run("action('/api/companies/co_test/rounds/select', {worker_ids: ['b']})");
  await page.run('refresh()');
  assert.equal(page.requests.length, 2);
  page.requests[1].resolve(response({thread: thread([person('a'), person('b', {selected: true})])}));
  await action;
});
