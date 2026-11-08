# Freedom CQA Workspace — Frontend + Local API Bridge

Corporate-style plain **HTML / CSS / vanilla JavaScript** dashboard for the `Arnu1-oss/Doom-challenge` CQA hackathon project. No frontend framework or build step required.

# Project Overview

Freedom CQA Workspace is an AI-powered conversation quality analytics platform developed for the Freedom Broker CQA Hackathon.

The project combines a Python-based AI evaluator, a local HTTP API, and a web dashboard built with HTML, CSS, and vanilla JavaScript.

The platform supports:

AI-powered conversation quality scoring

Chat and voice call analysis

Customizable evaluation checklists

Evidence-based scoring and explanations

Appeals and reviewer adjustments

Audio playback for supported conversations

Local persistence of evaluation results

The application runs locally using python api_server.py and is accessible at http://127.0.0.1:8000.

This package therefore includes `api_server.py`, a **working local HTTP adapter**. It serves the frontend and reads the project's real CSV/JSONL. It does not claim to use an undisclosed organizer endpoint. API calls are real; data is not mocked.

## Install

1. In your existing `Doom-challenge` repo, copy `api_server.py` and the complete `frontend/` folder to the repository root, **next to `main.py`, the CSV and `evaluations.jsonl`**.
2. Install the dependencies of your existing evaluator (for AI requests):

   ```bash
   python -m pip install -r requirements.txt
   ```

   Browsing CSV/evaluation results and working with appeals only needs the Python standard library; AI evaluation calls your existing `main.py`, which requires `pandas`, `requests`, and `python-dotenv`.

3. Start the local server **from anywhere**:

   ```bash
   python api_server.py
   ```

4. Open **http://127.0.0.1:8000** in a browser. **Do not open `frontend/index.html` via file://** — the page needs the API.

5. To generate AI results, add `OPENROUTER_API_KEY=...` to `.env` in the repo root (never commit it). Use the **Запустить AI-оценку** button on a conversation; or evaluate multiple conversations with the existing CLI and refresh the dashboard:

   ```bash
   python main.py --limit 0
   ```

If your dataset sits elsewhere, set `CQA_DATA_DIR` to the **absolute path** of the folder containing the CSV, JSONL, `.env`, `appeals.json`, and `checklist.json`. The `api_server.py` and `frontend/` folder can remain in the repository root.

## Features

| Screen | Functionality |
| --- | --- |
| Обзор | Real conversation count, evaluated count, mean score, open appeals, channel & language distribution |
| Диалоги | Search by ID/preview, filters by channel (agent chat/voice call), language RU/KZ/EN, score status, opening a conversation |
| Карточка диалога | Full messages in original order, speaker roles (client/operator/bot/system), MM:SS/ISO timestamps, optional HTML5 audio player, clickable evidence references `m1...mN`, time seeking for voice when possible |
| Чек-лист | Edit descriptions, weights, types, scales, N/A; add custom criteria (IDs 9+); edit gross-error description; validation enforces total weight of 100% |
| Оценка | Seven base criteria, explanations and evidence, relative weights, separate gross-error flag; recalculates with N/A redistribution. Incomplete custom criteria require manual grading before final score is shown |
| Апелляции | Choose graded criterion or the separate gross-error flag, enter comment, create appeal; persisted in `appeals.json` |
| Проверка споров | Confirm or modify one disputed criterion, require reviewer reason, automatically recalculate and persist score |
| Ручная оценка | When a new custom criterion has no AI result, reviewer can enter a result, explanation, evidence message reference; saved to JSONL |

## API endpoints

All served on the **same origin** with no CORS needed:

| Method | Path | Role |
| --- | --- | --- |
| GET | `/api/health` | Health check |
| GET | `/api/bootstrap` | Conversations list, checklist, appeals |
| GET | `/api/conversations` | CSV conversation summaries plus scores |
| GET | `/api/conversations/{conversation_id}` | Transcript with stable message IDs, evaluation, weighted scores |
| POST | `/api/conversations/{conversation_id}/evaluate` | Invoke `main.evaluate_row()`, persist result |
| PATCH | `/api/conversations/{conversation_id}/criteria/{criterion_id}` | Manually assign or revise a score |
| GET / PUT | `/api/checklist` | Read and update configurable scoring rubric |
| GET / POST | `/api/appeals` | Read and create appeals |
| PATCH | `/api/appeals/{appeal_id}/review` | Confirm or modify score, with reason |

**Backend storage:** CSV (read-only), `evaluations.jsonl` (AI and reviewer evaluations), `checklist.json` (created after first save), `appeals.json` (created on first appeal). All are handled by the local adapter. Initial checklist follows `CQA_Chat_Quality_Checklist.pdf`.

## Limits and decisions

- **No login** as requested. **Local development only.** The server binds to `127.0.0.1` by default. Never expose publicly or embed in a production Dynamics environment without adding authentication, authorization, CSRF protection, HTTPS, rate limits, and an audit trail.
- Local records are stored in files, not a transactional multi-user database. Suitable for hackathon DEV usage; not for concurrent production reviewers.
- AI prompt in the existing `main.py` follows the **official seven criteria**. Editing the checklist in UI does **not** automatically retrain or rewrite the AI prompt. New criteria must be scored manually until the backend evaluator supports them.
- `audio_url` must be a reachable HTTP(S) URL and the browser must have permission to access it; an `s3://` link without a signed HTTPS URL won't play. Time seeking for ISO 8601 call messages uses elapsed time since the first message as an approximation; actual offset mapping should be provided by the organizer if available.
- Saved evaluation evidence IDs (`m1`, `m2`, etc.) follow original transcript order. Existing `main.py` should support `evaluate_row(row, api_key)` returning criterion `result` and `evidence_message_ids` per your updated code.
- N/A criteria are excluded; applicable weights are renormalized to **100**. Gross errors never affect the numeric total. A changed custom checklist may render results incomplete until all new criteria are scored.
- Client/transcript content is escaped before display; the browser never interprets transcript text as HTML.

## Dynamics 365 adaptation

This is framework-free HTML/CSS/JS. For a Dynamics HTML Web Resource, bundle the contents of `frontend/` as web resources and update `frontend/config.js`:

```js
window.CQA_CONFIG = { apiBase: "https://your-approved-api-host.example.com/api" };
```

That API host must expose the documented JSON endpoints, configure CORS for the Dynamics origin, and implement production security. The included local Python server does **not** implement Dynamics/Dataverse authorization. For a single HTML web resource, inline `styles.css`, `config.js` and `app.js` in `index.html` or publish them as relative web resources.

## Run API tests (no OpenRouter calls)

```bash
python -m unittest discover -s tests -v
```

These tests create temporary CSV/JSONL fixtures, start a real local HTTP server, and verify score redistribution, real transcript IDs, appeals and reviewer decisions, checklist editing, manual grading, static frontend, and API errors.

## Git hygiene

Keep `.env` secret. For a shared/public repository, consider adding these generated local files to your existing `.gitignore` (choose whether you want to version-control the custom checklist separately):

```gitignore
.DS_Store
appeals.json
checklist.json
# evaluations.jsonl  # uncomment if evaluations contain customer data and should not be tracked
```

A tracked file is not untracked merely by adding it to `.gitignore`; remove it from the Git index first with `git rm --cached filename` if needed.
