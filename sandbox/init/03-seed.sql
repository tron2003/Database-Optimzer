-- ---------------------------------------------------------------------------
-- Seed data.
--
-- Sized so first boot takes roughly 30-60s on a laptop while still being big
-- enough that a sequential scan is visibly, measurably worse than an index
-- scan.  Below ~100k rows Postgres will happily seq-scan everything and the
-- whole demo falls flat.
-- ---------------------------------------------------------------------------

SET ROLE app_owner;
SET synchronous_commit = off;   -- seed speed only; irrelevant afterwards

-- --- categories ------------------------------------------------------------
INSERT INTO categories (id, name, parent_id)
SELECT g,
       'category-' || g,
       CASE WHEN g > 10 THEN 1 + (g % 10) ELSE NULL END
FROM generate_series(1, 60) AS g;

-- --- customers (50k) -------------------------------------------------------
INSERT INTO customers (email, full_name, country, signup_at, lifetime_cents)
SELECT
    'user' || g || '@example.com',
    'Customer ' || g,
    (ARRAY['IN','US','GB','DE','JP','BR','AU','CA'])[1 + (g % 8)],
    now() - (random() * interval '900 days'),
    (random() * 500000)::bigint
FROM generate_series(1, 50000) AS g;

-- --- products (5k) ---------------------------------------------------------
INSERT INTO products (sku, name, category_id, price_cents, stock, created_at)
SELECT
    'SKU-' || lpad(g::text, 8, '0'),
    'Product ' || g,
    1 + (g % 60),
    100 + (random() * 50000)::int,
    (random() * 500)::int,
    now() - (random() * interval '700 days')
FROM generate_series(1, 5000) AS g;

-- --- orders (120k) ---------------------------------------------------------
-- status is deliberately skewed: ~70% delivered.
INSERT INTO orders (customer_id, status, placed_at, total_cents)
SELECT
    1 + (random() * 49999)::bigint,
    CASE
        WHEN random() < 0.70 THEN 'delivered'
        WHEN random() < 0.80 THEN 'shipped'
        WHEN random() < 0.92 THEN 'pending'
        WHEN random() < 0.98 THEN 'cancelled'
        ELSE 'refunded'
    END,
    now() - (random() * interval '365 days'),
    (random() * 200000)::bigint
FROM generate_series(1, 120000) AS g;

-- --- order_items (~360k) ---------------------------------------------------
-- The item count per order is derived from o.id rather than from random().
-- A random() call in the LATERAL argument does not reference the outer row,
-- so the planner is free to hoist it out of the loop and evaluate it once for
-- the whole scan — which it does, giving every order an identical item count
-- and roughly a third of the intended rows. Keying off o.id makes it a real
-- lateral dependency, evaluated per row, and makes the seed reproducible,
-- which matters for a fixture whose purpose is to exhibit specific problems.
INSERT INTO order_items (order_id, product_id, qty, unit_price_cents)
SELECT
    o.id,
    1 + (random() * 4999)::bigint,
    1 + (random() * 4)::int,
    100 + (random() * 50000)::int
FROM orders o
CROSS JOIN LATERAL generate_series(1, 1 + (o.id % 5)::int) AS item;

-- --- events (300k) ---------------------------------------------------------
INSERT INTO events (customer_id, kind, payload, occurred_at)
SELECT
    1 + (random() * 49999)::bigint,
    (ARRAY['page_view','add_to_cart','search','checkout_start','login'])[1 + (g % 5)],
    jsonb_build_object('source', (ARRAY['web','ios','android'])[1 + (g % 3)],
                       'ms', (random() * 3000)::int),
    now() - (random() * interval '120 days')
FROM generate_series(1, 300000) AS g;

-- --- sessions (120k) -------------------------------------------------------
INSERT INTO sessions (customer_id, ip, started_at, ended_at)
SELECT
    1 + (random() * 49999)::bigint,
    ('10.' || (g % 255) || '.' || ((g / 255) % 255) || '.' || ((g / 65025) % 255))::inet,
    now() - (random() * interval '90 days'),
    now() - (random() * interval '89 days')
FROM generate_series(1, 120000) AS g;

-- ---------------------------------------------------------------------------
-- Manufacture bloat in one table so the bloat report has a real subject.
-- Repeatedly updating the same rows without vacuuming leaves dead tuples.
-- ---------------------------------------------------------------------------
UPDATE products SET stock = stock + 1 WHERE id % 2 = 0;
UPDATE products SET stock = stock - 1 WHERE id % 2 = 0;
UPDATE products SET price_cents = price_cents + 1 WHERE id % 3 = 0;
UPDATE products SET price_cents = price_cents - 1 WHERE id % 3 = 0;

-- RESET first: a bare ANALYZE while SET ROLE is in effect walks every
-- relation the role can see, including system catalogs it cannot read, and
-- fills the boot log with permission warnings.
RESET ROLE;

ANALYZE categories, customers, products, orders, order_items, events, sessions;
