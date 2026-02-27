import requests

base = "http://127.0.0.1:8001"

start = requests.post(f"{base}/api/v1/intake/start", json={}).json()
sid = start["session_id"]

r1 = requests.post(f"{base}/api/v1/intake/{sid}/answer", json={"answer": "Qlirim"}).json()
print("Q1:", r1.get("next_question"))

r2 = requests.post(f"{base}/api/v1/intake/{sid}/answer", json={"answer": "25"}).json()
print("Q2:", r2.get("next_question"))

if r2.get("next_question") and "sex" not in r2.get("next_question").lower():
    raise SystemExit("Expected sex question after age")

print("OK")
