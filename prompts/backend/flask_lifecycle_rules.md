---
version: 1.0.0
last_modified: 2026-11-22
author: Backend Engineering Team
---
Flask Application Lifecycle Rules:
When developing Flask applications:
- Never use `@app.before_first_request` (removed in Flask 2.3+).
- Never execute database initialization or access Flask `g` at module import time.
- Generated Flask application modules must be safely importable by pytest.
- Database initialization must occur inside an active Flask application context.
- Tests must explicitly initialize their own test database.
- For standalone execution, initialize the database inside the main entry point with an application context, not during import.
- Keep request-scoped database connections and teardown behavior correct.
