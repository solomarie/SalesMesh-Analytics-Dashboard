-- =============================================================
-- SalesMesh Analytics Dashboard - Test Users
-- Creates one admin and one standard user for testing.
-- Run once on a fresh database, after schema.sql.
-- Passwords are stored as SHA-256 hashes, never as plain text.
-- =============================================================

USE salesmesh;

INSERT INTO users (email, password_hash, role) VALUES
('admin@salesmesh.com', SHA2('Admin@123', 256), 'admin'),
('user@salesmesh.com',  SHA2('User@123', 256),  'user');

INSERT INTO user_biodata (user_id, full_name, phone, department, job_title) VALUES
((SELECT user_id FROM users WHERE email = 'admin@salesmesh.com'),
 'System Administrator', '0700000001', 'IT', 'System Admin'),
((SELECT user_id FROM users WHERE email = 'user@salesmesh.com'),
 'Marketing User', '0700000002', 'Marketing', 'Marketing Manager');

-- Verify the two tables link correctly
SELECT u.user_id, u.email, u.role, b.full_name, b.department, b.job_title
FROM users u
JOIN user_biodata b ON u.user_id = b.user_id;
