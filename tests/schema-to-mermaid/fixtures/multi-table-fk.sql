-- multi-table-fk.sql: Tests Phase 1+2 (multiple tables, inline REFERENCES, CONSTRAINT skip)
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    total DECIMAL(10,2) NOT NULL,
    CONSTRAINT fk_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    product VARCHAR(100) NOT NULL,
    quantity INTEGER DEFAULT 1
);
