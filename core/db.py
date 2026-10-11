"""
SQLite database — stdlib sqlite3 only, zero third-party DB driver.
All blocking calls are wrapped in asyncio.to_thread so FastAPI stays async.
"""

import sqlite3
import os
import asyncio
from contextlib import contextmanager
from typing import Generator

DB_PATH = os.getenv("DB_PATH", "calcvoyager.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    UNIQUE NOT NULL,
    email       TEXT    UNIQUE,
    hashed_pw   TEXT    NOT NULL,
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS sections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    section_id   TEXT    NOT NULL,
    completed    INTEGER NOT NULL DEFAULT 1,
    completed_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(user_id, section_id)
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bm_id     TEXT    NOT NULL,
    title     TEXT    NOT NULL,
    path      TEXT    NOT NULL,
    added_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(user_id, bm_id)
);

CREATE TABLE IF NOT EXISTS quiz_scores (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    quiz_id   TEXT    NOT NULL,
    score     INTEGER NOT NULL,
    total     INTEGER NOT NULL,
    taken_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(user_id, quiz_id)
);

CREATE TABLE IF NOT EXISTS solver_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expression TEXT,
    result     TEXT,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    quiz_id      TEXT    NOT NULL,
    score        INTEGER NOT NULL,
    total        INTEGER NOT NULL,
    passed       INTEGER NOT NULL DEFAULT 0,
    attempted_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS certificates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_id       TEXT    UNIQUE NOT NULL,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id     TEXT    NOT NULL,
    course_title  TEXT    NOT NULL,
    full_name     TEXT    NOT NULL,
    score         INTEGER,
    total         INTEGER,
    issued_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(user_id, course_id)
);

CREATE TABLE IF NOT EXISTS learning_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(user_id, event_id)
);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id              INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    leaderboard_opt_in   INTEGER NOT NULL DEFAULT 0
);
"""


# ── Synchronous helpers (run inside to_thread) ────────────────────────────────


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db_sync():
    conn = _connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _fetchone(sql: str, params: tuple = ()):
    conn = _connect()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _fetchall(sql: str, params: tuple = ()):
    conn = _connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _execute(sql: str, params: tuple = ()):
    """Execute a write statement, return lastrowid."""
    conn = _connect()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _scalar(sql: str, params: tuple = ()):
    """Return the first column of the first row."""
    conn = _connect()
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ── Async wrappers (called from FastAPI route handlers) ───────────────────────


async def init_db():
    await asyncio.to_thread(_init_db_sync)


async def fetchone(sql: str, params: tuple = ()):
    return await asyncio.to_thread(_fetchone, sql, params)


async def fetchall(sql: str, params: tuple = ()):
    return await asyncio.to_thread(_fetchall, sql, params)


async def execute(sql: str, params: tuple = ()):
    return await asyncio.to_thread(_execute, sql, params)


async def scalar(sql: str, params: tuple = ()):
    return await asyncio.to_thread(_scalar, sql, params)
