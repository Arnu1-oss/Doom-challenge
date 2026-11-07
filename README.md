# Doom-challenge — CQA evaluator

Quality assessment of human customer-support operators in the hackathon conversation CSV using OpenRouter.

## Setup

1. Install Python 3.10+.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file (do not commit it):
   ```text
   OPENROUTER_API_KEY=sk-or-v1-YOUR_KEY
   ```

## Run

Try one conversation first:
```bash
python main.py --limit 1
```

Run the whole CSV:
```bash
python main.py --limit 0 --output evaluations.jsonl
```

Choose another file/model:
```bash
python main.py --csv conversations_202611071332.csv --model nvidia/nemotron-3-ultra-550b-a55b:free
```

The JSON Lines (`.jsonl`) output contains one JSON object per conversation.
Errors appear as `{"conversation_id": "...", "error": "..."}` so a single
failed request does not stop the batch. Do **not** publish evaluations or
conversations without permission.

## Quality and scoring

- Evaluates the **human operator**; bot/system/client messages provide context.
- Requires seven scored criteria and separately checks gross errors.
- Verifies every evidence quotation *exactly* against an operator message.
- Computes the final score in Python; N/A criteria are removed from the
  denominator and applicable weights are redistributed:
  `100 * sum(weight * points / max_points) / sum(applicable weight)`.
- Saves model/API/validation failures for inspection rather than inventing scores.

**Important:** The prompt is a starting implementation based on the criteria
described in the project. Before treating results as authoritative, compare
each rule, N/A condition, allowable point value, and gross-error definition
against the supplied `CQA_Chat_Quality_Checklist.pdf`. Human review is
necessary: matching evidence quotations does not prove an AI judgment correct.

## Tests

```bash
python -m unittest discover -s tests -v
```

No API key or network connection is needed for these tests.

## Repository files

- `main.py` — CLI, transcript reading, OpenRouter, validation, scoring
- `tests/test_evaluator.py` — offline tests
- `conversations_202611071332.csv` — provided conversation source
- `CQA_Chat_Quality_Checklist.pdf` — authoritative scoring rules
- `CQA_Hackathon_Student_Guide_v3 1.pdf` — hackathon guidance
