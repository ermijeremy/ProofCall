"""Prepare a local dashboard campaign and assistant without submitting calls."""
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from dotenv import dotenv_values
from app.schemas.recruitment import RecruitmentConfig

environment = dotenv_values(root / ".env")
token = environment.get("PROOFCALL_RECRUITMENT_OPERATOR_TOKEN", "")
if len(token) < 32:
    raise SystemExit("Configure the private local operator token in .env first.")
api_prefix = '/api/recruitment'
def request(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8767" + api_prefix + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Dashboard preparation failed (HTTP {error.code}); check the dashboard notice and local server log.") from None

data = json.loads((root / "demo/besingularity/outbound-campaign.json").read_text())
api_prefix = '/api/company-workspaces'
companies = request('')['companies']
company = next((c for c in companies if c['name'].lower() == 'besingularity'), None)
if company is None:
    raise SystemExit('Provision BeSingularity first with scripts/provision_company_login.py.')
api_prefix = '/api/workspaces/' + company['company_id'] + '/recruitment'
data['name'] = 'BeSingularity — Henok demo'
data["prompt"] = (root / "demo/besingularity/outbound-prompt.md").read_text()
data["greeting"] = data["prompt"].split("OPENING — GREET IN AMHARIC, THEN OFFER LANGUAGE OPTIONS", 1)[1].split("LANGUAGE CHOICE", 1)[0].strip()
config = RecruitmentConfig.model_validate(data)
existing = request("/campaigns")["campaigns"]
campaign = next((item for item in existing if item["config"]["name"] == config.name), None)
if campaign is None:
    campaign = request("/campaigns", config.model_dump(mode="json"))
# Existing operator edits are deliberately preserved on repeated runs.
prepared = request("/campaigns/" + campaign["campaign_id"] + "/prepare", {})
print("Prepared campaign:", prepared["config"]["name"])
print("Dashboard: http://127.0.0.1:8767/recruitment")
print("No calls submitted. Add a permitted test contact and confirm one call in the dashboard.")
