"""
Triage API Server Startup Script
Ensures server starts from correct directory with proper environment
"""
import os
import sys

# Ensure we're in the project directory
project_dir = r"C:\Users\QTene\Nurse-Triage-Assistant"
os.chdir(project_dir)
sys.path.insert(0, project_dir)

print("=" * 60)
print(" TRIAGE API SERVER")
print("=" * 60)
print(f"Working Directory: {os.getcwd()}")
print(f"Python: {sys.executable}")

# Verify .env file exists
env_path = os.path.join(project_dir, ".env")
if os.path.exists(env_path):
    print("[OK] Found .env file")
else:
    print(f"[WARNING] .env file not found at {env_path}")

# Load config and display
from src.llm.config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL  # noqa: E402

print(f"[OK] DeepSeek URL: {DEEPSEEK_BASE_URL}")
print(f"[OK] DeepSeek Model: {DEEPSEEK_MODEL}")
print("=" * 60)
print("\nStarting server on http://127.0.0.1:8000")
print("Press CTRL+C to stop\n")

# Start uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, reload=True)
