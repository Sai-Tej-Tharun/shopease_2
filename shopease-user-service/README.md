# ShopEase User Service

Independent User Management microservice of ShopEase: customer profiles, contact details, delivery addresses and default-address handling. It does **not** have its own login or registration: users sign up and log in through the Authentication Service, and this service only checks the JWT that the Authentication Service issued.

## 1. Tech stack

FastAPI, SQLAlchemy 2, MySQL 8, Alembic, PyJWT (token validation), Pydantic v2, Docker and Docker Compose (Python 3.11).

## 2. Project structure

```
shopease-user-service/
├── app/
│   ├── main.py                      app, exception handlers, /health
│   ├── routers/user_routes.py       profile + account endpoints
│   ├── routers/address_routes.py    address endpoints
│   ├── models/user_model.py         user_profiles table
│   ├── models/address_model.py      user_addresses table
│   ├── schemas/user_schema.py       profile validation rules / responses
│   ├── schemas/address_schema.py    address validation rules / responses
│   ├── services/user_service.py     profile business logic, "current profile" dependencies
│   ├── services/address_service.py  address business logic, default-address rules
│   ├── core/config.py               settings from environment (.env)
│   ├── core/security.py             JWT validation (+ optional check with the Auth Service)
│   └── database/connection.py       engine, session, safe commit/rollback
├── alembic/  alembic.ini            migrations
├── tests/                           automated tests (pytest)
├── postman/                         Postman collection
├── Dockerfile  docker-compose.yml  requirements.txt  .env.example  .env  .dockerignore
```

Responsibilities are kept apart: routers only receive requests, schemas validate, services hold the business rules, models describe tables, `security.py` handles authentication.

## 3. Database (`shopease_user_db`)

**user_profiles**

| Column | Type | Notes |
|---|---|---|
| id | INT | primary key |
| auth_user_id | INT | user id from the Authentication Service; **unique** constraint |
| full_name | VARCHAR(100) | |
| phone_number | VARCHAR(20) | normalised, e.g. `+919876543210` |
| date_of_birth | DATE | optional |
| gender | VARCHAR(20) | optional |
| profile_image | VARCHAR(255) | optional URL or path |
| is_active | BOOLEAN | **added** for account management (default true), see section 6 |
| created_at / updated_at | DATETIME | |

**user_addresses**

| Column | Type | Notes |
|---|---|---|
| id | INT | primary key |
| user_id | INT | foreign key to `user_profiles.id`, `ON DELETE CASCADE` |
| address_type | VARCHAR(10) | Home, Office or Other |
| full_name, phone_number | | recipient |
| address_line_1, address_line_2, city, state, country, postal_code | | line 2 optional |
| is_default | BOOLEAN | at most one true per user |
| created_at | DATETIME | |

SQLAlchemy relationship: `UserProfile.addresses` ⇄ `UserAddress.user` (cascade delete-orphan). No password, e-mail or other credential is stored here.

## 4. Environment variables (`.env`)

```bat
copy .env.example .env
```

| Variable | Purpose |
|---|---|
| MYSQL_ROOT_PASSWORD, MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD | MySQL container setup and app connection |
| MYSQL_HOST_PORT | port published on your PC for MySQL (default 3308) |
| MYSQL_HOST, MYSQL_PORT | used when running the API on your PC (`localhost` / `3308`); Compose overrides them with `user-db` / `3306` |
| JWT_SECRET_KEY, JWT_ALGORITHM | **must be identical to the Authentication Service**; secret has no default and needs at least 32 characters |
| AUTH_VERIFY_REMOTE | `false` (default): validate tokens locally. `true`: additionally ask the Authentication Service (see section 5) |
| AUTH_SERVICE_URL | Authentication Service address for the optional check (default `http://localhost:8001`) |
| AUTH_SERVICE_URL_IN_DOCKER | the same address as seen from inside Docker (default `http://host.docker.internal:8001`) |

Nothing secret is hard-coded. `.env` is git-ignored and docker-ignored: do not commit it (commit `.env.example` only).

## 5. Authentication integration

**Agreed mechanism:** the Authentication Service signs a JWT (HS256) with claims `sub` (its user id), `role`, `iat`, `exp`. The User Service validates the signature and expiry with the same `JWT_SECRET_KEY` / `JWT_ALGORITHM`, so no call to the Authentication Service is needed for each request. Both services must therefore be configured with the same secret.

How it is used:

- Every endpoint takes the `Authorization: Bearer <token>` header. A missing, malformed, tampered, expired or `alg: none` token gives **401**.
- The user id is read from the token (`sub`) and used to find the profile (`auth_user_id`). Request bodies never carry an owner: fields such as `auth_user_id`, `user_id` or `id` are rejected with 422, so another user's id cannot be supplied as proof of ownership.
- **403** is returned when access is not permitted: an address that belongs to another user, an address/profile change while the profile is deactivated, or (with remote verification) an account that is inactive in the Authentication Service. A non-existent address gives 404.
- Optional stricter mode `AUTH_VERIFY_REMOTE=true`: after the local check the User Service also calls `POST {AUTH_SERVICE_URL}/api/auth/verify-token` with the same token. The Authentication Service then also checks that the account still exists and is active (it answers 401/403 otherwise, which is passed on). If the Authentication Service cannot be reached the API answers 503. Invalid tokens are rejected locally and never sent to the Authentication Service.

Get a token from the Authentication Service (`POST http://localhost:8001/api/auth/register`, then `/api/auth/login`) and paste the `access_token` in Swagger UI (**Authorize**) at `http://localhost:8002/docs`.

## 6. Account status: how the two services work together

| Situation | Authentication Service | User Service |
|---|---|---|
| New customer | registers and logs in, owns e-mail, password and the login status (`is_active`) | the customer creates a profile; it is linked only by `auth_user_id` |
| Customer deactivates the profile (`POST /api/users/profile/deactivate`) | **not changed**: the login account stays active and the customer can still log in | profile is kept (`is_active = false`). Reads of the profile and `/account` still work; profile changes and all address operations return 403 until `POST /api/users/profile/reactivate` |
| Profile deleted (`DELETE /api/users/profile`) | not changed | profile and its addresses are removed permanently |
| Account disabled in the Authentication Service | the user can no longer log in; already issued tokens stay valid until they expire (30 minutes) | with `AUTH_VERIFY_REMOTE=true` the User Service rejects those tokens immediately (403); with the default local check they work until expiry |

Rules that follow from this: the User Service never changes or deletes anything in the Authentication Service (no silent deletion), and the Authentication Service never touches the User Service database. Disabling a login account is done in the Authentication Service; deactivating the customer's profile data is done here. Full account closure is therefore a two-step operation owned by two different services.

## 7. API endpoints

Base URL `http://localhost:8002`. All endpoints need a Bearer token. Errors have one shape:
`{ "status_code": 403, "error": "Forbidden", "message": "...", "details": [...] }` (`details` only for 422; submitted values are never echoed).

### Profile and account

| Method | Endpoint | Purpose | Codes |
|---|---|---|---|
| POST | /api/users/profile | Create the profile of the logged-in user | 201, 401, 409, 422 |
| GET | /api/users/profile | Get my profile | 200, 401, 404 |
| PUT | /api/users/profile | Replace the profile (optional fields left out are cleared) | 200, 401, 403, 404, 422 |
| PATCH | /api/users/profile | Change only the fields that are sent | 200, 401, 403, 404, 422 |
| DELETE | /api/users/profile | Delete the profile and its addresses | 200, 401, 404 |
| POST | /api/users/profile/deactivate | Deactivate the profile | 200, 401, 404, 409 |
| POST | /api/users/profile/reactivate | Reactivate the profile | 200, 401, 404, 409 |
| GET | /api/users/account | Account overview: token identity, profile status, address count, default address | 200, 401 |

Create / PUT body:

```json
{ "full_name": "Sai Tej", "phone_number": "9876543210", "date_of_birth": "2000-05-17", "gender": "Male", "profile_image": "https://example.com/sai.png" }
```

Rules: `full_name` 2–100 letters (spaces . ' - allowed); `phone_number` 10–15 digits with optional leading `+` (spaces, dashes and brackets are removed); `date_of_birth` in the past and after 1900; `gender` Male, Female, Other or Prefer not to say; `profile_image` an http(s) URL or a path starting with `/`; unknown fields are rejected. PATCH needs at least one field and cannot set `full_name` or `phone_number` to null.

### Addresses

| Method | Endpoint | Purpose | Codes |
|---|---|---|---|
| POST | /api/users/addresses | Add an address | 201, 401, 403, 404, 422 |
| GET | /api/users/addresses | List my addresses (default first) | 200, 401, 403, 404 |
| GET | /api/users/addresses/{address_id} | Get one address | 200, 401, 403, 404 |
| PUT | /api/users/addresses/{address_id} | Update an address | 200, 401, 403, 404, 422 |
| DELETE | /api/users/addresses/{address_id} | Delete an address | 200, 401, 403, 404 |
| PATCH | /api/users/addresses/{address_id}/default | Make it the default address | 200, 401, 403, 404 |

Body:

```json
{ "address_type": "Home", "full_name": "Sai Tej", "phone_number": "9876543210", "address_line_1": "12-3 Main Road",
  "address_line_2": "Near Park", "city": "Hyderabad", "state": "Telangana", "country": "India", "postal_code": "500001", "is_default": true }
```

Mandatory: address_type (Home/Office/Other), full_name, phone_number, address_line_1, city, state, country, postal_code. `address_line_2` is optional.

Default-address rules:

- A user has at most one default address; marking another address as default clears the previous one in the same transaction (the profile row is locked while this happens).
- The first address of a user is always the default.
- `PUT` with `is_default: false` on the current default does not remove the default; choose another one with `PATCH .../{id}/default`.
- Deleting the default address promotes the newest remaining address, so a user with addresses always has exactly one default.
- Address ids of other users give 403 (`You do not have permission to access this address`); ids that do not exist give 404.

## 8. Alembic migrations

```bat
alembic upgrade head
alembic current
alembic history
alembic downgrade -1
alembic revision --autogenerate -m "describe change"
alembic check
```

Revision `12de4ed45893_create_user_profiles_and_addresses` creates both tables, the unique constraint `uq_user_profiles_auth_user_id`, the foreign key with `ON DELETE CASCADE` and the indexes. The database URL comes from `.env`. With Docker the migration runs automatically before the API starts.

Database errors are handled in one place: a failed commit is rolled back (the session stays usable) and the client receives a generic 500 without internal details; duplicate profiles are caught by the unique constraint even under concurrent requests.

## 9. Docker

Requires Docker Desktop. From the project folder (Command Prompt or PowerShell):

```bat
copy .env.example .env
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f user-service
```

- Two containers: `user-db` (MySQL 8, named volume `user_db_data`, health check) and `user-service`.
- `user-service` starts only when `user-db` is healthy (`depends_on: condition: service_healthy`), connects to MySQL by the Compose service name `user-db`, then runs `alembic upgrade head` and Uvicorn on port 8002.
- Put the **same** `JWT_SECRET_KEY` and `JWT_ALGORITHM` as the Authentication Service in `.env`; Compose refuses to start without a secret.
- `AUTH_SERVICE_URL_IN_DOCKER` (default `http://host.docker.internal:8001`) points to the Authentication Service published on your PC; it is only used when `AUTH_VERIFY_REMOTE=true`.
- Verify the schema: `docker exec -it shopease-user-db mysql -u root -p shopease_user_db -e "SHOW TABLES; DESCRIBE user_profiles; DESCRIBE user_addresses;"`

```bat
docker compose down
docker compose down -v
```
(`-v` also deletes the database volume.)

Swagger UI: http://localhost:8002/docs  Health: http://localhost:8002/health

Run without Docker: start MySQL (`docker compose up -d user-db`), then `python -m venv venv`, `venv\Scripts\activate`, `pip install -r requirements.txt`, `alembic upgrade head`, `uvicorn app.main:app --reload --port 8002`.

## 10. Testing

### Swagger UI and Postman
Start the Authentication Service (port 8001) and this service (port 8002). In Postman import `postman/ShopEase_User_Service.postman_collection.json` and run the collection with the Collection Runner. The Setup folder registers and logs in two users in the Authentication Service and stores their tokens; then 34 requests with test scripts check status codes, messages and the one-default rule. The last requests delete the test profiles so the collection can be run again. To check database records, use `docker exec ... mysql ... -e "SELECT * FROM user_profiles; SELECT id, user_id, address_type, is_default FROM user_addresses;"` after any request (the automated tests do this check after every operation).

### Automated tests
```bat
pip install pytest httpx
pytest -v
```
Tests use in-memory SQLite and tokens signed the way the Authentication Service signs them, so MySQL and the other service are not needed.

### Results

| Check | Result |
|---|---|
| pytest (SQLite) | 125 tests, all passed |
| Postman collection run with Newman against the running User Service **and the real Authentication Service** (SQLite databases) | 38 requests, 68 assertions, 0 failed; run twice (second run exercises the already-registered path) |
| Optional remote verification against the running Authentication Service | active account: 200; account disabled in the Authentication Service: 403; Authentication Service stopped: 503 |
| Alembic on SQLite | upgrade, `alembic check` (no differences), downgrade passed; MySQL SQL generated offline (`upgrade head --sql`) and reviewed |

The 15 mandatory scenarios:

| # | Scenario | Expected | Result |
|---|---|---|---|
| 1 | Profile API without token | 401 | Pass |
| 2 | Profile API with invalid token | 401 | Pass |
| 3 | Create profile with valid token | 201 | Pass |
| 4 | Create duplicate profile | 409 | Pass |
| 5 | Retrieve own profile | 200 | Pass |
| 6 | Update profile (PUT) | 200 | Pass |
| 7 | Partially update profile (PATCH) | 200 | Pass |
| 8 | Add multiple addresses | 201 each | Pass |
| 9 | List all addresses | 200 | Pass |
| 10 | Update an address | 200 | Pass |
| 11 | Set default address | 200 | Pass |
| 12 | Only one default exists (checked in the API and in the database) | exactly 1 | Pass |
| 13 | Delete an address | 200 | Pass |
| 14 | Access another user's profile | not visible / body owner rejected (404 / 422) | Pass |
| 15 | Access another user's address (get, update, delete, set default) | 403 | Pass |

Also tested: expired, tampered, foreign-secret and `alg: none` tokens, invalid tokens with bad claims, phone/name/date/gender/image/address validation, owner fields in request bodies, deactivation and reactivation, profile deletion cascade, account overview, database-error rollback, no credentials stored, no login/registration routes, configuration rules and the Alembic migration.

**Not executed in the environment where this was built:** the Docker image build and the MySQL container (Docker Hub and MySQL were not available). Run `docker compose up -d --build` on your PC to confirm them; the Compose file was parsed and the MySQL migration SQL was reviewed, but neither was run against a live MySQL.

## 11. GitHub

```bat
git init
git add .
git commit -m "Add ShopEase User Service"
git branch -M main
git remote add origin https://github.com/<your-account>/shopease-user-service.git
git push -u origin main
```
`.env` is in `.gitignore` and is not pushed; check with `git status` before committing.
