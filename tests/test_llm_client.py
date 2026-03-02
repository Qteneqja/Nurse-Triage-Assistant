"""
Test DeepSeek API Connection
"""
import os
from openai import OpenAI

# Load API key from environment — never hardcode
api_key = os.getenv("DEEPSEEK_API_KEY", "")
if not api_key:
    print("ERROR: DEEPSEEK_API_KEY environment variable is not set.")
    print("Set it via .env file or export it before running this test.")
    exit(1)

print(f"Testing DeepSeek API...")
print(f"API Key: {api_key[:10]}...{api_key[-4:]}")
print(f"Base URL: https://api.deepseek.com")
print()

try:
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )
    
    print("Sending test request...")
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Say 'Connection successful!' and nothing else."},
        ],
        stream=False
    )
    
    print("✅ SUCCESS!")
    print(f"Response: {response.choices[0].message.content}")
    print()
    print("DeepSeek API is working correctly!")
    
except Exception as e:
    print("❌ ERROR!")
    print(f"Error type: {type(e).__name__}")
    print(f"Error message: {str(e)}")
    print()
    print("Possible issues:")
    print("1. Invalid API key")
    print("2. Insufficient balance")
    print("3. API endpoint incorrect")
    print("4. Network/firewall blocking connection")
