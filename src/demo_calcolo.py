"""
demo_calcolo.py
===============
Verifica "a secco" del modello a COMPONENTI, SENZA database.

Riproduce in Python la logica delle stored procedure:
  - ogni componente produce le sue righe (importo per riga documento);
  - il registro (livello + segno) guida l'assemblaggio dei margini;
  - MdC I/II/III sono cumulativi.
Serve a controllare la logica PRIMA di lanciarla su SQL Server.

Lancio:  python src/demo_calcolo.py
"""

from __future__ import annotations

import pandas as pd

COMPETENZA = 2025 * 100 + 3  # marzo 2025

# --- Registro componenti (come cfg.componenti) -------------------------------
registro = pd.DataFrame([
    {"codice": "COSTO_VENDUTO", "livello": 1, "segno": -1, "attivo": True},
    {"codice": "PROVVIGIONI",   "livello": 2, "segno": -1, "attivo": True},   # attivo qui per mostrare MdC II
    {"codice": "TRASPORTO",     "livello": 3, "segno": -1, "attivo": True},   # attivo qui per mostrare MdC III
])

# --- Righe di vendita (come src.righe_vendita) -------------------------------
righe = pd.DataFrame([
    {"line": 1, "codice_articolo": "ART-A", "quantita": 10, "ricavo_netto": 1000.00},
    {"line": 2, "codice_articolo": "ART-B", "quantita": 5,  "ricavo_netto": 750.00},
    {"line": 3, "codice_articolo": "ART-C", "quantita": 4,  "ricavo_netto": 400.00},  # WAP mancante
])

# --- Storia WAP (come src.costo_wap) -----------------------------------------
wap = pd.DataFrame([
    {"codice_articolo": "ART-A", "aaamm": 202501, "wap": 40.0},
    {"codice_articolo": "ART-A", "aaamm": 202503, "wap": 42.0},
    {"codice_articolo": "ART-B", "aaamm": 202501, "wap": 90.0},  # risalita a gennaio
])


def wap_risalito(articolo: str):
    c = wap[(wap["codice_articolo"] == articolo) & (wap["aaamm"] <= COMPETENZA)]
    return float(c.sort_values("aaamm").iloc[-1]["wap"]) if not c.empty else None


# --- Ogni componente produce le sue righe (formato "lungo") ------------------
componenti = []
for _, r in righe.iterrows():
    # COSTO_VENDUTO = quantita * WAP risalito
    w = wap_risalito(r["codice_articolo"])
    componenti.append({"line": r["line"], "codice": "COSTO_VENDUTO",
                       "importo": (r["quantita"] * w) if w is not None else None})
    # PROVVIGIONI = 12% del ricavo (esempio dimostrativo)
    componenti.append({"line": r["line"], "codice": "PROVVIGIONI",
                       "importo": round(r["ricavo_netto"] * 0.12, 2)})
    # TRASPORTO = 3 euro a unita (esempio dimostrativo, "per driver")
    componenti.append({"line": r["line"], "codice": "TRASPORTO",
                       "importo": round(r["quantita"] * 3.0, 2)})
comp = pd.DataFrame(componenti).merge(registro, on="codice", how="left")
comp = comp[comp["attivo"]]

# --- Controllo per componente (come pres.controllo_componenti) ---------------
controllo = comp.groupby("codice").agg(
    n_righe=("importo", "size"),
    n_senza_importo=("importo", lambda s: int(s.isna().sum())),
    totale=("importo", "sum"),
).reset_index()

# --- Assemblaggio dei margini (come usp_build_fatto_riga) --------------------
comp["contrib"] = comp["importo"].fillna(0) * comp["segno"]
piv = comp.pivot_table(index="line", columns="livello", values="contrib",
                       aggfunc="sum", fill_value=0)
for lv in (1, 2, 3):
    if lv not in piv.columns:
        piv[lv] = 0.0
fatto = righe.merge(piv, on="line", how="left").fillna(0)
fatto["mdc1"] = fatto["ricavo_netto"] + fatto[1]
fatto["mdc2"] = fatto["mdc1"] + fatto[2]
fatto["mdc3"] = fatto["mdc2"] + fatto[3]

pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
print("== Controllo per componente ==")
print(controllo.to_string(index=False))
print("\n== Conto economico di riga (MdC I/II/III) ==")
print(fatto[["line", "codice_articolo", "ricavo_netto", "mdc1", "mdc2", "mdc3"]].to_string(index=False))
print(f"\nTotali  MdC I: {fatto['mdc1'].sum():,.2f}  |  MdC II: {fatto['mdc2'].sum():,.2f}  |  MdC III: {fatto['mdc3'].sum():,.2f}")
print("Nota: ART-B usa il WAP di gennaio (risalita); ART-C resta senza costo del venduto (da verificare).")
