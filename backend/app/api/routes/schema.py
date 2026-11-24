"""Index, table and bloat endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analysis.maintenance import bloat_from_pgstattuple
from app.api.deps import ResolvedConnection, get_target
from app.collector import queries as q
from app.db.target import TargetUnavailable, rows_to_dicts
from app.schemas.api import IndexOut, TableOut

router = APIRouter(prefix="/connections/{connection_id}", tags=["schema"])


@router.get("/indexes", response_model=list[IndexOut])
async def list_indexes(target: ResolvedConnection = Depends(get_target)):
    rows = rows_to_dicts(await target.pool.fetch(q.INDEX_STATS))
    return [
        IndexOut(
            index_name=r["index_name"],
            table_name=r["table_name"],
            schemaname=r["schemaname"],
            key_columns=list(r.get("key_columns") or []),
            method=r.get("method"),
            indisunique=bool(r.get("indisunique")),
            indisprimary=bool(r.get("indisprimary")),
            indisvalid=bool(r.get("indisvalid", True)),
            is_partial=bool(r.get("is_partial")),
            idx_scan=int(r.get("idx_scan") or 0),
            index_bytes=int(r.get("index_bytes") or 0),
            indexdef=r.get("indexdef"),
        )
        for r in rows
    ]


@router.get("/indexes/invalid")
async def invalid_indexes(target: ResolvedConnection = Depends(get_target)):
    """Indexes left INVALID by a failed CREATE INDEX CONCURRENTLY.

    An invalid index is the worst of both worlds: the planner will not use it,
    but every write still maintains it. They are easy to miss because nothing
    surfaces them by default.
    """
    rows = rows_to_dicts(await target.pool.fetch(q.INDEX_STATS))
    invalid = [r for r in rows if not r.get("indisvalid", True)]
    return {
        "count": len(invalid),
        "note": (
            "These were most likely left behind by a failed CREATE INDEX "
            "CONCURRENTLY. They cost writes and space but are never used. Drop each "
            "with DROP INDEX CONCURRENTLY and rebuild if still wanted."
        )
        if invalid
        else "No invalid indexes.",
        "indexes": [
            {
                "index_name": r["index_name"],
                "table_name": r["table_name"],
                "schemaname": r["schemaname"],
                "index_bytes": r.get("index_bytes"),
                "indexdef": r.get("indexdef"),
            }
            for r in invalid
        ],
    }


@router.get("/tables", response_model=list[TableOut])
async def list_tables(
    limit: int = Query(default=100, ge=1, le=500),
    target: ResolvedConnection = Depends(get_target),
):
    rows = rows_to_dicts(await target.pool.fetch(q.TABLE_STATS, limit))
    out: list[TableOut] = []
    for r in rows:
        live = int(r.get("n_live_tup") or 0)
        dead = int(r.get("n_dead_tup") or 0)
        out.append(
            TableOut(
                schemaname=r["schemaname"],
                table_name=r["table_name"],
                seq_scan=int(r.get("seq_scan") or 0),
                seq_tup_read=int(r.get("seq_tup_read") or 0),
                idx_scan=int(r.get("idx_scan") or 0),
                n_live_tup=live,
                n_dead_tup=dead,
                n_mod_since_analyze=int(r.get("n_mod_since_analyze") or 0),
                total_bytes=int(r.get("total_bytes") or 0),
                heap_bytes=int(r.get("heap_bytes") or 0),
                index_bytes=int(r.get("index_bytes") or 0),
                last_autovacuum=r.get("last_autovacuum"),
                last_autoanalyze=r.get("last_autoanalyze"),
                dead_ratio=(dead / (live + dead)) if (live + dead) else 0.0,
            )
        )
    return out


@router.get("/schema")
async def schema_overview(
    limit: int = Query(default=100, ge=1, le=500),
    target: ResolvedConnection = Depends(get_target),
):
    return {"tables": rows_to_dicts(await target.pool.fetch(q.SCHEMA_OVERVIEW, limit))}


@router.get("/tables/{schema_name}/{table_name}/bloat")
async def exact_bloat(
    schema_name: str,
    table_name: str,
    target: ResolvedConnection = Depends(get_target),
):
    """Exact bloat via pgstattuple.

    This reads the entire relation, so it is a deliberate, on-demand action
    rather than something the collector does on a timer. On a large table it
    is slow and generates real I/O.
    """
    if not target.has_pgstattuple:
        raise HTTPException(
            status_code=409,
            detail=(
                "pgstattuple is not installed on this database. Run CREATE EXTENSION "
                "pgstattuple to enable exact measurement; until then the Tables view "
                "shows the statistics collector's estimate."
            ),
        )
    try:
        row = await target.pool.fetchrow(q.EXACT_BLOAT, schema_name, table_name)
    except TargetUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="table not found")
    return bloat_from_pgstattuple(dict(row))


@router.get("/foreign-keys/unindexed")
async def unindexed_foreign_keys(target: ResolvedConnection = Depends(get_target)):
    rows = rows_to_dicts(await target.pool.fetch(q.UNINDEXED_FOREIGN_KEYS))
    return {"count": len(rows), "foreign_keys": rows}
