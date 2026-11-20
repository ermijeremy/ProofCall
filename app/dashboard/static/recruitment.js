/* The console keeps credentials in memory and all display values in text nodes. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const configForm = $('demo-config');
  const leadForm = $('demo-lead');
  const fields = ['name', 'training_location', 'training_date', 'session_one', 'session_two', 'clock_convention', 'contact_source', 'prompt'];
  let token = '', defaults = null, current = null, workspace = null, selectedLead = '', busy = false, polling = false;
  let sequence = 0;
  const pendingRequests = new Map();

  function notice(message, error = false) {
    $('demo-notice').textContent = message;
    $('demo-notice').dataset.error = String(error);
    $('demo-notice').hidden = false;
  }
  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/recruitment' + path, {
      method, cache: 'no-store', headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(result.detail) ? result.detail.map(item => item.msg).join('; ') : result.detail;
      throw Error(detail || 'The request failed. Refresh before trying another call.');
    }
    return result;
  }
  function configValue() {
    const config = Object.fromEntries(fields.map(key => [key, configForm.elements[key].value]));
    config.timezone = 'Africa/Addis_Ababa';
    return config;
  }
  function editConfig(config) {
    for (const key of fields) configForm.elements[key].value = config[key];
    leadForm.elements.contact_source.value = config.contact_source;
  }
  function dirty() {
    return !current?.config || JSON.stringify(configValue()) !== JSON.stringify(Object.fromEntries([...fields, 'timezone'].map(key => [key, current.config[key]])));
  }
  function controls() {
    $('demo-prepare').disabled = busy || !current || dirty();
    const lead = workspace?.leads.find(item => item.lead_id === selectedLead);
    const outstanding = workspace?.calls.some(item => item.outstanding && item.lead_id === selectedLead);
    $('demo-call').disabled = busy || dirty() || !current?.assistant_id || !lead || lead.do_not_contact || outstanding;
    configForm.querySelector('button').disabled = busy;
    leadForm.querySelector('button').disabled = busy || !current;
    $('demo-campaign').disabled = busy;
    $('demo-new').disabled = busy;
    for (const form of [configForm, leadForm]) for (const input of form.querySelectorAll('input, select, textarea')) input.disabled = busy;
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
      text(card, 'p', `Training: ${call.training.training_location} · ${call.training.training_date} · ${call.training.session_one} / ${call.training.session_two} (${call.training.clock_convention})`);
      if (call.error) text(card, 'p', call.error);
      if (call.result_state === 'ready') {
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
    const data = await api('/campaigns');
    $('demo-campaign').replaceChildren();
    const empty = text($('demo-campaign'), 'option', 'New campaign'); empty.value = '';
    for (const item of data.campaigns) {
      const option = text($('demo-campaign'), 'option', `${item.config.name} · revision ${item.revision}`); option.value = item.campaign_id;
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
      $('demo-connection').textContent = defaults.connection_configured ?
        `TeleExpert configured. ${defaults.webhook_configured ? 'Signed callbacks configured.' : 'Polling is available; callbacks are not configured.'}` : 'Configure the TeleExpert tenant connection on the server before preparing an assistant.';
      editConfig(defaults.config); await loadCampaigns(); controls();
      notice('Workspace open. Review the sample settings and save a campaign.');
    } catch (error) { token = ''; notice(error.message, true); }
  };
  $('demo-campaign').onchange = () => action(async () => {
    selectedLead = ''; workspace = null;
    current = $('demo-campaign').value ? {campaign_id: $('demo-campaign').value} : null;
    if (current) await loadWorkspace(true); else { editConfig(defaults.config); render(); }
  });
  $('demo-new').onclick = () => {
    if (busy) return;
    sequence++; current = null; workspace = null; selectedLead = ''; $('demo-campaign').value = '';
    editConfig(defaults.config); render();
  };
  $('demo-lock').onclick = () => window.location.reload();
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
    if (!lead || dirty() || !current.assistant_id) throw Error('Save and prepare the campaign, then select a contact.');
    if (!window.confirm(`Place a REAL call to ${lead.name || 'this contact'} (${lead.phone_number})?\n\n${current.config.training_location}\n${current.config.training_date}\n${current.config.session_one} / ${current.config.session_two}\n\nConfirm these details and permission to call.`)) return;
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
    if (!token || !current || busy || polling) return;
    const campaignId = current.campaign_id;
    polling = true;
    try {
      for (const call of workspace?.calls || []) {
        if (busy || current?.campaign_id !== campaignId) return;
        if (call.provider_call_id && call.outstanding && call.result_state !== 'needs_review') await api(`/calls/${call.request_id}/sync`, 'POST');
      }
      if (!busy && current?.campaign_id === campaignId) await loadWorkspace();
    } catch (error) { notice('Live status could not refresh: ' + error.message, true); }
    finally { polling = false; }
  }, 5000);
})();
