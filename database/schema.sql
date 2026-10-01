-- =============================================================
-- SalesMesh Analytics Dashboard - Database Schema
-- Creates the salesmesh database and its five tables.
-- Run this first, then the load scripts in this folder.
-- =============================================================

CREATE DATABASE IF NOT EXISTS salesmesh;
USE salesmesh;

-- -------------------------------------------------------------
-- 1. USERS - authentication and role
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id        INT AUTO_INCREMENT PRIMARY KEY,
    email          VARCHAR(100) NOT NULL UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,
    role           VARCHAR(50) DEFAULT 'user',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- -------------------------------------------------------------
-- 2. USER_BIODATA - personal profile, separated from login data
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_biodata (
    biodata_id  INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    full_name   VARCHAR(100) NOT NULL,
    phone       VARCHAR(20),
    department  VARCHAR(100),
    job_title   VARCHAR(100),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- -------------------------------------------------------------
-- 3. LEADS - preprocessed records from the Kaggle Lead Scoring dataset
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads (
    lead_id          INT AUTO_INCREMENT PRIMARY KEY,
    prospect_id      VARCHAR(100),
    lead_source      VARCHAR(100),
    lead_origin      VARCHAR(100),
    occupation       VARCHAR(100),
    city             VARCHAR(100),
    total_visits     INT,
    time_on_website  INT,
    last_activity    VARCHAR(100),
    converted        INT
);

-- -------------------------------------------------------------
-- 4. CAMPAIGNS - records from the Kaggle Marketing Campaign
--    Performance dataset
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id       INT AUTO_INCREMENT PRIMARY KEY,
    campaign_type     VARCHAR(100),
    target_audience   VARCHAR(100),
    channels_used     VARCHAR(100),
    duration_days     INT,
    conversion_rate   FLOAT,
    acquisition_cost  FLOAT,
    roi               FLOAT
);

-- -------------------------------------------------------------
-- 5. CONVERSION_PREDICTIONS - Random Forest model outputs
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversion_predictions (
    prediction_id           INT AUTO_INCREMENT PRIMARY KEY,
    lead_id                 INT,
    campaign_id             INT,
    user_id                 INT,
    conversion_probability  FLOAT,
    priority_tier           VARCHAR(50),
    predicted_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lead_id)     REFERENCES leads(lead_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id),
    FOREIGN KEY (user_id)     REFERENCES users(user_id)
);

SHOW TABLES;
