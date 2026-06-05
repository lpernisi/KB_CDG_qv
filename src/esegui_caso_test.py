"""
esegui_caso_test.py
===================
Esegue il documento di esempio (dashboard/caso_test.json) attraverso le
PROCEDURE REALI di CDG_QV, non con una simulazione. E' la verifica autorevole.

Cosa fa:
  1. inietta righe_vendita e costo_wap del caso direttamente negli strati 'src'
     (saltando l'estrazione da Mago, che per un caso di test non serve);
  2. esegue i componenti attivi + l'assemblaggio (le stesse procedure della pipeline);
  3. legge pres.controllo_componenti e pres.conto_economico_riga;
  4. scrive dashboard/risultati_caso.json, che la dashboard mostra come
     "Risultati ufficiali dal database".

Richiede una connessione a SQL Server (vedi config + .env) e la struttura gia'
creata (lancia prima run_pipeline.py, o almeno la parte DDL/procedure).

Lancio:  python src/esegui_caso_test.py
"""

from __future__ import annotations

import json

from sqlalchemy import text

from src.config_loader import carica_config, ROOT
from src import db

CASO = ROOT / "dashboard" / "caso_test.json"
OUT = ROOT / "dashboard" / "risultati_caso.json"


def main():
    config = carica_config()
    caso = json.loads(CASO.read_text(encoding="utf-8"))
    anno = caso["competenza"]["anno"]
    mese = caso["competenza"]["mese"]
    dwh = db.engine_dwh(config)

    # 1) Inietta il caso negli strati src (sostituisce il periodo).
    with dwh.begin() as conn:
        conn.execute(text("DELETE FROM src.righe_vendita WHERE anno=:a AND mese=:m"), {"a": anno, "m": mese})
        for r in caso["righe_vendita"]:
            conn.execute(text("""
                INSERT INTO src.righe_vendita (anno, mese, sale_doc_id, line, codice_articolo, quantita, ricavo_netto)
                VALUES (:anno, :mese, :sale, :line, :art, :qta, :ric)"""),
                {"anno": anno, "mese": mese, "sale": 999000 + int(r["line"]), "line": int(r["line"]),
                 "art": r["codice_articolo"], "qta": r["quantita"], "ric": r["ricavo_netto"]})
        conn.execute(text("TRUNCATE TABLE src.costo_wap"))
        for w in caso["costo_wap"]:
            conn.execute(text("""
                INSERT INTO src.costo_wap (anno, mese, codice_articolo, wap)
                VALUES (:anno, :mese, :art, :wap)"""),
                {"anno": w["anno"], "mese": w["mese"], "art": w["codice_articolo"], "wap": w["wap"]})

    # 2) Esegue i componenti attivi + assemblaggio (le procedure reali).
    attivi = db.valori(dwh, "SELECT codice_componente FROM cfg.componenti WHERE attivo=1 ORDER BY livello, codice_componente")
    for codice in attivi:
        try:
            db.esegui_proc(dwh, f"dbo.usp_comp_{codice}", anno, mese)
        except Exception as e:
            print(f"  [!! ] {codice}: {type(e).__name__}")
    db.esegui_proc(dwh, "dbo.usp_build_fatto_riga", anno, mese)

    # 3) Legge i risultati dalle viste di presentazione.
    with dwh.begin() as conn:
        controllo = [dict(r._mapping) for r in conn.execute(text(
            "SELECT * FROM pres.controllo_componenti WHERE anno=:a AND mese=:m"), {"a": anno, "m": mese})]
        righe = [dict(r._mapping) for r in conn.execute(text(
            "SELECT * FROM pres.conto_economico_riga WHERE anno=:a AND mese=:m ORDER BY line"), {"a": anno, "m": mese})]

    # 4) Scrive il file che la dashboard mostra (default=str per i Decimal/date).
    OUT.write_text(json.dumps(
        {"competenza": {"anno": anno, "mese": mese}, "controllo_componenti": controllo, "conto_economico_riga": righe},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"✓ Risultati scritti in {OUT}. Rigenera la dashboard: python src/genera_dashboard.py")


if __name__ == "__main__":
    main()
