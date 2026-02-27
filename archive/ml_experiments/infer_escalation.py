from __future__ import annotations

import os
import joblib
import pandas as pd


ARTIFACTS_DIR = "ml_sandbox/artifacts"
MODEL_PATH = os.path.join(ARTIFACTS_DIR, "escalation_model.joblib")


def predict_escalation_proba(event: dict) -> float:
    bundle = joblib.load(MODEL_PATH)
    pipe = bundle["pipeline"]
    feature_cols = bundle["feature_cols"]

    X = pd.DataFrame([{k: event.get(k) for k in feature_cols}])
    proba = pipe.predict_proba(X)[0][1]
    return float(proba)


def main() -> None:
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Missing {MODEL_PATH}. Run train_escalation_model.py first.")

    sample_event = {
        "symptom": "chest_pain",
        "duration_symptom_hours": 2.0,
        "severity_score": 9,
        "red_flag": True,
        "chronic_conditions_present": True,
        "has_primary_doctor": False,
        "language_preference": "English",
        "device_type": "mobile",
        "region": "winnipeg",
        "agent_type": "doctor",
    }

    p = predict_escalation_proba(sample_event)
    print(f"Escalation probability: {p:.3f} for sample_event={sample_event}")


if __name__ == "__main__":
    main()
