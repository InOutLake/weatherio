import collections
from locust import HttpUser, task, between
from datetime import time
import random
import os

HOST = os.getenv("WEATHERIO_HOST", "http://127.0.0.1:8000")


def rand_lat():
    return round(random.uniform(-90, 90), 3)


def rand_lon():
    return round(random.uniform(-180, 180), 3)


def rand_time():
    return time(random.randint(0, 23), random.randint(0, 59))


City = collections.namedtuple("City", ["name", "lat", "lon"])

all_cities = {City(f"city_{i}", rand_lat(), rand_lon()) for i in range(500)}
added_cities = set()


class WeatherIoUser(HttpUser):
    wait_time = between(1, 3)
    host = HOST

    def on_start(self):
        user_data = {"name": f"load_test_user_{random.randint(1000, 9999)}"}
        response = self.client.post("/users", json=user_data)

        if response.status_code == 200:
            json_response = response.json()
            self.user_id = (
                json_response.get("id")
                if isinstance(json_response, dict)
                else json_response
            )
        else:
            self.user_id = None

    @task(1)
    def get_weather_current(self):
        lat = round(random.uniform(-90, 90))
        lon = random.randint(-180, 180)
        self.client.get(f"/weather/current?lat={lat}&lon={lon}")

    @task(20)
    def get_city_weather(self):
        if not added_cities:
            return

        city_obj = next(iter(added_cities))

        all_params = [
            "temperature_2m",
            "relative_humidity_2m",
            "precipitation",
            "wind_speed_10m",
            "surface_pressure",
        ]
        selected_params = random.sample(all_params, random.randint(1, len(all_params)))
        params_str = "&".join([f"include={param}" for param in selected_params])

        self.client.get(
            f"/weather/city/{city_obj.name}?time={rand_time()}&{params_str}"
        )

    @task(10)
    def list_all_cities(self):
        self.client.get("/cities")

    @task(10)
    def list_user_cities(self):
        if hasattr(self, "user_id") and self.user_id:
            self.client.get(f"/cities?user_id={self.user_id}")

    @task(20)
    def add_new_city(self):
        if not all_cities:
            return

        city_data = all_cities.pop()
        added_cities.add(city_data)

        payload = city_data._asdict()
        self.client.post("/cities", json=payload)
