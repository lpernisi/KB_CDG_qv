"""
import_fatture_vettori.py
=========================
Importa il RIEPILOGATIVO delle fatture dei vettori (il CSV/Excel scaricato da
Google Sheet) nella landing del datawarehouse: src.fattura_vettore_riga.

Una riga del file = una SPEDIZIONE. Colonne attese (intestazioni leggibili):
  Trasportatore, Destino, Anno, Mese, Data spedizione, Tipo Spedizione,
  Categoria Cliente, N. rif. Cliente, Destinatario, Prov. Destinatario,
  Regione Dest, Nazione, [Mittente...], N. colli, Peso, Volume,
  Nolo, Spese Accessorie, Totale

"N. rif. Cliente" e' il NUMERO ORDINE (MA_SaleOrd.InternalOrdNo): e' la chiave
con cui poi si risale al documento di vendita (kodice.ordine_documento).

Idempotente: per ogni (anno, mese, vettore) presente nel file, cancella le righe
gia' caricate e reinserisce. Cosi' ricaricare un file corretto non duplica.

Uso da riga di comando (test):
    python src/import_fatture_vettori.py <percorso_file.csv|.xlsx>
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ─── normalizzazione intestazioni ──────────────────────────────────────────
def _norm(s: str) -> str:
    return (
        str(s).strip().lower()
        .replace(".", "").replace("'", "").replace("`", "")
        .replace("à", "a").replace("è", "e").replace("é", "e")
        .replace("ì", "i").replace("ò", "o").replace("ù", "u")
        .replace("  ", " ").strip()
    )


# header normalizzato -> campo della tabella
MAPPA = {
    "trasportatore": "vettore",
    "destino": "destino",
    "anno": "anno",
    "mese": "mese",
    "data spedizione": "data_spedizione",
    "tipo spedizione": "tipo_spedizione",
    "categoria cliente": "categoria_cliente",
    "n rif cliente": "rif_ordine",
    "destinatario": "destinatario",
    "prov destinatario": "prov_destinatario",
    "regione dest": "regione_dest",
    "nazione": "nazione",
    "n colli": "n_colli",
    "peso": "peso",
    "volume": "volume",
    "nolo": "nolo",
    "spese accessorie": "spese_accessorie",
    "totale": "totale",
}


def _num(v) -> float | None:
    """Parsa un numero all'italiana: '€ 2.340,00' -> 2340.00, '3,00' -> 3.0."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none"):
        return None
    s = s.replace("€", "").replace(" ", "").replace("\xa0", "")
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _intero(v) -> int | None:
    n = _num(v)
    return int(round(n)) if n is not None else None


def _data(v):
    """Data gg/mm/aaaa (o aaaa-mm-gg) -> date."""
    import pandas as pd

    if v is None or str(v).strip() == "":
        return None
    try:
        return pd.to_datetime(str(v).strip(), dayfirst=True, errors="coerce").date()
    except Exception:
        return None


def _leggi_dataframe(contenuto: bytes, nome_file: str):
    """Legge CSV o Excel (tutto come stringa) in un DataFrame pandas."""
    import pandas as pd

    est = Path(nome_file).suffix.lower()
    if est in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(contenuto), dtype=str, engine="openpyxl")
    # CSV/TSV: autodetect separatore con engine python
    testo = contenuto.decode("utf-8-sig", errors="replace")
    return pd.read_csv(io.StringIO(testo), dtype=str, sep=None, engine="python")


def _normalizza(contenuto: bytes, nome_file: str):
    """Legge il file e rinomina le colonne secondo la mappa (header normalizzato).
    Verifica le colonne obbligatorie. NON fa parsing riga-per-riga (veloce)."""
    df = _leggi_dataframe(contenuto, nome_file)
    rename = {}
    for c in df.columns:
        chiave = _norm(c)
        if chiave in MAPPA:
            rename[c] = MAPPA[chiave]
    df = df.rename(columns=rename)
    mancanti = {"anno", "mese", "totale"} - set(df.columns)
    if mancanti:
        raise ValueError(
            f"Colonne obbligatorie mancanti nel file: {', '.join(sorted(mancanti))}. "
            f"Intestazioni trovate: {', '.join(str(c) for c in df.columns)}"
        )
    return df


def _num_series(s):
    """Parsing VETTORIZZATO di importi all'italiana ('€ 2.340,00' -> 2340.00)."""
    import pandas as pd

    t = (s.astype("string")
           .str.replace("€", "", regex=False)
           .str.replace("\xa0", "", regex=False)
           .str.replace(" ", "", regex=False)
           .str.replace(".", "", regex=False)
           .str.replace(",", ".", regex=False))
    return pd.to_numeric(t, errors="coerce")


def parse_righe(contenuto: bytes, nome_file: str) -> list[dict]:
    """Trasforma il file in una lista di dict pronti per l'INSERT (campi puliti).
    Tutto VETTORIZZATO (niente to_datetime/regex riga-per-riga): rapido anche su file annuali."""
    import pandas as pd

    df = _normalizza(contenuto, nome_file)
    n = len(df)

    def col(name):
        return df[name] if name in df.columns else pd.Series([None] * n, dtype="object")

    anno   = pd.to_numeric(col("anno"), errors="coerce")
    mese   = pd.to_numeric(col("mese"), errors="coerce")
    colli  = _num_series(col("n_colli"))
    peso   = _num_series(col("peso"))
    volume = _num_series(col("volume"))
    nolo   = _num_series(col("nolo"))
    spese  = _num_series(col("spese_accessorie"))
    totale = _num_series(col("totale")).fillna(nolo.fillna(0) + spese.fillna(0))
    data   = pd.to_datetime(col("data_spedizione"), dayfirst=True, errors="coerce")

    def txt(name, up=False, cut=None):
        s = col(name).astype("string").str.strip()
        if up:
            s = s.str.upper()
        if cut:
            s = s.str.slice(0, cut)
        return s

    vettore   = txt("vettore");           destino   = txt("destino")
    tipo      = txt("tipo_spedizione", up=True)
    categoria = txt("categoria_cliente", up=True)
    rif       = txt("rif_ordine");        destinat  = txt("destinatario", cut=160)
    prov      = txt("prov_destinatario", cut=20)
    regione   = txt("regione_dest", cut=60); naz    = txt("nazione", cut=60)

    righe = []
    for i in range(n):
        a, m = anno.iat[i], mese.iat[i]
        if pd.isna(a) or pd.isna(m):
            continue  # riga senza periodo (righe vuote di coda): salto
        def vs(s):                                   # stringa o None
            x = s.iat[i]; return None if pd.isna(x) else str(x)
        def vf(s):                                   # numero o None
            x = s.iat[i]; return None if pd.isna(x) else float(x)
        dt = data.iat[i]
        righe.append({
            "anno": int(a), "mese": int(m),
            "vettore": vs(vettore), "destino": vs(destino),
            "data_spedizione": (None if pd.isna(dt) else dt.date()),
            "tipo_spedizione": vs(tipo), "categoria_cliente": vs(categoria),
            "rif_ordine": vs(rif), "destinatario": vs(destinat),
            "prov_destinatario": vs(prov), "regione_dest": vs(regione), "nazione": vs(naz),
            "n_colli": (None if pd.isna(colli.iat[i]) else int(colli.iat[i])),
            "peso": vf(peso), "volume": vf(volume),
            "nolo": vf(nolo), "spese_accessorie": vf(spese), "totale": vf(totale),
        })
    return righe


def riepilogo(contenuto: bytes, nome_file: str) -> dict:
    """Legge il file e restituisce SOLO i valori disponibili per la scelta
    (Vettore / Destino / Anno / Mese), come la maschera della solution C#.
    Vettorizzato: non costruisce le righe, quindi e' istantaneo anche su file grandi."""
    import pandas as pd

    df = _normalizza(contenuto, nome_file)
    anno = pd.to_numeric(df.get("anno"), errors="coerce").dropna().astype(int)
    mese = pd.to_numeric(df.get("mese"), errors="coerce").dropna().astype(int)

    def uniq(name):
        if name not in df.columns:
            return []
        s = df[name].astype("string").str.strip()
        return sorted(x for x in s.dropna().unique() if x != "")

    return {
        "righe":   int(df.shape[0]),
        "vettori": uniq("vettore"),
        "destini": uniq("destino"),
        "anni":    sorted({int(x) for x in anno.unique()}),
        "mesi":    sorted({int(x) for x in mese.unique()}),
    }


def _passa_filtro(r: dict, filtri: dict | None) -> bool:
    """Tiene la riga se rispetta i filtri scelti (vuoto/None = qualsiasi)."""
    if not filtri:
        return True
    if filtri.get("vettore") and r["vettore"] != filtri["vettore"]:
        return False
    if filtri.get("destino") and r["destino"] != filtri["destino"]:
        return False
    if filtri.get("anno") and r["anno"] != int(filtri["anno"]):
        return False
    if filtri.get("mese") and r["mese"] != int(filtri["mese"]):
        return False
    return True


def importa(config: dict, contenuto: bytes, nome_file: str, filtri: dict | None = None) -> dict:
    """
    Carica il file in src.fattura_vettore_riga, eventualmente filtrando per
    Vettore/Destino/Anno/Mese (come la maschera C#). Sostituisce le righe gia'
    caricate per le combinazioni (anno, mese, vettore, destino) presenti nella
    selezione, cosi' ricaricare la stessa fattura non duplica e non tocca le altre.
    """
    from sqlalchemy import text
    from db import _engine

    righe = [r for r in parse_righe(contenuto, nome_file) if _passa_filtro(r, filtri)]
    if not righe:
        return {"righe": 0, "periodi": [], "messaggio": "Nessuna riga corrisponde alla selezione."}

    # combinazioni (anno, mese, vettore, destino) da sostituire
    combo = sorted({(r["anno"], r["mese"], r["vettore"], r["destino"]) for r in righe})

    eng = _engine(config, config["database"]["dwh"])
    with eng.begin() as conn:
        for anno, mese, vettore, destino in combo:
            conn.execute(
                text("DELETE FROM src.fattura_vettore_riga "
                     "WHERE anno=:a AND mese=:m AND ISNULL(vettore,'')=ISNULL(:v,'') "
                     "AND ISNULL(destino,'')=ISNULL(:d,'')"),
                {"a": anno, "m": mese, "v": vettore, "d": destino},
            )
        conn.execute(
            text("""
                INSERT INTO src.fattura_vettore_riga
                    (anno, mese, vettore, destino, data_spedizione, tipo_spedizione,
                     categoria_cliente, rif_ordine, destinatario, prov_destinatario,
                     regione_dest, nazione, n_colli, peso, volume, nolo, spese_accessorie, totale)
                VALUES
                    (:anno, :mese, :vettore, :destino, :data_spedizione, :tipo_spedizione,
                     :categoria_cliente, :rif_ordine, :destinatario, :prov_destinatario,
                     :regione_dest, :nazione, :n_colli, :peso, :volume, :nolo, :spese_accessorie, :totale)
            """),
            righe,
        )

    tot = round(sum(r["totale"] or 0 for r in righe), 2)
    periodi = sorted({(r["anno"], r["mese"]) for r in righe})
    vettori = sorted({r["vettore"] for r in righe if r["vettore"]})
    return {
        "righe": len(righe),
        "totale_costo": tot,
        "periodi": [f"{a}-{m:02d}" for a, m in periodi],
        "vettori": vettori,
        "messaggio": f"Caricate {len(righe)} spedizioni ({tot:,.2f} €) per {len(periodi)} periodo/i.",
    }


if __name__ == "__main__":
    from config_loader import carica_config

    if len(sys.argv) < 2:
        print("Uso: python src/import_fatture_vettori.py <file.csv|.xlsx>")
        sys.exit(1)
    percorso = Path(sys.argv[1])
    esito = importa(carica_config(), percorso.read_bytes(), percorso.name)
    print(esito["messaggio"])
    print("  periodi:", esito.get("periodi"))
    print("  vettori:", esito.get("vettori"))
