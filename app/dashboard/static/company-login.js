(() => {
  'use strict';
  const login = document.getElementById('company-login');
  const password = document.getElementById('company-password');
  const notice = document.getElementById('auth-notice');
  let session = null;
  async function api(path, body) {
    const response = await fetch('/api/company-auth/' + path, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': session?.csrf_token || ''},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Check the form and try again.');
    return data;
  }
  function proceed(data) {
    session = data;
    if (data.must_change_password || new URLSearchParams(location.search).has('change')) {
      login.hidden = true; password.hidden = false;
      if (!data.must_change_password) document.getElementById('password-reason').textContent = 'Change your password. Other signed-in sessions will be ended.';
    } else location.replace(data.url);
  }
  login.onsubmit = async event => {
    event.preventDefault();
    const button = login.querySelector('button'); button.disabled = true;
    try {
      const current = login.elements.password.value;
      const data = await api('login', {username: login.elements.username.value, password: current});
      login.elements.password.value = '';
      if (data.must_change_password) password.elements.current_password.value = current;
      proceed(data);
    } catch (error) { notice.textContent = error.message; }
    finally { button.disabled = false; }
  };
  password.onsubmit = async event => {
    event.preventDefault();
    if (password.elements.new_password.value !== password.elements.confirm_password.value) {
      notice.textContent = 'New passwords do not match.'; return;
    }
    const button = password.querySelector('button'); button.disabled = true;
    try {
      const data = await api('password', {current_password: password.elements.current_password.value, new_password: password.elements.new_password.value});
      password.reset(); location.replace(data.url);
    } catch (error) { notice.textContent = error.message; }
    finally { button.disabled = false; }
  };
  api('session').then(proceed).catch(() => {});
})();
