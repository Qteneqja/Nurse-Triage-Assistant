"""Demo scenarios for hackathon presentation"""

DEMO_SCENARIOS = {
    "scenario_a": {
        "name": "Non-Urgent Care",
        "description": "Common cold symptoms - should route to primary care",
        "answers": {
            "name": "Alex Morgan",
            "age": "29",
            "sex": "female",
            "category": "other",
            "complaint": "I've had a runny nose, sore throat, and a mild cough for a few days",
            "duration": "About 3 days",
            "onset": "gradual",
            "severity": "Moderate, about 5 out of 10"
        },
        "yes_no": {
            "chest_pain_now": "No",
            "can_speak_full_sentences": "Yes",
            "fainted_or_altered": "No",
            "heavy_bleeding": "No",
            "fever_present": "No",
            "dehydration_signs": "No"
        },
        "followups": [
            "I have some nasal congestion and a scratchy throat",
            "No chest pain or shortness of breath",
            "No vomiting or diarrhea",
            "I've been taking fluids and resting"
        ]
    },

    "scenario_b": {
        "name": "Red Flag Emergency",
        "description": "Severe chest pain - should route to emergency",
        "answers": {
            "name": "Michael Carter",
            "age": "54",
            "sex": "male",
            "category": "chest",
            "complaint": "I'm having severe chest pain",
            "duration": "It started about 20 minutes ago",
            "onset": "sudden",
            "severity": "Severe, 9 out of 10"
        },
        "yes_no": {
            "chest_pain_now": "Yes",
            "can_speak_full_sentences": "Yes",
            "fainted_or_altered": "No",
            "heavy_bleeding": "No",
            "fever_present": "No",
            "dehydration_signs": "No"
        },
        "followups": [
            "It's crushing pain in the center of my chest",
            "I feel short of breath and I'm sweating",
            "The pain is radiating down my left arm",
            "I feel nauseous and lightheaded",
            "It started while I was resting",
            "Nothing seems to make it better"
        ]
    },

    "scenario_c": {
        "name": "Ambiguous Case",
        "description": "Moderate abdominal pain - tests LLM reasoning",
        "answers": {
            "name": "Sara Patel",
            "age": "26",
            "sex": "female",
            "category": "stomach",
            "complaint": "I have stomach pain that's been bothering me",
            "duration": "It's been getting worse over the last 6 hours",
            "onset": "gradual",
            "severity": "Moderate, about 6 out of 10"
        },
        "yes_no": {
            "chest_pain_now": "No",
            "can_speak_full_sentences": "Yes",
            "fainted_or_altered": "No",
            "heavy_bleeding": "No",
            "fever_present": "Yes",
            "dehydration_signs": "No"
        },
        "followups": [
            "It's in my lower right abdomen",
            "I feel a bit nauseous",
            "No vomiting, but I have no appetite",
            "Maybe a slight fever, I feel warm"
        ]
    }
}
