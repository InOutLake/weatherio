The main service functions:
- Keep track of cities to look for;
- Fetch and store the weather forecast data from https://open-meteo.com every 15 minutes;
- Serve the data to users via web api.

TODO:
- [x] Examine the https://open-meteo.com api
- [x] Analyze the core features:
  - [x] The way to store the data;
  - [x] The way to serve partial data;
  - [x] The stack;

- [ ] Set the cities to look for.
- [ ] Setup background process that:
  - [ ] Fetches the data every 15 minutes;
  - [ ] Saves the data to a storage.
- [ ] Fetch the data and save it to a temporary storage.
- [ ] Setup fetching interfaces:
  - [ ] Cities list;
  - [ ] Weather forecast on the time with filters;
- [ ] API methods

Additional:
- [ ] Users system
- [ ] Unit tests

Analysis:
1. One of the concerns is the `current_day` term. The best solution would be the requester's (user's) current day. Since many users can request one city, to satisfy this requirement I'll save two day forecast, from UTC-6 to UTC+42. This ensures that service could provide `current_day` weather forecast for any timezone. This can be achieved with `start_hour` and `end_hour` request variables on open-meteo.
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
a) I will not need to change scheme if there is any changes related to data in the forecast.
b) I need to update db every 15 minutes. If there is even 15k cities it may take a while to update 'hourly' data.

Required data will be extracted from the json on the server.

3. Data will be fetched and saved to database in batches of 100 cities every 15 minutes by background task.
4. I'll use REST api structure even for the 4th method. GraphQL seems to be overhead here.
5. API methods logic:
  - GET weather/current(lat, long):
    - Get requester's timezone
    - Redirect request to open-meteo api with timezone set.
  - GET city:
    - Returns cities list (name and coordinates).
  - POST city(name, lat, long):
    - Inserts city into the database
    - Sends update request to open-meteo and updates forecast for the city.
  - GET weather/city/{name} (time, include\[temp, hum, wind, rain\])
    - Fetch forecast json
    - Get forecast of the hour closest to the required time.
    - Send fields specified in include.
  - POST user(username)
    - Saves user to db
  - POST user/city/{city_name}:
    - Assigns city to user.

Honestly, storing forecasts for the same cities separately for each user is BS because we have one point of truth (open-meteo). And 2-4 methods don't need user's id: data is not dependent on the user in any way. 
This is an issue of weak requirements and I will not do that. Instead I'll add endpoint that retrieves forecast data for all the user's cities:
  - GET weather/{user_id}(time, include)
    - Executes GET weather/city/{name} method logic for all the user's cities. Returns a list with forecasts.
  
6. I'll use FastAPI due to native async and autodocs. Sqlite as database, standard python sqlite interface, httpx to fetch the data, pytest to setup tests.

First glance analysis and formalization took: ~1h
