from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from axis.db.schema import Base


def make_engine(db_url: str) -> Engine:
    if db_url.startswith("sqlite:///") and not db_url.startswith("sqlite:///:memory:"):
        Path(db_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def make_session_factory(db_url: str) -> sessionmaker[Session]:
    return sessionmaker(make_engine(db_url), expire_on_commit=False)


def upsert(session: Session, table, rows: list[dict], keys: list[str]) -> int:
    """按唯一键批量插入或更新。同一份数据重复同步不会产生重复行。"""
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    insert = {"sqlite": sqlite.insert, "postgresql": postgresql.insert}.get(dialect)
    if insert is None:
        raise RuntimeError(f"不支持的数据库: {dialect}")
    tbl = table.__table__
    update_cols = [c for c in rows[0] if c not in keys]
    # SQLite 单条语句的参数个数有限制，分批写入
    for i in range(0, len(rows), 500):
        stmt = insert(tbl).values(rows[i : i + 500])
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=keys, set_={c: stmt.excluded[c] for c in update_cols}
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=keys)
        session.execute(stmt)
    return len(rows)
