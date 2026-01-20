import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
import sys
from typing import Annotated, Any, List
from uuid import UUID, uuid4

from aiosqlite import Connection, connect
from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field
import logging


# --- Prerequisites ---
DATABASE_URL = "weather_app.db"
LOGS_PATH = Path(__file__ + ".log")

logger = logging.getLogger("weatherio")
logger.setLevel(logging.INFO)

stdout_handler = logging.StreamHandler(sys.stdout)
file_handler = logging.FileHandler(LOGS_PATH)

logging.basicConfig(level=logging.INFO, handlers=[stdout_handler, file_handler])


# --- Models and DTOs ---
class CitySummary(BaseModel):
    id: Annotated[UUID, Field(default_factory=uuid4)]
    name: str
    lat: float
    lon: float


class CityDB(CitySummary):
    forecast: dict | None = None
    updated_at: str


class CityCreate(BaseModel):
    name: Annotated[str, Field(max_length=64)]
    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]
    user_id: UUID | None = None


class WeatherResponse(BaseModel):
    city_name: str
    time: str
    data: dict[str, Any]


class WeatherParameter(str, Enum):
    TEMPERATURE = "temperature_2m"
    HUMIDITY = "relative_humidity_2m"
    PRECIPITATION = "precipitation"
    WIND_SPEED = "wind_speed_10m"
    PRESSURE = "surface_pressure"

    ...


# --- logic ---
async def get_db_connection():
    async with connect(DATABASE_URL) as db:
        db.row_factory = None
        yield db


async def init_db(db: Connection):
    cursor = await db.cursor()

    await cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cities';"
    )
    exists = await cursor.fetchone()

    if not exists:
        logger.info("Initializing database schema...")
        await cursor.executescript("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL
            );

            CREATE TABLE cities (
                id TEXT,
                name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                forecast_json TEXT,
                last_update TIMESTAMP,
                UNIQUE(latitude, longitude)
            );

            CREATE TABLE user_cities (
                user_id TEXT,
                city_id TEXT,
                PRIMARY KEY (user_id, city_id),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (city_id) REFERENCES cities (id) ON DELETE CASCADE
            );
        """)
        await db.commit()


class OpenMeteoRepo:
    @staticmethod
    async def fetch_forecasts(
        coordinates: List[tuple[float, float]],
    ) -> list[dict[str, Any]]: ...


# background task performed every 15 minutes
async def refresh_forecasts(db: Connection) -> None: ...


async def refresh_task():
    while True:
        start_time = datetime.now()
        logging.info("Refreshing forecasts...")
        ...
        end_time = datetime.now()
        await asyncio.sleep(
            (timedelta(minutes=15) - (end_time - start_time)).total_seconds()
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with connect(DATABASE_URL) as db:
        await init_db(db)
    logger.info("System startup complete.")
    bg_task = asyncio.create_task(refresh_task())
    yield
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        logger.info("Background task shut down.")


class CityRepo:
    def __init__(self, db: Connection):
        self.db = db

    async def add_city(self, city: CityCreate) -> CitySummary: ...

    async def get_cities(self, user_id: UUID | None = None) -> List[CitySummary]: ...

    async def get_forecast_json(self, name: str) -> dict | None: ...

    async def link_user_city(self, user_id: UUID, city_id: UUID): ...


class UserRepo:
    def __init__(self, db: Connection):
        self.db = db

    async def create_user(self, name: str) -> UUID: ...


# --- API ---
app = FastAPI(lifespan=lifespan)


@app.get("/weather/current")
async def get_current_weather(lat: float, lon: float): ...


@app.get("/cities", response_model=List[CitySummary])
async def list_cities(
    user_id: UUID | None = None, db: Connection = Depends(get_db_connection)
):
    repo = CityRepo(db)
    return await repo.get_cities(user_id)


@app.post("/cities", response_model=CitySummary)
async def add_city(
    city_in: CityCreate,
    user_id: UUID | None = None,
    db: Connection = Depends(get_db_connection),
):
    repo = CityRepo(db)
    ...


@app.get("/weather/city/{name}", response_model=WeatherResponse)
async def city_weather(
    name: str,
    time: datetime,
    include: List[WeatherParameter] = Query(...),
    db: Connection = Depends(get_db_connection),
):
    repo = CityRepo(db)
    ...


@app.post("/users", response_model=UUID)
async def register_user(name: str, db: Connection = Depends(get_db_connection)):
    repo = UserRepo(db)
    return await repo.create_user(name)
