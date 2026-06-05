"""
db.py
=====
Accesso a SQL Server. Tre cose soltanto:
1. creare un 'engine' verso un database della stessa istanza (master o CDG_QV);
2. eseguire un file .sql che puo' contenere piu' batch separati da "GO";
3. eseguire una stored procedure con i parametri @anno/@mese.

Tutto passa dalla stessa istanza configurata in settings.yaml: cambiamo solo
il nome del database a cui ci colleghiamo.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path


def _engine(config: dict, database: str):
    """Crea un engine SQLAlchemy verso il database indicato, sulla stessa istanza."""
    from sqlalchemy import create_engine

    ist = config["istanza"]
    parti = [
        f"DRIVER={{{ist['driver']}}}",
        f"SERVER={ist['server']}",
        f"DATABASE={database}",
        f"Connection Timeout={ist.get('timeout', 30)}",
    ]
    if ist.get("trusted_connection"):
        parti.append("Trusted_Connection=yes")        # autenticazione Windows
    else:
        parti.append(f"UID={ist['user']}")
        parti.append(f"PWD={ist['password']}")

    odbc = ";".join(parti)
    url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
    # autocommit: i DDL (CREATE DATABASE/SCHEMA) vogliono essere fuori da transazione.
    return create_engine(url, isolation_level="AUTOCOMMIT")


def engine_master(config: dict):
    """Engine verso 'master' (serve solo per creare il database CDG_QV)."""
    return _engine(config, "master")


def engine_dwh(config: dict):
    """Engine verso il datawarehouse CDG_QV."""
    return _engine(config, config["database"]["dwh"])


def esegui_script(engine, percorso_sql: Path):
    """
    Esegue un file .sql che puo' contenere piu' batch separati dalla parola GO.
    SQL Server usa GO come separatore di batch: il driver pero' non lo capisce,
    quindi qui spezziamo noi il file sui "GO" e mandiamo un batch alla volta.
    """
    from sqlalchemy import text

    testo = Path(percorso_sql).read_text(encoding="utf-8")
    # Spezza sui "GO" che stanno da soli su una riga (case-insensitive).
    batch = re.split(r"(?im)^\s*GO\s*$", testo)

    with engine.begin() as conn:
        for b in batch:
            if b.strip():                 # salta i pezzi vuoti
                conn.execute(text(b))


def esegui_proc(engine, nome_proc: str, anno: int, mese: int):
    """Esegue una stored procedure passando @anno e @mese."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(f"EXEC {nome_proc} @anno = :anno, @mese = :mese"),
            {"anno": anno, "mese": mese},
        )


def conta(engine, query: str) -> int:
    """Esegue una query che restituisce un singolo numero (es. COUNT(*))."""
    from sqlalchemy import text

    with engine.begin() as conn:
        return int(conn.execute(text(query)).scalar() or 0)


def valori(engine, query: str) -> list:
    """Esegue una query e restituisce la lista dei valori della prima colonna."""
    from sqlalchemy import text

    with engine.begin() as conn:
        return [r[0] for r in conn.execute(text(query)).fetchall()]
