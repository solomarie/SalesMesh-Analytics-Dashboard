# SalesMesh Analytics Dashboard

**A Marketing Lead Conversion Prediction Model and Pipeline Analytics Dashboard for Data-Driven Decision Making**

Okeyo Hoppe Solini Marie (161031) · Supervisor: Mr. Tiberius Tabulu
BSc Informatics and Computer Science · Strathmore University

---

## Overview

SalesMesh combines machine learning and Business Intelligence to help marketing teams decide which leads to pursue first. A Gradient Boosting model, selected after comparing three algorithms, predicts each lead's probability of converting, assigns it a High, Medium or Low priority tier, and stores the results in MySQL for visualisation in a Power BI dashboard alongside campaign performance and ROI metrics.

## Project Status

| Phase | Status |
|---|---|
| Proposal (Chapters 1–3) | Complete |
| System analysis and design (Chapter 4) | Complete |
| ML pipeline | Complete |
| MySQL database | Complete |
| Power BI dashboard | In progress |
| Testing and evaluation | Not started |

## Tech Stack

- **Python** (Pandas, NumPy, Scikit-learn, Imbalanced-learn) in **Google Colab**
- **MySQL 9.1** with **MySQL Workbench**
- **Microsoft Power BI**

## Repository Structure

```
SalesMesh/
├── Data/          Raw and cleaned datasets
├── database/      Schema and data load scripts (SQL)
├── diagrams/      UML, ERD, schema and architecture diagrams
├── models/        Trained model and scaler (not tracked - see below)
├── notebooks/     ML pipeline notebook
├── power bi/      Power BI dashboard files
├── predictions/   Lead conversion predictions
├── report/        Project documentation
└── screenshots/   Progress and results screenshots
```

## Data Sources

| Dataset | Records | Used for |
|---|---|---|
| [Lead Scoring Dataset](https://www.kaggle.com/datasets/amritachatterjee09/lead-scoring-dataset) | 9,240 leads, 37 columns | ML model training and lead scoring |
| [Marketing Campaign Performance Dataset](https://www.kaggle.com/datasets/manishabhatt22/marketing-campaign-performance-dataset) | 200,000 campaigns, 16 columns | Campaign and ROI analytics in Power BI |

Both datasets are public and anonymised and contain no personally identifiable information. The campaign CSV files exceed GitHub's upload limit, so download them from Kaggle into `Data/`.

## ML Pipeline

The notebook `notebooks/SalesMesh_ML_Pipeline.ipynb` runs these steps in order:

1. **Cleaning** — dropped ID columns and columns with over 35% missing values; treated `Select` placeholder values as missing; imputed categorical gaps with the mode and numerical gaps with the median
2. **Encoding** — one-hot encoded categorical features (15 columns → 132 features)
3. **Train/test split** — 80/20 stratified split (7,392 / 1,848 records); the test set is used only for final evaluation
4. **Pipelines** — each model is wrapped as Min-Max scaling → SMOTE → classifier, so scaling and oversampling are refitted on every training fold and never see validation or test data
5. **Tuning** — Logistic Regression, Random Forest and Gradient Boosting tuned with GridSearchCV and stratified 10-fold cross-validation, scored by F1
6. **Selection** — the model with the highest mean cross-validation F1 is selected
7. **Evaluation** — precision, recall, F1, AUC-ROC and confusion matrix on the held-out test set; Random Forest feature importance
8. **Scoring** — every lead is scored with out-of-fold prediction (by a model that never trained on it) and assigned a priority tier

### Model Comparison

| Model | CV F1 (mean ± std) | Test precision | Test recall | Test F1 | Test AUC-ROC |
|---|---|---|---|---|---|
| Logistic Regression | 0.7648 ± 0.0186 | 0.7237 | 0.7907 | 0.7557 | 0.8670 |
| Random Forest | 0.7827 ± 0.0131 | 0.7436 | 0.7781 | 0.7605 | 0.8788 |
| **Gradient Boosting** | **0.7857 ± 0.0120** | 0.7421 | 0.7879 | **0.7643** | 0.8781 |

**Selected model:** Gradient Boosting (100 trees, learning rate 0.1, max depth 5). It meets both NFR-03 targets on the held-out test set: **F1 0.7643** (target ≥ 0.75) and **AUC-ROC 0.8781** (target ≥ 0.80), with 81% accuracy. Gradient Boosting and Random Forest performed almost identically; Gradient Boosting was selected because it had the highest mean cross-validation F1, the criterion defined before training.

Charts and the full comparison table are in `report/`.

### Priority Tiers

| Tier | Probability threshold | Leads | Actual conversion rate |
|---|---|---|---|
| High | ≥ 0.70 | 2,776 | 83.6% |
| Medium | 0.40 – 0.69 | 1,243 | 51.9% |
| Low | < 0.40 | 5,221 | 11.4% |

High-priority leads convert more than seven times as often as Low-priority leads.

## Database

MySQL database `salesmesh`, normalised to Third Normal Form.

| Table | Rows | Contents |
|---|---|---|
| users | 2 | Login credentials and roles |
| user_biodata | 2 | User profile details |
| leads | 9,240 | Preprocessed lead records |
| campaigns | 200,000 | Campaign performance metrics |
| conversion_predictions | 9,240 | Model probabilities and priority tiers |

## How to Reproduce

1. Download both datasets from Kaggle into `Data/`
2. Run `notebooks/SalesMesh_ML_Pipeline.ipynb` in Google Colab (CPU runtime, Runtime → Run all) to clean the data, train and compare the models and generate the SQL load files
3. In MySQL Workbench, run the scripts in `database/` in this order:
   1. `schema.sql`
   2. `load_leads_and_predictions.sql`
   3. `load_campaigns_part1.sql` to `load_campaigns_part4.sql`, in order
   4. `seed_users.sql`

The trained model (`models/salesmesh_model.pkl`, containing the scaler and classifier) is not tracked; running the notebook regenerates it.

## Known Limitations

- The two public datasets share no common identifier, so leads cannot be linked to specific campaigns and `campaign_id` in `conversion_predictions` is empty. With an organisation's CRM data this link would be populated.
- Campaign ROI values are nearly identical across campaign types (4.99–5.01), suggesting the public campaign dataset was synthetically generated.
- Missing values were imputed using the full dataset before the train/test split. The effect on results is negligible but is noted for completeness.
- `seed_users.sql` contains test accounts only.
