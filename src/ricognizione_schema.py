"""
ricognizione_schema.py
======================
Script di SOLA LETTURA per confermare i nomi reali delle tabelle/colonne
sorgente di Mago (database KODICEBAGNO_4) prima di sostituire i segnaposto
"-- ADATTA" in sql/extract/10_usp_load_src.sql.

Cosa fa (nessun INSERT/UPDATE/DDL, solo SELECT su INFORMATION_SCHEMA e TOP 5):
  1. elenca le tabelle il cui nome contiene 'Sale' o 'Doc' (candidate per
     testata + dettaglio righe di vendita), con colonne e tipi;
  2. stampa la struttura completa di MA_ItemsWAP (colonne e tipi);
  3. per ogni tabella candidata, mostra le prime 5 righe (TOP 5).

Riusa la logica di connessione di src/db.py puntando al database Mago
(config["database"]["mago"]) con le credenziali di config/settings.yaml + .env.

Lancio:  python src/ricognizione_schema.py
Se la connessione non e' disponibile lo script lo dice ed esce: in quel caso
si usa sql/ricognizione/ricognizione_mago.sql, da eseguire a mano in SSMS.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permette il lancio sia con "python src/ricognizione_schema.py" sia con
# "python -m src.ricognizione_schema": assicura che la radice del progetto
# (che contiene il package 'src') sia importabile.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config_loader import carica_config
from src import db


def _engine_mago(config: dict):
    """Engine verso il database Mago (sola lettura), sulla stessa istanza."""
    return db._engine(config, config["database"]["mago"])


def _righe(conn, sql: str, params: dict | None = None):
    from sqlalchemy import text

    res = conn.execute(text(sql), params or {})
    cols = list(res.keys())
    return cols, res.fetchall()


def _stampa_tabella(cols, righe, max_celle: int = 60):
    """Stampa semplice a colonne, troncando i valori lunghi."""
    if not righe:
        print("    (nessuna riga)")
        return
    larghezze = []
    for i, c in enumerate(cols):
        w = len(str(c))
        for r in righe:
            w = max(w, min(len(str(r[i])), max_celle))
        larghezze.append(w)
    print("    " + " | ".join(str(c).ljust(larghezze[i]) for i, c in enumerate(cols)))
    print("    " + "-+-".join("-" * larghezze[i] for i in range(len(cols))))
    for r in righe:
        celle = []
        for i in range(len(cols)):
            v = "" if r[i] is None else str(r[i])
            if len(v) > max_celle:
                v = v[: max_celle - 1] + "…"
            celle.append(v.ljust(larghezze[i]))
        print("    " + " | ".join(celle))


def main() -> int:
    config = carica_config()
    db_mago = config["database"]["mago"]
    print(f"=== Ricognizione schema Mago ({db_mago}) — SOLA LETTURA ===\n")

    try:
        from sqlalchemy.exc import SQLAlchemyError

        engine = _engine_mago(config)
        with engine.begin() as conn:
            # --- 1) Tabelle candidate (nome contiene 'Sale' o 'Doc') -----------
            print("[1] Tabelle con nome che contiene 'Sale' o 'Doc'\n")
            cols, tabelle = _righe(
                conn,
                """
                SELECT TABLE_SCHEMA, TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_TYPE = 'BASE TABLE'
                  AND (TABLE_NAME LIKE '%Sale%' OR TABLE_NAME LIKE '%Doc%')
                ORDER BY TABLE_NAME
                """,
            )
            _stampa_tabella(cols, tabelle)
            print()

            candidate = [(r[0], r[1]) for r in tabelle]

            # --- Colonne e tipi di ogni tabella candidata ----------------------
            for schema, nome in candidate:
                print(f"    --- Colonne di {schema}.{nome} ---")
                cols, righe = _righe(
                    conn,
                    """
                    SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                           NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = :s AND TABLE_NAME = :t
                    ORDER BY ORDINAL_POSITION
                    """,
                    {"s": schema, "t": nome},
                )
                _stampa_tabella(cols, righe)
                print()

            # --- 2) Struttura completa di MA_ItemsWAP --------------------------
            print("[2] Struttura di MA_ItemsWAP\n")
            cols, righe = _righe(
                conn,
                """
                SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                       NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'MA_ItemsWAP'
                ORDER BY ORDINAL_POSITION
                """,
            )
            if righe:
                _stampa_tabella(cols, righe)
            else:
                print("    (tabella MA_ItemsWAP non trovata: verifica il nome)")
            print()

            # --- 3) TOP 5 righe per ogni tabella candidata + MA_ItemsWAP -------
            print("[3] Prime 5 righe (TOP 5) di ogni tabella candidata\n")
            anteprima = candidate[:]
            if not any(n == "MA_ItemsWAP" for _, n in anteprima):
                anteprima.append(("dbo", "MA_ItemsWAP"))
            for schema, nome in anteprima:
                print(f"    --- TOP 5 di {schema}.{nome} ---")
                try:
                    cols, righe = _righe(conn, f"SELECT TOP 5 * FROM [{schema}].[{nome}]")
                    _stampa_tabella(cols, righe)
                except SQLAlchemyError as e:
                    print(f"    (impossibile leggere: {type(e).__name__})")
                print()

        print("✓ Ricognizione completata. Incolla l'output per la mappatura segnaposto→reale.")
        return 0

    except Exception as e:  # noqa: BLE001 — vogliamo un messaggio chiaro all'utente
        print("✗ Connessione al DB Mago NON disponibile.")
        print(f"  Dettaglio: {type(e).__name__}: {e}")
        print()
        print("  Esegui invece a mano in SSMS:  sql/ricognizione/ricognizione_mago.sql")
        print("  e incollami i risultati: NON indovino i nomi.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
