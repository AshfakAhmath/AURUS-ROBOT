import os
import sys
from dotenv import load_dotenv

# Load env variables
load_dotenv()

api_key = os.getenv("GROQ_API_KEY")
model_name = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-20b")

if not api_key:
    print("ERROR: GROQ_API_KEY is not set in your .env file!")
    sys.exit(1)

print("Testing configured Groq API key...")
print(f"Using Model: {model_name}")

try:
    from groq import Groq
except ImportError:
    print("ERROR: 'groq' library is not installed. Run: pip install groq")
    sys.exit(1)

try:
    client = Groq(api_key=api_key, timeout=5.0, max_retries=0)
    completion = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": "Hello! Respond with 'Groq connection successful!'"}],
        max_tokens=20
    )
    print("\nSUCCESS! Connection established.")
    print(f"Response from Groq: {completion.choices[0].message.content.strip()}")
except Exception as e:
    print("\nERROR calling Groq API:")
    print(e)
    sys.exit(1)
