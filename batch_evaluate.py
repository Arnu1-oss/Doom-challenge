
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent

WORKERS = 3
RETRIES = 3

def request(method, path):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=b"" if method == "POST" else None,
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=240) as response:
        return json.load(response)

conversations = request("GET", "/api/conversations")

if len(conversations) != 65:
    raise SystemExit(
        f"Expected 65 DEV conversations, found {len(conversations)}"
    )

saved = set()
path = ROOT / "evaluations.jsonl"

if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("conversation_id"):
                saved.add(str(record["conversation_id"]))

pending = [
    str(c["conversation_id"])
    for c in conversations
    if str(c["conversation_id"]) not in saved
]

print(f"Already evaluated: {len(saved)}")
print(f"Remaining: {len(pending)}")
print(f"Parallel workers: {WORKERS}")

def evaluate(cid):
    url = (
        "/api/conversations/"
        + urllib.parse.quote(cid, safe="")
        + "/evaluate"
    )

    for attempt in range(RETRIES):
        try:
            request("POST", url)
            return cid, True, ""
        except Exception as exc:
            if attempt == RETRIES - 1:
                return cid, False, str(exc)
            time.sleep(2 ** (attempt + 1))

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = [pool.submit(evaluate, cid) for cid in pending]

    for index, future in enumerate(as_completed(futures), 1):
        cid, success, error = future.result()
        if success:
            print(f"[{index}/{len(pending)}] OK: {cid}", flush=True)
        else:
            print(f"[{index}/{len(pending)}] FAILED: {cid}: {error}", flush=True)

print("Batch complete!")
