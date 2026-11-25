"""Company login, ownership and configurable-questionnaire contracts; no live calls."""
from datetime import datetime, timedelta
import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.test_recruitment_demo import setup, AUTH, BASE
from app.core.company_access import COOKIE, verify_password
from app.models.recruitment import CompanyUser, CompanySession, RecruitmentCampaign, RecruitmentCall


def provision(client, name, username):
    response = client.post('/api/company-workspaces', headers=AUTH, json={'name': name, 'username': username})
    assert response.status_code == 201, response.text
    return response.json()


def login(client, credentials):
    response = client.post('/api/company-auth/login', json={
        'username': credentials['username'], 'password': credentials['temporary_password']})
    assert response.status_code == 200, response.text
    return response.json(), response.cookies[COOKIE]


def activate(client, credentials):
    session, _ = login(client, credentials)
    assert session['must_change_password'] is True
    response = client.post('/api/company-auth/password', headers={'X-CSRF-Token': session['csrf_token']}, json={
        'current_password': credentials['temporary_password'], 'new_password': 'A new long test password 2026!'})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['must_change_password'] is False
    return {'Cookie': COOKIE + '=' + response.cookies[COOKIE], 'X-CSRF-Token': data['csrf_token']}


def test_first_login_password_hash_session_rotation_and_logout(setup, db):
    client, _, _ = setup
    company = provision(client, 'Alpha Services', 'Alpha.Owner')
    user = db.get(CompanyUser, 'alpha.owner')
    assert user.password_hash != company['temporary_password']
    assert verify_password(company['temporary_password'], user.password_hash)
    data, old_cookie = login(client, company)
    assert data['must_change_password']
    path = '/api/workspaces/' + company['company_id'] + '/recruitment'
    assert client.get(path + '/campaigns').status_code == 403
    assert client.post('/api/company-auth/password', json={
        'current_password': company['temporary_password'], 'new_password': 'A new long test password 2026!'}).status_code == 403
    response = client.post('/api/company-auth/password', headers={'X-CSRF-Token': data['csrf_token']}, json={
        'current_password': company['temporary_password'], 'new_password': 'A new long test password 2026!'})
    assert response.status_code == 200
    new_cookie = response.cookies[COOKIE]
    assert new_cookie != old_cookie
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert db.get(CompanySession, hashlib.sha256(old_cookie.encode()).hexdigest()) is None
    assert client.get(path + '/campaigns').status_code == 200
    csrf = response.json()['csrf_token']
    assert client.post('/api/company-auth/logout', headers={'X-CSRF-Token': csrf}).status_code == 200
    assert client.get(path + '/campaigns').status_code == 401
    assert client.post('/api/company-auth/login', json={'username': company['username'], 'password': company['temporary_password']}).status_code == 401


def test_company_cannot_access_other_company_ids_calls_or_global_endpoints(setup, db):
    client, provider, _ = setup
    a = provision(client, 'Alpha', 'alpha')
    b = provision(client, 'Beta', 'beta')
    a_headers = activate(client, a)
    client.cookies.clear()  # A separate company's browser session.
    b_headers = activate(client, b)
    a_base = '/api/workspaces/' + a['company_id'] + '/recruitment'
    b_base = '/api/workspaces/' + b['company_id'] + '/recruitment'
    config = client.get(a_base + '/defaults', headers=a_headers).json()['config']
    assert config['company_name'] == 'Alpha' and config['purpose'] == 'questionnaire'
    assert 'BeSingularity' not in config['prompt']
    config['questions'] = ['How was our service?', 'What can we improve?']
    campaign = client.post(a_base + '/campaigns', headers=a_headers, json=config).json()
    cid = campaign['campaign_id']
    assert db.get(RecruitmentCampaign, cid).company_id == a['company_id']
    assert client.get(b_base + '/campaigns', headers=b_headers).json()['campaigns'] == []
    for suffix in ('', '/assistant-payload'):
        assert client.get(b_base + '/campaigns/' + cid + suffix, headers=b_headers).status_code == 404
    for suffix in ('/prepare', '/leads/import/preview'):
        assert client.post(b_base + '/campaigns/' + cid + suffix, headers=b_headers).status_code == 404
    assert client.get(a_base + '/campaigns', headers=b_headers).status_code == 404
    assert client.get(BASE + '/campaigns', headers=a_headers).status_code == 404
    assert client.get('/api/company-workspaces', headers=a_headers).status_code == 401
    assert client.post(a_base + '/campaigns/' + cid + '/prepare', headers=a_headers).status_code == 200
    lead = client.post(a_base + '/campaigns/' + cid + '/leads', headers=a_headers, json={
        'name': 'Permitted person', 'phone_number': '+251900000001', 'contact_source': 'Opted-in customer', 'contact_permission': True,
    }).json()['lead_id']
    request_id = str(uuid4())
    response = client.post(a_base + f'/campaigns/{cid}/leads/{lead}/call', headers=a_headers, json={'request_id': request_id, 'confirmed': True})
    assert response.status_code == 202, response.text
    for suffix in ('/sync', '/cancel', '/analysis/retry', '/resume'):
        assert client.post(b_base + '/calls/' + request_id + suffix, headers=b_headers, json={'request_id': request_id, 'confirmed': True}).status_code == 404
    assert provider.submissions == 1
    assert client.post(a_base + '/campaigns', headers={'Cookie': a_headers['Cookie']}, json=config).status_code == 403
    assert client.post(a_base + '/campaigns', headers={**a_headers, 'Origin': 'https://attacker.test'}, json=config).status_code == 403


def test_questionnaire_answers_match_pinned_questions_and_are_persisted(setup, db):
    client, provider, _ = setup
    c = provision(client, 'Customer Care', 'care')
    headers = activate(client, c)
    base = '/api/workspaces/' + c['company_id'] + '/recruitment'
    config = client.get(base + '/defaults', headers=headers).json()['config']
    config['questions'] = ['What worked?', 'What needs fixing?']
    cid = client.post(base + '/campaigns', headers=headers, json=config).json()['campaign_id']
    assert client.post(base + f'/campaigns/{cid}/prepare', headers=headers).status_code == 200
    definition = provider.definition
    assert 'q1' in definition['analysis_schema']['properties']['answers']['properties']
    assert 'What worked?' in definition['instructions']
    assert 'salary' not in definition['instructions']
    lead = client.post(base + f'/campaigns/{cid}/leads', headers=headers, json={
        'name': 'Customer', 'phone_number': '+251900000001', 'contact_source': 'Customer permission', 'contact_permission': True,
    }).json()['lead_id']
    request_id = str(uuid4())
    client.post(base + f'/campaigns/{cid}/leads/{lead}/call', headers=headers, json={'request_id': request_id, 'confirmed': True})
    result = {'answers': {'q1': {'answer': 'Delivery', 'evidence': 'Delivery worked well', 'state': 'stated'},
                          'q2': {'answer': '', 'evidence': '', 'state': 'not_asked'}},
              'summary': 'Delivery was good; second question was not asked.', 'conversation_complete': False,
              'callback_request': '', 'do_not_contact': False}
    provider.complete(request_id, result)
    response = client.post(base + '/calls/' + request_id + '/sync', headers=headers)
    assert response.json()['result_state'] == 'ready', response.text
    db.expire_all()
    call = db.get(RecruitmentCall, request_id)
    assert call.result['answers']['q2']['state'] == 'not_asked'
    workspace = client.get(base + f'/campaigns/{cid}', headers=headers).json()
    assert workspace['calls'][0]['questions'] == config['questions']
    assert workspace['calls'][0]['purpose'] == 'questionnaire'


def test_expired_session_and_password_reset_revoke_access(setup, db):
    client, _, _ = setup
    c = provision(client, 'Company', 'company')
    headers = activate(client, c)
    base = '/api/workspaces/' + c['company_id'] + '/recruitment/campaigns'
    raw = headers['Cookie'].split('=', 1)[1]
    row = db.get(CompanySession, hashlib.sha256(raw.encode()).hexdigest())
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get(base, headers=headers).status_code == 401
    response = client.post('/api/company-workspaces/' + c['company_id'] + '/reset-password', headers=AUTH, json={})
    assert response.status_code == 200
    creds = {**response.json(), 'company_id': c['company_id']}
    data, _ = login(client, creds)
    assert data['must_change_password'] is True


def test_login_limits_and_duplicate_username(setup):
    client, _, _ = setup
    c = provision(client, 'One', 'unique')
    assert client.post('/api/company-workspaces', headers=AUTH, json={'name': 'Two', 'username': 'UNIQUE'}).status_code == 409
    for _ in range(10):
        assert client.post('/api/company-auth/login', json={'username': c['username'], 'password': 'wrong'}).status_code == 401
    assert client.post('/api/company-auth/login', json={'username': c['username'], 'password': 'wrong'}).status_code == 429


def test_public_shells_do_not_expose_company_data_and_legacy_api_requires_admin(setup):
    from app.main import protect_legacy_admin_apis
    from starlette.middleware.base import BaseHTTPMiddleware
    client, _, _ = setup
    with TestClient(BaseHTTPMiddleware(client.app, dispatch=protect_legacy_admin_apis)) as secured:
        c = provision(secured, 'Private Company Name', 'private')
        for path in ('/', '/companies/' + c['company_id'], '/login'):
            response = secured.get(path)
            assert response.status_code == 200
            assert 'Private Company Name' not in response.text
        assert secured.get('/api/companies').status_code == 401
        assert secured.get('/api/dashboard').status_code == 401
        assert secured.get('/api/calls/someone-else').status_code == 401


def test_existing_company_gets_initial_login_without_creating_duplicate_company(setup, db):
    from app.models.employer import Employer
    client, _, _ = setup
    db.add(Employer(company_id='existing_company', name='Existing company'))
    db.commit()
    response = client.post('/api/company-workspaces/existing_company/initial-login', headers=AUTH, json={'username': 'existing.owner'})
    assert response.status_code == 201
    assert client.post('/api/company-workspaces/existing_company/initial-login', headers=AUTH, json={'username': 'another.owner'}).status_code == 409
    credentials = response.json()
    data, _ = login(client, credentials)
    assert data['company_id'] == 'existing_company' and data['must_change_password']
    assert len(client.get('/api/company-workspaces', headers=AUTH).json()['companies']) == 1


def test_company_password_persists_and_new_password_signs_in(setup, db):
    client, _, _ = setup
    c = provision(client, 'Persistent company', 'persistent')
    headers = activate(client, c)
    client.post('/api/company-auth/logout', headers=headers)
    db.expire_all()
    response = client.post('/api/company-auth/login', json={'username': 'persistent', 'password': 'A new long test password 2026!'})
    assert response.status_code == 200 and not response.json()['must_change_password']
    assert client.post('/api/company-auth/login', json={'username': 'persistent', 'password': c['temporary_password']}).status_code == 401


def test_questionnaire_rejects_missing_or_extra_question_answers(setup):
    import pytest
    from app.schemas.recruitment import RecruitmentConfig, validate_campaign_result
    config = RecruitmentConfig(purpose='questionnaire', questions=['First question', 'Second question'])
    value = {'answers': {'q1': {'answer': '', 'evidence': '', 'state': 'not_asked'}},
             'summary': '', 'conversation_complete': False, 'callback_request': '', 'do_not_contact': False}
    with pytest.raises(ValueError):
        validate_campaign_result(config, value)
    with pytest.raises(ValueError):
        RecruitmentConfig(purpose='questionnaire', questions=[])
