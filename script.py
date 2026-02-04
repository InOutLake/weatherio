import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, List
from uuid import UUID, uuid4

import httpx
import uvicorn
from aiosqlite import Connection, connect
from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, BeforeValidator, Field, field_validator

# --- Prerequisites ---
DATABASE_URL = "weatherio.db"
LOGS_PATH = Path(__file__).with_suffix(".log")

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)

file_handler = logging.FileHandler("weatherio.log")
file_handler.setFormatter(formatter)


# --- Models and Dtos ---
# Types
LowerCaseStr = Annotated[
    str, BeforeValidator(lambda v: v.lower() if isinstance(v, str) else v)
]

ID = UUID
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
NewId = Annotated[ID, Field(default_factory=uuid4)]


# Models
class CitySummary(BaseModel):
    id: ID
    name: str
    lat: float
    lon: float


class CityDB(CitySummary):
    forecast: dict | None = None


class CityCreate(BaseModel):
    id: NewId
    name: Annotated[LowerCaseStr, Field(min_length=1, max_length=64)]
    lat: Latitude
    lon: Longitude

    @field_validator("lat", "lon", mode="before")
    @classmethod
    def round_coordinates(cls, v: float) -> float:
        # Rounded to ~100m
        return round(v, 3)

    @field_validator("name")
    @classmethod
    def name_to_lowercase(cls, v: str) -> str:
        return v.lower()


class UserCreate(BaseModel):
    id: NewId
    name: Annotated[str, Field(max_length=64)]


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


# --- Logic ---
# Using one global connection since SQLite is a file db and many connections leads to I/O heavy operations
db_instance: Connection | None = None


async def get_db_connection():
    global db_instance
    if db_instance is None:
        raise RuntimeError("Database is not initialized")
    yield db_instance


async def init_db(db: Connection):
    cursor = await db.cursor()
    await cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cities';"
    )
    exists = await cursor.fetchone()

    if not exists:
        logging.info("Initializing database schema...")
        await cursor.executescript("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL
            );

            CREATE TABLE cities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                forecast_json TEXT,
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


class OpenMeteoUnnacessableError(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=500, detail={"message": "Open meteo is unnaccessable"}
        )


class OpenMeteoRepo:
    url = "https://api.open-meteo.com/v1/forecast"

    @classmethod
    async def fetch_forecasts(
        cls,
        coordinates: List[tuple[float, float]],
    ) -> list[dict[str, Any]]:  # type: ignore
        if not coordinates:
            return []

        lats = ",".join(str(c[0]) for c in coordinates)
        lons = ",".join(str(c[1]) for c in coordinates)

        start_datetime = datetime.now().replace(minute=0, second=0, microsecond=0)

        params = {
            "latitude": lats,
            "longitude": lons,
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,surface_pressure",
            "timezone": "auto",
            "start_hour": start_datetime.isoformat(),
            "end_hour": (start_datetime + timedelta(hours=23)).isoformat(),
        }

        async with httpx.AsyncClient() as client:
            retries, wait_for = 3, 10
            for attempt in range(retries):
                try:
                    resp = await client.get(cls.url, params=params, timeout=20)
                    resp.raise_for_status()
                    data = resp.json()
                    return data if isinstance(data, list) else [data]
                except httpx.HTTPStatusError as e:
                    logging.warning(f"Couldn't fetch data from open-meteo: {e}")
                    if attempt == retries - 1:
                        logging.error("Open-meteo is unnaccessable. Aborting fetch.")
                        raise OpenMeteoUnnacessableError
                    await asyncio.sleep(wait_for)

    @classmethod
    async def fetch_current(cls, lat: float, lon: float):
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,wind_speed_10m,surface_pressure",
        }
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(cls.url, params=params)
                resp.raise_for_status()
                data = resp.json()
                curr = data["current"]
                return {
                    "temperature": curr["temperature_2m"],
                    "wind_speed": curr["wind_speed_10m"],
                    "pressure": curr["surface_pressure"],
                }
            except httpx.HTTPStatusError:
                raise OpenMeteoUnnacessableError


# --- update forecasts task ---
async def refresh_forecasts(db: Connection) -> None:
    """Updates forecasts in batches"""
    cursor = await db.cursor()
    await cursor.execute("SELECT id, latitude, longitude FROM cities")

    while True:
        rows = await cursor.fetchmany(100)
        if not rows:
            break

        ids = [row[0] for row in rows]
        coords = [(row[1], row[2]) for row in rows]

        try:
            forecasts = await OpenMeteoRepo.fetch_forecasts(coords)
            for city_id, forecast in zip(ids, forecasts):
                await cursor.execute(
                    "UPDATE cities SET forecast_json = ? WHERE id = ?",
                    (json.dumps(forecast), city_id),
                )
            await db.commit()
        except OpenMeteoUnnacessableError as e:
            logging.error(f"Failed to update batch: {e}")
            continue


async def refresh_task(db: Connection):
    while True:
        start_time = datetime.now()
        try:
            await refresh_forecasts(db)
        except Exception as e:
            logging.error(f"Refresh failed: {e}")
        end_time = datetime.now()
        sleep_time = max(
            0, (timedelta(minutes=15) - (end_time - start_time)).total_seconds()
        )
        await asyncio.sleep(sleep_time)


# ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_instance
    db_instance = await connect(DATABASE_URL, timeout=60.0)

    # Most operations are reading so WAL might help a little and freshness of the data is not THAT important
    await db_instance.execute("PRAGMA journal_mode=WAL;")
    await db_instance.execute("PRAGMA synchronous=NORMAL;")

    await init_db(db_instance)

    bg_task = asyncio.create_task(refresh_task(db_instance))

    yield

    bg_task.cancel()
    await db_instance.close()


class UserRepo:
    def __init__(self, db: Connection):
        self.db = db

    async def create_user(self, user: UserCreate) -> ID:
        cursor = await self.db.cursor()
        await cursor.execute(
            "INSERT INTO users (id, username) VALUES (?, ?)", (str(user.id), user.name)
        )
        await self.db.commit()
        return user.id

    async def check_exists(self, user_id: ID) -> bool:
        cursor = await self.db.cursor()
        await cursor.execute("SELECT id FROM users WHERE id = ?", (str(user_id),))
        user = await cursor.fetchone()
        if user is not None:
            return True
        return False


class CityRepo:
    def __init__(self, db: Connection):
        self.db = db

    async def add_city(self, city: CityCreate) -> CitySummary:
        cursor = await self.db.cursor()
        # Not letting duplicates. Check by name left for user's freedom.
        await cursor.execute(
            "SELECT id, name, latitude, longitude FROM cities WHERE name = ? AND latitude = ? AND longitude = ?",
            (city.name, city.lat, city.lon),
        )
        existing = await cursor.fetchone()

        if existing:
            return CitySummary(
                id=ID(existing[0]), name=existing[1], lat=existing[2], lon=existing[3]
            )

        initial_forecast = None
        try:
            initial_forecast = await OpenMeteoRepo.fetch_forecasts(
                [(city.lat, city.lon)]
            )
            initial_forecast = json.dumps(initial_forecast[0])
        except OpenMeteoUnnacessableError:
            ...

        await cursor.execute(
            "INSERT INTO cities (id, name, latitude, longitude, forecast_json) VALUES (?, ?, ?, ?, ?)",
            (
                str(city.id),
                city.name,
                city.lat,
                city.lon,
                initial_forecast,
            ),
        )
        await self.db.commit()
        return CitySummary(id=city.id, name=city.name, lat=city.lat, lon=city.lon)

    async def get_cities(self, user_id: ID | None = None) -> List[CitySummary]:
        cursor = await self.db.cursor()
        if user_id:
            await cursor.execute(
                "SELECT c.id, c.name, c.latitude, c.longitude FROM cities c JOIN user_cities uc ON c.id = uc.city_id WHERE uc.user_id = ?",
                (str(user_id),),
            )
        else:
            await cursor.execute("SELECT id, name, latitude, longitude FROM cities")

        rows = await cursor.fetchall()
        return [
            CitySummary(id=ID(row[0]), name=row[1], lat=row[2], lon=row[3])
            for row in rows
        ]

    async def get_forecast_json(self, name: str) -> dict | None:
        cursor = await self.db.cursor()
        await cursor.execute("SELECT forecast_json FROM cities WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if row and row[0] is not None:
            return json.loads(row[0])
        return None

    async def link_user_city(self, user_id: ID, city_id: ID):
        user_repo = UserRepo(self.db)
        if not await user_repo.check_exists(user_id):
            raise ValueError("User does not exists")
        cursor = await self.db.cursor()
        await cursor.execute(
            "INSERT OR IGNORE INTO user_cities (user_id, city_id) VALUES (?, ?)",
            (str(user_id), str(city_id)),
        )
        await self.db.commit()


app = FastAPI(lifespan=lifespan)


@app.get("/weather/current")
async def get_current_weather(lat: Latitude, lon: Longitude):
    return await OpenMeteoRepo.fetch_current(lat, lon)


@app.get("/weather/city/{name}", response_model=WeatherResponse)
async def city_weather(
    name: LowerCaseStr,
    time: time,
    include: List[WeatherParameter] = Query(...),
    db: Connection = Depends(get_db_connection),
):
    repo = CityRepo(db)
    forecast = await repo.get_forecast_json(name)
    if not forecast:
        raise HTTPException(
            status_code=404, detail="City not found or forecast is not available"
        )

    hourly = forecast["hourly"]
    update_hour = datetime.fromisoformat(hourly["time"][0]).hour
    # this check prevents searching for current hour if user searches for the time 10 minutes before now and keeps time to the closest known point
    if time > datetime.now().time():
        requested_hour = time.hour if time.minute <= 30 else (time.hour + 1) % 24
    else:
        requested_hour = time.hour

    index = requested_hour - update_hour

    extracted_data = {p.value: hourly[p.value][index] for p in include}

    return WeatherResponse(
        city_name=name, time=time.strftime("%H:%M"), data=extracted_data
    )


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
    city = await repo.add_city(city_in)
    if user_id:
        try:
            await repo.link_user_city(user_id, city.id)
        except ValueError:
            ...
    return city


@app.post("/users", response_model=ID)
async def register_user(user: UserCreate, db: Connection = Depends(get_db_connection)):
    repo = UserRepo(db)
    return await repo.create_user(user)


if __name__ == "__main__":
    uvicorn.run("script:app", workers=4)
