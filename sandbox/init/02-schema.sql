-- ---------------------------------------------------------------------------
-- A small e-commerce schema, deliberately mis-indexed.
--
-- Every problem below is one the analyzer is expected to find on first boot.
-- They are labelled so you can check the tool's output against ground truth.
-- ---------------------------------------------------------------------------

SET ROLE app_owner;

CREATE TABLE categories (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    parent_id   integer REFERENCES categories (id)
);

CREATE TABLE customers (
    id          bigserial PRIMARY KEY,
    email       text NOT NULL,
    full_name   text NOT NULL,
    country     text NOT NULL,
    signup_at   timestamptz NOT NULL,
    lifetime_cents bigint NOT NULL DEFAULT 0
);

CREATE TABLE products (
    id          bigserial PRIMARY KEY,
    sku         text NOT NULL,
    name        text NOT NULL,
    category_id integer NOT NULL REFERENCES categories (id),
    price_cents integer NOT NULL,
    stock       integer NOT NULL,
    created_at  timestamptz NOT NULL
);

CREATE TABLE orders (
    id          bigserial PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers (id),
    status      text NOT NULL,
    placed_at   timestamptz NOT NULL,
    total_cents bigint NOT NULL
);

CREATE TABLE order_items (
    id              bigserial PRIMARY KEY,
    order_id        bigint NOT NULL REFERENCES orders (id),
    product_id      bigint NOT NULL REFERENCES products (id),
    qty             integer NOT NULL,
    unit_price_cents integer NOT NULL
);

CREATE TABLE events (
    id          bigserial PRIMARY KEY,
    customer_id bigint,
    kind        text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL
);

CREATE TABLE sessions (
    id          bigserial PRIMARY KEY,
    customer_id bigint,
    ip          inet NOT NULL,
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz
);

-- ===========================================================================
-- PROBLEM 1 — duplicate index.
-- The UNIQUE constraint already builds an index on (email).  This second
-- btree on the same column is pure write amplification and wasted space.
-- ===========================================================================
ALTER TABLE customers ADD CONSTRAINT customers_email_key UNIQUE (email);
CREATE INDEX idx_customers_email ON customers (email);

-- ===========================================================================
-- PROBLEM 2 — redundant prefix index.
-- idx_orders_customer_status covers (customer_id, status); the single-column
-- index on customer_id is a left-prefix of it and earns nothing.
-- NOTE both are created, but the workload only ever uses the composite.
-- ===========================================================================
CREATE INDEX idx_orders_customer_status ON orders (customer_id, status);
CREATE INDEX idx_orders_customer ON orders (customer_id);

-- ===========================================================================
-- PROBLEM 3 — unused indexes.
-- Nothing in the workload touches either of these.  After a few minutes of
-- traffic pg_stat_user_indexes.idx_scan stays at 0 for both.
-- ===========================================================================
CREATE INDEX idx_products_created_at ON products (created_at);
CREATE INDEX idx_sessions_ip ON sessions (ip);

-- ===========================================================================
-- PROBLEM 4 — MISSING indexes.  These are the expensive ones.
--
--   order_items.order_id   : the workload joins on it constantly.  Without an
--                            index every order detail lookup seq-scans ~400k
--                            rows.  This is the single biggest win available.
--   events.occurred_at     : time-range queries scan the whole table.
--   events.customer_id     : per-customer timelines scan the whole table.
--   products.sku           : exact-match lookups seq-scan.
--   orders.placed_at       : the daily revenue rollup scans everything.
--
-- They are intentionally NOT created here.
-- ===========================================================================

-- A legitimate, useful index, so the analyzer has to distinguish good from bad.
CREATE INDEX idx_order_items_product ON order_items (product_id);

-- ===========================================================================
-- PROBLEM 5 — a column whose statistics target is too low for its skew.
-- `status` is heavily skewed ('delivered' dominates) which makes the planner
-- misestimate selectivity on the rarer values.
-- ===========================================================================
ALTER TABLE orders ALTER COLUMN status SET STATISTICS 1;

RESET ROLE;
