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


def parse_righe(contenuto: bytes, nome_file: str) -> list[dict]:
    """Trasforma il file in una lista di dict pronti per l'INSERT (campi puliti)."""
    df = _leggi_dataframe(contenuto, nome_file)

    # rinomina colonne secondo la mappa (header normalizzato)
    rename = {}
    for col in df.columns:
        chiave = _norm(col)
        if chiave in MAPPA:
            rename[col] = MAPPA[chiave]
    df = df.rename(columns=rename)

    obbligatorie = {"anno", "mese", "totale"}
    mancanti = obbligatorie - set(df.columns)
    if mancanti:
        raise ValueError(
            f"Colonne obbligatorie mancanti nel file: {', '.join(sorted(mancanti))}. "
            f"Intestazioni trovate: {', '.join(str(c) for c in df.columns)}"
        )

    righe = []
    for _, r in df.iterrows():
        anno = _intero(r.get("anno"))
        mese = _intero(r.get("mese"))
        if anno is None or mese is None:
            continue  # riga senza periodo: salto (di solito righe vuote di coda)
        nolo = _num(r.get("nolo"))
        spese = _num(r.get("spese_accessorie"))
        totale = _num(r.get("totale"))
        if totale is None:
            totale = (nolo or 0) + (spese or 0)
        righe.append({
            "anno": anno, "mese": mese,
            "vettore": (str(r.get("vettore")).strip() if r.get("vettore") is not None else None),
            "destino": (str(r.get("destino")).strip() if r.get("destino") is not None else None),
            "data_spedizione": _data(r.get("data_spedizione")),
            "tipo_spedizione": (str(r.get("tipo_spedizione")).strip().upper() if r.get("tipo_spedizione") is not None else None),
            "categoria_cliente": (str(r.get("categoria_cliente")).strip().upper() if r.get("categoria_cliente") is not None else None),
            "rif_ordine": (str(r.get("rif_ordine")).strip() if r.get("rif_ordine") is not None else None),
            "destinatario": (str(r.get("destinatario")).strip()[:160] if r.get("destinatario") is not None else None),
            "prov_destinatario": (str(r.get("prov_destinatario")).strip()[:20] if r.get("prov_destinatario") is not None else None),
            "regione_dest": (str(r.get("regione_dest")).strip()[:60] if r.get("regione_dest") is not None else None),
            "nazione": (str(r.get("nazione")).strip()[:60] if r.get("nazione") is not None else None),
            "n_colli": _intero(r.get("n_colli")),
            "peso": _num(r.get("peso")),
            "volume": _num(r.get("volume")),
            "nolo": nolo,
            "spese_accessorie": spese,
            "totale": totale,
        })
    return righe


def importa(config: dict, contenuto: bytes, nome_file: str) -> dict:
    """
    Carica il file in src.fattura_vettore_riga. Per ogni (anno, mese, vettore)
    presente nel file, sostituisce le righe gia' caricate. Restituisce un riepilogo.
    """
    from sqlalchemy import text
    from db import _engine

    righe = parse_righe(contenuto, nome_file)
    if not righe:
        return {"righe": 0, "periodi": [], "messaggio": "Nessuna riga valida nel file."}

    # combinazioni (anno, mese, vettore) da sostituire
    combo = sorted({(r["anno"], r["mese"], r["vettore"]) for r in righe})

    eng = _engine(config, config["database"]["dwh"])
    with eng.begin() as conn:
        for anno, mese, vettore in combo:
            conn.execute(
                text("DELETE FROM src.fattura_vettore_riga "
                     "WHERE anno=:a AND mese=:m AND ISNULL(vettore,'')=ISNULL(:v,'')"),
                {"a": anno, "m": mese, "v": vettore},
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
