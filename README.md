# SalesMesh Analytics Dashboard

**A Marketing Lead Conversion Prediction Model and Pipeline Analytics Dashboard for Data-Driven Decision Making**

Okeyo Hoppe Solini Marie (161031) · Supervisor: Mr. Tiberius Tabulu
BSc Informatics and Computer Science · Strathmore University

---

## Overview

SalesMesh combines machine learning and Business Intelligence to help marketing teams decide which leads to pursue first. A Gradient Boosting model, selected after comparing three algorithms, predicts each lead's probability of converting and assigns it a High, Medium or Low priority tier. The results are stored in MySQL and shown in a Power BI dashboard alongside campaign performance and ROI metrics.

## Project Status

| Phase | Status |
|---|---|
| Proposal (Chapters 1–3) | Complete |
| System analysis and design (Chapter 4) | Complete |
| ML pipeline | Complete |
| MySQL database | Complete |
| Power BI dashboard (3 pages) | Complete |
| New-lead scoring workflow | Complete |
| Testing and initial evaluation (Sprint 2) | Complete: 40 of 40 tests passed |
| Online hosting (Power BI Service + data gateway) | Published; scheduled refresh working (sharing needs a Pro licence) |
| Login (FR-01) | Planned for Sprint III, awaiting supervisor approval |

## How the System Works

```
 INPUT                     PROCESSING                                OUTPUT
 CSV of new leads   →   Google Colab: validate each row       →   MySQL (salesmesh)   →   Power BI dashboard
                         → encode → score with the saved model       leads +                  (after Refresh)
                         → assign tier → write a SQL load file       conversion_predictions
```

1. **Training (once):** `SalesMesh_ML_Pipeline.ipynb` cleans the Kaggle data, compares three models, scores all 9,240 leads and writes the SQL files that load MySQL.
2. **Scoring new leads (repeatable):** `SalesMesh_Score_New_Leads.ipynb` takes a CSV of new leads, rejects invalid rows with a reason, scores the valid ones with `salesmesh_model.pkl`, and writes `load_new_leads.sql`.
3. **Storage:** the SQL files are run in MySQL Workbench. Colab runs in the cloud and cannot reach a local MySQL server, so data moves through SQL files.
4. **Reporting:** Power BI Desktop imports the `leads`, `conversion_predictions` and `campaigns` tables. The `users` table is never loaded into the report.
5. **Hosting:** the report is published to the Power BI Service. An on-premises data gateway on the host laptop lets the Service refresh the data from MySQL on a schedule, using a read-only MySQL account (`powerbi_reader`).

## Tech Stack

- **Python 3** (pandas, NumPy, scikit-learn, imbalanced-learn, joblib, matplotlib) in **Google Colab**
- **MySQL 9.1** with **MySQL Workbench**
- **Microsoft Power BI Desktop** with **MySQL Connector/NET**
- **Git / GitHub Desktop**, with a GitHub Projects board for sprint tracking

## Repository Structure

```
SalesMesh/
├── Data/          Raw Kaggle lead data, cleaned leads, demo file of new leads
├── database/      Schema, data load scripts and database test scripts (SQL)
├── notebooks/     ML pipeline notebook and new-lead scoring notebook
├── power bi/      Dashboard (.pbix) and colour theme (.json)
├── predictions/   Scored leads, scored new leads, rejected new leads
├── report/        Model comparison, evaluation charts, test results
└── screenshots/   Dashboard screenshots
```

`models/` is created locally when the ML notebook runs; the model file is not tracked (see below).

## Data Sources

| Dataset | Records | Used for |
|---|---|---|
| [Lead Scoring Dataset](https://www.kaggle.com/datasets/amritachatterjee09/lead-scoring-dataset) | 9,240 leads, 37 columns | ML model training and lead scoring |
| [Marketing Campaign Performance Dataset](https://www.kaggle.com/datasets/manishabhatt22/marketing-campaign-performance-dataset) | 200,000 campaigns, 16 columns | Campaign and ROI analytics in Power BI |

Both datasets are public and anonymised and contain no personally identifiable information. The campaign CSV exceeds GitHub's upload limit, so download it from Kaggle into `Data/`.

## ML Pipeline

`notebooks/SalesMesh_ML_Pipeline.ipynb` runs these steps in order:

1. **Cleaning:** dropped ID columns and columns with over 35% missing values; treated `Select` placeholder values as missing; imputed categorical gaps with the mode and numerical gaps with the median
2. **Encoding:** one-hot encoded categorical features (15 columns → 132 features)
3. **Train/test split:** 80/20 stratified split (7,392 / 1,848 records); the test set is used only for final evaluation
4. **Pipelines:** each model is wrapped as Min-Max scaling → SMOTE → classifier, so scaling and oversampling are refitted on every training fold and never see validation or test data
5. **Tuning:** Logistic Regression (baseline), Random Forest and Gradient Boosting tuned with GridSearchCV and stratified 10-fold cross-validation, scored by F1
6. **Selection:** the model with the highest mean cross-validation F1 is selected
7. **Evaluation:** precision, recall, F1, AUC-ROC and confusion matrix on the held-out test set; feature importance
8. **Scoring:** every lead is scored with out-of-fold prediction (by a model that never trained on it) and assigned a priority tier

### Model Comparison

| Model | CV F1 (mean ± std) | Test precision | Test recall | Test F1 | Test AUC-ROC |
|---|---|---|---|---|---|
| Logistic Regression (baseline) | 0.7648 ± 0.0186 | 0.7237 | 0.7907 | 0.7557 | 0.8670 |
| Random Forest | 0.7827 ± 0.0131 | 0.7436 | 0.7781 | 0.7605 | 0.8788 |
| **Gradient Boosting** | **0.7857 ± 0.0120** | 0.7421 | 0.7879 | **0.7643** | 0.8781 |

**Selected model:** Gradient Boosting (100 trees, learning rate 0.1, max depth 5). It meets both NFR-03 targets on the held-out test set: **F1 0.7643** (target ≥ 0.75) and **AUC-ROC 0.8781** (target ≥ 0.80), with 81% accuracy. Gradient Boosting and Random Forest performed almost identically; Gradient Boosting was selected because it had the highest mean cross-validation F1, the criterion defined before training.

### Priority Tiers

| Tier | Probability threshold | Leads | Actual conversion rate |
|---|---|---|---|
| High | ≥ 0.70 | 2,776 | 83.6% |
| Medium | 0.40 – 0.69 | 1,243 | 51.9% |
| Low | < 0.40 | 5,221 | 11.4% |

High-priority leads convert about seven times as often as Low-priority leads.

## New-Lead Scoring Workflow

`notebooks/SalesMesh_Score_New_Leads.ipynb` scores leads that were not part of training.

- **Input format:** a CSV with `Prospect ID` and the 14 lead attributes, using the same column names as the Kaggle dataset (see `Data/new_leads_demo.csv`)
- **Validation:** a row is rejected, with the reason, if its Prospect ID is blank, repeated or already in the database; if a numeric field is blank, not a number or negative; or if a category is blank, `Select`, or a value the model never saw. A file missing a required column, or with no rows, is rejected as a whole.
- **Output:** `scored_new_leads.csv`, `rejected_leads.csv`, and `load_new_leads.sql`, which inserts each lead and its prediction into MySQL. New leads are stored with `converted = NULL` because their outcome is not known yet. Running the SQL twice does not create duplicates.

By default the notebook builds a demonstration file of 14 leads: 6 valid (NEW-0001 to NEW-0006) and 8 with one deliberate error each.

## Database

MySQL database `salesmesh`, normalised to Third Normal Form. Row counts after the 6 demonstration leads were loaded:

| Table | Rows | Contents |
|---|---|---|
| users | 2 | Login email, SHA-256 password hash and role |
| user_biodata | 2 | User profile details |
| leads | 9,246 | 9,240 Kaggle leads + 6 scored new leads |
| campaigns | 200,000 | Campaign performance metrics |
| conversion_predictions | 9,246 | One probability and priority tier per lead |

## Power BI Dashboard

`power bi/SalesMesh_Dashboard.pbix` (Import mode) has three pages:

| Page | Shows |
|---|---|
| Pipeline Overview | Total leads, lead conversion rate, High-priority leads and share; leads per tier; actual conversion rate per tier |
| Lead Priority List | Every lead with source, last activity, probability (data bar) and colour-coded tier, filtered by a tier slicer |
| Campaign and ROI | Total campaigns, average conversion rate, ROI and acquisition cost; ROI by campaign type; acquisition cost by channel |

## How to Run

### Prerequisites

- A Google account (for Google Colab)
- MySQL Server 9.1 and MySQL Workbench
- Power BI Desktop (Windows) and **MySQL Connector/NET**, which Power BI's MySQL connector needs

### A. Build everything from scratch

1. Download both datasets from Kaggle into `Data/`.
2. Open `notebooks/SalesMesh_ML_Pipeline.ipynb` in Google Colab. Set Runtime → Change runtime type → **CPU**, then Runtime → Run all. Upload `Lead Scoring.csv` (Section 2) and `marketing_campaign_dataset.csv` (Section 14) when asked. Save the downloaded files into the folders listed in Section 16 of the notebook.
3. In MySQL Workbench, run these scripts from `database/`, in order, each once:
   1. `schema.sql`
   2. `load_leads_and_predictions.sql`
   3. `load_campaigns_part1.sql` to `load_campaigns_part4.sql`
   4. `seed_users.sql`

### B. Score new leads

1. Open `notebooks/SalesMesh_Score_New_Leads.ipynb` in Colab (CPU runtime) and Run all.
2. Upload `models/salesmesh_model.pkl` and `predictions/leads_with_predictions.csv` when asked.
3. Run the downloaded `load_new_leads.sql` in MySQL Workbench.

To score your own file, set `USE_DEMO_FILE = False` in Section 4 and upload a CSV in the same format as `Data/new_leads_demo.csv`.

### C. Open the dashboard

1. Open `power bi/SalesMesh_Dashboard.pbix` in Power BI Desktop.
2. If asked for credentials, choose **Database** and sign in with your MySQL user (server `localhost`, database `salesmesh`).
3. Click **Home → Refresh** to load the latest data. If the first refresh reports "A cyclic reference was encountered", click Refresh again (see defect D10).

### D. Host the dashboard online

1. In MySQL Workbench, run `database/create_powerbi_reader.sql` after replacing the placeholder password. It creates a read-only account that can only read the three report tables.
2. In Power BI Desktop, sign in with a work or school account and click **Home → Publish → My workspace**.
3. Install the **on-premises data gateway (standard mode)** on the computer that runs MySQL, sign in with the same account and register it (here: `SalesMesh-Laptop`).
4. At app.powerbi.com: **Settings → Manage connections and gateways → + New → On-premises**. Choose the gateway, connection type MySQL, server `localhost`, database `salesmesh`, Basic authentication with `powerbi_reader`, and **Encrypted connection: Encrypted**.
5. Open the semantic model's **Settings → Gateway and cloud connections**, select the gateway, map the MySQL source to the new connection and click Apply.
6. Under **Settings → Refresh**, turn on the schedule (Daily, UTC+03:00 Nairobi, 08:00 and 18:00).

### E. Run the tests

- **Scoring workflow (T01–T16):** run automatically in Section 7 of the scoring notebook; results are saved to `report/test_results_scoring.csv`.
- **Database integrity (DB01–DB11):** run `database/database_tests.sql` in Workbench; the first result grid shows each test with Pass or Fail.
- **Database failure conditions (NEG01–NEG04):** run each statement in `database/database_negative_tests.sql` on its own; each must return an error.

## Testing Summary

| Test group | Tests | Passed |
|---|---|---|
| Scoring workflow (T01–T16) | 16 | 16 |
| Database integrity (DB01–DB11) | 11 | 11 |
| Database failure conditions (NEG01–NEG04) | 4 | 4 |
| Dashboard integration (PBI01–PBI06) | 6 | 6 |
| Online hosting (HOST01–HOST03) | 3 | 3 |
| **Total** | **40** | **40** |

The full testing and evaluation record, including the defect log, is part of the Chapter 5 documentation.

## Known Limitations and Outstanding Defects

- **No link between leads and campaigns.** The two public datasets share no identifier, so `campaign_id` (and `user_id`) in `conversion_predictions` is empty and lead and campaign analysis are shown separately. With an organisation's CRM data this link would be populated.
- **Campaign data appears synthetic.** ROI is 4.99–5.01 for every campaign type, so the campaign charts show near-equal bars.
- **Imputation before the split.** Missing values were imputed using the full dataset before the train/test split, giving the test set a small influence on preprocessing.
- **Scoring is a manual batch process.** New leads move from Colab to a SQL file, to Workbench, to a Power BI refresh; nothing is scored in real time.
- **Login (FR-01) is not enforced yet.** The `users` table stores SHA-256 password hashes, but Power BI does not use it. Sign-in through the Power BI Service is planned for Sprint III, subject to supervisor approval.
- **Online refresh depends on the host laptop.** Scheduled refreshes run only while the laptop is on, online, and running MySQL and the gateway; otherwise the online report keeps its last data.
- **Sharing needs a Pro licence.** On a free licence the online report is visible only to its owner; a Pro licence or trial is needed to share it with assessors.
- **Demonstration leads reuse existing profiles.** NEW-0001 to NEW-0006 copy the attributes of six existing leads under new IDs; they demonstrate the workflow, not accuracy on new real-world leads.
- **The model file is not tracked.** `salesmesh_model.pkl` is excluded by `.gitignore`; run the ML notebook before the scoring notebook.
- **Defect D10 (open).** A Power BI refresh can fail once with "A cyclic reference was encountered during evaluation"; repeating the refresh works.
- **Test accounts only.** `seed_users.sql` contains test logins (this repository is public); never store real credentials in it.

## Deployment

The dashboard is hosted on the **Power BI Service** (Strathmore University tenant), in the owner's workspace.

| Requirement | Status |
|---|---|
| Work or school Microsoft account (personal email cannot sign up) | In place: Strathmore account |
| Report published to the Power BI Service | Done |
| On-premises data gateway (standard mode), registered as `SalesMesh-Laptop` | Done, online |
| Gateway connection to MySQL with a read-only account (`powerbi_reader`), encrypted | Done |
| Scheduled refresh (daily, 08:00 and 18:00 EAT) | Done |
| Power BI Pro licence or trial, to share the report with others | Planned: start the 60-day trial shortly before the final demonstration |
| Login and access control (FR-01) | Awaiting supervisor approval |
