-- Disposable database for the API integration tests (TEST_DATABASE_URL).
-- The test suite drops and re-creates its schema on every run.
CREATE DATABASE careerpilot_test;
\connect careerpilot_test
CREATE EXTENSION IF NOT EXISTS vector;
