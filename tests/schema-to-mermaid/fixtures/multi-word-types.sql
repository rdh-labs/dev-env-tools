-- multi-word-types.sql: Tests Phase 3 (multi-word SQL types, stop-keyword list)
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE,
    score DOUBLE PRECISION,
    description CHARACTER VARYING(255),
    count INT NOT NULL,
    label VARCHAR(50) COLLATE utf8mb4_unicode_ci
);
