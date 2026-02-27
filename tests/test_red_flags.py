"""
Tests for deterministic red-flag rules.

Covers:
- Each rule category triggers on expected patterns
- Rules do NOT trigger on innocuous input
- State-based checks
- check_all combines both methods
"""
import pytest
from src.safety.red_flags import check_utterance, check_state, check_all


# -----------------------------------------------------------------------
# Breathing difficulty
# -----------------------------------------------------------------------

class TestBreathingDifficulty:
    @pytest.mark.parametrize("utterance", [
        "I can't breathe",
        "I cannot breathe properly",
        "I'm having trouble breathing",
        "I'm having difficulty breathing",
        "I'm struggling to breathe",
        "I'm gasping for air",
        "My lips are turning blue",
        "I can't get enough air",
        "I have very severe shortness of breath",
    ])
    def test_triggers(self, utterance: str):
        result = check_utterance(utterance)
        assert result.triggered is True
        assert "severe_breathing_difficulty" in result.matched_rules
        assert result.script_to_say is not None
        assert "9 1 1" in result.script_to_say

    @pytest.mark.parametrize("utterance", [
        "I have a mild cough",
        "My breathing is fine",
        "I took a deep breath",
        "The air quality is bad today",
    ])
    def test_does_not_trigger(self, utterance: str):
        result = check_utterance(utterance)
        assert result.triggered is False


# -----------------------------------------------------------------------
# Chest pain
# -----------------------------------------------------------------------

class TestChestPain:
    @pytest.mark.parametrize("utterance", [
        "I have crushing chest pain",
        "There is a squeezing pressure in my chest",
        "I think I'm having a heart attack",
        "I have chest pain radiating to my arm",
        "I have severe chest pain and I'm sweating",
        "I have the worst chest pain of my life",
    ])
    def test_triggers(self, utterance: str):
        result = check_utterance(utterance)
        assert result.triggered is True
        assert "severe_chest_pain" in result.matched_rules

    @pytest.mark.parametrize("utterance", [
        "My chest itches from the rash",
        "I have mild chest discomfort after exercise",
        "I fell and hit my chest",
        "My chest feels tight",
        "I have some tightness in my chest that comes and goes",
        "My chest feels heavy sometimes",
    ])
    def test_does_not_trigger(self, utterance: str):
        result = check_utterance(utterance)
        assert "severe_chest_pain" not in result.matched_rules


# -----------------------------------------------------------------------
# Stroke symptoms
# -----------------------------------------------------------------------

class TestStrokeSymptoms:
    @pytest.mark.parametrize("utterance", [
        "My face is drooping on one side",
        "My arm is weak and I can't move it",
        "I can't speak properly, words are slurred",
        "I'm having slurred speech",
        "I have the worst headache of my life",
        "One side of my body is not working",
        "I think I'm having a stroke",
    ])
    def test_triggers(self, utterance: str):
        result = check_utterance(utterance)
        assert result.triggered is True
        assert "stroke_symptoms" in result.matched_rules

    def test_does_not_trigger(self):
        result = check_utterance("I have a mild headache")
        assert "stroke_symptoms" not in result.matched_rules


# -----------------------------------------------------------------------
# Allergic reaction
# -----------------------------------------------------------------------

class TestAllergicReaction:
    @pytest.mark.parametrize("utterance", [
        "My throat is swelling shut",
        "I think I'm having anaphylaxis",
        "I can't swallow and my tongue is swelling",
        "I had a severe allergic reaction to peanuts",
        "I need my epipen",
    ])
    def test_triggers(self, utterance: str):
        result = check_utterance(utterance)
        assert result.triggered is True
        assert "severe_allergic_reaction" in result.matched_rules


# -----------------------------------------------------------------------
# Uncontrolled bleeding
# -----------------------------------------------------------------------

class TestUncontrolledBleeding:
    @pytest.mark.parametrize("utterance", [
        "The bleeding won't stop",
        "I have heavy bleeding from the wound",
        "Blood is pouring out everywhere",
        "I'm soaked through in blood",
    ])
    def test_triggers(self, utterance: str):
        result = check_utterance(utterance)
        assert result.triggered is True
        assert "uncontrolled_bleeding" in result.matched_rules


# -----------------------------------------------------------------------
# Loss of consciousness
# -----------------------------------------------------------------------

class TestLossOfConsciousness:
    @pytest.mark.parametrize("utterance", [
        "I passed out earlier",
        "I lost consciousness",
        "My husband is unresponsive",
        "She collapsed on the floor",
        "He's having a seizure",
        "My child won't wake up",
    ])
    def test_triggers(self, utterance: str):
        result = check_utterance(utterance)
        assert result.triggered is True
        assert "loss_of_consciousness" in result.matched_rules


# -----------------------------------------------------------------------
# Suicidal / self-harm
# -----------------------------------------------------------------------

class TestSuicidalSelfHarm:
    @pytest.mark.parametrize("utterance", [
        "I want to kill myself",
        "I'm thinking about suicide",
        "I don't want to live anymore",
        "I've been self-harming",
        "I'd be better off dead",
        "I want to end it all",
    ])
    def test_triggers(self, utterance: str):
        result = check_utterance(utterance)
        assert result.triggered is True
        assert "suicidal_self_harm" in result.matched_rules
        assert result.script_to_say is not None
        assert "9 8 8" in result.script_to_say  # Crisis lifeline number

    def test_does_not_trigger_on_unrelated(self):
        result = check_utterance("I'm feeling a bit down today")
        assert "suicidal_self_harm" not in result.matched_rules


# -----------------------------------------------------------------------
# Multiple rules
# -----------------------------------------------------------------------

class TestMultipleRules:
    def test_multiple_rules_can_match(self):
        result = check_utterance(
            "I have crushing chest pain and I can't breathe and I think I'm having a heart attack"
        )
        assert result.triggered is True
        assert len(result.matched_rules) >= 2

    def test_no_rules_on_innocuous(self):
        result = check_utterance("I have a mild cold and runny nose")
        assert result.triggered is False
        assert result.matched_rules == []


# -----------------------------------------------------------------------
# State-based checks
# -----------------------------------------------------------------------

class TestStateChecks:
    def test_chief_complaint_triggers(self):
        result = check_state(chief_complaint="I'm having a heart attack")
        assert result.triggered is True

    def test_severe_confusion_with_severe_symptoms(self):
        result = check_state(
            confusion_score=0.9,
            symptom_severity="severe",
        )
        assert result.triggered is True
        assert "severe_confusion_with_severe_symptoms" in result.matched_rules

    def test_llm_reported_critical_flags(self):
        result = check_state(
            red_flags_reported=["chest pain with radiation"]
        )
        assert result.triggered is True

    def test_normal_state_no_trigger(self):
        result = check_state(
            chief_complaint="mild headache",
            symptom_severity="mild",
            confusion_score=0.1,
        )
        assert result.triggered is False


# -----------------------------------------------------------------------
# check_all combines utterance + state
# -----------------------------------------------------------------------

class TestCheckAll:
    def test_utterance_takes_priority(self):
        result = check_all(
            utterance="I can't breathe",
            chief_complaint="mild cold",
        )
        assert result.triggered is True
        assert "severe_breathing_difficulty" in result.matched_rules

    def test_state_triggers_when_utterance_clean(self):
        result = check_all(
            utterance="yes that's right",
            red_flags_reported=["uncontrolled bleeding noted"],
        )
        assert result.triggered is True

    def test_nothing_triggers(self):
        result = check_all(
            utterance="I have a mild headache for two days",
            chief_complaint="headache",
            symptom_severity="mild",
        )
        assert result.triggered is False
