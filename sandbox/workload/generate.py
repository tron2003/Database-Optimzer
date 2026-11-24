"""Continuous synthetic workload against the sandbox database.

The point of this process is that the monitoring tool should have something
real to find within a minute of `docker compose up`.  The query mix is chosen
so that the five missing indexes declared in 02-schema.sql actually hurt:
roughly two thirds of the traffic hits an unindexed predicate.

Each statement is wrapped in its own short transaction and every connection
carries a statement_timeout, so a pathological plan degrades throughput
instead of wedging the database.
"""

from __future__ import annotations

import os
import random
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import sql  # noqa: F401  (kept for future parameterised DDL)

DSN = os.environ.get(
    "SANDBOX_DSN", "postgresql://workload:workload@localhost:5434/shopdb"
)
CONCURRENCY = int(os.environ.get("WORKLOAD_CONCURRENCY", "4"))
PAUSE = float(os.environ.get("WORKLOAD_PAUSE_SECONDS", "0.4"))

_stop = threading.Event()


# --------------------------------------------------------------------------
# Query definitions.
#
# weight:  relative frequency
# label:   human name, matched against pg_stat_statements output by eye
# slow:    True when this statement is expected to be slow until the tool's
#          recommended index is applied.  Used only for the startup banner.
# --------------------------------------------------------------------------
def _q_order_items(cur: psycopg.Cursor) -> None:
    """Missing index on order_items.order_id — the headline problem."""
    order_id = random.randint(1, 120_000)
    cur.execute(
        """
        SELECT oi.id, oi.product_id, oi.qty, oi.unit_price_cents
        FROM order_items oi
        WHERE oi.order_id = %s
        """,
        (order_id,),
    )
    cur.fetchall()


def _q_customer_timeline(cur: psycopg.Cursor) -> None:
    """Missing index on events.customer_id."""
    customer_id = random.randint(1, 50_000)
    cur.execute(
        """
        SELECT kind, occurred_at, payload
        FROM events
        WHERE customer_id = %s
        ORDER BY occurred_at DESC
        LIMIT 50
        """,
        (customer_id,),
    )
    cur.fetchall()


def _q_daily_revenue(cur: psycopg.Cursor) -> None:
    """Missing index on orders.placed_at."""
    days = random.choice([7, 14, 30])
    cur.execute(
        """
        SELECT date_trunc('day', placed_at) AS day,
               count(*)                     AS orders,
               sum(total_cents)             AS revenue_cents
        FROM orders
        WHERE placed_at >= now() - make_interval(days => %s)
        GROUP BY 1
        ORDER BY 1 DESC
        """,
        (days,),
    )
    cur.fetchall()


def _q_sku_lookup(cur: psycopg.Cursor) -> None:
    """Missing index on products.sku."""
    sku = f"SKU-{random.randint(1, 5000):08d}"
    cur.execute(
        "SELECT id, name, price_cents, stock FROM products WHERE sku = %s",
        (sku,),
    )
    cur.fetchall()


def _q_event_window(cur: psycopg.Cursor) -> None:
    """Missing index on events.occurred_at."""
    end = datetime.now(timezone.utc) - timedelta(days=random.randint(0, 100))
    start = end - timedelta(hours=random.randint(6, 48))
    cur.execute(
        """
        SELECT kind, count(*)
        FROM events
        WHERE occurred_at BETWEEN %s AND %s
        GROUP BY kind
        """,
        (start, end),
    )
    cur.fetchall()


def _q_orders_by_status(cur: psycopg.Cursor) -> None:
    """Well served by idx_orders_customer_status — the control case."""
    customer_id = random.randint(1, 50_000)
    status = random.choice(["delivered", "pending", "shipped"])
    cur.execute(
        """
        SELECT id, placed_at, total_cents
        FROM orders
        WHERE customer_id = %s AND status = %s
        ORDER BY placed_at DESC
        """,
        (customer_id, status),
    )
    cur.fetchall()


def _q_product_popularity(cur: psycopg.Cursor) -> None:
    """Uses idx_order_items_product; a heavier aggregate either way."""
    category_id = random.randint(1, 60)
    cur.execute(
        """
        SELECT p.name, sum(oi.qty) AS units
        FROM products p
        JOIN order_items oi ON oi.product_id = p.id
        WHERE p.category_id = %s
        GROUP BY p.name
        ORDER BY units DESC
        LIMIT 20
        """,
        (category_id,),
    )
    cur.fetchall()


def _w_insert_event(cur: psycopg.Cursor) -> None:
    cur.execute(
        """
        INSERT INTO events (customer_id, kind, payload, occurred_at)
        VALUES (%s, %s, %s::jsonb, now())
        """,
        (
            random.randint(1, 50_000),
            random.choice(["page_view", "add_to_cart", "search"]),
            '{"source": "web"}',
        ),
    )


def _w_update_stock(cur: psycopg.Cursor) -> None:
    """Churns rows to keep dead-tuple counts climbing, feeding the bloat view."""
    cur.execute(
        "UPDATE products SET stock = stock + %s WHERE id = %s",
        (random.choice([-1, 1]), random.randint(1, 5000)),
    )


WORKLOAD: list[tuple[int, str, bool, object]] = [
    (22, "order_items_by_order", True, _q_order_items),
    (16, "customer_event_timeline", True, _q_customer_timeline),
    (10, "daily_revenue_rollup", True, _q_daily_revenue),
    (12, "product_by_sku", True, _q_sku_lookup),
    (8, "event_window_counts", True, _q_event_window),
    (14, "orders_by_customer_status", False, _q_orders_by_status),
    (6, "product_popularity", False, _q_product_popularity),
    (8, "insert_event", False, _w_insert_event),
    (4, "update_stock", False, _w_update_stock),
]

_CHOICES = [item for item in WORKLOAD]
_WEIGHTS = [item[0] for item in WORKLOAD]


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(DSN, autocommit=True, connect_timeout=10)
    with conn.cursor() as cur:
        # A runaway plan should fail this one statement, not stall the worker.
        cur.execute("SET statement_timeout = '30s'")
        cur.execute("SET application_name = 'dbopt-workload'")
    return conn


def worker(idx: int) -> None:
    conn: psycopg.Connection | None = None
    while not _stop.is_set():
        try:
            if conn is None or conn.closed:
                conn = _connect()
            _, label, _, fn = random.choices(_CHOICES, weights=_WEIGHTS, k=1)[0]
            try:
                with conn.cursor() as cur:
                    fn(cur)
            except psycopg.errors.QueryCanceled:
                # Expected while the schema is still un-indexed.
                pass
            except psycopg.Error as exc:
                print(f"[worker {idx}] {label}: {exc}", file=sys.stderr, flush=True)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
            _stop.wait(PAUSE * random.uniform(0.5, 1.5))
        except psycopg.OperationalError as exc:
            print(f"[worker {idx}] reconnecting: {exc}", file=sys.stderr, flush=True)
            conn = None
            _stop.wait(2.0)
    if conn is not None and not conn.closed:
        conn.close()


def _wait_for_db(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(DSN, connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM order_items")
                    (n,) = cur.fetchone()
                    if n and n > 0:
                        print(f"sandbox ready ({n} order_items)", flush=True)
                        return
                    print("waiting for seed data...", flush=True)
        except psycopg.Error:
            print("waiting for sandbox...", flush=True)
        time.sleep(3)
    raise SystemExit("sandbox did not become ready in time")


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())

    _wait_for_db()

    slow = [label for _, label, is_slow, _ in WORKLOAD if is_slow]
    print(
        f"starting {CONCURRENCY} workers; "
        f"{len(slow)} statement shapes are expected to be slow until indexed: "
        + ", ".join(slow),
        flush=True,
    )

    threads = [
        threading.Thread(target=worker, args=(i,), daemon=True)
        for i in range(CONCURRENCY)
    ]
    for t in threads:
        t.start()
    try:
        while not _stop.is_set():
            _stop.wait(1.0)
    finally:
        _stop.set()
        for t in threads:
            t.join(timeout=5)
    print("workload stopped", flush=True)


if __name__ == "__main__":
    main()
