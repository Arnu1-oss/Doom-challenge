Replace Doom-challenge/main.py with this main.py. Keep your .env and CSV where they are.
The prompt now uses evidence_message_ids and the validator resolves these IDs to exact transcript messages.
Run: python main.py --limit 1
Run offline checks: python -m unittest -v test_format (from this extracted folder, with dependencies installed)
Do not commit your .env or API key. This update does not make live API requests by itself.
