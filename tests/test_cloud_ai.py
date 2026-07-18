import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError(f"OPENAI_API_KEY not found in {env_path}")

client = OpenAI(api_key=api_key)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": "Ты отвечаешь кратко и по-русски."},
        {"role": "user", "content": "Скажи одним предложением, что такое RSI."}
    ],
    temperature=0.2
)

print(response.choices[0].message.content)