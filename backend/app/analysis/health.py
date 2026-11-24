"""Instance health checks derived from a metrics snapshot.

Each check returns a status and an explanation of what the number means,
because a raw gauge on a dashboard tells an operator nothing they can act on.
Where a figure is commonly misread, the check says so — the cache hit ratio
in particular is a famously misleading number and is treated as such here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.analysis.types import human_bytes

Status = Literal["ok", "warn", "critical", "unknown"]

_WEIGHT = {"ok": 0.0, "warn": 1.0, "critical": 3.0, "unknown": 0.0}


@dataclass(slots=True)
class HealthCheck:
    key: str
    label: str
    status: Status
    value: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "value": self.value,
            "message": self.message,
            "detail": self.detail,
        }


def evaluate(metrics: dict[str, Any], rates: dict[str, float] | None = None) -> dict[str, Any]:
    rates = rates or {}
    checks = [
        _cache_hit_ratio(metrics),
        _connection_saturation(metrics),
        _blocked_sessions(metrics),
        _idle_in_transaction(metrics),
        _long_transaction(metrics),
        _rollback_ratio(metrics, rates),
        _deadlocks(metrics),
        _temp_spill(metrics, rates),
    ]

    penalty = sum(_WEIGHT[c.status] for c in checks)
    worst = max((c.status for c in checks), key=lambda s: _WEIGHT[s], default="ok")
    score = max(0, round(100 - (penalty / (3.0 * len(checks))) * 100))

    return {
        "score": score,
        "status": worst,
        "checks": [c.as_dict() for c in checks],
    }


def _cache_hit_ratio(m: dict[str, Any]) -> HealthCheck:
    ratio = float(m.get("cache_hit_ratio") or 0.0)
    pct = f"{ratio:.2%}"
    # Deliberately not alarming on a "low" ratio alone. The number is
    # cumulative since the last stats reset and counts only shared buffers —
    # the OS page cache sits underneath it and is invisible here, so a 90%
    # ratio on a machine with plenty of RAM can be perfectly healthy.
    if ratio >= 0.99:
        return HealthCheck(
            "cache_hit_ratio",
            "Buffer cache hit ratio",
            "ok",
            pct,
            "Almost every block request is served from shared buffers.",
            {"ratio": ratio},
        )
    if ratio >= 0.90:
        return HealthCheck(
            "cache_hit_ratio",
            "Buffer cache hit ratio",
            "ok",
            pct,
            "Within a normal range. This counter excludes the operating system page "
            "cache, so the true hit rate is higher than it shows.",
            {"ratio": ratio},
        )
    return HealthCheck(
        "cache_hit_ratio",
        "Buffer cache hit ratio",
        "warn",
        pct,
        "A meaningful share of block requests miss shared buffers. Worth checking "
        "alongside the slow-query list: this is often one or two large sequential "
        "scans evicting everything else, in which case indexing those queries fixes "
        "the ratio as a side effect. Enlarging shared_buffers is the second answer, "
        "not the first.",
        {"ratio": ratio},
    )


def _connection_saturation(m: dict[str, Any]) -> HealthCheck:
    total = int(m.get("backends_total") or 0)
    maximum = int(m.get("max_connections") or 0)
    if maximum <= 0:
        return HealthCheck(
            "connections", "Connections", "unknown", str(total), "max_connections unknown."
        )
    used = total / maximum
    value = f"{total} / {maximum}"
    if used >= 0.9:
        return HealthCheck(
            "connections",
            "Connections",
            "critical",
            value,
            "Connection slots are nearly exhausted. When they run out, new sessions are "
            "refused outright, including the one you would use to investigate. Reserve "
            "slots with superuser_reserved_connections and put a pooler in front.",
            {"used_ratio": used},
        )
    if used >= 0.7:
        return HealthCheck(
            "connections",
            "Connections",
            "warn",
            value,
            "Connection use is climbing. Each backend costs memory whether or not it is "
            "doing anything, so a pooler is usually a better answer than raising the limit.",
            {"used_ratio": used},
        )
    return HealthCheck(
        "connections", "Connections", "ok", value, "Plenty of headroom.", {"used_ratio": used}
    )


def _blocked_sessions(m: dict[str, Any]) -> HealthCheck:
    blocked = int(m.get("blocked_sessions") or 0)
    if blocked == 0:
        return HealthCheck("locks", "Blocked sessions", "ok", "0", "Nothing is waiting on a lock.")
    status: Status = "critical" if blocked >= 5 else "warn"
    return HealthCheck(
        "locks",
        "Blocked sessions",
        status,
        str(blocked),
        f"{blocked} session(s) are waiting on a lock held by another. Lock waits "
        f"cascade: the blocker is often itself idle in a transaction someone forgot to "
        f"commit. Check the blocking tree to find the root holder.",
        {"blocked": blocked},
    )


def _idle_in_transaction(m: dict[str, Any]) -> HealthCheck:
    idle_tx = int(m.get("backends_idle_in_tx") or 0)
    if idle_tx == 0:
        return HealthCheck(
            "idle_in_tx", "Idle in transaction", "ok", "0", "No open, idle transactions."
        )
    status: Status = "critical" if idle_tx >= 5 else "warn"
    return HealthCheck(
        "idle_in_tx",
        "Idle in transaction",
        status,
        str(idle_tx),
        f"{idle_tx} session(s) hold an open transaction while doing nothing. These keep "
        f"locks and hold back the vacuum horizon, so dead tuples accumulate across the "
        f"whole database for as long as they persist. Set "
        f"idle_in_transaction_session_timeout on the application's role.",
        {"idle_in_transaction": idle_tx},
    )


def _long_transaction(m: dict[str, Any]) -> HealthCheck:
    seconds = float(m.get("longest_xact_seconds") or 0.0)
    value = f"{seconds:,.0f}s"
    if seconds < 60:
        return HealthCheck(
            "long_xact", "Longest transaction", "ok", value, "No long-running transactions."
        )
    status: Status = "critical" if seconds > 3600 else "warn"
    return HealthCheck(
        "long_xact",
        "Longest transaction",
        status,
        value,
        f"The oldest transaction has been open for {seconds / 60:,.0f} minutes. Until it "
        f"ends, vacuum cannot reclaim any row version newer than its snapshot anywhere "
        f"in the database.",
        {"seconds": seconds},
    )


def _rollback_ratio(m: dict[str, Any], rates: dict[str, float]) -> HealthCheck:
    commits = float(rates.get("xact_commit_per_sec") or 0.0)
    rollbacks = float(rates.get("xact_rollback_per_sec") or 0.0)
    total = commits + rollbacks
    if total < 1:
        return HealthCheck(
            "rollbacks", "Rollback ratio", "ok", "-", "Not enough transaction traffic to judge."
        )
    ratio = rollbacks / total
    value = f"{ratio:.1%}"
    if ratio < 0.05:
        return HealthCheck("rollbacks", "Rollback ratio", "ok", value, "Rollbacks are rare.")
    status: Status = "warn" if ratio < 0.25 else "critical"
    return HealthCheck(
        "rollbacks",
        "Rollback ratio",
        status,
        value,
        f"{ratio:.0%} of transactions are rolling back. That is usually an application "
        f"error path firing repeatedly — a constraint violation, a deadlock retry loop, "
        f"or a timeout — rather than a database problem as such.",
        {"ratio": ratio},
    )


def _deadlocks(m: dict[str, Any]) -> HealthCheck:
    deadlocks = int(m.get("deadlocks") or 0)
    if deadlocks == 0:
        return HealthCheck(
            "deadlocks", "Deadlocks", "ok", "0", "No deadlocks since the last stats reset."
        )
    return HealthCheck(
        "deadlocks",
        "Deadlocks",
        "warn",
        str(deadlocks),
        f"{deadlocks} deadlock(s) since the last statistics reset. Postgres resolves them "
        f"by killing one transaction, so they surface as intermittent application errors. "
        f"They almost always mean two code paths take the same locks in different orders.",
        {"deadlocks": deadlocks},
    )


def _temp_spill(m: dict[str, Any], rates: dict[str, float]) -> HealthCheck:
    temp_bytes = int(m.get("temp_bytes") or 0)
    per_sec = float(rates.get("temp_bytes_per_sec") or 0.0)
    value = human_bytes(temp_bytes)
    if per_sec <= 0:
        return HealthCheck(
            "temp_files",
            "Temporary files",
            "ok",
            value,
            "Nothing is currently spilling to temporary files.",
            {"temp_bytes": temp_bytes},
        )
    status: Status = "warn" if per_sec < 5 * 1024 * 1024 else "critical"
    return HealthCheck(
        "temp_files",
        "Temporary files",
        status,
        f"{human_bytes(per_sec)}/s",
        f"Queries are writing temporary files at {human_bytes(per_sec)} per second, which "
        f"means sorts or hashes are exceeding work_mem and falling back to disk. Find the "
        f"responsible query before raising work_mem globally: the setting applies per sort "
        f"node per worker, so a large value multiplies alarmingly under concurrency.",
        {"temp_bytes": temp_bytes, "temp_bytes_per_sec": per_sec},
    )
