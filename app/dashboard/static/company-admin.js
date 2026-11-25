(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let token = '';
  async function api(path = '', body) {
    const response = await fetch('/api/company-workspaces' + path, {
      method: body ? 'POST' : 'GET', cache: 'no-store',
      headers: {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Check the form fields.');
    return data;
  }
  function showCredentials(data, url) {
    $('credentials-text').textContent = `Workspace: ${location.origin}${url}\nUsername: ${data.username}\nTemporary password: ${data.temporary_password}`;
    $('initial-credentials').hidden = false;
  }
  async function render() {
    const data = await api(); $('company-list').replaceChildren();
    for (const company of data.companies) {
      const row = document.createElement('p');
      const link = document.createElement('a'); link.textContent = company.name; link.href = company.url;
      const reset = document.createElement('button'); reset.textContent = company.username ? 'Reset company password' : 'Create initial login'; reset.className = 'ghost-button';
      reset.onclick = async () => {
        let username;
        if (!company.username) {
          username = prompt('Choose an initial username for ' + company.name);
          if (!username) return;
        } else if (!confirm('Issue a new temporary password and sign out existing sessions for ' + company.name + '?')) return;
        reset.disabled = true;
        try {
          showCredentials(await api('/' + company.company_id + (company.username ? '/reset-password' : '/initial-login'), company.username ? {} : {username}), company.url);
          await render();
        }
        catch (error) { $('admin-notice').textContent = error.message; }
        finally { reset.disabled = false; }
      };
      row.append(link, document.createTextNode(' '), reset); $('company-list').append(row);
    }
  }
  $('admin-login').onsubmit = async event => {
    event.preventDefault(); token = $('admin-token').value;
    try { await render(); $('admin-token').value = ''; $('admin-login').hidden = true; $('admin-workspace').hidden = false; }
    catch (error) { token = ''; $('admin-notice').textContent = error.message; }
  };
  $('admin-create').onsubmit = async event => {
    event.preventDefault(); const form = event.currentTarget; const button = form.querySelector('button'); button.disabled = true;
    try { const data = await api('', {name: form.elements.name.value, username: form.elements.username.value}); showCredentials(data, data.url); form.reset(); await render(); }
    catch (error) { $('admin-notice').textContent = error.message; }
    finally { button.disabled = false; }
  };
  $('hide-credentials').onclick = () => { $('credentials-text').textContent = ''; $('initial-credentials').hidden = true; };
  $('admin-lock').onclick = () => location.reload();
})();
