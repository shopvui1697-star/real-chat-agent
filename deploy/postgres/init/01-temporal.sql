-- Temporal databases (Phase 3)
-- auto-setup needs both DBs; temporal user must be able to create/manage them.
CREATE USER temporal WITH PASSWORD 'temporal' CREATEDB;
CREATE DATABASE temporal OWNER temporal;
CREATE DATABASE temporal_visibility OWNER temporal;
