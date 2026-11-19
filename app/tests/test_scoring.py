"""Input checks and scoring of new leads (FR-02 to FR-04). Needs the model files in app/model."""
import pandas as pd
import pytest

from salesmesh import scoring

pytestmark = pytest.mark.skipif(not (scoring.MODEL_DIR / "salesmesh_model.pkl").exists(),
                                reason="Model file not found in app/model")


@pytest.fixture(scope="module")
def reference():
    return scoring.load_reference()


def lead(reference, pid="T-1", **changes):
    row = {"Prospect ID": pid, **{c: reference["baseline"][c] for c in scoring.FEATURE_INPUT_COLS}}
    row.update(changes)
    return row


def test_a_good_row_passes(reference):
    valid, rejected = scoring.validate(pd.DataFrame([lead(reference)]), set(), reference)
    assert len(valid) == 1 and rejected.empty


def test_bad_rows_are_rejected_with_reasons(reference):
    rows = pd.DataFrame([
        lead(reference, pid=""),
        lead(reference, pid="X" * 101),                                 # defect D02
        lead(reference, pid="DUP"), lead(reference, pid="DUP"),         # defect D01
        lead(reference, pid="OLD"),
        lead(reference, pid="NEG", TotalVisits="-1"),
        lead(reference, pid="CAT", **{"Lead Source": "Carrier pigeon"}),
    ])
    valid, rejected = scoring.validate(rows, {"OLD"}, reference)
    assert list(valid["Prospect ID"]) == ["DUP"]
    reasons = " ".join(rejected["Reason"])
    for text in ["blank", "longer than 100", "more than once", "already exists", "cannot be negative",
                 "unknown value"]:
        assert text in reasons


def test_missing_columns_or_empty_file(reference):
    with pytest.raises(scoring.InputFileError):
        scoring.validate(pd.DataFrame([{"Prospect ID": "A"}]), set(), reference)
    with pytest.raises(scoring.InputFileError):
        scoring.validate(pd.DataFrame(columns=["Prospect ID"] + scoring.FEATURE_INPUT_COLS), set(), reference)


def test_scores_are_probabilities_with_matching_tiers(reference):
    rows = pd.DataFrame([lead(reference, pid=f"T-{i}", **{"Total Time Spent on Website": str(t)})
                         for i, t in enumerate([0, 300, 1500])])
    valid, _ = scoring.validate(rows, set(), reference)
    scored = scoring.score_leads(valid)
    assert scored["conversion_probability"].between(0, 1).all()
    assert all(scoring.assign_tier(p) == t for p, t in zip(scored["conversion_probability"], scored["priority_tier"]))
    # more time on the website should not lower the score in this model
    assert scored["conversion_probability"].iloc[2] >= scored["conversion_probability"].iloc[0]


def test_each_score_has_at_most_three_reasons(reference):
    valid, _ = scoring.validate(pd.DataFrame([lead(reference, **{"Total Time Spent on Website": "1234"})]),
                                set(), reference)
    reasons = scoring.score_leads(valid)["top_reasons"].iloc[0].split(" | ")
    assert 1 <= len(reasons) <= 3


def test_optional_details_can_be_left_out(reference):
    """The six optional details may be missing: saved as not provided, scored with the typical value."""
    full = lead(reference, pid="FULL", **{"Total Time Spent on Website": "900"})
    short = {k: v for k, v in lead(reference, pid="SHORT", **{"Total Time Spent on Website": "900"}).items()
             if k not in scoring.OPTIONAL_COLS}
    valid_full, _ = scoring.validate(pd.DataFrame([full]), set(), reference)
    valid_short, rejected = scoring.validate(pd.DataFrame([short]), set(), reference)
    assert rejected.empty and (valid_short[scoring.OPTIONAL_COLS] == scoring.NOT_PROVIDED).all(axis=None)
    a, b = scoring.score_leads(valid_full).iloc[0], scoring.score_leads(valid_short).iloc[0]
    assert abs(a["conversion_probability"] - b["conversion_probability"]) < 1e-9
    assert "Not provided" not in b["top_reasons"]
