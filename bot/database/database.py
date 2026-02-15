from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from bot.config import Config
from contextlib import asynccontextmanager

# Поддержка PostgreSQL и SQLite
_db_url = Config.DATABASE_URL or "sqlite+aiosqlite:///meownomeow.db"
_connect_args = {}
if _db_url.startswith("postgresql"):
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif "sqlite" in _db_url:
    if not _db_url.startswith("sqlite+"):
        _db_url = "sqlite+aiosqlite:///" + (_db_url.replace("sqlite:///", "").replace("sqlite://", "") or "meownomeow.db")
    _connect_args = {"timeout": 15}

engine = create_async_engine(_db_url, echo=False, connect_args=_connect_args)

# Создаем session factory
async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()


@asynccontextmanager
async def get_session():
    async with async_session_maker() as session:
        yield session
