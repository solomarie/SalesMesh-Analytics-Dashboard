# SalesMesh Analytics Dashboard

**A Marketing Lead Conversion Prediction Model and Pipeline Analytics Dashboard for Data-Driven Decision Making**

Okeyo Hoppe Solini Marie (161031) · Supervisor: Mr. Tiberius Tabulu
BSc Informatics and Computer Science · Strathmore University

---

## Overview

SalesMesh combines machine learning and Business Intelligence to help marketing teams decide which leads to pursue first. A Random Forest model predicts each lead's probability of converting, assigns it a High, Medium or Low priority tier, and stores the results in MySQL for visualisation in a Power BI dashboard alongside campaign performance and ROI metrics.

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
3. **Train/test split** — 80/20 stratified split (7,392 / 1,848 records)
4. **Scaling** — Min-Max scaler fitted on the training set only, to avoid data leakage
5. **Balancing** — SMOTE applied to the training set only (4,543 / 4,543)
6. **Training** — Random Forest (200 trees, max depth 15)
7. **Evaluation** — on the untouched 20% test set
8. **Scoring** — conversion probability and priority tier generated for all 9,240 leads

### Results (held-out test set)

| Metric | Score | Target |
|---|---|---|
| F1-score (converted class) | 0.75 | ≥ 0.75 |
| AUC-ROC | 0.8789 | ≥ 0.80 |
| Accuracy | 0.80 | — |

### Priority Tiers

| Tier | Probability threshold | Leads |
|---|---|---|
| High | ≥ 0.70 | 2,205 |
| Medium | 0.40 – 0.69 | 2,153 |
| Low | < 0.40 | 4,882 |

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
2. Run `notebooks/SalesMesh_ML_Pipeline.ipynb` in Google Colab to clean the data, train the model and generate the SQL load files
3. In MySQL Workbench, run the scripts in `database/` in this order:
   1. `schema.sql`
   2. `load_leads_and_predictions.sql`
   3. `load_campaigns_part1.sql` to `load_campaigns_part4.sql`, in order
   4. `seed_users.sql`

The `.pkl` model files are not tracked; running the notebook regenerates them.

## Known Limitations

- The two public datasets share no common identifier, so leads cannot be linked to specific campaigns and `campaign_id` in `conversion_predictions` is empty. With an organisation's CRM data this link would be populated.
- Campaign ROI values are nearly identical across campaign types (4.99–5.01), suggesting the public campaign dataset was synthetically generated.
- `seed_users.sql` contains test accounts only.
