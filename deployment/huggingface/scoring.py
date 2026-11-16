"""
Scoring: validate new leads, score them with the trained model, assign the
FR-04 priority tier and explain each score in plain words (FR-12).

The validation rules are the same as in SalesMesh_Score_New_Leads.ipynb, so a
lead gets the same answer in the app as in the notebook.
"""
import json
from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd

NUMERIC_COLS = ["TotalVisits", "Total Time Spent on Website", "Page Views Per Visit"]
CATEGORICAL_COLS = ["Lead Origin", "Lead Source", "Do Not Email", "Do Not Call", "Last Activity",
                    "Country", "Specialization", "How did you hear about X Education",
                    "What is your current occupation", "City", "Last Notable Activity"]
FEATURE_INPUT_COLS = ["Lead Origin", "Lead Source", "Do Not Email", "Do Not Call",
                      "TotalVisits", "Total Time Spent on Website", "Page Views Per Visit",
                      "Last Activity", "Country", "Specialization",
                      "How did you hear about X Education", "What is your current occupation",
                      "City", "Last Notable Activity"]

# Plain-language names used in explanations and forms
FRIENDLY_NAMES = {
    "Lead Origin": "Lead origin",
    "Lead Source": "Lead source",
    "Do Not Email": "Do-not-email request",
    "Do Not Call": "Do-not-call request",
    "TotalVisits": "Website visits",
    "Total Time Spent on Website": "Time on website",
    "Page Views Per Visit": "Pages viewed per visit",
    "Last Activity": "Last activity",
    "Country": "Country",
    "Specialization": "Specialisation",
    "How did you hear about X Education": "How they heard about us",
    "What is your current occupation": "Occupation",
    "City": "City",
    "Last Notable Activity": "Last notable activity",
}

MODEL_VERSION = "gb-v1"
MODEL_DIR = Path(__file__).resolve().parent  # the model files sit next to this file


class InputFileError(Exception):
    """The whole file is unusable (missing columns or no rows)."""


def assign_tier(prob: float) -> str:
    """FR-04 priority tiers."""
    if prob >= 0.70:
        return "High"
    if prob >= 0.40:
        return "Medium"
    return "Low"


@lru_cache(maxsize=1)
def load_reference(model_dir: str = str(MODEL_DIR)) -> dict:
    return json.loads((Path(model_dir) / "reference_values.json").read_text())


@lru_cache(maxsize=1)
def load_model(model_dir: str = str(MODEL_DIR)):
    return joblib.load(Path(model_dir) / "salesmesh_model.pkl")


def model_features(model) -> list:
    return list(model.named_steps["scaler"].feature_names_in_)


def validate(batch: pd.DataFrame, existing_ids: set, reference: dict):
    """Split a raw batch into valid rows and rejected rows with a 'Reason' column."""
    required = ["Prospect ID"] + FEATURE_INPUT_COLS
    missing = [c for c in required if c not in batch.columns]
    if missing:
        raise InputFileError("The file is missing required column(s): " + ", ".join(missing))
    if len(batch) == 0:
        raise InputFileError("The file contains no lead rows.")

    batch = batch.copy().reset_index(drop=True)
    for col in required:
        batch[col] = batch[col].fillna("").astype(str).str.strip()

    reasons = [[] for _ in range(len(batch))]
    ids = batch["Prospect ID"]
    repeated = ids.duplicated(keep="first") & (ids != "")
    for i, pid in enumerate(ids):
        if pid == "":
            reasons[i].append("Prospect ID is blank")
        elif len(pid) > 100:
            reasons[i].append("Prospect ID is longer than 100 characters")
        elif repeated[i]:
            reasons[i].append(f"Prospect ID {pid} appears more than once in the file")
        elif pid in existing_ids:
            reasons[i].append(f"Prospect ID {pid} already exists in the database")

    for col in NUMERIC_COLS:
        values = pd.to_numeric(batch[col], errors="coerce")
        for i, (raw, num) in enumerate(zip(batch[col], values)):
            if raw == "":
                reasons[i].append(f"{col} is missing")
            elif pd.isna(num):
                reasons[i].append(f"{col} must be a number (got '{raw}')")
            elif num < 0:
                reasons[i].append(f"{col} cannot be negative (got {raw})")

    allowed = reference["allowed_values"]
    for col in CATEGORICAL_COLS:
        for i, raw in enumerate(batch[col]):
            if raw == "" or raw == "Select":
                reasons[i].append(f"{col} is missing")
            elif raw not in allowed[col]:
                reasons[i].append(f"{col} has an unknown value '{raw}'")

    batch["Reason"] = ["; ".join(r) for r in reasons]
    ok = batch["Reason"] == ""
    valid = batch[ok].drop(columns="Reason").reset_index(drop=True)
    rejected = batch[~ok].reset_index(drop=True)
    for col in NUMERIC_COLS:
        valid[col] = valid[col].astype(float)
    return valid, rejected


def encode(rows: pd.DataFrame, features: list) -> pd.DataFrame:
    """One-hot encode and line the columns up exactly with the model's features."""
    encoded = pd.get_dummies(rows[FEATURE_INPUT_COLS]).astype(float)
    return encoded.reindex(columns=features, fill_value=0.0)


def predict(rows: pd.DataFrame, model=None) -> pd.Series:
    model = model or load_model()
    return pd.Series(model.predict_proba(encode(rows, model_features(model)))[:, 1], index=rows.index)


def _describe(col: str, value) -> str:
    name = FRIENDLY_NAMES[col]
    if col in NUMERIC_COLS:
        return f"{name} ({value:g})"
    return f"{name} '{value}'"


def explain(row: pd.Series, model=None, reference=None, top_n: int = 3, min_effect: float = 0.02) -> list:
    """
    What-if explanation: for each input, swap in the typical value from the
    training data and see how much the score moves. The inputs that move it
    most are this lead's strongest reasons.
    """
    model = model or load_model()
    reference = reference or load_reference()
    baseline = reference["baseline"]
    base = pd.DataFrame([row[FEATURE_INPUT_COLS]])
    variants, cols = [base], []
    for col in FEATURE_INPUT_COLS:
        typical = baseline[col]
        same = (float(row[col]) == float(typical)) if col in NUMERIC_COLS else (str(row[col]) == str(typical))
        if same:
            continue
        v = base.copy()
        v[col] = typical
        variants.append(v)
        cols.append(col)
    if not cols:
        return []
    probs = predict(pd.concat(variants, ignore_index=True), model).values
    actual = probs[0]
    effects = sorted(((actual - p, c) for p, c in zip(probs[1:], cols)), key=lambda t: abs(t[0]), reverse=True)
    reasons = []
    for effect, col in effects[:top_n]:
        if abs(effect) < min_effect:
            continue
        direction = "raises" if effect > 0 else "lowers"
        reasons.append(f"{_describe(col, row[col])} {direction} the score by {abs(effect) * 100:.0f} points")
    return reasons


def score_leads(valid: pd.DataFrame, model=None, reference=None, with_reasons: bool = True) -> pd.DataFrame:
    """Add conversion_probability, priority_tier and top_reasons to validated leads."""
    model = model or load_model()
    reference = reference or load_reference()
    scored = valid.copy()
    scored["conversion_probability"] = predict(scored, model).values
    scored["priority_tier"] = scored["conversion_probability"].apply(assign_tier)
    if with_reasons:
        scored["top_reasons"] = [" | ".join(explain(r, model, reference)) for _, r in scored.iterrows()]
    return scored
