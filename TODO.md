The main service functions:
- Keep track of cities to look for;
- Fetch and store the weather forecast data from https://open-meteo.com every 15 minutes;
- Serve the data to users via web api.

TODO:
- [ ] Examine the https://open-meteo.com api
- [ ] Analyze the core features:
  - [ ] The way to store the data;
  - [ ] The way to serve partial data;
  - [ ] The stack;

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
