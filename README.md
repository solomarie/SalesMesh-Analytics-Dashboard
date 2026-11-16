# SalesMesh Analytics Dashboard

**A Marketing Lead Conversion Prediction Model and Pipeline Analytics Dashboard for Data-Driven Decision Making**

Okeyo Hoppe Solini Marie (161031) · Supervisor: Mr. Tiberius Tabulu
BSc Informatics and Computer Science · Strathmore University

---

## Overview

Marketing teams often have more leads than they can follow up, and decide who to call first by guesswork. SalesMesh scores every new lead with a Gradient Boosting model, explains the score in plain words, gives it a High, Medium or Low priority, assigns it to the marketer with the fewest open leads and tracks every follow-up until the lead is converted or lost. Managers see each marketer's progress on the pipeline analytics dashboard (the Team page).

## Project Status

| Sprint | Work | Status |
|---|---|---|
| Sprint I | Data cleaning, comparison of three models, final Gradient Boosting model, MySQL database | Complete |
| Sprint II | Validation and scoring of new leads, priority tiers, first round of testing; Power BI prototype | Complete (the Power BI prototype was later replaced, see below) |
| Sprint III | Web application: sign-in and roles, lead assignment, call list, follow-ups, pipeline analytics dashboard; after the progress review, invitations, lead pop-up, handover and requests, reopening lost leads, notifications, live score preview | Complete |
| Testing | 61 automated tests | 61 of 61 passed |

## How the System Works

```
 New lead (form or CSV)  →  validate  →  score with the Gradient Boosting model  →  explain the score
        →  assign priority tier  →  assign to the marketer with the fewest open leads  →  MySQL
        →  marketer's daily call list  →  follow-ups, handover, reopening  →  manager's Team dashboard
```

1. **Training (once):** `notebooks/SalesMesh_ML_Pipeline.ipynb` cleans the Kaggle data, compares three models and saves the selected pipeline as `salesmesh_model.pkl`.
2. **Scoring:** the web application loads the saved model and scores each lead as soon as it is entered, with up to three plain-language reasons.
3. **Lead management:** marketers work from a daily call list, log every contact and hand leads over; managers monitor the team on the Team page.
4. **Explainability:** `notebooks/SalesMesh_Explainability.ipynb` shows feature importance, permutation importance and SHAP for the saved model, and checks the app's score explanations against SHAP.

## Tech Stack

- **Python 3** (pandas, NumPy, scikit-learn 1.6.1, imbalanced-learn 0.14.2, joblib, SHAP) in **Google Colab** for training
- **Streamlit** web application with **bcrypt** passwords and **Google sign-in (OpenID Connect)**
- **MySQL 9.1** with MySQL Workbench
- **Cloudflare Tunnel** to make the application available online; **Hugging Face Spaces** files for serving the model
- **Git / GitHub Desktop**

## Repository Structure

```
SalesMesh/
├── app/           Web application, its automated tests, setup_database.py and refresh_demo_dates.py
├── deployment/    Hugging Face Space files for serving the model online
├── Data/          Raw Kaggle lead data and cleaned leads
├── database/      Schema and data load scripts (SQL)
├── notebooks/     ML pipeline, new-lead scoring (Sprint II) and explainability notebooks
├── predictions/   Scored leads
├── report/        Model comparison, evaluation charts, test results
├── power bi/      Sprint II Power BI prototype, kept for reference only (no longer part of the system)
└── screenshots/   Screenshots
```

The trained model file (`*.pkl`), `app/.env` and `app/.streamlit/secrets.toml` are excluded by `.gitignore`.

## Data

| Dataset | Records | Used for |
|---|---|---|
| [Lead Scoring Dataset](https://www.kaggle.com/datasets/amritachatterjee09/lead-scoring-dataset) (X Education) | 9,240 leads, 37 columns | Model training and evaluation; 300 test-set leads form the demonstration pipeline |

The dataset is public and anonymised. The 300 demonstration leads were given simulated follow-ups, clearly marked `simulated` in the database.

## ML Pipeline

1. **Cleaning:** removed identifier columns, columns with over 35% missing values and near-constant yes/no columns; treated `Select` as missing; filled categorical gaps with the most common value and numerical gaps with the median
2. **Encoding:** one-hot encoding (14 lead details → 132 model features)
3. **Split:** 80/20 stratified split (7,392 / 1,848 leads); the test set is used only for the final evaluation
4. **Pipelines:** Min-Max scaling → SMOTE → classifier, so scaling and oversampling are refitted on every training fold
5. **Tuning and selection:** Logistic Regression, Random Forest and Gradient Boosting tuned with GridSearchCV and stratified 10-fold cross-validation; the highest mean cross-validation F1 was selected

| Model | CV F1 (mean ± std) | Test precision | Test recall | Test F1 | Test AUC-ROC |
|---|---|---|---|---|---|
| Logistic Regression | 0.765 ± 0.019 | 0.724 | 0.791 | 0.756 | 0.867 |
| Random Forest | 0.783 ± 0.013 | 0.744 | 0.778 | 0.761 | 0.879 |
| **Gradient Boosting** | **0.786 ± 0.012** | 0.742 | 0.788 | **0.764** | 0.878 |

**Selected model:** Gradient Boosting (100 trees, learning rate 0.1, maximum depth 5), with accuracy 81.3% on the test set.

| Priority tier | Threshold | Test leads | Actual conversion rate |
|---|---|---|---|
| High | ≥ 0.70 | 569 | 81.7% |
| Medium | 0.40 – 0.69 | 250 | 48.4% |
| Low | < 0.40 | 1,029 | 12.2% |

The 20% of test leads with the highest scores contained 45.1% of all conversions.

## The Web Application

| Role | What they can do |
|---|---|
| Marketer | Today's call list, own leads, log follow-ups, add leads with a live score preview, find a lead and ask for it, hand over a lead, reopen a lost lead that comes back, notifications |
| Marketing Manager | Team page (pipeline analytics dashboard), escalations, all leads, hand over or reassign any lead, invite marketers one at a time or by CSV |
| Admin | Everything a manager can do, plus inviting managers and admins, changing roles and resetting passwords |

- **Scoring:** each lead is validated, scored and explained as it is entered. The reasons come from changing one detail at a time to its typical value and showing the details that move the score most. The form asks for the eight details that drive the score; city, country, specialisation, how they heard about us, do-not-email and do-not-call are optional, are saved as unknown when not given and are scored with their most common value (on the test set this changes F1 from 0.764 to 0.763).
- **Fair assignment:** a new lead goes to the active marketer with the fewest open leads; when marketers are tied, it goes to the one who has waited longest since their last new lead.
- **Deadlines:** first contact within 1 day (High), 3 days (Medium) or 7 days (Low); later contacts follow the next follow-up date the marketer sets.
- **Outcomes and stages:** No answer and Needs follow-up → Contacted; Interested → Qualified; Converted or Lost closes the lead. Open leads only move forward.
- **Joining:** no open sign-up. A manager sends an invitation; the link works once and expires after 7 days, and the account is active as soon as it is set up.
- **Handover, requests and reopening:** an owner or manager hands a lead to a colleague with a reason; a marketer can find any lead by Prospect ID and ask for it; a lost lead can be reopened when the customer returns.
- **Pipeline analytics dashboard (Team page):** open, overdue, converted and lost leads per marketer, actual versus model-expected conversions, forecast from open leads, pipeline by stage, leads and conversion rate by priority tier, escalations and a drill-down per marketer.
- **Security:** bcrypt passwords, role checks on every action, lockout after 5 failed sign-ins, sign-out after 30 minutes without activity, single-use links stored as SHA-256 hashes, parameterised queries and a database account limited to select, insert and update.

## How to Run

**Prerequisites:** Python 3, MySQL Server and MySQL Workbench, and a Google account for Colab.

1. Train the model: open `notebooks/SalesMesh_ML_Pipeline.ipynb` in Google Colab and run all cells. Save `salesmesh_model.pkl` into `app/model/`.
2. Create the database: in MySQL Workbench run `database/schema.sql`, then `database/load_leads_and_predictions.sql`.
3. Set up the application, from the `app` folder:
   ```
   pip install -r requirements.txt
   python make_env.py
   python setup_database.py
   ```
   `setup_database.py` asks for the MySQL root password once. It creates the remaining tables, views, demonstration accounts and the 300-lead demonstration pipeline. Running it again later adds new tables without changing existing data or passwords.
4. Start the application with `Start SalesMesh.bat` (or `python -m streamlit run streamlit_app.py`).
5. Optional: Google sign-in needs `app/.streamlit/secrets.toml` (see `make_secrets.py`).
6. Before a demonstration, run `python refresh_demo_dates.py` to move the simulated follow-up dates forward to today, so the pipeline is not shown as entirely overdue. Only simulated data is changed.

**Tests:** in the `app` folder run `python -m pytest -q` (61 tests). The database tests only read data or check that forbidden actions are refused.

## Deployment

| Part | Where | How |
|---|---|---|
| Web application | **Cloudflare Tunnel** | `app/Start SalesMesh online.bat` starts the application and gives it a public https address (`*.trycloudflare.com`) while MySQL stays on the host laptop. The address changes each time it is started, and the application is online only while the laptop is on. Google sign-in works only on the registered `localhost` address; online users sign in with email and password. |
| Model serving | **Hugging Face Spaces** (free CPU) | `deployment/huggingface/` contains a Gradio page that scores one lead or a CSV file, with `/score_lead` and `/score_csv` API endpoints. Upload these files plus `salesmesh_model.pkl` to a new Gradio Space. |

## Changes from the Original Plan

- **Power BI replaced by the Team page.** A three-page Power BI prototype was built in Sprint II. It showed the 9,240 historical leads rather than the working pipeline and duplicated what the application already showed, so in Sprint III the analytics were built into the application as the Team page, where managers see live figures from the same pipeline the marketers work in.
- **Campaign analytics removed.** The marketing campaign dataset planned for ROI analysis was found to be synthetic (the same averages for every campaign type and channel) and had no link to the lead data, so it was not used.

## Known Limitations

- **One dataset.** The model was trained on one public dataset from one company and would need retraining on an organisation's own leads.
- **Imputation before the split.** Missing values were filled using the full dataset before the train/test split, giving the test set a small influence on preprocessing.
- **Simulated pipeline.** The demonstration pipeline uses test-set leads with simulated follow-ups; the system has not been used by a real marketing team.
- **Explanations change one detail at a time** and do not capture details that only matter in combination (SHAP is used in the explainability notebook as a check).
- **Email needs a working SMTP connection (defect D22, open).** On networks that block email, invitation and reset links are shown on screen or printed in the application window instead.
- **Test accounts only.** The demonstration accounts are for testing; never store real credentials in the repository.
