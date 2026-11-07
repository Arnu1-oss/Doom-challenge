"""CQA conversation quality evaluator.

Runs one or all rows in the source CSV through OpenRouter, validates the model's
evidence against the transcript, and computes weighted scores deterministically.

No network requests occur merely by importing this module.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# ID: (label, weight, maximum points, allowed non-N/A points, N/A permitted)
RULES = {
    1: ("Contact establishment", 10, 10, {0, 10}, False),
    2: ("Need identification", 15, 15, {0, 15}, True),
    3: ("Customer identification", 20, 20, {0, 20}, True),
    4: ("Correct and complete consultation", 20, 20, {0, 5, 10, 15, 20}, True),
    5: ("Empathy and customer orientation", 15, 15, {0, 15}, False),
    6: ("Dialogue management, speed and literacy", 15, 10, {0, 4, 7, 10}, False),
    7: ("Closing", 5, 5, {0, 5}, True),
}
REQUIRED_COLUMNS = {"conversation_id", "channel", "language", "transcript"}

SCORING_INSTRUCTIONS = """
You are evaluating ONLY the human operator of a customer support conversation.
Client, bot, and system messages provide context, but MUST NOT be scored as the
operator's own actions. Read the whole transcript, including escalation context.

Use these criteria; report EACH of criteria 1 through 7 exactly once:
1. Contact establishment: 0 or 10. Operator greets in the client's language
   and introduces themself in their first response.
2. Need identification: 0, 15, or null if N/A. Consider known details;
   clarify missing information before answering. N/A if already sufficient.
3. Customer identification: 0, 20, or null if N/A. Required before disclosing
   protected personal information or performing a protected operation; N/A when
   the request does not need verification. Never assume verification happened.
4. Correct and complete consultation: 0, 5, 10, 15, 20, or null if N/A.
   Judge correctness and completeness only from evidence available in transcript;
   do not invent policy or outside facts.
5. Empathy and customer orientation: 0 or 15.
6. Dialogue management, speed and literacy: 0, 4, 7, or 10.
   The Python scoring function scales this criterion to its 15% weight.
7. Closing: 0, 5, or null if N/A.

Also separately identify gross errors: insulting the client, offering paid
services outside the company, or disclosure of confidential information.
Do not assert a gross error without a concrete operator quotation.

Required JSON shape:
{
  "criteria": [
    {"id": 1, "points": 10, "explanation": "reason",
     "evidence": [{"timestamp": "01:15", "speaker": "operator",
                   "text": "exact complete operator message"}]}
  ],
  "gross_errors": {"detected": false, "explanation": "", "evidence": []}
}
Use null for N/A points. Evidence MUST consist of actual operator messages:
copy timestamp, speaker and text EXACTLY from the transcript.
For missing behaviors, evidence may be [] and explanation must state what was
missing. Do not manufacture quotes or add markdown fences. Return JSON only.
"""


def load_conversations(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, keep_default_na=False)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
    return df


def parse_transcript(raw: str) -> list[dict[str, str]]:
    transcript = json.loads(raw)
    if not isinstance(transcript, list) or not transcript:
        raise ValueError("Transcript must be a nonempty JSON list")
    for index, msg in enumerate(transcript):
        if not isinstance(msg, dict) or not all(
            isinstance(msg.get(field), str) for field in ("speaker", "timestamp", "text")
        ):
            raise ValueError(f"Transcript message {index} has missing/non-string fields")
    return transcript


def create_prompt(row: pd.Series, transcript: list[dict[str, str]]) -> str:
    conversation = "\n".join(
        f'[{msg["timestamp"]}] {msg["speaker"]}: {msg["text"]}'
        for msg in transcript
    )
    return (
        SCORING_INSTRUCTIONS
        + f'\nConversation ID: {row["conversation_id"]}\n'
        + f'Channel: {row["channel"]}\nLanguage: {row["language"]}\n'
        + f'\nTRANSCRIPT:\n{conversation}\n'
    )


def extract_json(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            value = "\n".join(lines[1:-1]).strip()
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("Model response must be a JSON object")
    return result


def _check_evidence(evidence: Any, transcript: list[dict[str, str]]) -> None:
    if not isinstance(evidence, list):
        raise ValueError("Evidence must be a list")
    operator_messages = {
        (m["timestamp"], m["speaker"], m["text"])
        for m in transcript if m["speaker"].lower() == "operator"
    }
    for item in evidence:
        if not isinstance(item, dict) or (
            item.get("timestamp"), item.get("speaker"), item.get("text")
        ) not in operator_messages:
            raise ValueError("Evidence does not exactly match an operator message")


def validate_evaluation(data: dict[str, Any], transcript: list[dict[str, str]]) -> dict[str, Any]:
    criteria = data.get("criteria")
    if not isinstance(criteria, list) or len(criteria) != 7:
        raise ValueError("Expected exactly 7 scored criteria")
    seen: set[int] = set()
    for item in criteria:
        if not isinstance(item, dict):
            raise ValueError("Criterion must be a JSON object")
        ident = item.get("id")
        if type(ident) is not int or ident not in RULES or ident in seen:
            raise ValueError(f"Invalid or duplicate criterion ID: {ident}")
        seen.add(ident)
        name, weight, maximum, allowed, na_allowed = RULES[ident]
        points = item.get("points")
        if points is None:
            if not na_allowed:
                raise ValueError(f"Criterion {ident} cannot be N/A")
        elif type(points) is not int or points not in allowed:
            raise ValueError(f"Invalid points for criterion {ident}: {points}")
        if not isinstance(item.get("explanation"), str) or not item["explanation"].strip():
            raise ValueError(f"Criterion {ident} requires an explanation")
        _check_evidence(item.get("evidence"), transcript)
        if points is not None and points > 0 and not item["evidence"]:
            raise ValueError(f"Positive criterion {ident} needs operator evidence")
        item["name"] = name
        item["weight"] = weight
        item["max_points"] = maximum
    gross = data.get("gross_errors")
    if not isinstance(gross, dict) or type(gross.get("detected")) is not bool:
        raise ValueError("gross_errors.detected must be boolean")
    if not isinstance(gross.get("explanation"), str):
        raise ValueError("Gross error explanation must be text")
    _check_evidence(gross.get("evidence"), transcript)
    if gross["detected"] and (not gross["evidence"] or not gross["explanation"].strip()):
        raise ValueError("Gross error needs both explanation and operator evidence")
    data["criteria"] = sorted(criteria, key=lambda item: item["id"])
    return data


def calculate_score(criteria: list[dict[str, Any]]) -> float:
    """Redistribute N/A weights and normalize onto a 100-point scale."""
    applicable_weight = sum(c["weight"] for c in criteria if c["points"] is not None)
    if applicable_weight == 0:
        raise ValueError("No applicable scoring criteria")
    weighted = sum(
        c["weight"] * c["points"] / c["max_points"]
        for c in criteria if c["points"] is not None
    )
    return round(100 * weighted / applicable_weight, 2)


def query_openrouter(
    prompt: str, api_key: str, *, model: str = MODEL, timeout: int = 90,
    attempts: int = 3, session: requests.Session | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is missing")
    client = session or requests
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for attempt in range(attempts):
        try:
            response = client.post(
                ENDPOINT, headers=headers, timeout=timeout,
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Model returned empty content")
            return extract_json(content)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError("OpenRouter request failed after retries") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        if attempt < attempts - 1:
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("Unexpected API retry exhaustion")


def evaluate_row(row: pd.Series, api_key: str, *, model: str = MODEL) -> dict[str, Any]:
    transcript = parse_transcript(row["transcript"])
    evaluation = query_openrouter(create_prompt(row, transcript), api_key, model=model)
    evaluation = validate_evaluation(evaluation, transcript)
    evaluation["conversation_id"] = str(row["conversation_id"])
    evaluation["language"] = str(row["language"])
    evaluation["channel"] = str(row["channel"])
    evaluation["total_score"] = calculate_score(evaluation["criteria"])
    return evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate CQA CSV conversations")
    parser.add_argument("--csv", default="conversations_202611071332.csv")
    parser.add_argument("--output", default="evaluations.jsonl")
    parser.add_argument("--limit", type=int, default=1, help="Number of rows; 0 means all")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be 0 or greater")
    load_dotenv()
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        parser.error("Set OPENROUTER_API_KEY in .env or your environment")
    frame = load_conversations(args.csv)
    if args.limit:
        frame = frame.head(args.limit)
    output_path = Path(args.output)
    successes = failures = 0
    with output_path.open("w", encoding="utf-8") as output:
        for _, row in frame.iterrows():
            try:
                record = evaluate_row(row, api_key, model=args.model)
                successes += 1
                print(f'{record["conversation_id"]}: {record["total_score"]:.2f}/100')
            except (ValueError, KeyError, requests.RequestException, RuntimeError) as exc:
                failures += 1
                record = {
                    "conversation_id": str(row["conversation_id"]),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(f'{record["conversation_id"]}: ERROR: {exc}')
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
    print(f"Finished: {successes} successful, {failures} failed; saved to {output_path}")


if __name__ == "__main__":
    main()
