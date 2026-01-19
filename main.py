from enum import Enum
from fastapi import FastAPI
from decimal import Decimal
from aiosqlite import connect
from typing import Annotated
from uuid import uuid4
from pydantic import UUID4, BaseModel, Field, Json
from pydantic.types import datetime
from contextlib import asynccontextmanager

DATABASE_CONNECTION = "weather_app.db"
OPEN_METEO_URL = "https://open-meteo.com"


class WeatherParameter(str, Enum):
    TEMPERATURE = "temperature_2m"
    HUMIDITY = "relative_humidity_2m"
    PRECIPITATION = "precipitation"
    WIND_SPEED = "wind_speed_10m"


async def init_db(): ...


async def fetch_data(): ...


class City(BaseModel):
    id: Annotated[UUID4, Field(default_factory=uuid4)]
    name: Annotated[str, Field(max_length=64)]
    lat: Annotated[Decimal, Field(ge=-90, le=90, max_digits=2, decimal_places=6)]
    lon: Annotated[Decimal, Field(ge=-180, le=180, max_digits=3, decimal_places=6)]
    forecast: Json
    updated_at: datetime


class User(BaseModel):
    id: Annotated[UUID4, Field(default_factory=uuid4)]
    name: Annotated[str, Field(max_length=64)]


# TODO: figure out forecast models if needed

# NOTE: Classes are present only for functions organizing purpose.


class ForecastRepo:
    @staticmethod
    async def fetch_forecasts(coordinates: list[tuple[float, float]]) -> list[Json]: ...

    @staticmethod
    async def fetch_current(coordinates: tuple[float, float]): ...


class CityRepo:
    @staticmethod
    async def add(name: str, lat: float, lon: float) -> City: ...

    @staticmethod
    async def list(
        user_id: str | None = None, forecast: bool = False
    ) -> list[City]: ...

    @staticmethod
    async def update_forecasts(): ...


class UserRepo:
    @staticmethod
    async def add(name: str) -> UUID4: ...

    @staticmethod
    async def add_city(name: str, lat: float, lon: float): ...


app = FastAPI()


@app.get("/weather/current", response_model=...)
async def current_weather(latitude: float, longitude: float): ...


@app.get("/city", response_model=...)
async def cities_list(): ...


@app.post("/city", response_model=...)
async def add_city(
    name: str,
    latitude: float,
    longitude: float,
    user_id: UUID4 | None = None,
): ...


@app.get("/weather/city/{name}", response_model=...)
async def city_weather(name: str, time: datetime, include: list[WeatherParameter]): ...


@app.post("/user")
async def register_user(name: str) -> UUID4: ...


@app.post("/user/city")
async def add_city_to_user(name: str) -> City: ...

