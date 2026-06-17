"""
importa_tariffa_trasporto.py
============================
Importa un CSV di tariffe di trasporto (livello 1) in cfg.trasporto_stima_peso.

FORMATO ATTESO (vedi config/tariffa_trasporto.csv):
    canale;area;peso_da_kg;peso_a_kg;costo_eur;valido_dal;note
  - separatore ';' (o ',' o TAB: rilevato automaticamente);
  - decimali con ',' o '.' (entrambi accettati);
  - date 'YYYY-MM-DD' o 'GG/MM/AAAA';
  - righe che iniziano con '#' = commento, ignorate;
  - canale '*' = qualsiasi; area = 'ITALIA' | nome Paese | 'ESTERO' | '*'.

AGGIORNAMENTI SENZA IMPATTARE IL PREGRESSO:
  l'import e' idempotente PER GENERAZIONE (valido_dal): per ogni valido_dal presente nel file
  cancella e re-inserisce SOLO le righe di quella data, lasciando intatte le altre generazioni.
  Per aggiornare la tariffa dal 1/7/2026 si importa un file con valido_dal=2026-07-01: i documenti
  fino al 30/6 continuano a usare la generazione precedente (il componente sceglie la valido_dal
  piu' recente <= data documento).

Uso:  python src/importa_tariffa_trasporto.py [file.csv] [--dry-run]
      (default file: config/tariffa_trasporto.csv)
"""
import sys, csv, io, datetime
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from src.config_loader import carica_config
from src import db

COLONNE = ["canale", "area", "peso_da_kg", "peso_a_kg", "costo_eur", "valido_dal", "note"]

def _num(s):
    s = (s or "").strip().replace(" ", "")
    if not s: return 0.0
    if "," in s and "." in s:           # 1.234,56 -> 1234.56
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return float(s)

def _data(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try: return datetime.datetime.strptime(s, fmt).date()
        except ValueError: pass
    raise ValueError(f"data non valida: '{s}'")

def leggi_righe(testo: str):
    # togli i commenti '#', poi rileva il separatore sull'header
    linee = [l for l in testo.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not linee:
        return []
    try:
        sep = csv.Sniffer().sniff(linee[0], delimiters=";,\t").delimiter
    except csv.Error:
        sep = ";"
    rdr = csv.DictReader(io.StringIO("\n".join(linee)), delimiter=sep)
    rdr.fieldnames = [(h or "").strip().lower().lstrip("﻿") for h in rdr.fieldnames]
    mancanti = [c for c in COLONNE if c not in rdr.fieldnames]
    if mancanti:
        raise SystemExit(f"Colonne mancanti nel CSV: {mancanti}\nTrovate: {rdr.fieldnames}")
    righe = []
    for i, r in enumerate(rdr, start=2):
        canale = (r["canale"] or "*").strip() or "*"
        area   = (r["area"] or "*").strip() or "*"
        da, a, costo = _num(r["peso_da_kg"]), _num(r["peso_a_kg"]), _num(r["costo_eur"])
        dal = _data(r["valido_dal"])
        note = (r.get("note") or "").strip()[:200]
        if a <= da:
            raise SystemExit(f"Riga {i}: peso_a_kg ({a}) deve essere > peso_da_kg ({da}).")
        if costo < 0:
            raise SystemExit(f"Riga {i}: costo_eur negativo ({costo}).")
        righe.append({"canale": canale, "area": area, "pda": da, "pa": a,
                      "costo": round(costo, 2), "dal": dal, "note": note})
    return righe

def importa(percorso: Path, dry_run=False):
    from sqlalchemy import text
    testo = percorso.read_text(encoding="utf-8-sig")
    righe = leggi_righe(testo)
    if not righe:
        print("Nessuna riga da importare."); return
    generazioni = sorted({r["dal"] for r in righe})
    print(f"File: {percorso}")
    print(f"Righe valide: {len(righe)}  |  generazioni (valido_dal): {[str(g) for g in generazioni]}")
    if dry_run:
        print("[dry-run] nessuna scrittura. Anteprima prime 8 righe:")
        for r in righe[:8]:
            print(f"  {r['canale']:<18} {r['area']:<12} {r['pda']:>5g}-{r['pa']:<6g} "
                  f"{r['costo']:>7.2f}  {r['dal']}  {r['note']}")
        return

    eng = db._engine(carica_config(), carica_config()["database"]["dwh"])
    with eng.begin() as c:
        for g in generazioni:   # sostituisci SOLO la generazione presente nel file
            c.execute(text("DELETE FROM cfg.trasporto_stima_peso WHERE valido_dal = :g"), {"g": g})
        c.execute(   # in SQLAlchemy 2.0 passare la lista di dict esegue un executemany
            text("""INSERT INTO cfg.trasporto_stima_peso
                       (canale, area, peso_da_kg, peso_a_kg, costo_eur, valido_dal, note)
                     VALUES (:canale, :area, :pda, :pa, :costo, :dal, :note)"""),
            righe,
        )
    print(f"Importate {len(righe)} righe in cfg.trasporto_stima_peso "
          f"(sostituite le generazioni {[str(g) for g in generazioni]}; altre date intatte).")

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    percorso = Path(args[0]) if args else (_ROOT / "config" / "tariffa_trasporto.csv")
    importa(percorso, dry_run=dry)
