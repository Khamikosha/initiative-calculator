#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Хранилище: инициативы, отчёты панели, расчёты.

SQLite — сознательный выбор для первой версии: сто пользователей внутреннего
планировщика это нагрузка, которую он держит без разговоров, а ноль внешних сервисов
означает, что приложение запускается одной командой. Схема написана обычным SQL без ORM,
поэтому переезд на Postgres (BACKLOG 1.4) — это замена соединения, а не переписывание.

Главное правило хранилища: расчёт неизменяем. Он хранит СНИМОК конфига и версию движка,
которым посчитан. Изменение дефолтов или правка инициативы не должны задним числом менять
цифру, которую человек уже показал комитету, — иначе воспроизвести решение невозможно.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / ".data" / "initiatives.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS initiative (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    author        TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL DEFAULT '',
    type          TEXT NOT NULL DEFAULT 'frk4',
    product       TEXT NOT NULL DEFAULT 'card',
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft',
    config_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS panel_report (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    initiative_id INTEGER NOT NULL REFERENCES initiative(id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL,
    segments      TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT ''
);

-- Расчёт неизменяем: правка инициативы создаёт НОВЫЙ расчёт, а не переписывает старый.
CREATE TABLE IF NOT EXISTS calculation (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    initiative_id  INTEGER NOT NULL REFERENCES initiative(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL,
    author         TEXT NOT NULL DEFAULT '',
    engine_version TEXT NOT NULL,
    config_snapshot TEXT NOT NULL,
    result_json    TEXT NOT NULL,
    npv            INTEGER,
    incremental    INTEGER,
    verdict        TEXT NOT NULL DEFAULT '',
    downgraded     INTEGER NOT NULL DEFAULT 0,
    confidence     REAL
);

CREATE INDEX IF NOT EXISTS idx_calc_initiative ON calculation(initiative_id);
CREATE INDEX IF NOT EXISTS idx_panel_initiative ON panel_report(initiative_id);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")       # параллельные чтения при записи
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


# ------------------------------------------------------------------ инициативы
def create_initiative(con, author, name, type_, product, description, config):
    ts = now()
    cur = con.execute(
        "INSERT INTO initiative (created_at, updated_at, author, name, type, product,"
        " description, status, config_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (ts, ts, author, name, type_, product, description, "draft",
         json.dumps(config, ensure_ascii=False)))
    con.commit()
    return cur.lastrowid


def get_initiative(con, iid):
    row = con.execute("SELECT * FROM initiative WHERE id=?", (iid,)).fetchone()
    return dict(row) if row else None


def list_initiatives(con):
    """Список со сводкой по последнему расчёту — без него таблица бесполезна."""
    rows = con.execute("""
        SELECT i.*, c.npv, c.verdict, c.confidence, c.downgraded,
               c.created_at AS calc_at, c.id AS calc_id,
               (SELECT COUNT(*) FROM calculation WHERE initiative_id = i.id) AS calc_count
        FROM initiative i
        LEFT JOIN calculation c ON c.id = (
            SELECT id FROM calculation WHERE initiative_id = i.id
            ORDER BY created_at DESC, id DESC LIMIT 1)
        ORDER BY i.updated_at DESC, i.id DESC""").fetchall()
    return [dict(r) for r in rows]


def update_initiative(con, iid, **fields):
    if not fields:
        return
    if "config" in fields:
        fields["config_json"] = json.dumps(fields.pop("config"), ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE initiative SET {cols} WHERE id=?", (*fields.values(), iid))
    con.commit()


def delete_initiative(con, iid):
    con.execute("DELETE FROM initiative WHERE id=?", (iid,))
    con.commit()


# ------------------------------------------------------------------ панель
def save_panel(con, iid, segments, body):
    con.execute("INSERT INTO panel_report (initiative_id, created_at, segments, body)"
                " VALUES (?,?,?,?)", (iid, now(), segments, body))
    con.commit()


def latest_panel(con, iid):
    row = con.execute("SELECT * FROM panel_report WHERE initiative_id=?"
                      " ORDER BY created_at DESC, id DESC LIMIT 1", (iid,)).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------ расчёты
def save_calculation(con, iid, author, engine_version, config, result):
    v = result.get("verdict", {})
    conf = result.get("confidence") or {}
    cur = con.execute(
        "INSERT INTO calculation (initiative_id, created_at, author, engine_version,"
        " config_snapshot, result_json, npv, incremental, verdict, downgraded, confidence)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (iid, now(), author, engine_version,
         json.dumps(config, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
         result["pl"]["npv"], result["funnel"]["incremental_clients"],
         v.get("recommendation", ""), int(bool(v.get("downgraded_by_confidence"))),
         conf.get("index")))
    con.commit()
    return cur.lastrowid


def get_calculation(con, cid):
    row = con.execute("SELECT * FROM calculation WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


def list_calculations(con, iid=None):
    if iid is None:
        rows = con.execute(
            "SELECT c.*, i.name AS initiative_name, i.type AS initiative_type,"
            " i.product AS initiative_product FROM calculation c"
            " JOIN initiative i ON i.id = c.initiative_id"
            " ORDER BY c.created_at DESC, c.id DESC").fetchall()
    else:
        rows = con.execute("SELECT * FROM calculation WHERE initiative_id=?"
                           " ORDER BY created_at DESC, id DESC", (iid,)).fetchall()
    return [dict(r) for r in rows]
