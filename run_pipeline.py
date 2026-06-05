"""
run_pipeline.py
===============
Orchestratore della pipeline CDG_QV. In ordine:

  1. DDL       : crea CDG_QV, la struttura e popola il registro componenti.
  2. PROC      : (ri)crea le procedure di estrazione, dei componenti, di assemblaggio, le viste.
  3. ESTRAI    : carica src.righe_vendita da Mago. Il costo (kodice.costi_articolo_mese)
                 e' preparato a parte dal motore core.usp_prepara_costi.
  4. COMPONENTI: esegue SOLO i componenti attivi nel registro (cfg.componenti).
  5. ASSEMBLA  : costruisce core.fatto_riga (MdC I/II/III).

Aggiungere un componente = una procedura in sql/components/ + una riga attiva nel
registro: questo file non cambia, scopre i componenti attivi da solo.

Periodo da config/settings.yaml. Lancio:  python run_pipeline.py
"""

from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from src.config_loader import carica_config, ROOT
from src import db


def main():
    print("=== Pipeline CDG_QV ===")
    config = carica_config()
    anno = config["periodo"]["anno"]
    mese = config["periodo"]["mese"]
    print(f"Periodo di competenza: {anno}-{mese:02d}\n")

    # --- 1) DDL + registro ----------------------------------------------------
    print("[1/5] DDL: database, struttura, registro componenti")
    db.esegui_script(db.engine_master(config), ROOT / "sql/ddl/00_crea_database.sql")
    dwh = db.engine_dwh(config)
    db.esegui_script(dwh, ROOT / "sql/ddl/01_struttura.sql")
    db.esegui_script(dwh, ROOT / "sql/ddl/02_seed_componenti.sql")

    # --- 2) (ri)crea tutte le procedure e viste (idempotente) -----------------
    print("[2/5] Creo/aggiorno procedure e viste")
    db.esegui_script(dwh, ROOT / "sql/extract/10_usp_load_src.sql")
    # tutte le procedure di componente in sql/components/ (ordine per nome file)
    for f in sorted((ROOT / "sql/components").glob("*.sql")):
        db.esegui_script(dwh, f)
    db.esegui_script(dwh, ROOT / "sql/build/30_usp_build_fatto_riga.sql")
    db.esegui_script(dwh, ROOT / "sql/build/40_pres_viste.sql")

    # --- 3) Estrazione da Mago ------------------------------------------------
    print("[3/5] Estrazione da Mago")
    db.esegui_proc(dwh, "dbo.usp_load_righe_vendita", anno, mese)
    print(f"      righe vendita: {db.conta(dwh, f'SELECT COUNT(*) FROM src.righe_vendita WHERE anno={anno} AND mese={mese}')}")

    # Prerequisito: il costo del periodo dev'essere gia' certificato dal motore.
    n_costi = db.conta(dwh, f"SELECT COUNT(*) FROM kodice.costi_articolo_mese WHERE Anno={anno} AND Mese={mese}")
    if n_costi == 0:
        print(f"      [!! ] kodice.costi_articolo_mese vuoto per {anno}-{mese:02d}. "
              f"Esegui prima: EXEC core.usp_prepara_costi @schema_azienda='kodice', @anno={anno}, @mese={mese};")
    else:
        print(f"      costi certificati (kodice): {n_costi}")

    # --- 4) Componenti ATTIVI (scoperti dal registro) -------------------------
    print("[4/5] Eseguo i componenti attivi")
    attivi = db.valori(dwh, "SELECT codice_componente FROM cfg.componenti WHERE attivo = 1 ORDER BY livello, codice_componente")
    for codice in attivi:
        proc = f"dbo.usp_comp_{codice}"
        try:
            db.esegui_proc(dwh, proc, anno, mese)
            print(f"      [OK ] {codice}")
        except SQLAlchemyError as e:
            # Se la procedura non esiste ancora, segnalo e proseguo con gli altri.
            print(f"      [!! ] {codice}: procedura {proc} non eseguita ({type(e).__name__})")

    # --- 5) Assemblaggio dei margini ------------------------------------------
    print("[5/5] Assemblaggio core.fatto_riga (MdC I/II/III)")
    db.esegui_proc(dwh, "dbo.usp_build_fatto_riga", anno, mese)
    print(f"      righe calcolate: {db.conta(dwh, f'SELECT COUNT(*) FROM core.fatto_riga WHERE anno={anno} AND mese={mese}')}")

    print("\n✓ Fatto. Per validare ogni componente:  SELECT * FROM pres.controllo_componenti;")
    print("         Conto economico di riga:        SELECT * FROM pres.conto_economico_riga;")


if __name__ == "__main__":
    main()
