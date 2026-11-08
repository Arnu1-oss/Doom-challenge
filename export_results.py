
import json
from pathlib import Path

# Change this if your PROD evaluations are somewhere else
SOURCE = Path("evaluations.jsonl")
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

def convert_value(criterion):
    value = criterion.get("result")

    if isinstance(value, str):
        if value.upper() == "YES":
            return True
        if value.upper() == "NO":
            return False
        if value.upper() == "N/A":
            return None

    return value

def convert_evidence(criterion):
    turns = []
    for message_id in criterion.get("evidence_message_ids", []):
        if isinstance(message_id, str) and message_id.startswith("m"):
            try:
                turns.append(int(message_id[1:]))
            except ValueError:
                pass
    return turns

if not SOURCE.exists():
    raise SystemExit(f"File not found: {SOURCE}")

records = {}
for line in SOURCE.read_text(encoding="utf-8").splitlines():
    if line.strip():
        item = json.loads(line)
        cid = item.get("conversation_id")
        if cid and cid.startswith("p2-"):
            records[cid] = item

results = []

for cid, item in sorted(records.items()):
    criteria = []

    for criterion in item.get("criteria", []):
        index = criterion.get("id")
        if not isinstance(index, int) or not 1 <= index <= 7:
            continue

        criteria.append({
            "criterion_id": CODES[index - 1],
            "value": convert_value(criterion),
            "rationale": criterion.get("explanation", ""),
            "evidence_turns": convert_evidence(criterion),
        })

    gross_errors = item.get("gross_errors") or {}
    critical = bool(gross_errors.get("detected", False))

    criteria.append({
        "criterion_id": "critical",
        "value": critical,
        "rationale": gross_errors.get("explanation", ""),
        "evidence_turns": convert_evidence(gross_errors),
    })

    results.append({
        "conversation_id": cid,
        "status": "ok",
        "criteria": criteria,
        "score": {
            "final_score": item.get("total_score"),
            "critical_flag": critical,
        },
    })

if len(results) != :
    raise SystemExit(
        f"Expected 10 PROD evaluations, found {len(results)}. "
        "Check SOURCE before exporting."
    )

payload = {
    "schema_version": "1.0",
    "team_name": "Almighty",
    "model": {
        "mode": "external_api",
        "name": results and records[results[0]["conversation_id"]].get(
            "model", "nvidia/nemotron-3-ultra-550b-a55b:free"
        ),
    },
    "results": results,
}

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print(f"SUCCESS: {OUTPUT}")
print(f"PROD evaluations exported: {len(results)}")

