-- quoted-identifiers.sql: Tests Phase 2 (schema-qualified REFERENCES, all quoting styles)
CREATE TABLE source_table (
    id INTEGER PRIMARY KEY,
    double_quoted_ref INTEGER REFERENCES "public"."orders"("id"),
    backtick_ref INTEGER REFERENCES `catalog`.`products`(`id`),
    bracket_ref INTEGER REFERENCES [dbo].[users]([id]),
    simple_ref INTEGER REFERENCES invoices(id)
);
