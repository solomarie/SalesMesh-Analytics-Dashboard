-- =============================================================
-- SalesMesh - Database failure-condition tests (Sprint 2)
-- Each statement below SHOULD FAIL. A failure here is a PASS:
-- it proves the database refuses bad data.
--
-- Run them ONE AT A TIME: click on a statement's line and use
-- "Run current statement", then note the error message shown.
-- If any statement says OK instead of an error, stop and report it.
-- =============================================================

USE salesmesh;

-- NEG01: a prediction for a lead that does not exist
-- Expected: Error 1452 (foreign key constraint fails)
INSERT INTO conversion_predictions (lead_id, conversion_probability, priority_tier)
VALUES (999999, 0.5, 'Medium');

-- NEG02: a second user with an email that is already registered
-- Expected: Error 1062 (duplicate entry for key 'users.email')
INSERT INTO users (email, password_hash)
VALUES ('admin@salesmesh.com', SHA2('test', 256));

-- NEG03: a user with no email
-- Expected: Error 1048 (column 'email' cannot be null)
INSERT INTO users (email, password_hash)
VALUES (NULL, SHA2('test', 256));

-- NEG04: a campaign with an ID that already exists
-- Expected: Error 1062 (duplicate entry for key 'campaigns.PRIMARY')
INSERT INTO campaigns (campaign_id, campaign_type)
SELECT MIN(campaign_id), 'Email' FROM campaigns;
