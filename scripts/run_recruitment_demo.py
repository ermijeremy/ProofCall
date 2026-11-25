"""Run the isolated local recruitment dashboard; never place a call at startup."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
from dotenv import load_dotenv
import uvicorn

load_dotenv(root / ".env")
os.environ.setdefault("CALLPROOF_SCHEDULER_ENABLED", "false")
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8767)
