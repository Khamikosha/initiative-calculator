#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Снимок базы расчётов. Запуск: make backup [KEEP=20]

Почему не `cp`. База работает в режиме WAL: часть недавних транзакций лежит не в самом
файле, а в `-wal` рядом. Копирование файла даёт снимок без них — то есть тихо теряет
последние расчёты, ровно те, ради которых бэкап и делается. `Connection.backup()`
проходит через движок SQLite и снимает консистентное состояние даже под нагрузкой.

Расчёт объявлен неизменяемым (BACKLOG 1.3), но неизменяемый и потерянный — одно и то же.
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".data" / "initiatives.db"
OUT = ROOT / ".data" / "backups"
KEEP = int(sys.argv[1]) if len(sys.argv) > 1 else 20


def main():
    if not SRC.exists():
        print(f"Базы нет ({SRC}) — сохранять нечего.")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    # Два снимка в одну секунду не должны затирать друг друга: бэкап, молча уничтожающий
    # предыдущий бэкап, хуже отсутствия бэкапа — на него рассчитывают.
    stamp = f"{datetime.now():%Y-%m-%d-%H%M%S}"
    dst = OUT / f"{stamp}.db"
    n = 1
    while dst.exists():
        dst = OUT / f"{stamp}-{n}.db"
        n += 1

    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    # Считаем ДО снимка и по источнику: любой SELECT к цели держит читающую транзакцию,
    # а при открытой транзакции переключить журнал нельзя — снимок остался бы с хвостом -shm.
    n_init = src.execute("SELECT COUNT(*) FROM initiative").fetchone()[0]
    n_calc = src.execute("SELECT COUNT(*) FROM calculation").fetchone()[0]

    tgt = sqlite3.connect(dst)
    with tgt:
        src.backup(tgt)
    # Снимок наследует WAL у источника и становится тремя файлами. Бэкап, который можно
    # унести не целиком, — это не бэкап: переводим в обычный журнал, остаётся один файл.
    # fetchone() обязателен: PRAGMA journal_mode возвращает строку, и без чтения результата
    # переключение не применяется.
    tgt.execute("PRAGMA journal_mode=DELETE").fetchone()
    tgt.close(); src.close()
    # После переключения журнала `-wal` исчезает сам, а `-shm` SQLite иногда оставляет.
    # Снимок к этому моменту уже самодостаточен, так что это осиротевший файл: убираем,
    # чтобы каталог снимков не выглядел набором обломков.
    for tail in ("-wal", "-shm"):
        stray = dst.with_name(dst.name + tail)
        if stray.exists():
            stray.unlink()

    print(f"Снимок: {dst.relative_to(ROOT)}  "
          f"({dst.stat().st_size // 1024} КБ · инициатив {n_init} · расчётов {n_calc})")

    old = sorted(OUT.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP:]
    for p in old:
        p.unlink()
        for tail in ("-wal", "-shm"):          # хвосты от прежних снимков не оставляем
            sib = p.with_name(p.name + tail)
            if sib.exists():
                sib.unlink()
    if old:
        print(f"Удалено старых снимков: {len(old)} (храним последние {KEEP})")
    print(f"Всего снимков: {len(list(OUT.glob('*.db')))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
