-- =============================================================
-- SalesMesh - Read-only MySQL account for the Power BI gateway
-- Least privilege: Power BI can read only the three tables the
-- dashboard uses. It cannot change data and cannot read the
-- users or user_biodata tables.
-- Replace the placeholder password before running. Never commit
-- the real password.
-- =============================================================

CREATE USER 'powerbi_reader'@'localhost' IDENTIFIED BY 'REPLACE_WITH_A_STRONG_PASSWORD';

GRANT SELECT ON salesmesh.leads TO 'powerbi_reader'@'localhost';
GRANT SELECT ON salesmesh.conversion_predictions TO 'powerbi_reader'@'localhost';
GRANT SELECT ON salesmesh.campaigns TO 'powerbi_reader'@'localhost';

-- Check: should list exactly the three SELECT grants above
SHOW GRANTS FOR 'powerbi_reader'@'localhost';
