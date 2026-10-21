"""MySQL access. Every query uses parameters (%s), never string building, to prevent SQL injection."""
import mysql.connector
from mysql.connector import pooling

from . import config

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="salesmesh", pool_size=5, pool_reset_session=True,
            host=config.DB_HOST, port=config.DB_PORT, database=config.DB_NAME,
            user=config.DB_USER, password=config.DB_PASSWORD, autocommit=False)
    return _pool


def connect():
    return get_pool().get_connection()


def query(sql, params=(), conn=None):
    """Run a SELECT and return a list of dicts."""
    own = conn is None
    conn = conn or connect()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        if own:
            conn.close()


def query_one(sql, params=(), conn=None):
    rows = query(sql, params, conn)
    return rows[0] if rows else None


def execute(sql, params=(), conn=None):
    """Run one INSERT/UPDATE in its own transaction and return the new row id."""
    own = conn is None
    conn = conn or connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        new_id = cur.lastrowid
        cur.close()
        if own:
            conn.commit()
        return new_id
    except Exception:
        if own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()
