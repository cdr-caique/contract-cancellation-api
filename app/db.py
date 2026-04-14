import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False) if engine else None

Base = declarative_base()


def get_db():
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
