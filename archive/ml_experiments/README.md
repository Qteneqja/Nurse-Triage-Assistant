# ML Sandbox — Virtual Triage Decision Support (Experimental)

This folder contains **experimental machine learning work** used to explore
decision-support ideas for an AI-powered virtual healthcare triage system.

**Important**
- This code is **not part of the core triage logic**
- It does **not replace rules-based triage**
- It uses **synthetic (simulated) data only**
- It is intended for **prototyping, learning, and demonstration**

The production triage flow lives elsewhere in this repository and works
independently of anything in this folder.

---

## Purpose

The goal of this sandbox is to answer questions like:

- Can a model estimate the **probability of escalation** based on patient context?
- Can we learn patterns from simulated triage sessions to support decisions?
- How might ML assist (not override) human or rules-based triage systems?

This reflects how real healthcare systems use ML:
> as **decision support**, not decision authority.

---

## Dataset Design (Synthetic)

We generate a **synthetic event-level dataset** that simulates realistic
virtual triage sessions similar to Canadian nurse advice lines.

### Key characteristics
- Each row represents **one step** in a triage session
- Multiple rows share the same `session_id`
- Sessions may resolve via:
  - self-care
  - nurse escalation
  - ER referral
- Wait times, routing, and escalation follow **plausible constraints**
- No real patient data is used

The dataset supports:
- App testing / UI prototyping
- ML experimentation (classification)
- System efficiency analysis

---

## Machine Learning Tasks

Currently implemented:

### 1. Escalation Prediction (Event-Level)
- **Target:** `escalated` (yes / no)
- **Scope:** decision-step events only
- **Model:** Logistic Regression (interpretable, fast)
- **Purpose:** estimate escalation risk to support triage decisions

This model is **assistive only** and is not used to make final clinical decisions.

---

## Folder Structure

