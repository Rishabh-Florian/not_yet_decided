"""SQLite migrations for the GraphStore.

Migrations are idempotent and run automatically on `GraphStore` init.
Each migration is a module exposing a `migrate(conn: sqlite3.Connection) -> None`
function that crashes hard on any unexpected state.
"""
