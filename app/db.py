"""Database tables and a small wrapper around the engine."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager

from sqlalchemy import (Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
                        create_engine, event, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Coin(Base):
    __tablename__ = "coins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(48))
    venue: Mapped[str] = mapped_column(String(24))
    quote_volume: Mapped[float] = mapped_column(Float, default=0.0)
    last_price: Mapped[float] = mapped_column(Float, default=0.0)
    tier: Mapped[str] = mapped_column(String(1), default="C")
    sector: Mapped[str] = mapped_column(String(24), default="Other")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    note: Mapped[str] = mapped_column(String(80), default="")     # why it is inactive, if it is
    updated: Mapped[float] = mapped_column(Float, default=0.0)


class Candle(Base):
    __tablename__ = "candles"
    coin_id: Mapped[int] = mapped_column(ForeignKey("coins.id", ondelete="CASCADE"), primary_key=True)
    tf: Mapped[str] = mapped_column(String(3), primary_key=True)
    ts: Mapped[int] = mapped_column(Integer, primary_key=True)       # candle open, epoch seconds
    o: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    l: Mapped[float] = mapped_column(Float)
    c: Mapped[float] = mapped_column(Float)
    v: Mapped[float] = mapped_column(Float)


class ScanState(Base):
    """How far the pattern scan has got for one coin and timeframe."""
    __tablename__ = "scan_state"
    coin_id: Mapped[int] = mapped_column(ForeignKey("coins.id", ondelete="CASCADE"), primary_key=True)
    tf: Mapped[str] = mapped_column(String(3), primary_key=True)
    last_ts: Mapped[int] = mapped_column(Integer)
    last_close: Mapped[float] = mapped_column(Float, default=0.0)


class Chain(Base):
    __tablename__ = "chains"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coin_id: Mapped[int] = mapped_column(ForeignKey("coins.id", ondelete="CASCADE"), index=True)
    tf: Mapped[str] = mapped_column(String(3))
    pattern: Mapped[str] = mapped_column(String(12))           # pattern that opened the chain
    level: Mapped[float] = mapped_column(Float)                # level broken by the first signal
    target: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)          # fixed for the life of the chain
    entry_ts: Mapped[int] = mapped_column(Integer)
    initial_stop: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)                 # trailing, only ever moves up
    status: Mapped[str] = mapped_column(String(8), default="active")   # active | stopped
    last_ts: Mapped[int] = mapped_column(Integer)
    last_close: Mapped[float] = mapped_column(Float)
    volume_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    signal_count: Mapped[int] = mapped_column(Integer, default=1)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_ts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (Index("ix_chain_tf_status", "tf", "status"),)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coin_id: Mapped[int] = mapped_column(ForeignKey("coins.id", ondelete="CASCADE"))
    tf: Mapped[str] = mapped_column(String(3))
    pattern: Mapped[str] = mapped_column(String(12))
    direction: Mapped[str] = mapped_column(String(5))
    level: Mapped[float] = mapped_column(Float)
    entry: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    volume_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    candle_ts: Mapped[int] = mapped_column(Integer)
    chain_id: Mapped[int | None] = mapped_column(ForeignKey("chains.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (
        UniqueConstraint("coin_id", "tf", "pattern", "candle_ts", name="uq_signal"),
        Index("ix_signal_coin_tf", "coin_id", "tf"),
    )


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tf: Mapped[str] = mapped_column(String(3))
    started: Mapped[float] = mapped_column(Float)
    finished: Mapped[float | None] = mapped_column(Float, nullable=True)
    candle_ts: Mapped[int] = mapped_column(Integer, default=0)
    coins_scanned: Mapped[int] = mapped_column(Integer, default=0)
    coins_skipped: Mapped[int] = mapped_column(Integer, default=0)
    signals_created: Mapped[int] = mapped_column(Integer, default=0)
    missing: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str] = mapped_column(Text, default="")


class KV(Base):
    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class TradePosition(Base):
    __tablename__ = "trade_positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(48), index=True)
    base: Mapped[str] = mapped_column(String(32), index=True)
    venue: Mapped[str] = mapped_column(String(24), default="delta")
    side: Mapped[str] = mapped_column(String(8), default="long")
    size: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    pattern: Mapped[str] = mapped_column(String(24))
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed | stopped | target_hit
    mode: Mapped[str] = mapped_column(String(8), default="paper")     # paper | live
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)
    closed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "base": self.base,
            "venue": self.venue,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "pattern": self.pattern,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "status": self.status,
            "mode": self.mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "close_price": self.close_price,
            "exit_reason": self.exit_reason,
        }


def normalise_url(url: str) -> str:
    """Use the psycopg 3 driver for PostgreSQL URLs."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Database:
    def __init__(self, url: str) -> None:
        url = normalise_url(url)
        self.url = url
        kw: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, **kw)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragma(dbapi, _rec):                        # noqa: ANN001
                cur = dbapi.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        self.sessionmaker = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        s: Session = self.sessionmaker()
        try:
            yield s
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()


# ---- small key/value helpers (JSON values) ---------------------------------------------
def kv_get(s: Session, key: str, default=None):
    row = s.get(KV, key)
    if row is None:
        return default
    try:
        return json.loads(row.value)
    except ValueError:
        return default


def kv_set(s: Session, key: str, value) -> None:
    row = s.get(KV, key)
    text = json.dumps(value)
    if row is None:
        s.add(KV(key=key, value=text))
    else:
        row.value = text
    s.commit()


def now() -> float:
    return time.time()


def active_coins(s: Session) -> list[Coin]:
    return list(s.scalars(select(Coin).where(Coin.active.is_(True)).order_by(Coin.quote_volume.desc())))
