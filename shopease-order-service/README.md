# ShopEase Order Management Service

Creates and manages customer orders. It does not register or log in users: the JWT issued by the Authentication Service is verified here, the customer and delivery address come from the User Management Service, and prices and stock come from the Product Catalog Service. The service has its own database, `shopease_order_db`.

## 1. Tech stack
Python 3.11, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, PyJWT, httpx, MySQL 8 (SQLite is used only by the automated tests), Docker and Docker Compose.

## 2. Project structure
```
shopease-order-service/
  app/
    main.py
    routers/    order_routes.py, order_item_routes.py
    models/     order_model.py, order_item_model.py, stock_compensation_model.py
    schemas/    order_schema.py, order_item_schema.py
    services/   order_service.py, product_client.py, user_client.py, errors.py
    core/       config.py, security.py
    database/   connection.py
  alembic/  alembic.ini  tests/  postman/
  Dockerfile  docker-compose.yml  requirements.txt  .env  .env.example  .dockerignore
```

## 3. Database schema (`shopease_order_db`)
**orders**: id, order_number (unique), user_id (taken from the token, no foreign key to another service), shipping_address (JSON snapshot), subtotal, discount_amount, shipping_charge, tax_amount, total_amount, order_status, payment_status, notes, created_at, updated_at.
**order_items** (many per order, `order_id` foreign key, cascade delete): id, order_id, product_id, product_name, sku, unit_price, quantity, discount_amount, total_price, created_at, and `stock_status` (RESERVED or RELEASED, used so stock is restored only once).
**stock_compensations**: stock releases that could not be completed and still have to be retried (see section 10).

Money columns are `DECIMAL(12,2)` and `Decimal` in Python. Product name, SKU, unit price and the delivery address are copied (snapshot) into the order, so later catalog or profile changes never change an existing order. No user or product tables are copied. Database CHECK constraints enforce the status values, non-negative amounts and `total = subtotal - discount + tax + shipping`.

## 4. Authentication integration
The service only verifies the Bearer token (same `JWT_SECRET_KEY` and `JWT_ALGORITHM` as the Authentication Service; optionally also against it with `AUTH_VERIFY_REMOTE=true`).
- No token or an invalid/expired token: **401**.
- Valid token but not allowed (another customer's order, customer changing a status): **403**.
- The order owner is always the token's user. A body containing `user_id`, prices or totals is rejected with 422.
- Customers see only their own orders; administrators (role from `ADMIN_ROLE`) see all.

## 5. User Management Service integration
`GET /api/users/profile` and `GET /api/users/addresses/{id}` are called with the customer's own token. The order is refused if the profile does not exist or is deactivated (403/422) or if the address is not one of the customer's saved addresses (422; the User Service answers 403/404 for someone else's address). The address is stored as a snapshot. If the service is down or times out: **503** "The User Management Service is unavailable. Please try again later." Nothing is created.

## 6. Product Catalog Service integration
For every item the service reads the product (`GET /api/products/{id}`: exists, `is_active`, list price, discount price, SKU, name) and then reserves stock (`POST /api/inventory/{id}/stock-out`). All amounts are calculated in the Order Service from the catalog values, never from the client. Unknown product: 422, inactive: 422, not enough stock: 409, Catalog down/timeout: **503**. Calls have timeouts and a short retry; retries are safe because every stock call carries a unique `reference` (section 10).
Stock calls are made with a short-lived service token (role admin, subject `SERVICE_ACCOUNT_ID`) signed with the shared secret, because only administrators may change stock in the Catalog Service.

## 7. API documentation
Swagger UI: http://localhost:8004/docs, health: http://localhost:8004/health. All endpoints need `Authorization: Bearer <token>`.

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | /api/orders/ | customer, admin | Place an order. Body `{"items":[{"product_id":1,"quantity":2}],"shipping_address_id":1,"notes":"..."}`. Returns 201, status PENDING / payment PENDING, unique order number `ORD-YYYYMMDD-XXXXXXXX` |
| GET | /api/orders/ | customer (own), admin (all) | List with `page`, `page_size`, `status`, `date_from`, `date_to`, admin: `user_id` |
| GET | /api/orders/{id} | owner, admin | Order with items |
| GET | /api/orders/number/{order_number} | owner, admin | Order by number |
| GET | /api/orders/{id}/items | owner, admin | Items with price snapshots |
| GET | /api/orders/{id}/items/{item_id} | owner, admin | One item |
| PATCH | /api/orders/{id}/status | admin | Change status, body `{"status":"CONFIRMED"}` |
| POST | /api/orders/{id}/cancel | owner, admin | Cancel and restore stock |
| GET | /api/orders/compensations | admin | Stock releases still pending |
| POST | /api/orders/compensations/retry | admin | Retry pending releases |

Errors use `{"status_code":..,"error":"..","message":".."}`: 401, 403, 404, 409 (state or stock conflict), 422 (validation), 502/503 (other service).

## 8. Status workflow
| From | Allowed next |
|---|---|
| PENDING | CONFIRMED, CANCELLED |
| CONFIRMED | PROCESSING, CANCELLED |
| PROCESSING | SHIPPED, CANCELLED |
| SHIPPED | DELIVERED |
| DELIVERED, CANCELLED | none (final) |

Only administrators use `PATCH /status`; any other move gives 409. Setting CANCELLED through PATCH runs the same cancel workflow (stock is restored). A customer cancels only their own order while it is PENDING, CONFIRMED or PROCESSING; SHIPPED and DELIVERED give 409. Cancelling an already cancelled order returns 200 and restores nothing (`stock_restored_now: 0`).

## 9. Calculation rules
All amounts are `Decimal`, rounded half-up to 0.01.
- unit price = catalog list price; item discount = (price - discount price) x quantity; item total = price x quantity - item discount
- subtotal = sum of price x quantity; discount = sum of item discounts
- tax = `TAX_RATE` x (subtotal - discount), default 0.18
- shipping = `SHIPPING_FLAT_RATE` (50.00), or 0 when (subtotal - discount) >= `FREE_SHIPPING_THRESHOLD` (1000.00)
- **total = subtotal - discount + tax + shipping**
Limits: `MAX_ITEMS_PER_ORDER` 20, `MAX_QUANTITY_PER_ITEM` 100, no duplicate products in one order. Example (order 1 in the tests): 2 x 20000 (discount price 18000) + 1 x 299 (199): subtotal 40299.00, discount 4100.00, tax 6515.82, shipping 0.00, total 42714.82.

## 10. Inventory reservation and compensation
The order and catalog databases are separate, so there is no shared transaction. The service uses a saga with idempotency keys.
1. Validate the customer, address and every product; calculate the amounts.
2. For each item reserve stock with `reference = {order_number}:reserve:{product_id}`. The Catalog Service stores the reference with a unique constraint: repeating the same reference returns the original result (200) and never takes stock twice; the same reference with different data gives 409.
3. Save the order and its items in one database transaction.
4. If anything fails after some items were reserved (a later item has too little stock, the Catalog goes down, the order cannot be saved), the already reserved items are released with `reference = {order_number}:release:{product_id}`. If the outcome of a reservation is unknown (timeout), the reserve is repeated (safe, idempotent) and then released.
5. A release that cannot be completed is stored in `stock_compensations` and retried by `POST /api/orders/compensations/retry` (admin), so stock is not lost.
6. Cancellation releases each item once: `stock_status` changes RESERVED to RELEASED and the release reference is unique, so repeated or simultaneous cancels cannot restore stock twice.

Limitation: if the service process is killed exactly between reserving stock and saving the order, stock can stay reserved without an order; this is outside the handled failures and would need a periodic reconciliation job.

## 11. Environment (`.env`)
Copy `.env.example` to `.env` and replace every `change-me`.
`JWT_SECRET_KEY` must equal the Authentication Service value (at least 32 characters). `AUTH_SERVICE_URL`, `USER_SERVICE_URL` (8002), `PRODUCT_SERVICE_URL` (8003) are used when running on the host; `USER_SERVICE_URL_IN_DOCKER` and `PRODUCT_SERVICE_URL_IN_DOCKER` (default `http://host.docker.internal:800x`) are used inside the container. Also: `MYSQL_*`, `*_TIMEOUT`, `SERVICE_RETRY_ATTEMPTS`, `SERVICE_ACCOUNT_ID`, `TAX_RATE`, `SHIPPING_FLAT_RATE`, `FREE_SHIPPING_THRESHOLD`, `MAX_ITEMS_PER_ORDER`, `MAX_QUANTITY_PER_ITEM`. `.env` is never committed.

## 12. Alembic (Windows)
```bat
alembic upgrade head
alembic current
alembic history
alembic downgrade -1
alembic revision --autogenerate -m "describe change"
alembic check
```
Revision `1e7051830931` creates `orders`, `order_items` and `stock_compensations`.

## 13. Run with Docker (Windows)
Start the Authentication (8001), User (8002) and Product Catalog (8003) services first. The Catalog Service of this package includes the `reference` idempotency column and its migration; run `alembic upgrade head` there.
```bat
docker compose up -d --build
docker compose ps
docker compose logs -f order-service
docker compose down
docker compose down -v
```
`order-db` has a health check and the named volume `order_db_data`; `order-service` waits for it, uses the host name `order-db` (not localhost), runs `alembic upgrade head` and starts Uvicorn on port 8004. MySQL is published on host port 3310. Without Docker: `pip install -r requirements.txt`, then `alembic upgrade head` and `uvicorn app.main:app --reload --port 8004`.

## 14. Testing
Swagger UI: authorize with a token from `POST /api/auth/login`. Postman: import `postman/ShopEase_Order_Service.postman_collection.json`, set the variable `jwt_secret` to the shared secret (the collection creates its own test users), run the folders in order; the "Catalog unavailable" folder needs the Catalog Service stopped and "Recovery" needs it started again.
```bat
pip install -r requirements.txt
pytest -q
```

### Test results
Run against the real Authentication, User Management, Product Catalog and Order services (SQLite databases, started on ports 8001-8004):
- `pytest`: 146 passed (Order Service); Catalog Service: 167 passed.
- Postman/Newman, 51 requests, 88 assertions, all passed, including the Catalog-down and recovery folders.
- Swagger UI: 26 scenarios, all with the expected status (see the report).

| # | Scenario | Result |
|---|---|---|
| 1 | Valid order | 201, PENDING/PENDING, totals correct |
| 2 | Invalid product id | 422 |
| 3 | Inactive product | 422 |
| 4 | Insufficient stock | 409, nothing reserved |
| 5 | Invalid address (another user's) | 422 |
| 6, 7 | No token, invalid token | 401 |
| 8 | List own orders | 200 |
| 9 | Another user's order | 403 |
| 10 | Get by order number | 200 |
| 11 | Pagination and filters (status, dates) | 200 |
| 12, 13 | Admin status update, customer attempt | 200, 403 |
| 14 | Invalid transition | 409 |
| 15, 16 | Cancel eligible order, stock restored | 200, stock +1 |
| 17 | Cancel shipped/delivered | 409 |
| 18 | Repeat cancel | 200, no second restore |
| 19, 20 | Catalog down, consistency afterwards | 503, no order, stock unchanged, works after restart |

Not verified in the build environment: `docker compose build/up` and the MySQL database itself (no Docker or MySQL was available); the tests use SQLite, where row locks are not enforced. Run the Docker commands above on your machine.

## 15. Push to GitHub (Windows)
```bat
git init
git add .
git commit -m "Add Order Management microservice"
git branch -M main
git remote add origin https://github.com/<your-username>/shopease-order-service.git
git push -u origin main
```
Commit `.env.example` only.
