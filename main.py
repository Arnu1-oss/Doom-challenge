import pandas as pd
import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()

# Load conversations
df = pd.read_csv("conversations_202611071332.csv")

# Select conversation
row = df.iloc[0]

# Parse transcript
transcript = json.loads(row["transcript"])

# Format transcript
conversation_text = "\n".join(
    f'[{msg["timestamp"]}] {msg["speaker"]}: {msg["text"]}'
    for msg in transcript
)


API_KEY = os.getenv("OPENROUTER_API_KEY")

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

prompt = f"""
Evaluate ONLY the OPERATOR's performance in this customer conversation.

IMPORTANT:
- Messages from client, bot, and system are context.
- Evaluate the operator according to the criteria below.
- Use ONLY information present in the transcript.
- Do not invent actions or messages.
- Provide concrete evidence using timestamps and exact message references.

Conversation ID: {row["conversation_id"]}
Language: {row["language"]}

TRANSCRIPT:
{conversation_text}

CHECKLIST:

1. Contact establishment — YES/NO, weight 10
YES if the operator greeted the client in the client's language
and introduced themselves in their first response.

2. Need identification — YES/NO/N/A, weight 15
YES if the operator considered known details and clarified missing
information before answering.
N/A if sufficient information was available from the beginning.

3. Identification — YES/NO/N/A, weight 20
YES if identity verification occurred before protected information
was disclosed or a protected action was performed.
N/A if the request did not require identification.

4. Correct and complete consultation — score 0/5/10/15/20/N/A, weight 20

5. Empathy and customer orientation — YES/NO, weight 15

6. Dialogue management, speed and literacy — score 0/4/7/10, weight 15

7. Closing — YES/NO/N/A, weight 5

8. Gross errors — YES/NO, no score
Gross error means:
- insulting the client
- offering paid services outside the company
- disclosure of confidential information

Return ONLY valid JSON in exactly this format:

{{
  "conversation_id": "{row["conversation_id"]}",
  "criteria": [
    {{
      "id": 1,
      "name": "Contact establishment",
      "result": "YES",
      "raw_score": 10,
      "weight": 10,
      "explanation": "Short explanation",
      "evidence": [
        {{
          "timestamp": "00:00",
          "speaker": "operator",
          "text": "Exact relevant message"
        }}
      ]
    }}
  ],
  "gross_errors": {{
    "detected": false,
    "explanation": ""
  }}
}}

Evaluate all 8 criteria.
"""

response = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers=headers,
    json={
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
)

print("Status:", response.status_code)

data = response.json()

if response.status_code == 200:
    answer = data["choices"][0]["message"]["content"]
    print(answer)
else:
    print("ERROR:")
    print(data)