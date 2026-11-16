"""
SalesMesh lead scorer: the SalesMesh Gradient Boosting model served on Hugging Face Spaces.

People can score one lead in a form or a whole CSV file. Programs can call the same
model through the API (see "Use via API" at the bottom of the page), for example:

    from gradio_client import Client
    client = Client("YOUR-HF-USERNAME/salesmesh-lead-scorer")
    result = client.predict(..., api_name="/score_lead")
"""
import tempfile
from pathlib import Path

import gradio as gr
import pandas as pd

import scoring
from scoring import FEATURE_INPUT_COLS, FRIENDLY_NAMES, NUMERIC_COLS

REFERENCE = scoring.load_reference()
MODEL = scoring.load_model()
ALLOWED = REFERENCE["allowed_values"]
BASELINE = REFERENCE["baseline"]
FIRST_CONTACT_DAYS = {"High": 1, "Medium": 3, "Low": 7}
MAX_CSV_ROWS = 1000
TMP = Path(tempfile.gettempdir())


def _options(col):
    return [v for v in ALLOWED[col] if v != "Select"]


def score_lead(*values):
    """Score one lead. Returns a readable summary and the same result as JSON."""
    row = {"Prospect ID": "WEB-1", **dict(zip(FEATURE_INPUT_COLS, values))}
    batch = pd.DataFrame([row]).astype(str)
    valid, rejected = scoring.validate(batch, set(), REFERENCE)
    if valid.empty:
        message = rejected["Reason"].iloc[0]
        return f"### Cannot score this lead\n{message}", {"error": message}
    scored = scoring.score_leads(valid, MODEL, REFERENCE).iloc[0]
    prob, tier = float(scored["conversion_probability"]), scored["priority_tier"]
    reasons = [r for r in scored["top_reasons"].split(" | ") if r]
    days = FIRST_CONTACT_DAYS[tier]
    summary = (f"## {prob * 100:.0f}% chance of converting · {tier} priority\n"
               f"First contact due within **{days} day(s)**.\n\n**Why:**\n"
               + ("\n".join(f"- {r}" for r in reasons) if reasons
                  else "- No single detail moves this score much; it is close to a typical lead."))
    result = {"conversion_probability": round(prob, 4), "priority_tier": tier,
              "first_contact_within_days": days, "reasons": reasons, "model_version": scoring.MODEL_VERSION}
    return summary, result


def score_csv(file):
    """Score every row of a CSV file; rows with problems are returned with the reason."""
    if file is None:
        raise gr.Error("Choose a CSV file first.")
    try:
        batch = pd.read_csv(file, dtype=str, keep_default_na=False)
    except Exception:
        raise gr.Error("This file could not be read as a CSV.")
    if len(batch) > MAX_CSV_ROWS:
        raise gr.Error(f"Score at most {MAX_CSV_ROWS} leads at a time (this file has {len(batch)}).")
    try:
        valid, rejected = scoring.validate(batch, set(), REFERENCE)
    except scoring.InputFileError as e:
        raise gr.Error(str(e))
    note = f"{len(valid)} lead(s) scored, {len(rejected)} rejected."
    if valid.empty:
        scored_view = pd.DataFrame()
        scored_path = None
    else:
        scored = scoring.score_leads(valid, MODEL, REFERENCE)
        scored_view = pd.DataFrame({
            "Prospect ID": scored["Prospect ID"],
            "Score %": (scored["conversion_probability"] * 100).round(1),
            "Tier": scored["priority_tier"],
            "Contact within (days)": scored["priority_tier"].map(FIRST_CONTACT_DAYS),
            "Why": scored["top_reasons"].str.replace(" | ", "; ", regex=False).replace("", "Close to a typical lead"),
        }).sort_values("Score %", ascending=False)
        scored_path = TMP / "salesmesh_scored_leads.csv"
        scored_view.to_csv(scored_path, index=False)
    rejected_view = rejected[["Prospect ID", "Reason"]] if not rejected.empty else pd.DataFrame()
    return note, scored_view, (str(scored_path) if scored_path else None), rejected_view


def write_template():
    path = TMP / "salesmesh_leads_template.csv"
    rows = [{"Prospect ID": "LEAD-001", **BASELINE},
            {"Prospect ID": "LEAD-002", **BASELINE, "Total Time Spent on Website": 1234,
             "What is your current occupation": "Working Professional", "Lead Source": "Reference"}]
    pd.DataFrame(rows)[["Prospect ID"] + FEATURE_INPUT_COLS].to_csv(path, index=False)
    return str(path)


ABOUT = """
### About the model
- **Algorithm:** Gradient Boosting (100 trees, learning rate 0.1, depth 5), with min-max scaling and SMOTE
  for the class imbalance, chosen over Logistic Regression and Random Forest by 10-fold cross-validated F1.
- **Data:** 9,240 leads from the public *X Education* lead-scoring dataset (Kaggle); 80/20 stratified split.
- **Results on 1,848 test leads it never saw:** F1 0.764 · ROC-AUC 0.878 · accuracy 81.3%.
  High-priority leads converted 83.6% of the time, Medium 51.9%, Low 11.4%.
- **Tiers:** High ≥ 70% (contact within 1 day), Medium 40–69% (3 days), Low < 40% (7 days).
- **Reasons:** each input is swapped for the typical training value; the inputs that move the score most are shown.

This page is the model-serving part of **SalesMesh**, a final-year project by Okeyo Hoppe Solini Marie
(Strathmore University). The full lead-management app uses the same model and the same rules.
The inputs contain no names or contact details.
"""

with gr.Blocks(title="SalesMesh Lead Scorer") as demo:
    gr.Markdown("# SalesMesh Lead Scorer\nPredict how likely a marketing lead is to convert, "
                "see its priority tier and the reasons behind the score.")
    with gr.Tab("Score one lead"):
        inputs = []
        with gr.Row():
            with gr.Column():
                for col in FEATURE_INPUT_COLS[:7]:
                    if col in NUMERIC_COLS:
                        inputs.append(gr.Number(value=BASELINE[col], minimum=0, label=FRIENDLY_NAMES[col]))
                    else:
                        inputs.append(gr.Dropdown(_options(col), value=BASELINE[col], label=FRIENDLY_NAMES[col]))
            with gr.Column():
                for col in FEATURE_INPUT_COLS[7:]:
                    inputs.append(gr.Dropdown(_options(col), value=BASELINE[col], label=FRIENDLY_NAMES[col]))
            with gr.Column():
                button = gr.Button("Score this lead", variant="primary")
                summary = gr.Markdown()
                as_json = gr.JSON(label="API result")
        button.click(score_lead, inputs, [summary, as_json], api_name="score_lead")
        example = dict(BASELINE, **{"Total Time Spent on Website": 1234, "Lead Source": "Reference",
                                    "What is your current occupation": "Working Professional",
                                    "Last Notable Activity": "SMS Sent"})
        gr.Examples([[BASELINE[c] for c in FEATURE_INPUT_COLS], [example[c] for c in FEATURE_INPUT_COLS]],
                    inputs, label="Examples: a typical lead, and a strong one")
    with gr.Tab("Score a CSV file"):
        gr.Markdown(f"Upload up to {MAX_CSV_ROWS} leads with the same columns as the template. "
                    "Rows with missing or unknown values are rejected with a reason instead of being guessed.")
        gr.DownloadButton("Download the template", value=write_template())
        upload = gr.File(label="CSV file of leads", file_types=[".csv"])
        go = gr.Button("Score the file", variant="primary")
        note = gr.Markdown()
        table = gr.Dataframe(label="Scored leads (highest first)", wrap=True)
        download = gr.File(label="Download the scored leads")
        rejected_table = gr.Dataframe(label="Rejected rows")
        go.click(score_csv, upload, [note, table, download, rejected_table], api_name="score_csv")
    with gr.Tab("About the model"):
        gr.Markdown(ABOUT)

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft(primary_hue="blue"))
