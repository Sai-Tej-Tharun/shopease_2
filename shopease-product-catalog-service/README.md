# ShopEase Product Catalog Service

Independent microservice for **categories, products and inventory** (stock in / stock out / history / low stock). It has its own MySQL database and does not reuse the earlier in-memory Product Service. Login and registration stay in the Authentication Service: this service only checks the JWT that service issues.

## 1. Tech stack

| Area | Technology |
|---|---|
| API | FastAPI (Swagger UI at `/docs`) |
| Database | MySQL 8 (`shopease_product_catalog_db`) |
| ORM / migrations | SQLAlchemy 2 + Alembic |
| Tokens | JWT (PyJWT, HS256), shared secret with the Authentication Service |
| Validation | Pydantic v2 |
| Containers | Docker + Docker Compose (Python 3.11) |

## 2. Project structure

```
shopease-product-catalog-service/
├── app/
│   ├── main.py                       # app, exception handlers, /health
│   ├── routers/                      # product_routes, category_routes, inventory_routes
│   ├── models/                       # product_model, category_model, inventory_model
│   ├── schemas/                      # product_schema, category_schema, inventory_schema
│   ├── services/                     # product_service, category_service, inventory_service, errors
│   ├── core/config.py, security.py   # settings from environment, JWT + role checks
│   └── database/connection.py        # engine, session, Base
├── alembic/, alembic.ini             # migrations
├── tests/                            # 163 automated tests (pytest)
├── postman/                          # Postman collection (45 requests)
├── scripts/concurrent_stock_test.py  # simultaneous stock updates against a running service
├── Dockerfile, docker-compose.yml, requirements.txt
├── .env.example                      # template; copy to .env
└── .dockerignore, .gitignore
```

## 3. Database

| Table | Columns | Rules |
|---|---|---|
| categories | id, name, description (TEXT), is_active, created_at, updated_at | name UNIQUE |
| products | id, category_id, name, description, sku, price DECIMAL(10,2), discount_price DECIMAL(10,2), stock_quantity, image_url, brand, is_active, created_at, updated_at | sku UNIQUE; FK to categories (RESTRICT); CHECK price > 0, discount_price < price, stock_quantity >= 0 |
| inventory_transactions | id, product_id, transaction_type (IN/OUT), quantity, previous_stock, updated_stock, remarks, created_at, performed_by | FK to products (CASCADE); CHECK quantity > 0, stocks >= 0 |

Money is `Decimal`, never float. The rules exist both in the API validation and as database constraints. `performed_by` is the admin's user id from the token (an addition to the required columns).

## 4. Environment

Nothing secret is hard-coded. The service refuses to start without `JWT_SECRET_KEY` (32+ characters) and the database settings.

```bat
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

| Variable | Purpose |
|---|---|
| MYSQL_ROOT_PASSWORD, MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD | MySQL container and app connection |
| MYSQL_HOST_PORT | port published on your PC (default 3309) |
| MYSQL_HOST, MYSQL_PORT | only when running the API on your PC (`localhost` / `3309`); Compose uses `catalog-db` / `3306` |
| JWT_SECRET_KEY, JWT_ALGORITHM | **must be identical to the Authentication Service's values** |
| ADMIN_ROLE | role claim allowed to manage the catalog (default `admin`) |
| LOW_STOCK_THRESHOLD | default threshold of the low-stock report (default 10) |
| AUTH_VERIFY_REMOTE, AUTH_SERVICE_URL, AUTH_SERVICE_URL_IN_DOCKER | optional extra check against `/api/auth/verify-token` of the Authentication Service (`host.docker.internal:8001` from inside Docker) |

## 5. Authentication and roles

Every endpoint needs `Authorization: Bearer <token>` from the Authentication Service (`POST /api/auth/login`). The token is verified locally with the shared secret: claims `sub`, `role`, `iat`, `exp`.

| Situation | Result |
|---|---|
| No token, invalid, tampered or expired token | 401 |
| Valid token, role is not `admin`, admin API | 403 `Administrator access is required for this operation` |
| Customer reads products / categories | allowed; **active items only** (inactive ones return 404) |

Admin only: create, update, delete, activate/deactivate products and categories, and every inventory endpoint.

New accounts register as `customer`. To make an administrator, promote one account in the Authentication Service database and log in again (the role is inside the token):

```bat
docker exec -it shopease-auth-db mysql -u root -p shopease_auth_db -e "UPDATE users SET role = 'admin' WHERE email = 'admin@shopease.com';"
```

In Swagger UI: log in at http://localhost:8001/docs, copy `access_token`, click **Authorize** here and paste it (without the word Bearer).

## 6. Run with Docker

Start the Authentication Service first (port 8001), then from this folder:

```bat
docker compose up -d --build
docker compose ps
docker compose logs -f catalog-service
```

`catalog-db` has a health check and a named volume (`catalog_db_data`); `catalog-service` waits for it, reaches it as `catalog-db` (not localhost), runs `alembic upgrade head` and then starts Uvicorn. Swagger UI: http://localhost:8003/docs, health: http://localhost:8003/health.

```bat
docker compose down        & rem keep the data
docker compose down -v     & rem also delete the database volume
```

Without Docker: `pip install -r requirements.txt`, start a MySQL 8 (for example `docker compose up -d catalog-db`), then `alembic upgrade head` and `uvicorn app.main:app --reload --port 8003`.

## 7. Alembic

```bat
alembic upgrade head
alembic current
alembic history
alembic downgrade -1
alembic revision --autogenerate -m "describe change"
alembic check
```

Revision `7e04a87abd19_create_catalog_tables` creates the three tables.

## 8. API documentation

Errors always look like `{ "status_code": 409, "error": "Conflict", "message": "..." }`; validation errors (422) add `"details": [{"field": "...", "message": "..."}]` and never echo the submitted value.

### Categories (`/api/categories/`)

| Method | Path | Access | Result |
|---|---|---|---|
| POST | /api/categories/ | admin | 201; 409 duplicate name (case-insensitive) |
| GET | /api/categories/ | any valid token | customers: active only; admin may use `?is_active=` |
| GET | /api/categories/{id} | any valid token | 404 for inactive (customers) or missing |
| PUT | /api/categories/{id} | admin | full update, `is_active` activates/deactivates |
| DELETE | /api/categories/{id} | admin | **409 while the category still contains products** (move or delete them first) |

Deactivating a category hides its products from customers. Products can only be created or moved into an active category.

### Products (`/api/products/`)

| Method | Path | Access | Result |
|---|---|---|---|
| POST | /api/products/ | admin | 201; 409 duplicate SKU; 422 unknown/inactive category, bad price |
| GET | /api/products/ | any valid token | paginated list (see below) |
| GET | /api/products/{id} | any valid token | 404 missing; customers also 404 for inactive |
| PUT | /api/products/{id} | admin | full replacement |
| PATCH | /api/products/{id} | admin | partial update; `{"is_active": false}` deactivates |
| DELETE | /api/products/{id} | admin | permanent, history rows are removed with it (deactivate to keep them) |

Create body:

```json
{ "category_id": 1, "name": "Smart Phone X", "description": "6.5 inch display", "sku": "PH-001",
  "price": "20000.00", "discount_price": "18000.00", "stock_quantity": 50,
  "image_url": "https://example.com/phone.png", "brand": "Acme", "is_active": true }
```

Rules: price > 0 with at most 2 decimals; discount_price lower than price; SKU letters/digits/`.`/`_`/`-`, stored upper-case; unknown fields are rejected. An initial `stock_quantity` is recorded as the first IN transaction ("Initial stock"). **Stock cannot be changed through PUT/PATCH** - use the inventory endpoints. Responses add `category`, `final_price` (discount if present) and `in_stock`.

### Search, filter, pagination, sorting - `GET /api/products/`

| Parameter | Meaning |
|---|---|
| search | text in name or SKU (case-insensitive, `%` and `_` treated literally) |
| category_id | only this category |
| min_price, max_price | selling price range (discount applied); min above max gives 422 |
| in_stock | `true` available, `false` out of stock |
| is_active | admin only: filter by status (customers always get active only) |
| sort_by | `id` (default), `name`, `price`, `stock_quantity`, `created_at` |
| sort_order | `asc` (default) or `desc` |
| page, page_size | default 1 and 10; page_size at most 100; only that page is loaded |

```
GET /api/products/?search=phone&category_id=1&min_price=1000&max_price=30000&in_stock=true&sort_by=price&sort_order=desc&page=1&page_size=10
```

Response: `{ "items": [...], "total": 4, "page": 1, "page_size": 10, "total_pages": 1 }`.

### Inventory (`/api/inventory`, admin only)

| Method | Path | Result |
|---|---|---|
| GET | /api/inventory/{id} | current stock, threshold, `is_low_stock` |
| POST | /api/inventory/{id}/stock-in | 201 with the transaction (`previous_stock`, `updated_stock`) |
| POST | /api/inventory/{id}/stock-out | 201; **409 `Insufficient stock: X available, Y requested`** |
| GET | /api/inventory/{id}/history | newest first; `transaction_type`, `page`, `page_size` |
| GET | /api/inventory/low-stock | products with stock <= `threshold` (default `LOW_STOCK_THRESHOLD`), lowest first; `include_inactive`, paging |

Body of stock-in / stock-out: `{ "quantity": 25, "remarks": "Supplier delivery" }`. `quantity` must be a whole number above 0.

### Status codes

200/201 success; 401 token problem; 403 not an admin; 404 not found; 409 duplicate SKU/category name, category not empty, insufficient stock; 422 validation; 503 Authentication Service unreachable (only with `AUTH_VERIFY_REMOTE=true`); 500 unexpected (generic message).

## 9. Inventory workflow and concurrency

1. Admin creates a product (initial stock is logged as an IN row) or calls stock-in / stock-out.
2. Each stock change is **one database transaction**:
   `UPDATE products SET stock_quantity = stock_quantity - :q WHERE id = :id AND stock_quantity - :q >= 0` -> read the new stock -> insert the history row (`previous_stock`, `updated_stock`) -> commit. Any error rolls back both the stock and the history row.
3. The update is a single atomic statement, so the database row lock serialises simultaneous requests: stock can never go negative and no update is lost. If no row is updated, the service reports 404 (unknown product) or 409 (insufficient stock).
4. The history is a continuous chain: every row's `previous_stock` equals the previous row's `updated_stock`, and the last `updated_stock` equals the product's stock.

### Idempotent stock changes (`reference`)
`stock-in` and `stock-out` accept an optional `reference` (up to 100 characters, unique). Repeating a request with the same reference returns the original transaction with status 200 and does not change the stock again; the same reference with different data returns 409. The Order Service uses it so that retries never reserve or release stock twice. Migration `a1c3d5e7f901_add_inventory_reference` adds the column; run `alembic upgrade head`.

## 10. Testing

### Swagger UI and Postman

Swagger UI: http://localhost:8003/docs. Postman: import `postman/ShopEase_Product_Catalog_Service.postman_collection.json`, set the variable `jwt_secret` to `JWT_SECRET_KEY` from `.env`, and run the collection top to bottom. The collection signs an admin, a customer and an expired token itself, creates its own data (unique per run) and cleans up after itself. Newman: `newman run postman\ShopEase_Product_Catalog_Service.postman_collection.json --env-var "jwt_secret=<secret>"`.

### Automated tests

```bat
pip install pytest httpx pyyaml
pytest -q
```

The tests use SQLite in memory, so they need neither MySQL nor Docker.

### Simultaneous stock updates (against the running service)

```bat
python scripts\concurrent_stock_test.py
```

Creates a product with 20 units, sends 30 stock-out requests of 1 unit at the same moment, then a mixed run of 20 stock-in and 20 stock-out requests, and checks the result through the API.

### Test results

| # | Mandatory scenario | Expected | Result |
|---|---|---|---|
| 1 | Create product (valid) | 201 | Pass |
| 2 | Duplicate SKU | 409 | Pass |
| 3 | Invalid category | 422 | Pass |
| 4 | Negative price | 422 | Pass |
| 5 | Retrieve active products (customer) | 200, active only | Pass |
| 6 | Invalid product id | 404 | Pass |
| 7 | Update and partial update | 200 | Pass |
| 8 | Delete / deactivate | 200, hidden from customers | Pass |
| 9 | Duplicate category | 409 | Pass |
| 10 | Search and filters | 200 | Pass |
| 11 | Pagination and sorting | 200 | Pass |
| 12 | Stock in | 201 | Pass |
| 13 | Stock out | 201 | Pass |
| 14 | Stock below zero | 409, stock unchanged | Pass |
| 15 | Transaction history | 200, continuous chain | Pass |
| 16 | Low-stock list | 200 | Pass |
| 17 | Customer on admin APIs | 403 | Pass |
| 18 | No token | 401 | Pass |
| 19 | Invalid token | 401 | Pass |
| 20 | Simultaneous stock updates | 20 succeed, 10 refused, stock 0, consistent history | Pass |
| 21 | DB records vs HTTP responses | match | Pass |

Results: **163 automated tests passed**; the Postman collection ran with Newman against the live service (**45 requests, 77 assertions, 0 failed**); the live concurrency script passed **8 of 8** checks (30 simultaneous stock-outs on 20 units: 20 succeeded, 10 refused with 409, final stock 0, history chain unbroken; 40 mixed requests left stock unchanged).

**Not executed where this project was built:** the Docker image build and the MySQL container (so the real-MySQL concurrency run is also untested). All tests, Newman and the concurrency run used the same code and Alembic migration on SQLite; the MySQL migration SQL was generated offline and checked, and the Compose file was parsed. Run `docker compose up -d --build` on your machine to confirm them.

## 11. Push to GitHub

```bat
git init
git add .
git commit -m "Add Product Catalog microservice"
git branch -M main
git remote add origin https://github.com/<your-username>/shopease-product-catalog-service.git
git push -u origin main
```

`.env` is in `.gitignore` and `.dockerignore`: commit `.env.example` only.
