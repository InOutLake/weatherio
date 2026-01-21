import asyncio
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from aiosqlite import connect
from fastapi import status
from httpx import ASGITransport, AsyncClient, Request, Response

from script import (
    CityCreate,
    CityRepo,
    OpenMeteoRepo,
    OpenMeteoUnnacessableError,
    UserCreate,
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
    user_id = await repo.create_user(UserCreate(name="Test User"))  # type: ignore
    assert isinstance(user_id, UUID)


@pytest.mark.asyncio
async def test_add_city_and_retrieve(db_connection):
    repo = CityRepo(db_connection)
    city_in = CityCreate(name="London", lat=51.5074, lon=-0.1278)  # type: ignore

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

    city_data = CityCreate(name="Paris", lat=48.8566, lon=2.3522)  # type: ignore
    user_name = "Alice"

    user_id = await user_repo.create_user(UserCreate(name=user_name))  # type: ignore
    city = await city_repo.add_city(city_data)

    await city_repo.link_user_city(user_id, city.id)

    user_cities = await city_repo.get_cities(user_id=user_id)
    assert len(user_cities) == 1
    assert user_cities[0].name == city_data.name


# -- API tests --
@pytest.mark.asyncio
async def test_register_user_endpoint(client):
    response = await client.post("/users", json={"name": "Bob"})
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


# -- Edge case and invalid data tests --
# NOTE: Most of that stuff below is generated and fixed afterwards.
@pytest.mark.asyncio
async def test_invalid_coordinates_low_latitude(client):
    response = await client.get("/weather/current", params={"lat": -91, "lon": 0})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_coordinates_high_latitude(client):
    response = await client.get("/weather/current", params={"lat": 91, "lon": 0})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_coordinates_low_longitude(client):
    response = await client.get("/weather/current", params={"lat": 0, "lon": -181})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_coordinates_high_longitude(client):
    response = await client.get("/weather/current", params={"lat": 0, "lon": 181})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_boundary_coordinates(client):
    response = await client.get("/weather/current", params={"lat": -90, "lon": -180})
    assert response.status_code == 200

    response = await client.get("/weather/current", params={"lat": 90, "lon": 180})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_invalid_city_name_too_long(client):
    long_name = "a" * 65
    city_data = {"name": long_name, "lat": 52.52, "lon": 13.405}
    response = await client.post("/cities", json=city_data)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_city_name_empty(client):
    city_data = {"name": "", "lat": 52.52, "lon": 13.405}
    response = await client.post("/cities", json=city_data)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_city_name_case_insensitive(client):
    city_data = {"name": "NEWYORK", "lat": 40.7128, "lon": -74.0060}
    response = await client.post("/cities", json=city_data)
    assert response.status_code == 200

    city = response.json()
    assert city["name"] == "newyork"


@pytest.mark.asyncio
async def test_invalid_weather_parameters(client):
    city_data = {"name": "TestCity", "lat": 40.7128, "lon": -74.0060}
    await client.post("/cities", json=city_data)

    params = {"time": time(12, 0).isoformat(), "include": ["invalid_parameter"]}
    response = await client.get("/weather/city/testcity", params=params)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_no_weather_parameters(client):
    city_data = {"name": "TestCity2", "lat": 40.7128, "lon": -74.0060}
    await client.post("/cities", json=city_data)

    params = {"time": time(12, 0).isoformat(), "include": []}
    response = await client.get("/weather/city/testcity2", params=params)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_missing_time_parameter(client):
    city_data = {"name": "TestCity3", "lat": 40.7128, "lon": -74.0060}
    await client.post("/cities", json=city_data)

    params = {"include": ["temperature_2m"]}
    response = await client.get("/weather/city/testcity3", params=params)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_time_format(client):
    city_data = {"name": "TestCity4", "lat": 40.7128, "lon": -74.0060}
    await client.post("/cities", json=city_data)

    params = {"time": "invalid_time_format", "include": ["temperature_2m"]}
    response = await client.get("/weather/city/testcity4", params=params)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_nonexistent_city_weather(client):
    params = {"time": time(12, 0).isoformat(), "include": ["temperature_2m"]}
    response = await client.get("/weather/city/nonexistentcity", params=params)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_nonexistent_city_list(client):
    import uuid

    fake_user_id = str(uuid.uuid4())
    response = await client.get(f"/cities?user_id={fake_user_id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_invalid_user_id_format(client):
    response = await client.get("/cities?user_id=invalid-uuid-format")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_add_city_with_invalid_user_id(client):
    city_data = {"name": "TestCity5", "lat": 40.7128, "lon": -74.0060}
    response = await client.post(
        "/cities", json=city_data, params={"user_id": "invalid-uuid-format"}
    )
    assert response.status_code == 422


# OpenMeteo API failure
@pytest.mark.asyncio
async def test_openuv_api_failure_during_city_creation():
    with patch.object(
        OpenMeteoRepo, "fetch_forecasts", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = OpenMeteoUnnacessableError()

        city_data = {"name": "TestCity7", "lat": 40.7128, "lon": -74.0060}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post("/cities", json=city_data)
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_openuv_api_http_error():
    with patch.object(
        OpenMeteoRepo, "fetch_current", new_callable=AsyncMock
    ) as mock_fetch:
        mock_fetch.side_effect = OpenMeteoUnnacessableError()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.get(
                "/weather/current", params={"lat": 40.7128, "lon": -74.0060}
            )
            assert response.status_code in [500, 422]


@pytest.mark.asyncio
async def test_concurrent_city_additions():
    async def override_get_db():
        async with connect(":memory:") as db:
            await init_db(db)
            yield db

    app.dependency_overrides[get_db_connection] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:

        async def add_city(city_num):
            city_data = {
                "name": f"ConcurrentCity{city_num}",
                "lat": 40.0 + city_num,
                "lon": -74.0,
            }
            response = await ac.post("/cities", json=city_data)
            return response.status_code

        tasks = [add_city(i) for i in range(5)]
        results = await asyncio.gather(*tasks)

        for result in results:
            assert result == 200

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_weather_requests(client):
    city_data = {"name": "WeatherCity", "lat": 40.7128, "lon": -74.0060}
    await client.post("/cities", json=city_data)

    async def get_weather(time_offset):
        params = {
            "time": time((10 + time_offset) % 24, 0).isoformat(),
            "include": ["temperature_2m"],
        }
        response = await client.get("/weather/city/weathercity", params=params)
        return response.status_code

    tasks = [get_weather(i) for i in range(3)]
    results = await asyncio.gather(*tasks)

    for result in results:
        assert result == 200 or result == 404
