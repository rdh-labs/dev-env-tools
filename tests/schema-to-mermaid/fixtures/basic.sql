-- basic.sql: Tests Phase 1 (constraint detection, type names, no false PK from digits)
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username VARCHAR(100) UNIQUE,
    score INTEGER DEFAULT 0,
    version VARCHAR(10) DEFAULT '1.0',
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
