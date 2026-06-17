"""
genera_tariffa_trasporto.py
===========================
Genera la PROPOSTA di tariffa di trasporto (livello 1, stima per fascia di peso) a partire
dal COSTO REALE fatturato dai vettori (src.fattura_vettore_riga), e la scrive nel formato di
config canonico -> config/tariffa_trasporto.csv (importabile in cfg.trasporto_stima_peso con
src/importa_tariffa_trasporto.py).

Logica:
 - tariffa = MEDIANA del Totale (Nolo + Spese Accessorie) fatturato, per (area, fascia di peso);
 - geografia a CASCATA: ITALIA, i Paesi esteri con volume sufficiente (FRANCIA, GERMANIA,
   SPAGNA, PORTOGALLO...), e un 'ESTERO' generico per tutti gli altri Paesi;
 - il canale entra solo come ECCEZIONE, dove la sua mediana devia oltre soglia dalla riga base
   (es. Amazon non spedisce su pallet, BTOB su pallet, corrieri ammessi diversi);
 - una sola data di validita' (valido_dal): per aggiornare in futuro si rigenera con una data
   nuova e si importa -> il pregresso resta valido per i documenti anteriori.

Uso:  python src/genera_tariffa_trasporto.py [ANNO] [VALIDO_DAL=YYYY-MM-DD]
      (default: ANNO=2026, VALIDO_DAL=ANNO-01-01)
"""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from src.config_loader import carica_config
from src import db
import pandas as pd, numpy as np

ANNO       = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
VALIDO_DAL = sys.argv[2] if len(sys.argv) > 2 else f"{ANNO}-01-01"
OUT_CSV    = _ROOT / "config" / "tariffa_trasporto.csv"

# Paesi esteri da tenere SEPARATI (devono coincidere con la mappa in kodice.vw_doc_trasporto).
# Gli altri Paesi confluiscono nell'ESTERO generico.
PAESI_SPECIFICI = {"FRANCIA", "GERMANIA", "SPAGNA", "PORTOGALLO"}
N_PAESE = 120     # spedizioni minime nel Paese per tenerlo separato
N_BASE  = 20      # spedizioni minime per emettere una riga base (area, fascia)
N_OVR   = 30      # spedizioni minime per un'eccezione di canale
DEV     = 0.15    # scostamento minimo dalla base perche' il canale meriti una riga propria

FASCE = [(0,1),(1,2),(2,5),(5,10),(10,20),(20,30),(30,50),(50,100),(100,9999)]
def fascia_di(p):
    for da,a in FASCE:
        if da <= p < a: return (da,a)
    return None

def main():
    eng = db._engine(carica_config(), carica_config()["database"]["dwh"])
    fatt = pd.read_sql(f"""
        SELECT LTRIM(RTRIM(rif_ordine)) AS ordine, nazione,
               CAST(peso AS float) AS peso, CAST(totale AS float) AS totale
        FROM src.fattura_vettore_riga
        WHERE tipo_spedizione='SPEDIZIONE' AND peso>0 AND totale>0 AND anno={ANNO}""", eng)
    can = pd.read_sql("""
        SELECT LTRIM(RTRIM(NrOrdine)) AS ordine, MAX(LTRIM(RTRIM(GruppoCliente))) AS canale
        FROM KODICEBAGNO_4.dbo.KB_OrdiniPrelevati
        WHERE NULLIF(LTRIM(RTRIM(GruppoCliente)),'') IS NOT NULL
        GROUP BY LTRIM(RTRIM(NrOrdine))""", eng)
    df = fatt.merge(can, on="ordine", how="left")
    df["canale"]  = df["canale"].fillna("(non assegnato)").replace("", "(non assegnato)")
    df["nazione"] = df["nazione"].fillna("ITALIA").str.upper().str.strip()

    # area_label = ITALIA | Paese specifico | ESTERO  (coincide con cio' che risolve la vista)
    vol_paese = df[df["nazione"] != "ITALIA"].groupby("nazione").size()
    paesi_ok = {p for p in PAESI_SPECIFICI if vol_paese.get(p, 0) >= N_PAESE}
    def area_label(naz):
        if naz in ("ITALIA", ""):      return "ITALIA"
        if naz in paesi_ok:            return naz
        return "ESTERO"
    df["area"] = df["nazione"].map(area_label)
    df = df.dropna(subset=["peso"])
    df["fascia"] = df["peso"].map(fascia_di)
    df = df[df["fascia"].notna()].copy()

    # BASE per (area, fascia): mediana, canale '*'. Tre insiemi con copertura a cascata:
    #  - ITALIA + Paesi specifici  (dal label area, escluso il generico ESTERO)
    #  - ESTERO generico  = mediana su TUTTO l'estero (copertura piena come ripiego)
    #  - '*' globale       = mediana su TUTTO (rete di sicurezza finale, nessun buco)
    spec = (df[df["area"] != "ESTERO"].groupby(["area","fascia"])
              .agg(n=("totale","size"), med=("totale","median")).reset_index())
    est  = (df[df["nazione"] != "ITALIA"].groupby(["fascia"])
              .agg(n=("totale","size"), med=("totale","median")).reset_index())
    est["area"] = "ESTERO"
    glob = (df.groupby(["fascia"])
              .agg(n=("totale","size"), med=("totale","median")).reset_index())
    glob["area"] = "*"
    base = pd.concat([spec, est, glob], ignore_index=True)
    base = base[base["n"] >= N_BASE].copy()
    med_base = {(r.area, r.fascia): round(r.med,2) for r in base.itertuples()}

    # ECCEZIONI di canale: deviano dalla base della stessa area/fascia
    canlev = (df[df["canale"] != "(non assegnato)"]
                .groupby(["canale","area","fascia"]).agg(n=("totale","size"), med=("totale","median"))
                .reset_index())
    righe = []
    for r in base.itertuples():
        righe.append(("*", r.area, r.fascia[0], r.fascia[1], round(r.med,2),
                      f"base mediana {ANNO} n={int(r.n)}"))
    n_ovr = 0
    for r in canlev.itertuples():
        b = med_base.get((r.area, r.fascia))
        if b is None or r.n < N_OVR: continue
        dev = round(r.med,2)/b - 1
        if abs(dev) > DEV:
            righe.append((r.canale, r.area, r.fascia[0], r.fascia[1], round(r.med,2),
                          f"eccezione canale n={int(r.n)} scost {dev*100:.0f}%"))
            n_ovr += 1

    # ordina: ITALIA, Paesi, ESTERO, poi '*' prima delle eccezioni, per fascia
    ord_area = {"ITALIA":0, **{p:1 for p in sorted(paesi_ok)}, "ESTERO":2, "*":3}
    righe.sort(key=lambda x: (ord_area.get(x[1],1), x[1], 0 if x[0]=="*" else 1, x[2]))

    OUT_CSV.parent.mkdir(exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("# Tariffa di trasporto - livello 1 (stima per fascia di peso).\n")
        fh.write("# Formato: separatore ';', decimali con ',', date 'YYYY-MM-DD', encoding UTF-8.\n")
        fh.write("# Colonne: canale;area;peso_da_kg;peso_a_kg;costo_eur;valido_dal;note\n")
        fh.write("#   canale: '*'=qualsiasi, oppure nome canale (es. 'Amazon').\n")
        fh.write("#   area:   'ITALIA' | nome Paese (es. 'FRANCIA') | 'ESTERO' (altri Paesi) | '*'.\n")
        fh.write("#   peso_da_kg incluso, peso_a_kg escluso. costo_eur = costo di UNA spedizione.\n")
        fh.write("#   Per aggiornare: rigenerare/duplicare con un valido_dal NUOVO e reimportare.\n")
        fh.write("canale;area;peso_da_kg;peso_a_kg;costo_eur;valido_dal;note\n")
        for canale, area, da, a, costo, note in righe:
            costo_it = f"{costo:.2f}".replace(".", ",")
            fh.write(f"{canale};{area};{da};{a};{costo_it};{VALIDO_DAL};{note}\n")

    print(f"Anno {ANNO}, valido_dal {VALIDO_DAL}")
    print(f"Paesi tenuti separati: {sorted(paesi_ok)}  (gli altri -> ESTERO)")
    print(f"Righe scritte: {len(righe)}  ({len(base)} base + {n_ovr} eccezioni canale)")
    print(f"File: {OUT_CSV}")

if __name__ == "__main__":
    main()
