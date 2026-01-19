# Notes
The main service functions:
- Keep track of cities to look for;
- Fetch and store the weather forecast data from https://open-meteo.com every 15 minutes;
- Serve the data to users via web api.

TODO:
- [x] Examine the https://open-meteo.com api
- [x] Analyze the core features:
  - [x] The way to store the data;
  - [x] The way to serve partial data;
  - [x] Time zones consideration
  - [x] The stack;

Implementation:
- [ ] Setup database.
- [ ] City repository (create, update, get)
- [ ] User repository (create, save_city, get_cities)
- [ ] Fetch function for coordinates.
- [ ] Fetch function for cities in the database.
- [ ] Background task with database automatic update.
- [ ] Web interface

Estimated time: ~8h
It could be way less but I would like to go with TDD, which may consume a bit extra time.

Additional:
- [ ] Users system
- [ ] Unit tests

Analysis:
1. One of the concerns is the `current_day` term. The best solution would be the requester's (user's) current day. Since many users can request one city, to satisfy this requirement I'll save two day forecast for each city, from UTC-12 to UTC+36. This ensures that service could provide `current_day` weather forecast for any timezone. This can be achieved with `start_hour` and `end_hour` request variables on open-meteo.
The other option is to save next 24 hours of and assume these 24 hours is the current day. This will eliminate need to save additional data and the only thing we would need to worry is to properly take into account user's timezone. This option seems more fitting.
2. I'll store data in SQLite database in the tables:

- City
  - id
  - Name      |
  - Latitude  | Unique constrain
  - Longitude |
  - Forecast (json)
  - Last_update
- User
  - username
  - id
- User_City
  - user_id
  - city_id

I store forecast in json because:
a) I will not need to change scheme if there will be any changes related to data in the forecast.
b) I need to update database every 15 minutes. inserting jsons is faster then properly mapping the data.

Required data will be extracted from the json on the server.

3. Data will be fetched and saved to database in batches of 100 cities every 15 minutes by background task.
4. I'll use REST api structure even for the 4th method. GraphQL seems to be overhead here.
5. API methods logic and structure:
  - GET weather/current(lat, long):
    - Get requester's timezone
    - Redirect (proxy) request to open-meteo api with timezone set.
    - NOTE: additional: if open-meteo api is not available, I may search for the closest city and provide it's forecast for the current time.
  - GET city:
    - Returns cities list (name and coordinates).
  - POST city(name, lat, long, user_id):
    - Check if city exists. If not:
      - Inserts city into the database
      - Sends update request to open-meteo and updates forecast for the city.
    - Assign city to user if user_id is provided
  - GET weather/city/{name} (time, include\[temp, hum, wind, rain\])
    - Fetch forecast json
    - Get forecast of the hour closest to the required time.
    - Send fields specified in include.
  - POST user(username)
    - Saves user to db

Honestly, storing forecasts for the same cities separately for each user is BS because we have one point of truth (open-meteo). And 2-4 methods don't need user's id: data does not depend on user in any way.
This is an issue of weak requirements and I will not do that. Instead I'll add endpoint that retrieves forecast data for all the user's cities to make use of the user entity:
  - GET weather/{user_id}(time, include)
    - Executes GET weather/city/{name} method logic for all the user's cities. Returns a list with forecasts.
  
6. I'll use FastAPI due to native async and autodocs. Sqlite as database, async sqlite interface (aiosqlite), httpx to fetch the data, pytest to setup tests.

# Time track
First glance analysis and formalization: ~1h
Second glance analysis: ~30m
