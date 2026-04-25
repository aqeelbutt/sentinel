"""Per-table repositories. Each module owns one table's reads and writes.

Repos are stateless functions taking a Path to the DB; callers don't pass
connections. This is fine for SQLite-WAL — connection setup is microseconds.
"""
