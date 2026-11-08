
import json
from pathlib import Path

import pandas as pd

SOURCE = Path("evaluations.jsonl")
CSV = Path("conversations_202611071332.csv")
OUTPUT = Path("results/dev/results.json")

CODES = [
    "contact",
    "discovery",
    "identification",
    "consultation",
    "empathy",
    "dialogue",
    "closure",
]

# Load all 65 real conversation IDs from DEV CSV
df = pd.read_csv(CSV)
all_ids = list(dict.fromkeys(df["conversation_id"].astype(str)))

if len(all_ids) != 65:
    raise SystemExit(f"Expected 65 DEV IDs, found {len(all_ids)}")

# Load saved evaluations
saved = {}

for line in SOURCE.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue

    item = json.loads(line)
    cid = item.get("conversation_id")

    if cid in all_ids:
        saved[cid] = item


def evidence_turns(item):
    turns = []

    for mid in item.get("evidence_message_ids", []):
        if isinstance(mid, str) and mid.startswith("m"):
            try:
                turns.append(int(mid[1:]))
            except ValueError:
                pass

    return turns


def convert_value(value):
    if isinstance(value, str):
        value = value.upper()

        if value == "YES":
            return True
        if value == "NO":
            return False
        if value == "N/A":
            return None

    return value


results = []

for cid in all_ids:
    if cid not in saved:
        results.append({
            "conversation_id": cid,
            "status": "error",
            "criteria": [],
            "score": None,
            "error": "OpenRouter rate limit (HTTP 429); evaluation unavailable",
        })
        continue

    item = saved[cid]
    criteria = []

    for criterion in item.get("criteria", []):
        index = criterion.get("id")

        if not isinstance(index, int) or not 1 <= index <= 7:
            continue

        criteria.append({
            "criterion_id": CODES[index - 1],
            "value": convert_value(criterion.get("result")),
            "rationale": criterion.get("explanation", ""),
            "evidence_turns": evidence_turns(criterion),
        })

    gross = item.get("gross_errors") or {}
    critical = bool(gross.get("detected", False))

    criteria.append({
        "criterion_id": "critical",
        "value": critical,
        "rationale": gross.get("explanation", ""),
        "evidence_turns": evidence_turns(gross),
    })

    score = item.get("total_score")

    if len(criteria) != 8 or score is None:
        results.append({
            "conversation_id": cid,
            "status": "error",
            "criteria": [],
            "score": None,
            "error": "Saved evaluation is incomplete",
        })
        continue

    results.append({
        "conversation_id": cid,
        "status": "ok",
        "criteria": criteria,
        "score": {
            "final_score": score,
            "critical_flag": critical,
        },
    })

models = {
    item.get("model")
    for item in saved.values()
    if item.get("model")
}

if len(models) != 1:
    raise SystemExit(
        f"Expected one recorded model, found: {models}. "
        "Verify the model used before exporting."
    )

payload = {
    "schema_version": "1.0",
    "team_name": "Almighty",
    "model": {
        "mode": "external_api",
        "name": models.pop(),
    },
    "results": results,
}

OUTPUT.parent.mkdir(parents=True, exist_ok=True)

OUTPUT.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

ok = sum(r["status"] == "ok" for r in results)
errors = sum(r["status"] == "error" for r in results)

print(f"SUCCESS: {OUTPUT}")
print(f"Total: {len(results)}")
print(f"Successful: {ok}")
print(f"Errors: {errors}")
