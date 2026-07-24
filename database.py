import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event

load_dotenv()  # reads .env into os.environ if not already set

from config import DATA_DIR

db_path = os.path.join(DATA_DIR, "workspace.db")

# Default to local sqlite if DATABASE_URL is not set
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{db_path}")

# Fix legacy heroku postgres:// URLs to postgresql:// (required by SQLAlchemy 1.4+)
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql+pg8000://", 1)
elif SQLALCHEMY_DATABASE_URL.startswith("postgresql://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1)

is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")

connect_args = {}
if is_sqlite:
    # check_same_thread is needed for SQLite, and timeout=15 helps with concurrent writes
    connect_args = {"check_same_thread": False, "timeout": 15.0}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args=connect_args
)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
