-- multiline-columns.sql: Tests Phase 8 (multi-line column definitions, DEFAULT parens)
CREATE TABLE products
(
    id INTEGER PRIMARY KEY,
    name VARCHAR(100)
        NOT NULL DEFAULT 'Unknown Product',
    description TEXT,
    price DECIMAL(10,2)
        NOT NULL DEFAULT 0.00,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);
