"""Healthcare vertical package."""

__all__ = ["HealthcareTriageWorkflow"]


def __getattr__(name: str):
    if name == "HealthcareTriageWorkflow":
        from src.verticals.healthcare.workflow import HealthcareTriageWorkflow

        return HealthcareTriageWorkflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
