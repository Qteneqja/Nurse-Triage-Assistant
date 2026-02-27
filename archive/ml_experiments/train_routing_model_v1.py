import os
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.linear_model import LogisticRegression


DATA_PATH = os.path.join("ml_sandbox", "data", "triage_sessions_v1.csv")

TARGET = "recommended_action"

FEATURES = [
    "symptom",
    "duration_symptom_days",
    "severity_score",
    "red_flag",
    "chronic_conditions_present",
    "has_primary_doctor",
    "agent_confidence_at_agent_step",
    "needs_nurse",
]


def main():
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Missing dataset: {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)

    # Basic sanity checks
    missing = [c for c in [TARGET] + FEATURES if c not in df.columns]
    if missing:
        raise ValueError(f"Dataset missing columns: {missing}")

    X = df[FEATURES].copy()
    y = df[TARGET].copy()

    # Define column types
    categorical = ["symptom"]
    numeric = [
        "duration_symptom_days",
        "severity_score",
        "agent_confidence_at_agent_step",
    ]
    boolean_cols = [
        "red_flag",
        "chronic_conditions_present",
        "has_primary_doctor",
        "needs_nurse",
    ]

    # Convert booleans safely (in case they load as True/False or 0/1 strings)
    for b in boolean_cols:
        X[b] = X[b].astype(int)

    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical),
            ("num", "passthrough", numeric + boolean_cols),
        ]
    )

    model = LogisticRegression(
        max_iter=500,
        n_jobs=None,
        multi_class="auto",
    )

    clf = Pipeline(steps=[("prep", preprocessor), ("model", model)])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    clf.fit(X_train, y_train)

    preds = clf.predict(X_test)

    print("=== Routing Model (v1 sessions) ===")
    print(f"Rows: {len(df)} | Train: {len(X_train)} | Test: {len(X_test)}")
    print("\nClassification report:")
    print(classification_report(y_test, preds, zero_division=0))

    print("Confusion matrix (rows=true, cols=pred):")
    print(confusion_matrix(y_test, preds))


if __name__ == "__main__":
    main()
