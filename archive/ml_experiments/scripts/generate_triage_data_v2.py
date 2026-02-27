import random
import csv
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple


BASE_SCORES: Dict[str, float] = {
    # Red Flag
    "confusion": 9,
    "seizure": 9,
    "chest pain": 8,
    "shortness of breath": 8,
    "vision loss": 8,
    "slurred speech": 8,
    "numbness": 7,

    # Mental Health
    "suicidal thoughts": 9,
    "anxiety": 4,
    "depression": 4,
    "insomnia": 3,

    # Chronic-Related
    "asthma attack": 7,
    "low blood sugar": 7,
    "high blood sugar": 6,
    "hypertension": 5,
    "back pain": 3,
    "joint swelling": 3,

    # Common
    "fever": 4,
    "vomiting": 4,
    "headache": 3,
    "cough": 3,
    "fatigue": 3,
    "nausea": 3,
    "diarrhea": 3,
    "sore throat": 2,
    "runny nose": 2,
    "muscle aches": 2,

    # Other
    "allergic reaction": 6,
    "dizziness": 4,
    "UTI symptoms": 4,
    "rash": 2,
    "ear pain": 2,
}

RED_FLAG_SYMPTOMS = {
    "chest pain", "shortness of breath", "numbness", "vision loss",
    "confusion", "slurred speech", "seizure", "suicidal thoughts",
}

COMMON_SYMPTOMS = {
    "fever", "headache", "cough", "sore throat", "fatigue", "runny nose",
    "muscle aches", "nausea", "vomiting", "diarrhea",
}


def generate_ids(prefix: str, n: int, width: int = 5) -> List[str]:
    return [f"{prefix}_{i:0{width}d}" for i in range(1, n + 1)]


def rand_duration(symptom: str) -> float:
    if symptom in RED_FLAG_SYMPTOMS:
        return round(random.uniform(0.02, 2.0), 2)
    return round(random.uniform(0.5, 14.0), 2)


def compute_severity_score(
    primary_symptom: str,
    symptoms: List[str],
    duration_days: float,
    chronic_conditions_present: bool,
    has_primary_doctor: bool,
) -> Tuple[float, bool]:
    """
    v2 severity: base from primary symptom + small additive from other symptoms.
    red_flag=True if ANY symptom is a red flag.
    """
    red_flag = any(s in RED_FLAG_SYMPTOMS for s in symptoms)
    score = float(BASE_SCORES.get(primary_symptom, 3.0))

    # Additive effect: other symptoms add small increments
    for s in symptoms:
        if s == primary_symptom:
            continue
        score += min(0.6, BASE_SCORES.get(s, 3.0) * 0.08)

    if red_flag:
        score += 2.0
    if chronic_conditions_present:
        score += 1.0

    if duration_days > 7:
        if primary_symptom in RED_FLAG_SYMPTOMS:
            score += 1.5
        elif primary_symptom in COMMON_SYMPTOMS:
            score += 0.5
        else:
            score += 0.5

    if not has_primary_doctor:
        score += 0.5

    return min(score, 10.0), red_flag


def action_from_score(score: float) -> str:
    if score >= 8.0:
        return "refer_ER"
    if score >= 6.0:
        return "escalate_to_nurse"
    if score >= 4.0:
        return "refer_virtual_care"
    if score >= 2.0:
        return "refer_family_doc"
    return "self_care_advice"


def final_outcome_from_action(action: str) -> str:
    if action == "refer_ER":
        return random.choices(["ER_visit", "admitted", "left_without_care"], weights=[0.65, 0.2, 0.15])[0]
    if action == "escalate_to_nurse":
        return random.choices(["resolved", "callback_pending", "ER_visit"], weights=[0.55, 0.3, 0.15])[0]
    if action == "refer_virtual_care":
        return random.choices(["resolved", "callback_pending", "left_without_care"], weights=[0.6, 0.25, 0.15])[0]
    if action == "refer_family_doc":
        return random.choices(["resolved", "callback_pending", "left_without_care"], weights=[0.55, 0.25, 0.2])[0]
    return random.choices(["resolved", "callback_pending"], weights=[0.75, 0.25])[0]


def rand_wait_minutes(step: str) -> float:
    if step == "intake":
        return round(random.uniform(0.1, 2.0), 2)
    if step == "agent":
        return round(random.uniform(1.0, 20.0), 2)
    if step == "nurse":
        return round(random.uniform(1.0, 60.0), 2)
    return 0.0


def rand_step_duration(step: str) -> float:
    if step == "intake":
        return round(random.uniform(2.0, 8.0), 2)
    if step == "agent":
        return round(random.uniform(1.0, 6.0), 2)
    if step == "nurse":
        return round(random.uniform(5.0, 25.0), 2)
    return round(random.uniform(0.2, 1.0), 2)


def pick_symptom_bundle(symptom_list: List[str]) -> Tuple[str, List[str]]:
    """
    Choose 1 primary symptom and 0–3 additional symptoms.
    Small chance of red-flag symptom + co-occurring common symptoms.
    """
    # Primary symptom distribution: mostly common/other, some red flags
    if random.random() < 0.12:
        primary = random.choice(list(RED_FLAG_SYMPTOMS))
    else:
        primary = random.choice(symptom_list)

    extra_count = random.choices([0, 1, 2, 3], weights=[0.25, 0.4, 0.25, 0.1])[0]

    extras = set()
    while len(extras) < extra_count:
        # If primary is red flag, extras tend to be common symptoms
        pool = list(COMMON_SYMPTOMS) if primary in RED_FLAG_SYMPTOMS else symptom_list
        extras.add(random.choice(pool))

    symptoms = [primary] + [s for s in extras if s != primary]
    return primary, symptoms


def write_csv(rows: List[dict], path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def generate_v2(n_sessions: int = 800, seed: int = 42) -> Tuple[List[dict], List[dict]]:
    random.seed(seed)
    now = datetime.now()

    symptom_list = list(BASE_SCORES.keys())
    patient_ids = generate_ids("pt", max(50, n_sessions // 5), width=5)
    session_ids = generate_ids("sess", n_sessions, width=5)

    events: List[dict] = []
    sessions: List[dict] = []

    for sess_id in session_ids:
        patient_id = random.choice(patient_ids)

        primary, symptoms = pick_symptom_bundle(symptom_list)
        duration_days = rand_duration(primary)

        chronic_conditions_present = random.random() < 0.25
        has_primary_doctor = random.random() < 0.85

        severity_score, red_flag = compute_severity_score(
            primary_symptom=primary,
            symptoms=symptoms,
            duration_days=duration_days,
            chronic_conditions_present=chronic_conditions_present,
            has_primary_doctor=has_primary_doctor,
        )

        triage_confidence = round(random.uniform(0.45, 0.95), 2)
        if red_flag:
            triage_confidence = round(random.uniform(0.55, 0.92), 2)

        action = action_from_score(severity_score)
        final_outcome = final_outcome_from_action(action)

        base_time = now - timedelta(days=random.uniform(0, 30))
        t_intake = base_time
        t_agent = t_intake + timedelta(minutes=rand_wait_minutes("intake") + rand_step_duration("intake"))

        needs_nurse = (action in ("escalate_to_nurse", "refer_ER")) or (red_flag and triage_confidence < 0.75)
        t_nurse = t_agent + timedelta(minutes=rand_wait_minutes("agent") + rand_step_duration("agent")) if needs_nurse else None
        t_end = (t_nurse if t_nurse else t_agent) + timedelta(minutes=rand_wait_minutes("nurse") + rand_step_duration("nurse"))

        symptoms_list = " | ".join(symptoms)
        num_symptoms = len(symptoms)

        def add_event(step: str, ts: datetime, agent_type: str, conf: float | None, act: str, wait_next: float):
            events.append({
                "patient_id": patient_id,
                "session_id": sess_id,
                "event_step": step,
                "timestamp": ts.isoformat(timespec="seconds"),
                "primary_symptom": primary,
                "symptoms_list": symptoms_list,
                "num_symptoms": num_symptoms,
                "duration_symptom_days": duration_days,
                "severity_score": severity_score,
                "red_flag": red_flag,
                "chronic_conditions_present": chronic_conditions_present,
                "has_primary_doctor": has_primary_doctor,
                "agent_type": agent_type,
                "triage_confidence": conf,
                "action": act,
                "final_outcome": final_outcome,
                "triage_duration_minutes": rand_step_duration(step),
                "wait_time_to_next_step": wait_next,
            })

        add_event("intake", t_intake, "agent", None, "continue", rand_wait_minutes("intake"))
        add_event("agent", t_agent, "agent", triage_confidence, action if not needs_nurse else "escalate_to_nurse", rand_wait_minutes("agent"))
        if needs_nurse and t_nurse:
            nurse_action = "refer_ER" if action == "refer_ER" else "continue"
            add_event("nurse", t_nurse, "human", None, nurse_action, rand_wait_minutes("nurse"))
        add_event("end", t_end, "agent", None, "end_session", 0.0)

        sessions.append({
            "patient_id": patient_id,
            "session_id": sess_id,
            "primary_symptom": primary,
            "symptoms_list": symptoms_list,
            "num_symptoms": num_symptoms,
            "duration_symptom_days": duration_days,
            "severity_score": severity_score,
            "red_flag": red_flag,
            "chronic_conditions_present": chronic_conditions_present,
            "has_primary_doctor": has_primary_doctor,
            "agent_confidence_at_agent_step": triage_confidence,
            "recommended_action": action,
            "final_outcome": final_outcome,
            "needs_nurse": needs_nurse,
        })

    return events, sessions


def main():
    events, sessions = generate_v2(n_sessions=900, seed=123)
    write_csv(events, "ml_sandbox/data/triage_events_v2.csv")
    write_csv(sessions, "ml_sandbox/data/triage_sessions_v2.csv")
    print("Wrote:")
    print(" - ml_sandbox/data/triage_events_v2.csv")
    print(" - ml_sandbox/data/triage_sessions_v2.csv")


if __name__ == "__main__":
    main()
