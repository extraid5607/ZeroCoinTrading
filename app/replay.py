"""History replay: run the exact live scanning code over stored candles from scratch.

The live scan and the replay share `signals.advance`, so they cannot disagree (tested). Use this
after changing a pattern threshold, to rebuild past signals and chains with the new rules."""
from __future__ import annotations

from sqlalchemy import delete

from .config import Settings
from .db import Chain, Database, ScanState, Signal
from .scanner import scan_timeframe


def replay_all(db: Database, tf: str, settings: Settings) -> dict:
    with db.session() as s:
        for table in (Signal, Chain, ScanState):
            s.execute(delete(table).where(table.tf == tf))
        s.commit()
    return scan_timeframe(db, tf, settings)
