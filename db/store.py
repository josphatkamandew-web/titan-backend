"""
Persistence layer — now portable between local SQLite (dev) and a
real hosted Postgres (production). This matters because most
serverless-adjacent hosts (Render free tier, Railway, etc.) do NOT
guarantee a persistent local disk between deploys — a SQLite file
sitting next to the app code can silently reset, wiping validation
history and the trade journal. Set DATABASE_URL to a Postgres
connection string in production; leave it unset for local dev and
it uses a SQLite file exactly as before.

ON CONFLICT ... DO UPDATE syntax is shared between modern SQLite
(3.24+) and Postgres, so the upsert logic didn't need to change —
only table creation (autoincrement syntax differs) and how bind
parameters are passed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DEFAULT_SQLITE_PATH = Path(__file__).parent / "titan.db"

DEFAULT_STATUS = "INVESTIGATE"


def _build_engine(database_url: Optional[str], db_path: Optional[Path]) -> Engine:
    url = database_url or os.environ.get("DATABASE_URL")
    if url:
        # Render/Heroku-style URLs sometimes use postgres:// — SQLAlchemy needs postgresql://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return create_engine(url, pool_pre_ping=True)
    path = db_path or DEFAULT_SQLITE_PATH
    return create_engine(f"sqlite:///{path}")


class ValidationStore:
    def __init__(self, db_path: Optional[Path] = None, database_url: Optional[str] = None):
        self.engine = _build_engine(database_url, db_path)
        self.is_postgres = self.engine.dialect.name == "postgresql"
        self._init_schema()

    def _init_schema(self) -> None:
        pk = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        statements = [
            """
            CREATE TABLE IF NOT EXISTS validation_status (
                instrument TEXT NOT NULL,
                engine TEXT NOT NULL,
                setup TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'INVESTIGATE',
                sample_size INTEGER NOT NULL DEFAULT 0,
                win_rate_percent DOUBLE PRECISION,
                expectancy_r DOUBLE PRECISION,
                profit_factor DOUBLE PRECISION,
                reason TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (instrument, engine, setup)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS journal (
                id {pk},
                instrument TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                setup TEXT,
                direction TEXT,
                entry DOUBLE PRECISION,
                stop DOUBLE PRECISION,
                target DOUBLE PRECISION,
                result TEXT,
                r_multiple DOUBLE PRECISION,
                confidence_percent DOUBLE PRECISION,
                directional_strength DOUBLE PRECISION,
                tradeability_percent DOUBLE PRECISION,
                opened_at TEXT,
                closed_at TEXT,
                raw_analysis_json TEXT
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS calibration_log (
                id {pk},
                instrument TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                predicted_confidence DOUBLE PRECISION NOT NULL,
                predicted_direction TEXT NOT NULL,
                outcome TEXT,
                r_multiple DOUBLE PRECISION,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """,
        ]
        with self.engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))

    # ---------------- validation status ----------------

    def get_status(self, instrument: str, engine: str, setup: str = "DEFAULT") -> Dict[str, Any]:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT * FROM validation_status WHERE instrument=:i AND engine=:e AND setup=:s"),
                {"i": instrument, "e": engine, "s": setup},
            ).mappings().fetchone()
        if row is None:
            return {"instrument": instrument, "engine": engine, "setup": setup,
                     "status": DEFAULT_STATUS, "sample_size": 0}
        return dict(row)

    def upsert_status(self, instrument: str, engine: str, setup: str, status: str,
                       sample_size: int, win_rate_percent: Optional[float] = None,
                       expectancy_r: Optional[float] = None, profit_factor: Optional[float] = None,
                       reason: str = "") -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO validation_status
                        (instrument, engine, setup, status, sample_size, win_rate_percent,
                         expectancy_r, profit_factor, reason, updated_at)
                    VALUES (:instrument, :engine, :setup, :status, :sample_size, :win_rate_percent,
                            :expectancy_r, :profit_factor, :reason, :updated_at)
                    ON CONFLICT (instrument, engine, setup) DO UPDATE SET
                        status=excluded.status,
                        sample_size=excluded.sample_size,
                        win_rate_percent=excluded.win_rate_percent,
                        expectancy_r=excluded.expectancy_r,
                        profit_factor=excluded.profit_factor,
                        reason=excluded.reason,
                        updated_at=excluded.updated_at
                """),
                {"instrument": instrument, "engine": engine, "setup": setup, "status": status,
                 "sample_size": sample_size, "win_rate_percent": win_rate_percent,
                 "expectancy_r": expectancy_r, "profit_factor": profit_factor, "reason": reason,
                 "updated_at": datetime.now(timezone.utc).isoformat()},
            )

    def all_statuses(self, instrument: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.engine.begin() as conn:
            if instrument:
                rows = conn.execute(
                    text("SELECT * FROM validation_status WHERE instrument=:i"), {"i": instrument}
                ).mappings().fetchall()
            else:
                rows = conn.execute(text("SELECT * FROM validation_status")).mappings().fetchall()
        return [dict(r) for r in rows]

    # ---------------- journal ----------------

    def log_trade(self, **kwargs) -> int:
        fields = ["instrument", "timeframe", "setup", "direction", "entry", "stop", "target",
                  "result", "r_multiple", "confidence_percent", "directional_strength",
                  "tradeability_percent", "opened_at", "closed_at", "raw_analysis_json"]
        params = {f: kwargs.get(f) for f in fields}
        col_list = ", ".join(fields)
        val_list = ", ".join(f":{f}" for f in fields)
        returning = " RETURNING id" if self.is_postgres else ""
        with self.engine.begin() as conn:
            result = conn.execute(text(f"INSERT INTO journal ({col_list}) VALUES ({val_list}){returning}"), params)
            if self.is_postgres:
                return result.scalar_one()
            return result.lastrowid

    def list_journal(self, instrument: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        with self.engine.begin() as conn:
            if instrument:
                rows = conn.execute(
                    text("SELECT * FROM journal WHERE instrument=:i ORDER BY id DESC LIMIT :lim"),
                    {"i": instrument, "lim": limit},
                ).mappings().fetchall()
            else:
                rows = conn.execute(
                    text("SELECT * FROM journal ORDER BY id DESC LIMIT :lim"), {"lim": limit}
                ).mappings().fetchall()
        return [dict(r) for r in rows]

    def open_trades_today(self, instrument: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) as c FROM journal WHERE instrument=:i AND substr(opened_at,1,10)=:d"),
                {"i": instrument, "d": today},
            ).mappings().fetchone()
        return int(row["c"]) if row else 0

    # ---------------- calibration ----------------

    def log_prediction(self, instrument: str, timeframe: str, predicted_confidence: float,
                        predicted_direction: str) -> int:
        returning = " RETURNING id" if self.is_postgres else ""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(f"""INSERT INTO calibration_log
                       (instrument, timeframe, predicted_confidence, predicted_direction, created_at)
                       VALUES (:i, :t, :c, :d, :ts){returning}"""),
                {"i": instrument, "t": timeframe, "c": predicted_confidence, "d": predicted_direction,
                 "ts": datetime.now(timezone.utc).isoformat()},
            )
            if self.is_postgres:
                return result.scalar_one()
            return result.lastrowid

    def resolve_prediction(self, prediction_id: int, outcome: str, r_multiple: float) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE calibration_log SET outcome=:o, r_multiple=:r, resolved_at=:ts WHERE id=:id"),
                {"o": outcome, "r": r_multiple, "ts": datetime.now(timezone.utc).isoformat(), "id": prediction_id},
            )

    def calibration_summary(self, instrument: Optional[str] = None) -> Dict[str, Any]:
        with self.engine.begin() as conn:
            if instrument:
                rows = conn.execute(
                    text("SELECT * FROM calibration_log WHERE outcome IS NOT NULL AND instrument=:i"),
                    {"i": instrument},
                ).mappings().fetchall()
            else:
                rows = conn.execute(
                    text("SELECT * FROM calibration_log WHERE outcome IS NOT NULL")
                ).mappings().fetchall()
        rows = [dict(r) for r in rows]

        if not rows:
            return {"status": "NO_RESOLVED_PREDICTIONS", "buckets": []}

        buckets: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            bucket = f"{int(r['predicted_confidence'] // 10) * 10}-{int(r['predicted_confidence'] // 10) * 10 + 9}%"
            b = buckets.setdefault(bucket, {"n": 0, "wins": 0})
            b["n"] += 1
            if r["outcome"] == "WIN":
                b["wins"] += 1

        out = [
            {"confidence_bucket": k, "n": v["n"], "actual_win_rate_percent": round(100 * v["wins"] / v["n"], 1)}
            for k, v in sorted(buckets.items())
        ]

        sorted_by_conf = sorted(buckets.items())
        miscalibrated = False
        if len(sorted_by_conf) >= 2:
            win_rates = [v["wins"] / v["n"] for _, v in sorted_by_conf]
            if win_rates[-1] < win_rates[0]:
                miscalibrated = True

        return {"status": "MISCALIBRATED" if miscalibrated else "OK", "buckets": out}
