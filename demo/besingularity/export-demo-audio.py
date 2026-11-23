"""List completed local demo recordings, or export the latest two WAV channels."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--latest", action="store_true", help="Export the latest completed call")
args = parser.parse_args()
os.umask(0o077)
container = "teleexpert-besingularity-teleexpert-1"
query = """import json,pathlib
rows=[]
for p in pathlib.Path('/data/recordings').glob('*/session.json'):
 d=json.loads(p.read_text())
 rows.append({k:d.get(k) for k in ('session_id','started_at','duration_seconds','downlink_bytes','uplink_bytes')})
print(json.dumps(sorted(rows,key=lambda d:d['started_at'])))
"""
result = subprocess.run(["docker", "exec", container, "python", "-c", query],
    check=True, capture_output=True, text=True, timeout=15)
rows = json.loads(result.stdout)
if not rows:
    raise SystemExit("No completed call recordings found. Make and end a controlled test call first.")
if not args.latest:
    for row in rows:
        print(json.dumps(row))
else:
    session = rows[-1]["session_id"]
    if not isinstance(session, str) or not re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z_[0-9a-f]{8}", session):
        raise SystemExit("Unexpected recording session ID; no export performed.")
    target = Path(__file__).resolve().parents[2] / ".local-demo/demo-recordings" / session
    target.mkdir(parents=True, exist_ok=True)
    for filename in ("downlink.wav", "uplink.wav"):
        subprocess.run(["docker", "cp", f"{container}:/data/recordings/{session}/{filename}",
            str(target / filename)], check=True, timeout=30)
        (target / filename).chmod(0o600)
    print("Exported caller and assistant audio separately to", target)
    print("These raw channels are not a synchronized mixed recording. Check the session before sharing.")
