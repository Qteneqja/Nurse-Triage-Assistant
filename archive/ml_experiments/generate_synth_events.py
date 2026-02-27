
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any

import numpy as np
import pandas as pd

# -----------------------------
# Reproducibility
# -----------------------------
rng = np.random.default_rng(42)
random.seed(42)

# -----------------------------
# Paths
# -----------------------------
ROOT = "ml_sandbox"
ARTIFACTS_DIR = os.path.join(ROOT, "artifacts")
SYMPTOM_BANK_PATH = os.path.join(ROOT, "symptom_bank.json")

# -----------------------------
# Core demographics / context
# -----------------------------
SEXES = ["male", "female"]
SEX_WEIGHTS = [0.49, 0.51]

# (Optional) If you want more categories later, add them here.
# Right now we follow your DataDictionary style: sex is male/female.

# -----------------------------
# Symptom category sampling weights
# -----------------------------
CATEGORY_SAMPLE_WEIGHTS = {
    "common": 0.55,
    "chronic_related": 0.18,
    "other": 0.12,
    "mental_health": 0.08,
    "red_flag": 0.07
}


# -----------------------------
# Utilities
# -----------------------------
def generate_age() -> int:
    """
    0–95 inclusive, mixture distribution:
    - ~18% children/teens
    - ~60% working-age
    - ~22% seniors
    """
    roll = rng.random()
    if roll < 0.18:
        age = int(np.clip(rng.normal(12, 6), 0, 19))
    elif roll < 0.78:
        age = int(np.clip(rng.normal(40, 13), 20, 64))
    else:
        age = int(np.clip(rng.normal(74, 9), 65, 95))
    return age


def generate_duration_days(primary_category: str) -> float:
    """
    Duration in days (0–60). Category influences distribution slightly.
    """
    if primary_category == "red_flag":
        days = float(np.clip(rng.gamma(shape=1.6, scale=0.8), 0.0, 14.0))  # more acute
    elif primary_category == "common":
        days = float(np.clip(rng.gamma(shape=2.0, scale=2.0), 0.0, 30.0))
    elif primary_category == "mental_health":
        days = float(np.clip(rng.gamma(shape=2.4, scale=4.0), 0.0, 60.0))
    else:
        days = float(np.clip(rng.gamma(shape=2.2, scale=2.8), 0.0, 60.0))

    # sprinkle some very acute same-day cases
    if rng.random() < 0.15:
        days = float(np.clip(rng.normal(0.2, 0.2), 0.0, 2.0))

    return days


def severity_score(
    base: float,
    red_flag: bool,
    chronic_conditions_present: bool,
    duration_symptom_days: float,
    has_primary_doctor: bool,
    symptom_category: str,
) -> float:
    """
    Severity scoring (0–10) per your rules:
    - start = base score
    - +2 if red_flag
    - +1 if chronic
    - if duration > 7 days: +1.5 for red_flag, +0.5 for common, +0.75 otherwise
    - +0.5 if no primary doctor
    - cap at 10
    """
    score = float(base)

    if red_flag:
        score += 2.0
    if chronic_conditions_present:
        score += 1.0

    if duration_symptom_days > 7.0:
        if symptom_category == "red_flag":
            score += 1.5
        elif symptom_category == "common":
            score += 0.5
        else:
            score += 0.75

    if not has_primary_doctor:
        score += 0.5

    return float(np.clip(score, 0.0, 10.0))


def action_from_score(score: float) -> str:
    """
    Thresholds:
    - >= 8: refer_ER
    - 6–7.99: escalate_to_nurse
    - 4–5.99: refer_virtual_care
    - 2–3.99: refer_family_doc
    - <2: self_care_advice
    """
    if score >= 8.0:
        return "refer_ER"
    if score >= 6.0:
        return "escalate_to_nurse"
    if score >= 4.0:
        return "refer_virtual_care"
    if score >= 2.0:
        return "refer_family_doc"
    return "self_care_advice"


# -----------------------------
# Symptom bank loading (robust)
# -----------------------------
def _extract_base_score(v: Any) -> float:
    """
    Accepts either:
      - number: 9
      - dict: {"base_score": 9, ...} or {"score": 9, ...}
    """
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for key in ("base_score", "score", "base", "value"):
            if key in v and isinstance(v[key], (int, float)):
                return float(v[key])
    raise TypeError(f"Cannot extract numeric base score from value: {v!r}")


def load_symptom_bank() -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str, float]]]:
    """
    Returns:
      bank: raw json
      flat: list[(category, symptom, base_score)]
    """
    if not os.path.exists(SYMPTOM_BANK_PATH):
        raise FileNotFoundError(
            f"Missing {SYMPTOM_BANK_PATH}. Create ml_sandbox/symptom_bank.json first."
        )

    with open(SYMPTOM_BANK_PATH, "r", encoding="utf-8") as f:
        bank = json.load(f)

    flat: List[Tuple[str, str, float]] = []
    for category, items in bank.items():
        if not isinstance(items, dict):
            continue
        for symptom, v in items.items():
            base = _extract_base_score(v)
            flat.append((category, symptom, base))

    if len(flat) < 5:
        raise ValueError("symptom_bank.json must contain at least 5 symptoms total.")

    return bank, flat


def pick_five_symptoms(flat: List[Tuple[str, str, float]]) -> List[Tuple[str, str, float]]:
    """
    Always returns EXACTLY 5 UNIQUE symptoms.
    First is primary.
    """
    if len(flat) < 5:
        raise ValueError("Symptom bank must contain at least 5 symptoms.")

    weights = [CATEGORY_SAMPLE_WEIGHTS.get(cat, 0.1) for (cat, _, _) in flat]

    # Start with weighted picks
    picked = random.choices(flat, weights=weights, k=8)  # over-sample a bit to help uniqueness

    unique: List[Tuple[str, str, float]] = []
    seen = set()
    for item in picked:
        if item[1] not in seen:
            unique.append(item)
            seen.add(item[1])
        if len(unique) == 5:
            break

    # If still short, fill remaining without replacement
    if len(unique) < 5:
        remaining = [s for s in flat if s[1] not in seen]
        if len(remaining) < (5 - len(unique)):
            raise ValueError("Not enough unique symptoms in bank to pick 5.")
        unique.extend(random.sample(remaining, k=5 - len(unique)))

    return unique


# -----------------------------
# Main generator (row-level)
# -----------------------------
def generate_rows(n_patients: int = 1500, n_rows: int = 6000) -> pd.DataFrame:
    """
    Row-level dataset:
    - Exactly 5 symptom columns per row.
    - Primary symptom drives severity and action.
    """
    _bank, flat = load_symptom_bank()

    patient_ids = [f"pt_{i:05d}" for i in range(1, n_patients + 1)]
    now = datetime.now(timezone.utc)

    rows: List[dict] = []

    for _ in range(n_rows):
        patient_id = random.choice(patient_ids)
        age = generate_age()
        sex = random.choices(SEXES, weights=SEX_WEIGHTS, k=1)[0]

        # simple priors
        has_primary_doctor = bool(rng.random() < 0.65)
        chronic_conditions_present = bool(rng.random() < 0.28)

        symptoms = pick_five_symptoms(flat)

        # Primary symptom
        primary_category, primary_symptom, base = symptoms[0]
        red_flag = (primary_category == "red_flag")

        duration_days = generate_duration_days(primary_category)

        sev = severity_score(
            base=base,
            red_flag=red_flag,
            chronic_conditions_present=chronic_conditions_present,
            duration_symptom_days=duration_days,
            has_primary_doctor=has_primary_doctor,
            symptom_category=primary_category,
        )

        action = action_from_score(sev)

        # Timestamp in last 14 days
        minutes_ago = int(rng.integers(0, 60 * 24 * 14))
        ts = (now - pd.Timedelta(minutes=minutes_ago)).isoformat()

        rows.append({
            "patient_id": patient_id,
            "age": age,
            "sex": sex,
            "timestamp": ts,

            # primary symptom (also equals symptom_1)
            "symptom": primary_symptom,

            # exactly 5 symptoms (always filled)
            "symptom_1": symptoms[0][1],
            "symptom_2": symptoms[1][1],
            "symptom_3": symptoms[2][1],
            "symptom_4": symptoms[3][1],
            "symptom_5": symptoms[4][1],

            "duration_symptom_days": float(round(duration_days, 3)),
            "severity_score": float(round(sev, 2)),
            "red_flag": bool(red_flag),
            "chronic_conditions_present": bool(chronic_conditions_present),
            "has_primary_doctor": bool(has_primary_doctor),
            "action": action,
        })

    return pd.DataFrame(rows)


def main() -> None:
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    df = generate_rows(n_patients=1500, n_rows=6000)

    # Avoid Windows PermissionError by writing a new filename each run
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(ARTIFACTS_DIR, f"events_{stamp}.csv")

    df.to_csv(out_path, index=False)

    print(f"Wrote {len(df):,} rows to {out_path}")
    print(df.head(8).to_string(index=False))


if __name__ == "__main__":
    main()
