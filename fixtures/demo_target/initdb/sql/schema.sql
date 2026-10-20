-- Demo target schema and seed. Run once by 01_schema.sh on the first start of an empty data volume
-- (psql variable :rows = DEMO_ORDERS_ROWS).
--
-- FAULT 1 is planted here: orders.customer_email has NO index, and the app role has a short
-- statement_timeout, so the email search seq-scans millions of rows and times out under load.
-- FAULT 2 is planted here too: app_settings has no 'refund_policy' row.

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Application role (what demo-api connects as). The investigator role is NOT created here:
-- it comes from scripts/sql/create_investigator_role.sql in step E3.
CREATE ROLE demo_app LOGIN PASSWORD 'demo-app-local-only';
GRANT CONNECT ON DATABASE target_db TO demo_app;
ALTER ROLE demo_app SET statement_timeout = '300ms';

CREATE TABLE customers (
    id          bigserial PRIMARY KEY,
    email       text NOT NULL UNIQUE,
    full_name   text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id              bigserial PRIMARY KEY,
    customer_id     bigint NOT NULL REFERENCES customers (id),
    customer_email  text NOT NULL,          -- denormalised for search; deliberately NOT indexed (fault 1)
    status          text NOT NULL,
    total_cents     integer NOT NULL,
    created_at      timestamptz NOT NULL,
    notes           text
);
CREATE INDEX ix_orders_customer_id ON orders (customer_id);

CREATE TABLE refunds (
    id           bigserial PRIMARY KEY,
    order_id     bigint NOT NULL REFERENCES orders (id),
    amount_cents integer NOT NULL,
    deadline     timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_settings (
    key    text PRIMARY KEY,
    value  jsonb NOT NULL
);
-- 'refund_policy' is intentionally missing (fault 2)
INSERT INTO app_settings (key, value) VALUES
    ('currency',         '"USD"'),
    ('search_page_size', '20'),
    ('support_email',    '"support@demo.invalid"');

-- Seed: 50k customers, DEMO_ORDERS_ROWS orders (default 2M; raise it if fault 1 doesn't reproduce on fast hardware)
INSERT INTO customers (email, full_name, created_at)
SELECT format('customer%s@demo.invalid', g), format('Customer %s', g), now() - (g % 900) * interval '1 day'
FROM generate_series(1, 50000) AS g;

INSERT INTO orders (customer_id, customer_email, status, total_cents, created_at, notes)
SELECT pick.c,
       format('customer%s@demo.invalid', pick.c),
       (ARRAY['paid', 'shipped', 'delivered', 'cancelled'])[1 + (g % 4)],
       500 + (g * 37) % 50000,
       now() - (g % 720) * interval '1 hour',
       repeat('x', 40)
FROM generate_series(1::bigint, :rows::bigint) AS g,
     LATERAL (SELECT 1 + (g * 7919) % 50000 AS c) AS pick;

ANALYZE;

GRANT SELECT ON customers, orders, app_settings TO demo_app;
GRANT SELECT, INSERT ON refunds TO demo_app;
GRANT USAGE ON SEQUENCE refunds_id_seq TO demo_app;

-- Marker the compose healthcheck waits for, so demo-api only starts after the seed completes
CREATE TABLE demo_seed_done (done int);
INSERT INTO demo_seed_done VALUES (1);
GRANT SELECT ON demo_seed_done TO demo_app;
