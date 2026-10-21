"""
Builds app/model/reference_values.json and app/model/test_set_prospect_ids.csv
from the cleaned lead data, using exactly the same split as the ML notebook.

Run once (already done for the repository):
    python build_reference.py ../predictions/leads_with_predictions.csv
"""
import json
import sys
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

from salesmesh.scoring import CATEGORICAL_COLS, FEATURE_INPUT_COLS, NUMERIC_COLS

RANDOM_STATE = 42
HERE = Path(__file__).parent

ref = pd.read_csv(sys.argv[1])
X = pd.get_dummies(ref[FEATURE_INPUT_COLS], drop_first=True).astype(float)
y = ref["Converted"]
X_train, X_test, _, _ = train_test_split(X, y, test_size=0.2, random_state=RANDOM_STATE, stratify=y)
train = ref.loc[X_train.index]

reference = {
    "allowed_values": {c: sorted(ref[c].dropna().astype(str).unique().tolist()) for c in CATEGORICAL_COLS},
    "baseline": {
        **{c: float(train[c].median()) for c in NUMERIC_COLS},
        **{c: str(train[c].mode()[0]) for c in CATEGORICAL_COLS},
    },
    "note": "Allowed values come from all 9,240 cleaned leads; baselines (median / most common value) "
            "come from the 7,392 training leads only.",
}
(HERE / "model" / "reference_values.json").write_text(json.dumps(reference, indent=1))
ref.loc[X_test.index, ["prospect_id"]].to_csv(HERE / "model" / "test_set_prospect_ids.csv", index=False)
print("Training rows:", len(X_train), "| Test rows:", len(X_test))
