# Tests

Run inside the backend container (has all deps + a real Postgres to
point at, or SQLite in-memory for the DB-backed tests below):

    docker compose run --rm backend pip install -r requirements-dev.txt
    docker compose run --rm backend pytest ../tests -v

Or locally with a venv:

    cd backend && pip install -r requirements-dev.txt
    pytest ../tests -v
