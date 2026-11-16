---
title: SalesMesh Lead Scorer
emoji: 📈
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 6.20.0
python_version: "3.12"
app_file: app.py
pinned: false
short_description: Predict which marketing leads will convert, with reasons
---

# SalesMesh Lead Scorer

The model-serving part of **SalesMesh**, a final-year project by Okeyo Hoppe Solini Marie (161031),
BSc Informatics and Computer Science, Strathmore University. Supervisor: Mr. Tiberius Tabulu.

A Gradient Boosting model trained on the public X Education lead-scoring dataset (9,240 leads) predicts how
likely a lead is to convert, assigns a High / Medium / Low priority tier and explains the score in plain words.

- **Web page:** score one lead in a form, or upload a CSV of up to 1,000 leads.
- **API:** every function on the page is also an API endpoint (`/score_lead`, `/score_csv`). Click
  "Use via API" at the bottom of the page for ready-made Python and JavaScript code.

Test-set results (1,848 leads not used in training): F1 0.764, ROC-AUC 0.878, accuracy 81.3%.

Source code: https://github.com/solomarie/salesmesh-analytics-dashboard
