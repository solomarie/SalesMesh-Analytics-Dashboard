-- =============================================================
-- SalesMesh - Database integrity tests (Sprint 2)
-- Run the whole script once. The first result grid lists every
-- test with its expected result, actual result and Pass/Fail.
-- Expected counts assume the 6 demo leads (NEW-0001 to NEW-0006)
-- from load_new_leads.sql have been loaded: 9,240 + 6 = 9,246.
-- =============================================================

USE salesmesh;

SELECT test_id AS `Test ID`, test_case AS `Test case`, expected AS `Expected result`,
       actual AS `Actual result`, IF(expected = actual, 'Pass', 'Fail') AS `Status`
FROM (
    SELECT 'DB01' AS test_id, 'Leads table row count' AS test_case, '9246' AS expected,
           CAST(COUNT(*) AS CHAR) AS actual
    FROM leads

    UNION ALL
    SELECT 'DB02', 'Every lead has exactly one prediction (prediction row count)', '9246',
           CAST(COUNT(*) AS CHAR)
    FROM conversion_predictions

    UNION ALL
    SELECT 'DB03', 'Predictions pointing to a lead that does not exist', '0',
           CAST(COUNT(*) AS CHAR)
    FROM conversion_predictions cp LEFT JOIN leads l ON cp.lead_id = l.lead_id
    WHERE l.lead_id IS NULL

    UNION ALL
    SELECT 'DB04', 'Leads with no prediction', '0',
           CAST(COUNT(*) AS CHAR)
    FROM leads l LEFT JOIN conversion_predictions cp ON cp.lead_id = l.lead_id
    WHERE cp.lead_id IS NULL

    UNION ALL
    SELECT 'DB05', 'Prospect IDs that appear more than once', '0',
           CAST(COUNT(*) AS CHAR)
    FROM (SELECT prospect_id FROM leads GROUP BY prospect_id HAVING COUNT(*) > 1) d

    UNION ALL
    SELECT 'DB06', 'Probabilities outside the range 0 to 1', '0',
           CAST(COUNT(*) AS CHAR)
    FROM conversion_predictions
    WHERE conversion_probability < 0 OR conversion_probability > 1 OR conversion_probability IS NULL

    UNION ALL
    SELECT 'DB07', 'Tiers that do not match the FR-04 thresholds', '0',
           CAST(COUNT(*) AS CHAR)
    FROM conversion_predictions
    WHERE priority_tier <> CASE WHEN conversion_probability >= 0.70 THEN 'High'
                                WHEN conversion_probability >= 0.40 THEN 'Medium'
                                ELSE 'Low' END

    UNION ALL
    SELECT 'DB08', 'Demo leads loaded by the scoring workflow (NEW-0001 to NEW-0006)', '6',
           CAST(COUNT(*) AS CHAR)
    FROM leads WHERE prospect_id LIKE 'NEW-%'

    UNION ALL
    SELECT 'DB09', 'New leads stored with an unknown outcome (converted is NULL)', '6',
           CAST(COUNT(*) AS CHAR)
    FROM leads WHERE converted IS NULL

    UNION ALL
    SELECT 'DB10', 'Campaigns table row count', '200000',
           CAST(COUNT(*) AS CHAR)
    FROM campaigns

    UNION ALL
    SELECT 'DB11', 'Passwords not stored as SHA-256 hashes (64 characters)', '0',
           CAST(COUNT(*) AS CHAR)
    FROM users WHERE CHAR_LENGTH(password_hash) <> 64
) AS results;

-- For information (not pass/fail): tier counts after the new leads were added
SELECT priority_tier, COUNT(*) AS leads
FROM conversion_predictions
GROUP BY priority_tier
ORDER BY FIELD(priority_tier, 'High', 'Medium', 'Low');
