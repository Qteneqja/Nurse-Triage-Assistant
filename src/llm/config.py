"""
LLM Configuration
Person A: Environment-based configuration for DeepSeek
"""
import os
from dotenv import load_dotenv

load_dotenv()

# DeepSeek Configuration (Reasoning LLM)
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Twilio Configuration (optional)
TWILIO_WEBHOOK_BASE_URL = os.getenv("TWILIO_WEBHOOK_BASE_URL", "")

# Mock mode for testing
USE_MOCK_LLM = os.getenv("USE_MOCK_LLM", "false").lower() == "true"

# Timeout settings
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
