/* The console keeps credentials in memory and all display values in text nodes. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const configForm = $('demo-config');
  const leadForm = $('demo-lead');
  const importForm = $('demo-import');
  const companyId = $('company-workspace')?.dataset.companyId || '';
  const apiBase = companyId ? '/api/workspaces/' + encodeURIComponent(companyId) + '/recruitment' : '/api/recruitment';
  let csrf = '';
  let importPreview = null;
  const fields = ['name', 'company_name', 'purpose', 'training_location', 'training_date', 'session_one', 'session_two', 'clock_convention', 'contact_source', 'prompt', 'greeting', 'language', 'voice'];
  let connection = null;
  let token = '', defaults = null, current = null, workspace = null, selectedLead = '', busy = false, polling = false;
  let sequence = 0;
  const pendingRequests = new Map();

  function notice(message, error = false) {
    $('demo-notice').textContent = message;
    $('demo-notice').dataset.error = String(error);
    $('demo-notice').hidden = false;
  }
  async function api(path, method = 'GET', body, raw = false) {
    const response = await fetch(apiBase + path, {
      method, cache: 'no-store', credentials: 'same-origin', headers: {...(companyId ? {'X-CSRF-Token': csrf} : {Authorization: 'Bearer ' + token}), 'Content-Type': raw ? 'application/octet-stream' : 'application/json'},
      body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(result.detail) ? result.detail.map(item => item.msg).join('; ') : result.detail;
      throw Error(detail || 'The request failed. Refresh before trying another call.');
    }
    return result;
  }
  async function refreshConnection() {
    try {
      connection = await api('/connection');
      if (document.activeElement !== configForm.elements.voice) voiceOptions(configForm.elements.voice.value);
      const labels = {ready: 'Phone connected and idle — ready to call.', busy: 'Phone is busy. Wait for the current call to end.', offline: 'No demo phone connected. Check USB and arm TeleExpert.'};
      $('demo-connection').textContent = labels[connection.status] || 'Phone status is unavailable.';
    } catch (error) {
      connection = null;
      $('demo-connection').textContent = 'Cannot reach TeleExpert: ' + error.message;
    }
    controls();
  }
  $('demo-refresh-connection').onclick = () => refreshConnection();
  // Gemini's documented synthetic voice labels; keep the wire value unchanged.
  function voiceLabel(voice) {
    const labels = {Kore: 'Female', Aoede: 'Female', Puck: 'Male', Charon: 'Male', Fenrir: 'Male'};
    return labels[voice] ? `${voice} — ${labels[voice]}` : voice;
  }
  function voiceOptions(selected = '') {
    const select = configForm.elements.voice;
    select.replaceChildren();
    const option = text(select, 'option', connection?.default_voice ? `Server default (${voiceLabel(connection.default_voice)})` : 'Server default');
    option.value = '';
    const voices = connection?.voices || [];
    for (const voice of voices) {
      const node = text(select, 'option', voiceLabel(voice)); node.value = voice;
    }
    if (selected && !voices.includes(selected)) {
      const node = text(select, 'option', `${voiceLabel(selected)} (not currently enabled)`); node.value = selected;
    }
    select.value = selected;
  }
  function configValue() {
    const config = Object.fromEntries(fields.map(key => [key, configForm.elements[key].value]));
    config.timezone = 'Africa/Addis_Ababa';
    config.questions = $('campaign-questions').value.split('\n').map(q => q.trim()).filter(Boolean);
    if (config.purpose === 'questionnaire') config.contact_source = $('generic-contact-source').value;
    return config;
  }
  function editConfig(config) {
    voiceOptions(config.voice || '');
    for (const key of fields) configForm.elements[key].value = config[key] ?? defaults?.config?.[key] ?? '';
    leadForm.elements.contact_source.value = config.contact_source;
    $('demo-import-source').value = config.contact_source;
    $('generic-contact-source').value = config.contact_source;
    $('campaign-questions').value = (config.questions || []).join('\n');
  }
  function dirty() {
    return !current?.config || JSON.stringify(configValue()) !== JSON.stringify({...Object.fromEntries([...fields, 'timezone'].map(key => [key, current.config[key] ?? defaults?.config?.[key] ?? ''])), questions: current.config.questions || []});
  }
  function controls() {
    const questionnaire = configForm.elements.purpose.value === 'questionnaire';
    $('recruitment-settings').hidden = questionnaire;
    $('generic-source-label').hidden = !questionnaire;
    $('questionnaire-editor').hidden = !questionnaire;
    $('questionnaire-help').hidden = !questionnaire;
    $('demo-prepare').disabled = busy || !current || dirty();
    const lead = workspace?.leads.find(item => item.lead_id === selectedLead);
    const outstanding = workspace?.calls.some(item => item.outstanding && item.lead_id === selectedLead);
    const reason = busy ? 'Finishing the current action. Please wait.' :
      connection?.status !== 'ready' ? (connection?.status === 'busy' ? 'The gateway phone is busy. Wait for the current call to end.' : 'The gateway phone is offline or unreachable. Check USB, open TeleExpert and arm the gateway, then check the phone connection.') :
      !current ? 'Select or save a campaign first.' :
      current.archived ? 'This campaign is archived. Select an active campaign to place calls.' :
      dirty() ? 'Campaign changes are unsaved. Save the campaign and prepare its assistant again.' :
      !current.assistant_id ? 'Prepare the assistant for this campaign revision.' :
      !lead ? 'Select a contact below the contact form.' :
      lead.do_not_contact ? 'This contact requested no further calls.' :
      outstanding ? 'This contact already has an outstanding call. Resolve it before another call.' : '';
    $('demo-call').disabled = Boolean(reason);
    if ($('demo-call-reason')) $('demo-call-reason').textContent = reason || 'Ready. Review the details and confirm one real call.';
    configForm.querySelector('button').disabled = busy;
    leadForm.querySelector('button').disabled = busy || !current;
    $('demo-import-preview').disabled = busy || !current;
    $('demo-import-confirm').disabled = busy || !current || !importPreview?.valid ||
      importPreview.campaignId !== current.campaign_id || !$('demo-import-permission').checked ||
      !$('demo-import-source').value.trim();
    $('demo-campaign').disabled = busy;
    $('demo-new').disabled = busy;
    for (const form of [configForm, leadForm, importForm]) for (const input of form.querySelectorAll('input, select, textarea')) input.disabled = busy;
    $('demo-assistant').textContent = current?.assistant_id ? `Assistant prepared for revision ${current.revision}` : 'Save and prepare this campaign before calling.';
    for (const input of document.querySelectorAll('#demo-leads input')) input.disabled = busy || input.dataset.blocked === 'true';
  }
  async function action(work) {
    if (busy) return;
    busy = true; sequence++; controls();
    try { await work(); }
    catch (error) { notice(error.message, true); }
    finally { busy = false; controls(); }
  }
  function text(parent, tag, value, className) {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    parent.append(node);
    return node;
  }
  function render() {
    const leads = $('demo-leads'); leads.replaceChildren();
    for (const lead of workspace?.leads || []) {
      const label = document.createElement('label'); label.className = 'demo-check demo-contact';
      const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'demo-contact';
      radio.dataset.blocked = String(lead.do_not_contact);
      radio.checked = selectedLead === lead.lead_id;
      radio.onchange = () => { selectedLead = lead.lead_id; controls(); };
      label.append(radio);
      text(label, 'span', `${lead.name || 'Contact'} · ${lead.phone_number}${lead.do_not_contact ? ' · Do not contact' : ''}`);
      leads.append(label);
    }
    const calls = $('demo-calls'); calls.replaceChildren();
    for (const call of workspace?.calls || []) {
      const lead = workspace.leads.find(item => item.lead_id === call.lead_id);
      const card = text(calls, 'article', '', 'demo-call-card');
      text(card, 'h3', `${lead?.name || 'Contact'} · ${call.status.replaceAll('_', ' ')}`);
      text(card, 'p', `Campaign revision ${call.campaign_revision} · ${new Date(call.created_at).toLocaleString()}`);
      if (call.purpose === 'questionnaire') text(card, 'p', `${call.company_name} · ${call.questions.length} questions`);
      else text(card, 'p', `Training: ${call.training.training_location} · ${call.training.training_date} · ${call.training.session_one} / ${call.training.session_two} (${call.training.clock_convention})`);
      if (call.error) text(card, 'p', call.error);
      if (call.result_state === 'ready' && call.purpose === 'questionnaire') {
        const result = call.result;
        text(card, 'p', result.conversation_complete ? 'Questionnaire addressed' : 'Conversation incomplete — review unanswered questions');
        text(card, 'p', result.summary);
        const list = text(card, 'dl', '');
        for (const [index, question] of (call.questions || []).entries()) {
          const answer = result.answers['q' + (index + 1)];
          text(list, 'dt', question);
          text(list, 'dd', `${answer.state}: ${answer.answer || 'No answer recorded'}`);
          if (answer.evidence) text(list, 'dd', 'Evidence: ' + answer.evidence);
        }
        if (result.callback_request) text(card, 'p', 'Callback requested: ' + result.callback_request);
        if (result.do_not_contact) text(card, 'p', 'Do not contact again.');
      } else if (call.result_state === 'ready') {
        const result = call.result;
        const list = text(card, 'dl', '');
        for (const [label, value] of [
          ['Outcome', result.outcome.replaceAll('_', ' ')], ['Interest', result.interest],
          ['Work preference', result.work_preference.replaceAll('_', ' ')], ['Attendance intent', result.attendance_intent],
          ['Preferred session', result.preferred_session === 'first' ? call.training.session_one : result.preferred_session === 'second' ? call.training.session_two : 'Unspecified'],
          ['Callback requested', result.callback_request || 'None recorded'], ['Summary', result.summary],
          ['Questions for a human', result.unresolved_questions.join('\n') || 'None recorded'],
          ['Next action', result.do_not_contact ? 'Do not call again.' : result.outcome === 'callback_requested' ? 'Review the requested time and arrange a callback.' : result.unresolved_questions.length ? 'A person should answer the unresolved questions.' : result.attendance_intent === 'yes' ? 'Review attendance intent; registration is not confirmed.' : 'Review the conversation outcome.'],
        ]) { text(list, 'dt', label); text(list, 'dd', value); }
      } else text(card, 'p', `Result: ${call.result_state.replaceAll('_', ' ')}`);
      function button(label, operation) {
        const node = text(card, 'button', label, 'ghost-button'); node.type = 'button';
        node.onclick = () => action(async () => { await operation(); await loadWorkspace(); });
      }
      if (call.provider_call_id) {
        if (call.status === 'completed' && call.result_state === 'needs_review') {
          button('Retry analysis (no call)', () => api(`/calls/${call.request_id}/analysis/retry`, 'POST'));
        }
        button('Refresh result', () => api(`/calls/${call.request_id}/sync`, 'POST'));
        if (!['completed', 'failed', 'cancelled', 'rejected'].includes(call.status)) {
          button('Cancel call', async () => {
            if (window.confirm('Cancel this existing call? It will not be automatically redialed.')) await api(`/calls/${call.request_id}/cancel`, 'POST');
          });
        }
      } else if (call.outstanding) {
        button('Check the same submission', async () => {
          if (window.confirm('Reconcile this same intended call with the same request ID? This does not create a new intention.')) {
            await api(`/calls/${call.request_id}/resume`, 'POST', {request_id: call.request_id, confirmed: true});
          }
        });
      }
    }
    if (!workspace?.calls.length) text(calls, 'p', 'No calls placed for this campaign.');
    controls();
  }
  async function loadCampaigns(selected = current?.campaign_id) {
    const data = await api('/campaigns' + ($('demo-show-archived').checked ? '?include_archived=true' : ''));
    $('demo-campaign').replaceChildren();
    const empty = text($('demo-campaign'), 'option', 'New campaign'); empty.value = '';
    for (const item of data.campaigns) {
      const option = text($('demo-campaign'), 'option', `${item.config.name}${item.archived ? ' (archived)' : ''} · revision ${item.revision}`); option.value = item.campaign_id;
    }
    $('demo-campaign').value = selected || '';
  }
  async function loadWorkspace(edit = false) {
    if (!current) return;
    const id = current.campaign_id, request = ++sequence;
    const result = await api(`/campaigns/${id}`);
    if (request !== sequence || id !== current?.campaign_id) return;
    workspace = result; current = result.campaign;
    if (edit) editConfig(current.config);
    if (!workspace.leads.some(item => item.lead_id === selectedLead)) selectedLead = '';
    render();
  }
  $('demo-access').onsubmit = async event => {
    event.preventDefault(); token = $('demo-token').value;
    try {
      defaults = await api('/defaults');
      $('demo-token').value = '';
      $('demo-access').hidden = true; $('demo-workspace').hidden = false;
      await refreshConnection();
      editConfig(defaults.config); await loadCampaigns(); controls();
      notice('Workspace open. Review the sample settings and save a campaign.');
    } catch (error) { token = ''; notice(error.message, true); }
  };
  $('demo-campaign').onchange = () => action(async () => {
    clearImport();
    selectedLead = ''; workspace = null;
    current = $('demo-campaign').value ? {campaign_id: $('demo-campaign').value} : null;
    if (current) await loadWorkspace(true); else { editConfig(defaults.config); render(); }
  });
  $('demo-show-archived').onchange = () => action(async () => {
    clearImport();
    if (current?.archived && !$('demo-show-archived').checked) {
      current = null; workspace = null; selectedLead = '';
      editConfig(defaults.config); render();
    }
    await loadCampaigns();
  });
  $('demo-new').onclick = () => {
    if (busy) return;
    clearImport();
    sequence++; current = null; workspace = null; selectedLead = ''; $('demo-campaign').value = '';
    editConfig(defaults.config); render();
  };
  $('demo-lock').onclick = () => window.location.reload();
  $('campaign-questions').oninput = controls;
  $('generic-contact-source').oninput = controls;
  configForm.oninput = controls;
  configForm.onsubmit = event => {
    event.preventDefault();
    action(async () => {
      const config = configValue();
      current = current ? await api(`/campaigns/${current.campaign_id}`, 'PUT', {...config, expected_revision: current.revision}) : await api('/campaigns', 'POST', config);
      await loadCampaigns(); await loadWorkspace(true);
      notice('Campaign saved. Prepare the assistant for this revision before calling.');
    });
  };
  $('demo-prepare').onclick = () => action(async () => {
    current = await api(`/campaigns/${current.campaign_id}/prepare`, 'POST');
    await loadWorkspace(); notice('Assistant prepared and checked. No phone call has been placed.');
  });
  function clearImport() {
    importPreview = null;
    $('demo-import-results').replaceChildren();
    $('demo-import-permission').checked = false;
    controls();
  }
  $('demo-import-file').onchange = clearImport;
  $('demo-import-permission').onchange = controls;
  $('demo-import-source').oninput = controls;
  importForm.onsubmit = event => {
    event.preventDefault();
    action(async () => {
      clearImport();
      if (!current) throw Error('Select or save a campaign first.');
      const file = $('demo-import-file').files?.[0];
      if (!file || !file.name.toLowerCase().endsWith('.xlsx')) throw Error('Choose an Excel .xlsx file.');
      if (file.size > 2 * 1024 * 1024) throw Error('Excel file must be 2 MB or smaller.');
      const result = await api(`/campaigns/${current.campaign_id}/leads/import/preview`, 'POST', file, true);
      importPreview = {...result, campaignId: current.campaign_id};
      const target = $('demo-import-results');
      text(target, 'p', `${result.sheet}: ${result.valid} ready to import; ${result.total - result.valid} skipped or invalid.`);
      const table = text(target, 'table', '');
      const header = text(table, 'tr', '');
      for (const value of ['Row', 'Name', 'Phone number', 'Status']) text(header, 'th', value);
      for (const row of result.rows) {
        const line = text(table, 'tr', '');
        for (const value of [row.row, row.name, row.phone_number, row.message]) text(line, 'td', String(value));
      }
      notice('Review the normalized numbers, confirm permission, then import valid contacts.');
    });
  };
  $('demo-import-confirm').onclick = () => action(async () => {
    if (!current || !importPreview?.valid || importPreview.campaignId !== current.campaign_id ||
        !$('demo-import-permission').checked) throw Error('Preview a spreadsheet and confirm permission first.');
    const result = await api(`/campaigns/${current.campaign_id}/leads/import`, 'POST', {
      contacts: importPreview.rows.filter(row => row.status === 'valid').map(({name, phone_number}) => ({name, phone_number})),
      contact_source: $('demo-import-source').value.trim(), contact_permission: true,
    });
    clearImport(); $('demo-import-file').value = '';
    await loadWorkspace();
    notice(`${result.imported} contacts imported; ${result.skipped} duplicates or do-not-call contacts skipped. No calls placed.`);
  });
  leadForm.onsubmit = event => {
    event.preventDefault();
    action(async () => {
      const result = await api(`/campaigns/${current.campaign_id}/leads`, 'POST', {
        name: leadForm.elements.name.value, phone_number: leadForm.elements.phone_number.value,
        contact_source: leadForm.elements.contact_source.value, contact_permission: leadForm.elements.contact_permission.checked,
      });
      selectedLead = result.lead_id; leadForm.reset(); leadForm.elements.contact_source.value = current.config.contact_source;
      await loadWorkspace(); notice('Contact added. Select and review before calling.');
    });
  };
  $('demo-call').onclick = () => action(async () => {
    const lead = workspace.leads.find(item => item.lead_id === selectedLead);
    await refreshConnection();
    if (connection?.status !== 'ready') throw Error('The demo phone is not ready. Check the connection before calling.');
    if (!lead || dirty() || !current.assistant_id) throw Error('Save and prepare the campaign, then select a contact.');
    const reviewNeeded = workspace.calls.some(item => item.lead_id === selectedLead && item.result_state === 'needs_review');
    const reviewNote = reviewNeeded ? '\n\nA previous call has no verified analysis. Review it and confirm the contact has not asked you to stop calling.' : '';
    const details = current.config.purpose === 'questionnaire' ? `${current.config.company_name}\n${current.config.questions.length} configured questions` : `${current.config.training_location}\n${current.config.training_date}\n${current.config.session_one} / ${current.config.session_two}`;
    if (!window.confirm(`Place a REAL call to ${lead.name || 'this contact'} (${lead.phone_number})?\n\n${details}\n\nConfirm these details and permission to call.${reviewNote}`)) return;
    const uuid = () => {
      if (crypto.randomUUID) return crypto.randomUUID();
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
      const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    };
    const id = pendingRequests.get(lead.lead_id) || uuid();
    pendingRequests.set(lead.lead_id, id);
    await api(`/campaigns/${current.campaign_id}/leads/${lead.lead_id}/call`, 'POST', {request_id: id, confirmed: true});
    pendingRequests.delete(lead.lead_id);
    await loadWorkspace(); notice('Submission recorded. Follow the status below; do not create another call while it is outstanding.');
  });
  setInterval(async () => {
    if ((!token && !companyId) || !defaults || busy || polling) return;
    const campaignId = current?.campaign_id;
    polling = true;
    try {
      await refreshConnection();
      if (!current) return;
      for (const call of workspace?.calls || []) {
        if (busy || current?.campaign_id !== campaignId) return;
        if (call.provider_call_id && call.outstanding) await api(`/calls/${call.request_id}/sync`, 'POST');
      }
      if (!busy && current?.campaign_id === campaignId) await loadWorkspace();
    } catch (error) { notice('Live status could not refresh: ' + error.message, true); }
    finally { polling = false; }
  }, 5000);
  if (companyId) {
    (async () => {
      try {
        const response = await fetch('/api/company-auth/session', {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok) { window.location.replace('/login'); return; }
        const session = await response.json();
        if (session.must_change_password) { window.location.replace('/login'); return; }
        if (session.company_id !== companyId) { notice('This workspace belongs to a different company. Sign in with that company account.', true); return; }
        csrf = session.csrf_token;
        defaults = await api('/defaults');
        $('workspace-title').textContent = session.company_name + ' · Campaigns';
        document.title = session.company_name + ' · ProofCall';
        $('demo-workspace').hidden = false;
        await refreshConnection(); editConfig(defaults.config); await loadCampaigns(); controls();
      } catch (error) { notice(error.message, true); }
    })();
    $('company-logout').onclick = async () => {
      const response = await fetch('/api/company-auth/logout', {method: 'POST', credentials: 'same-origin', headers: {'X-CSRF-Token': csrf}});
      if (response.ok) window.location.replace('/login');
      else notice('Sign out failed. Reload and try again.', true);
    };
  }
})();
