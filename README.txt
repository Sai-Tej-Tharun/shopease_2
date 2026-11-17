ShopEase - Authentication, User, Product Catalog and Order Services

shopease-auth-service/             Authentication Service   (port 8001, MySQL host port 3307)
shopease-user-service/             User Service             (port 8002, MySQL host port 3308)
shopease-product-catalog-service/  Product Catalog Service  (port 8003, MySQL host port 3309)
shopease-order-service/            Order Management Service (port 8004, MySQL host port 3310)
Documentation/                     Implementation reports (DOCX + PDF) and Swagger UI screenshots

All services are independent projects with their own database and docker-compose.yml.
They must use the SAME JWT_SECRET_KEY and JWT_ALGORITHM in their .env files.
The Product Catalog Service and the Order Service need the role claim "admin" for their admin APIs: promote one
account in the Authentication Service database (see section 5 of the Catalog README.md) and log in again.
Order Service start order: Authentication, User, Product Catalog, then Order.
The Product Catalog Service in this package has a new optional "reference" field on stock-in/stock-out (idempotent
stock changes, used by the Order Service). Run "alembic upgrade head" in it (migration a1c3d5e7f901).
See the README.md inside each project for setup, Docker commands, API details and test results.
Docker image builds and MySQL containers were not run where these projects were built; run
"docker compose up -d --build" in each project folder to confirm them.
