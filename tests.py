from datetime import datetime, time, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from aiosqlite import connect
from httpx import ASGITransport, AsyncClient

from script import (
    CityCreate,
    CityRepo,
    UserRepo,
    app,
    get_db_connection,
    init_db,
)


# -- setup --
@pytest_asyncio.fixture
async def db_connection():
    async with connect(":memory:") as db:
        await init_db(db)
        yield db


@pytest_asyncio.fixture
async def client(db_connection):
    async def override_get_db():
        yield db_connection

    app.dependency_overrides[get_db_connection] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# --- unit tests ---
@pytest.mark.asyncio
async def test_create_user_returns_uuid(db_connection):
    repo = UserRepo(db_connection)
    user_id = await repo.create_user("Test User")
    assert isinstance(user_id, UUID)


@pytest.mark.asyncio
async def test_add_city_and_retrieve(db_connection):
    repo = CityRepo(db_connection)
    city_in = CityCreate(name="London", lat=51.5074, lon=-0.1278)

    created_city = await repo.add_city(city_in)
    assert created_city.name == city_in.name.lower()

    cities = await repo.get_cities()
    assert len(cities) == 1
    assert cities[0].name == city_in.name.lower()

    # test forecast is there
    assert await repo.get_forecast_json(created_city.name) is not None


@pytest.mark.asyncio
async def test_link_user_to_city(db_connection):
    user_repo = UserRepo(db_connection)
    city_repo = CityRepo(db_connection)

    city_data = CityCreate(name="Paris", lat=48.8566, lon=2.3522)
    user_name = "Alice"

    user_id = await user_repo.create_user(user_name)
    city = await city_repo.add_city(city_data)

    await city_repo.link_user_city(user_id, city.id)

    user_cities = await city_repo.get_cities(user_id=user_id)
    assert len(user_cities) == 1
    assert user_cities[0].name == city_data.name


# -- API tests --
@pytest.mark.asyncio
async def test_register_user_endpoint(client):
    response = await client.post("/users", params={"name": "Bob"})
    assert response.status_code == 200
    assert isinstance(UUID(response.json()), UUID)


@pytest.mark.asyncio
async def test_add_and_list_cities_endpoint(client):
    city_data = {"name": "Berlin", "lat": 52.52, "lon": 13.405}
    post_res = await client.post("/cities", json=city_data)
    assert post_res.status_code == 200

    get_res = await client.get("/cities")
    assert get_res.status_code == 200
    assert len(get_res.json()) == 1
    assert get_res.json()[0]["name"] == city_data["name"].lower()


@pytest.mark.asyncio
async def test_get_city_weather_schema(client):
    city_data = {"name": "Tokyo", "lat": 35.68, "lon": 139.76}

    await client.post("/cities", json=city_data)

    request_forecast_time = (datetime.now() + timedelta(hours=5)).time()
    request_forecast_time = request_forecast_time.isoformat()
    params = {
        "time": request_forecast_time,
        "include": ["temperature_2m", "wind_speed_10m"],
    }
    response = await client.get("/weather/city/Tokyo", params=params)
    assert response.status_code == 200
    data = response.json()
    assert "city_name" in data


@pytest.mark.asyncio
async def test_weather_index_alignment(client, db_connection):
    city_name = "berlin"
    await client.post("/cities", json={"name": city_name, "lat": 52.52, "lon": 13.41})

    # the point of truth
    repo = CityRepo(db_connection)
    raw_forecast = await repo.get_forecast_json(city_name)
    assert raw_forecast

    hourly_data = raw_forecast["hourly"]

    for h in range(24):
        test_time = time(hour=h, minute=0)
        params = {"time": test_time.isoformat(), "include": ["temperature_2m"]}

        response = await client.get(f"/weather/city/{city_name}", params=params)
        assert response.status_code == 200

        res_data = response.json()

        for i, t in enumerate(hourly_data["time"]):
            if datetime.fromisoformat(t).hour == h:
                expected_temp = hourly_data["temperature_2m"][i]
                assert res_data["data"]["temperature_2m"] == expected_temp
