import json
from pathlib import Path
import sys
import urllib.request

root = Path(__file__).resolve().parent
project = root.parents[1]
local = project / ".local-demo"
sys.path.insert(0, str(project))
from app.schemas.recruitment import RecruitmentConfig, assistant_payload

data = json.loads((root / "besingularity-demo.json").read_text())
recipient_name = data.pop("recipient_name", "")
data["prompt"] = (root / "besingularity-prompt.md").read_text()
config = RecruitmentConfig.model_validate(data)
key = json.loads((local / "TeleExpert/.local-tenant.json").read_text())["api_key"]
def request(path, method="GET", body=None):
    req = urllib.request.Request("http://127.0.0.1:8080" + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)

payload = assistant_payload(config)
payload["definition"]["greeting"] = config.prompt.split("OPENING — GREET IN AMHARIC, THEN OFFER LANGUAGE OPTIONS", 1)[1].split("LANGUAGE CHOICE", 1)[0].strip()
# Explicit language permits the requested menu choice without Auto's mandatory
# instruction to override it with the detected language of the caller's reply.
payload["definition"]["language"] = "Amharic"
state_file = local / "besingularity-assistant.json"
if state_file.exists():
    previous = json.loads(state_file.read_text())
    latest = request("/v1/assistants/" + previous["assistant_id"])
    payload["expected_version"] = latest.get("latest_version", latest["version"])
    assistant = request("/v1/assistants/" + previous["assistant_id"], "PATCH", payload)
else:
    assistant = request("/v1/assistants", "POST", payload)
variables = {k: str(v) for k, v in config.model_dump(mode="json").items() if k in
    ("training_location", "training_date", "session_one", "session_two", "clock_convention", "timezone", "contact_source")}
variables["recipient_name"] = recipient_name
request("/v1/assistants/" + assistant["id"] + "/preview", "POST",
        {"version": assistant["version"], "variables": variables})
groups = request("/v1/gateway-groups")["gateway_groups"]
group = next(g["id"] for g in groups if g["default"])
binding = {"assistant_id": assistant["id"], "assistant_version": assistant["version"], "variables": variables}
request("/v1/gateway-groups/" + group + "/inbound-assistant", "PUT", binding)
verified = request("/v1/gateway-groups/" + group + "/inbound-assistant")
assert verified["assistant_id"] == assistant["id"], verified
assert verified["assistant_version"] == assistant["version"], verified
state_file.write_text(json.dumps(binding, ensure_ascii=False, indent=2))
print("BeSingularity assistant assigned and verified for incoming demo calls; version", assistant["version"])
print("No phone calls placed.")
