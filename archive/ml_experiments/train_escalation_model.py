from __future__ import annotations

import os
import joblib
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report
from sklearn.linear_model import LogisticRegression


ARTIFACTS_DIR = "ml_sandbox/artifacts"
EVENTS_CSV = os.path.join(ARTIFACTS_DIR, "events.csv")
MODEL_PATH = os.path.join(ARTIFACTS_DIR, "escalation_model.joblib")


def main() -> None:
    if not os.path.exists(EVENTS_CSV):
        raise FileNotFoundError(f"Missing {EVENTS_CSV}. Run generate_synth_events.py first.")

    df = pd.read_csv(EVENTS_CSV)

    # We train only on "decision" rows so the target is meaningful
    df = df[df["event_step"] == "decision"].copy()

    # Target: escalated (binary)
    y = df["escalated"].astype(int)

    # Features: keep simple & non-leaky
    feature_cols = [
        "symptom",
        "duration_symptom_hours",
        "severity_score",
        "red_flag",
        "chronic_conditions_present",
        "has_primary_doctor",
        "language_preference",
        "device_type",
        "region",
        "agent_type",
    ]
    X = df[feature_cols].copy()

    categorical = ["symptom", "language_preference", "device_type", "region", "agent_type"]
    numeric = [
        "duration_symptom_hours",
        "severity_score",
        "red_flag",
        "chronic_conditions_present",
        "has_primary_doctor",
    ]

    pre = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical),
            ("num", "passthrough", numeric),
        ]
    )

    model = LogisticRegression(max_iter=200, class_weight="balanced")

    pipe = Pipeline(steps=[("pre", pre), ("model", model)])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    pipe.fit(X_train, y_train)
    preds = pipe.predict(X_test)

    print("=== Escalation model report (decision-step rows) ===")
    print(classification_report(y_test, preds, digits=3))

    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    joblib.dump({"pipeline": pipe, "feature_cols": feature_cols}, MODEL_PATH)
    print(f"Saved model to {MODEL_PATH}")


if __name__ == "__main__":
    main()
