"""
SQLite persistence layer for CSI deal tracker.

Schema
------
deals
  id            INTEGER  PRIMARY KEY AUTOINCREMENT
  guid          TEXT     UNIQUE (GlobeNewsWire article GUID / URL — dedup key)
  published_at  TEXT     ISO-8601 date of press release
  entity        TEXT     Reporting entity (e.g. "Harris Computer")
  group_name    TEXT     CSI operating group (e.g. "Harris")
  title         TEXT     Press release headline
  summary       TEXT     First 500 chars of body text (optional)
  url           TEXT     Full article URL
  source        TEXT     "gnw_rss" | "gnw_search"
  created_at    TEXT     ISO-8601 timestamp of when row was inserted
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).parent / "deals.db"


def get_connection(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DEFAULT_DB) -> None:
    """Create tables if they don't exist."""
    with get_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guid         TEXT    NOT NULL UNIQUE,
                published_at TEXT,
                entity       TEXT,
                group_name   TEXT,
                title        TEXT,
                summary      TEXT,
                url          TEXT,
                source       TEXT,
                created_at   TEXT    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_published ON deals(published_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_group ON deals(group_name)")
        conn.commit()
    print(f"[db] Initialized: {db_path}")


def upsert_deal(deal: dict, db_path: Path = DEFAULT_DB) -> bool:
    """
    Insert deal if guid not present.
    Returns True if a new row was inserted, False if already existed.
    """
    deal["created_at"] = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO deals
                (guid, published_at, entity, group_name, title, summary, url, source, created_at)
            VALUES
                (:guid, :published_at, :entity, :group_name, :title, :summary, :url, :source, :created_at)
            """,
            deal,
        )
        conn.commit()
        return cur.rowcount == 1


def fetch_deals(
    group_name: str | None = None,
    since: str | None = None,
    limit: int = 200,
    db_path: Path = DEFAULT_DB,
) -> list[sqlite3.Row]:
    """
    Query deals with optional filters.

    Parameters
    ----------
    group_name : filter by operating group (e.g. "Harris")
    since      : ISO-8601 date string lower bound on published_at
    limit      : max rows returned
    """
    clauses, params = [], []
    if group_name:
        clauses.append("group_name = ?")
        params.append(group_name)
    if since:
        clauses.append("published_at >= ?")
        params.append(since)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    with get_connection(db_path) as conn:
        return conn.execute(
            f"SELECT * FROM deals {where} ORDER BY published_at DESC LIMIT ?",
            params,
        ).fetchall()


def deal_count(db_path: Path = DEFAULT_DB) -> int:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
