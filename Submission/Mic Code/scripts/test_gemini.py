import os
import sys
from dotenv import load_dotenv

# Load env variables
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

if not api_key:
    print("ERROR: GEMINI_API_KEY is not set in your .env file!")
    sys.exit(1)

print(f"Testing Gemini API Key: {api_key[:8]}...{api_key[-4:] if len(api_key) > 8 else ''}")
print(f"Using Model: {model_name}")

try:
    from google import genai
    from google.genai import types
    has_modern_sdk = True
except ImportError:
    has_modern_sdk = False

try:
    if has_modern_sdk and hasattr(genai, 'Client'):
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents="Hello! Respond with 'API connection successful!' if you can hear me."
        )
        print("\nSUCCESS! Connection established (Modern SDK).")
        print(f"Response from Gemini: {response.text.strip()}")
    else:
        import google.generativeai as legacy_genai
        legacy_genai.configure(api_key=api_key)
        model = legacy_genai.GenerativeModel(model_name)
        response = model.generate_content("Hello! Respond with 'API connection successful!' if you can hear me.")
        print("\nSUCCESS! Connection established (Legacy SDK).")
        print(f"Response from Gemini: {response.text.strip()}")
except Exception as e:
    print("\nERROR calling Gemini API:")
    print(e)
    sys.exit(1)
