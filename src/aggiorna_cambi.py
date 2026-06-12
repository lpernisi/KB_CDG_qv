# -*- coding: utf-8 -*-
"""Aggiorna i cambi valuta in CDG_QV dal feed BCE (eurofxref-daily.xml).

Indipendente da Mago (il cui fixing e' spesso fermo). Convenzione salvata:
CambioPerEur = quante unita' di valuta vale 1 EUR (USD 1.1537). Idempotente: se
gira piu' volte nello stesso giorno fa MERGE sulla stessa data. Schedulabile
settimanale/mensile (vedi deploy/installa-cambi.ps1).

Uso:  python src/aggiorna_cambi.py
"""
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import text
from src.config_loader import carica_config
from src import db

URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
NS = {"g": "http://www.gesmes.org/xml/2002-08-01",
      "e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}


def scarica_cambi():
    raw = urllib.request.urlopen(URL, timeout=30).read()
    cube = ET.fromstring(raw).find(".//e:Cube[@time]", NS)
    data = cube.get("time")
    rates = {c.get("currency"): float(c.get("rate"))
             for c in cube.findall("e:Cube", NS) if c.get("rate")}
    return data, rates


def main():
    cfg = carica_config()
    eng = db._engine(cfg, cfg["database"]["dwh"])
    data, rates = scarica_cambi()
    print(f"BCE {data}: {len(rates)} valute")
    with eng.begin() as c:
        for valuta, cambio in rates.items():
            c.execute(text("""
                MERGE kodice.cambio_valuta AS t
                USING (SELECT :d AS Data, :v AS Valuta) s
                  ON t.Data = s.Data AND t.Valuta = s.Valuta
                WHEN MATCHED THEN UPDATE SET CambioPerEur=:r, Fonte='BCE', Caricato=SYSDATETIME()
                WHEN NOT MATCHED THEN INSERT (Data,Valuta,CambioPerEur,Fonte,Caricato)
                     VALUES (:d,:v,:r,'BCE',SYSDATETIME());"""),
                {"d": data, "v": valuta, "r": cambio})
    print(f"OK: cambi del {data} salvati in kodice.cambio_valuta")


if __name__ == "__main__":
    main()
