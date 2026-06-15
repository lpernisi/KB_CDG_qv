"""
dashboard_app.py
================
App locale (Flask) di ESPLORAZIONE del datawarehouse CDG_QV. SOLA LETTURA.

Cosa fa:
  - scegli un periodo (anno/mese gia' elaborato);
  - cerchi e scegli un DOCUMENTO di vendita;
  - per ogni articolo (riga) vedi il dettaglio di TUTTI i componenti collegati
    (oggi: costo del venduto) e i margini MdC I/II/III;
  - sezione ANOMALIE: articoli venduti SENZA costo certificato (con impatto a
    ricavo) e le eccezioni del motore costi (kodice.costi_eccezioni) per tipo/stato,
    per indagare dove intervenire.

Niente scritture: la dashboard mostra, le correzioni si fanno in Mago / rilanciando
il motore costi (core.usp_prepara_costi).

Lancio:  python src/dashboard_app.py     poi apri  http://127.0.0.1:5000
"""

from __future__ import annotations

import sys
import datetime
import calendar
from pathlib import Path

# Permette il lancio sia con "python src/dashboard_app.py" sia con "-m".
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from flask import Flask, jsonify, request, Response
from sqlalchemy import text

from src.config_loader import carica_config
from src import db

cfg = carica_config()
engine = db._engine(cfg, cfg["database"]["dwh"])   # CDG_QV
app = Flask(__name__)
# Dietro reverse-proxy (IIS/ARR): onora gli header X-Forwarded-* (host/proto/prefisso),
# cosi' l'app genera URL corretti quando e' pubblicata sotto un sito/percorso IIS.
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
except Exception:
    pass


def righe(sql: str, **params):
    with engine.begin() as c:
        res = c.execute(text(sql), params)
        cols = list(res.keys())
        return [dict(zip(cols, r)) for r in res.fetchall()]


# ----------------------------------------------------------------------------- API
@app.get("/api/periodi")
def api_periodi():
    return jsonify(righe("""
        SELECT anno, mese, COUNT(*) AS righe,
               CAST(SUM(ricavo_netto) AS DECIMAL(18,2)) AS ricavo,
               CAST(SUM(mdc1) AS DECIMAL(18,2)) AS mdc1
        FROM core.fatto_riga GROUP BY anno, mese ORDER BY anno, mese
    """))


@app.get("/api/documenti")
def api_documenti():
    anno = int(request.args.get("anno", 0))
    mese = int(request.args.get("mese", 0))
    q = (request.args.get("q") or "").strip()
    ql = f"%{q}%"
    return jsonify(righe("""
        SELECT TOP 300
            f.sale_doc_id,
            MAX(t.DocNo)            AS docno,
            MAX(t.CustSupp)         AS cust,
            MAX(cs.CompanyName)     AS cliente,
            COUNT(*)                AS righe,
            CAST(SUM(f.ricavo_netto) AS DECIMAL(18,2)) AS ricavo,
            CAST(SUM(f.mdc1) AS DECIMAL(18,2))         AS mdc1,
            SUM(CASE WHEN cr.importo IS NULL THEN 1 ELSE 0 END) AS senza_costo
        FROM core.fatto_riga AS f
        JOIN KODICEBAGNO_4.dbo.MA_SaleDoc AS t ON t.SaleDocId = f.sale_doc_id
        LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSupp AS cs
               ON cs.CustSupp = t.CustSupp AND cs.CustSuppType = 3211264
        LEFT JOIN core.componente_riga AS cr
               ON cr.anno = f.anno AND cr.mese = f.mese
              AND cr.sale_doc_id = f.sale_doc_id AND cr.line = f.line
              AND cr.codice_componente = N'COSTO_VENDUTO'
        WHERE f.anno = :anno AND f.mese = :mese
          AND (:q = '' OR t.DocNo LIKE :ql OR cs.CompanyName LIKE :ql OR CAST(f.sale_doc_id AS VARCHAR(30)) LIKE :ql)
        GROUP BY f.sale_doc_id
        ORDER BY SUM(f.ricavo_netto) DESC
    """, anno=anno, mese=mese, q=q, ql=ql))


@app.get("/api/documento")
def api_documento():
    a = int(request.args.get("anno", 0))
    m = int(request.args.get("mese", 0))
    doc = int(request.args.get("doc", 0))
    testata = righe("""
        SELECT t.SaleDocId, t.DocNo, t.CustSupp, cs.CompanyName,
               CONVERT(date, t.DocumentDate) AS data, t.DocumentType
        FROM KODICEBAGNO_4.dbo.MA_SaleDoc AS t
        LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSupp AS cs
               ON cs.CustSupp = t.CustSupp AND cs.CustSuppType = 3211264
        WHERE t.SaleDocId = :doc
    """, doc=doc)
    linee = righe("""
        SELECT f.line, f.codice_articolo, f.tipo_articolo, f.quantita,
               CAST(f.ricavo_netto AS DECIMAL(18,2)) AS ricavo,
               CAST(f.mdc1 AS DECIMAL(18,2)) AS mdc1,
               CAST(f.mdc2 AS DECIMAL(18,2)) AS mdc2,
               CAST(f.mdc3 AS DECIMAL(18,2)) AS mdc3,
               it.Description AS descr
        FROM core.fatto_riga AS f
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS it ON it.Item = f.codice_articolo
        WHERE f.anno = :a AND f.mese = :m AND f.sale_doc_id = :doc
        ORDER BY f.line
    """, a=a, m=m, doc=doc)
    componenti = righe("""
        SELECT cr.line, cr.codice_componente, c.descrizione, c.livello, c.segno,
               CAST(cr.importo AS DECIMAL(18,2)) AS importo, cr.origine
        FROM core.componente_riga AS cr
        JOIN cfg.componenti AS c ON c.codice_componente = cr.codice_componente
        WHERE cr.anno = :a AND cr.mese = :m AND cr.sale_doc_id = :doc
        ORDER BY cr.line, c.livello, cr.codice_componente
    """, a=a, m=m, doc=doc)
    # raggruppo i componenti per riga
    per_riga = {}
    for x in componenti:
        per_riga.setdefault(x["line"], []).append(x)
    for ln in linee:
        ln["componenti"] = per_riga.get(ln["line"], [])
    return jsonify({"testata": testata[0] if testata else None, "linee": linee})


@app.get("/api/anomalie")
def api_anomalie():
    a = int(request.args.get("anno", 0))
    m = int(request.args.get("mese", 0))
    venduti_senza_costo = righe("""
        SELECT f.codice_articolo,
               MAX(it.Description) AS descr,
               COUNT(*) AS righe,
               CAST(SUM(f.ricavo_netto) AS DECIMAL(18,2)) AS ricavo,
               COUNT(DISTINCT f.sale_doc_id) AS documenti
        FROM core.fatto_riga AS f
        LEFT JOIN core.componente_riga AS cr
               ON cr.anno = f.anno AND cr.mese = f.mese
              AND cr.sale_doc_id = f.sale_doc_id AND cr.line = f.line
              AND cr.codice_componente = N'COSTO_VENDUTO'
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS it ON it.Item = f.codice_articolo
        WHERE f.anno = :a AND f.mese = :m AND cr.importo IS NULL
          AND f.tipo_articolo = N'MERCE'   -- i servizi non hanno costo del venduto: non sono anomalie
        GROUP BY f.codice_articolo
        ORDER BY SUM(f.ricavo_netto) DESC
    """, a=a, m=m)
    ecc_sintesi = righe("""
        SELECT TipoEccezione, Stato, COUNT(*) AS n
        FROM kodice.costi_eccezioni WHERE Anno = :a AND Mese = :m
        GROUP BY TipoEccezione, Stato ORDER BY n DESC
    """, a=a, m=m)
    ecc = righe("""
        SELECT TOP 500 Item, TipoEccezione, ComponenteColpevole, Dettaglio, Stato
        FROM kodice.costi_eccezioni
        WHERE Anno = :a AND Mese = :m AND Stato = 'APERTA'
        ORDER BY TipoEccezione, Item
    """, a=a, m=m)
    # Costi DATATI: articoli venduti il cui costo certificato e' stato "risalito" da un
    # mese precedente (MeseCostoUsato < competenza) = costo potenzialmente non aggiornato.
    costi_datati = righe("""
        SELECT TOP 300 f.codice_articolo,
               MAX(it.Description) AS descr,
               CONVERT(date, MAX(k.MeseCostoUsato)) AS mese_costo,
               MAX(DATEDIFF(MONTH, k.MeseCostoUsato, DATEFROMPARTS(:a, :m, 1))) AS mesi_indietro,
               COUNT(*) AS righe,
               CAST(SUM(f.ricavo_netto) AS DECIMAL(18,2)) AS ricavo
        FROM core.fatto_riga AS f
        JOIN kodice.costi_articolo_mese AS k
              ON LTRIM(RTRIM(k.Item)) = f.codice_articolo AND k.Anno = f.anno AND k.Mese = f.mese
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS it ON it.Item = f.codice_articolo
        WHERE f.anno = :a AND f.mese = :m
          AND k.MeseCostoUsato IS NOT NULL
          AND (YEAR(k.MeseCostoUsato) * 12 + MONTH(k.MeseCostoUsato)) < (:a * 12 + :m)
        GROUP BY f.codice_articolo
        ORDER BY MAX(DATEDIFF(MONTH, k.MeseCostoUsato, DATEFROMPARTS(:a, :m, 1))) DESC, SUM(f.ricavo_netto) DESC
    """, a=a, m=m)
    return jsonify({
        "venduti_senza_costo": venduti_senza_costo,
        "eccezioni_sintesi": ecc_sintesi,
        "eccezioni": ecc,
        "costi_datati": costi_datati,
    })


@app.get("/api/anomalia")
def api_anomalia():
    """Drill-down su un singolo articolo: causa esatta (eccezioni con componente colpevole)
    e stato del costo certificato."""
    a = int(request.args.get("anno", 0))
    m = int(request.args.get("mese", 0))
    item = (request.args.get("item") or "").strip()
    eccezioni = righe("""
        SELECT e.TipoEccezione, e.ComponenteColpevole, ci.Description AS colpevole_descr,
               e.Dettaglio, e.Stato, CONVERT(date, e.DataRilevazione) AS rilevata
        FROM kodice.costi_eccezioni AS e
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS ci ON ci.Item = e.ComponenteColpevole
        WHERE e.Anno = :a AND e.Mese = :m AND e.Item = :item
        ORDER BY e.TipoEccezione, e.ComponenteColpevole
    """, a=a, m=m, item=item)
    costo = righe("""
        SELECT TipoArticolo, CAST(Costo AS DECIMAL(18,4)) AS Costo, Completo,
               CONVERT(date, MeseCostoUsato) AS MeseCostoUsato,
               NComponentiTotali, NComponentiValidi
        FROM kodice.costi_articolo_mese
        WHERE Anno = :a AND Mese = :m AND LTRIM(RTRIM(Item)) = :item
    """, a=a, m=m, item=item)
    return jsonify({"item": item, "eccezioni": eccezioni, "costo": costo[0] if costo else None})


@app.get("/api/foglie")
def api_foglie():
    """Vista per COMPONENTE FOGLIA anomalo: quanti articoli (kit) impatta ciascuna foglia.
    Sanare una foglia molto 'trasversale' risolve tutti i kit che la usano."""
    a = int(request.args.get("anno", 0))
    m = int(request.args.get("mese", 0))
    return jsonify(righe("""
        WITH venduti AS (SELECT DISTINCT codice_articolo FROM core.fatto_riga WHERE anno = :a AND mese = :m)
        SELECT TOP 300
            e.ComponenteColpevole AS comp,
            MAX(ci.Description)    AS descr,
            COUNT(DISTINCT e.Item) AS kit_impattati,
            COUNT(DISTINCT v.codice_articolo) AS kit_venduti_impattati,
            MAX(e.TipoEccezione)   AS tipo
        FROM kodice.costi_eccezioni AS e
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS ci ON ci.Item = e.ComponenteColpevole
        LEFT JOIN venduti AS v ON v.codice_articolo = e.Item
        WHERE e.Anno = :a AND e.Mese = :m AND e.Stato = 'APERTA' AND e.ComponenteColpevole <> ''
        GROUP BY e.ComponenteColpevole
        ORDER BY COUNT(DISTINCT v.codice_articolo) DESC, COUNT(DISTINCT e.Item) DESC
    """, a=a, m=m))


@app.get("/api/foglia")
def api_foglia():
    """Drill di una foglia: gli articoli (kit) che la usano e sono in anomalia per causa sua."""
    a = int(request.args.get("anno", 0))
    m = int(request.args.get("mese", 0))
    comp = (request.args.get("comp") or "").strip()
    kit = righe("""
        SELECT DISTINCT e.Item,
               it.Description AS descr,
               CASE WHEN EXISTS (SELECT 1 FROM core.fatto_riga f
                                 WHERE f.anno = :a AND f.mese = :m AND f.codice_articolo = e.Item)
                    THEN 1 ELSE 0 END AS venduto
        FROM kodice.costi_eccezioni AS e
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items AS it ON it.Item = e.Item
        WHERE e.Anno = :a AND e.Mese = :m AND e.Stato = 'APERTA' AND e.ComponenteColpevole = :comp
        ORDER BY venduto DESC, e.Item
    """, a=a, m=m, comp=comp)
    return jsonify({"comp": comp, "kit": kit})


@app.get("/api/costi_wap")
def api_costi_wap():
    """Trend del costo unitario (WAPCost) di MA_ItemsWAP: confronta il costo dell'ULTIMO
    periodo con quello di ~N mesi prima, per articolo, per individuare i prodotti con
    costo in CALO o in AUMENTO. Sola lettura su KODICEBAGNO_4.dbo.MA_ItemsWAP."""
    try:
        mesi = int(request.args.get("mesi", 6))
    except Exception:
        mesi = 6
    if mesi not in (1, 3, 6, 12, 24):
        mesi = 6
    filtro = "AND cur.FinalQty > 0.001" if request.args.get("solo_giacenza") == "1" else ""
    return jsonify({
        "mesi": mesi,
        "righe": righe(f"""
            WITH wap AS (
                SELECT LTRIM(RTRIM(Item)) AS Item, EndPeriodDate,
                       CAST(WAPCost AS float) AS WAPCost, CAST(FinalQty AS float) AS FinalQty
                FROM KODICEBAGNO_4.dbo.MA_ItemsWAP
                WHERE Storage = '' AND WAPCost > 0
            ),
            cur AS (
                SELECT Item, WAPCost AS cur, EndPeriodDate AS cur_dt, FinalQty FROM (
                    SELECT Item, WAPCost, EndPeriodDate, FinalQty,
                           ROW_NUMBER() OVER (PARTITION BY Item ORDER BY EndPeriodDate DESC) rn
                    FROM wap
                ) t WHERE rn = 1
            ),
            rif AS (
                SELECT Item, ref, ref_dt FROM (
                    SELECT c.Item, w.WAPCost AS ref, w.EndPeriodDate AS ref_dt,
                           ROW_NUMBER() OVER (PARTITION BY c.Item ORDER BY w.EndPeriodDate DESC) rn
                    FROM cur c
                    JOIN wap w ON w.Item = c.Item AND w.EndPeriodDate <= DATEADD(MONTH, -{mesi}, c.cur_dt)
                ) t WHERE rn = 1
            )
            SELECT TOP 600
                cur.Item,
                it.Description AS descr,
                CAST(cur.FinalQty AS DECIMAL(18,2)) AS giacenza,
                CAST(rif.ref AS DECIMAL(18,4))      AS costo_rif,
                CONVERT(date, rif.ref_dt)            AS data_rif,
                CAST(cur.cur AS DECIMAL(18,4))      AS costo_attuale,
                CONVERT(date, cur.cur_dt)            AS data_attuale,
                CAST(cur.cur - rif.ref AS DECIMAL(18,4)) AS delta,
                CAST(100.0 * (cur.cur - rif.ref) / NULLIF(rif.ref, 0) AS DECIMAL(9,1)) AS pct
            FROM cur
            JOIN rif ON rif.Item = cur.Item
            LEFT JOIN KODICEBAGNO_4.dbo.MA_Items it ON LTRIM(RTRIM(it.Item)) = cur.Item
            WHERE ABS(cur.cur - rif.ref) > 0.001 {filtro}
            ORDER BY ABS(100.0 * (cur.cur - rif.ref) / NULLIF(rif.ref, 0)) DESC
        """)
    })


@app.get("/api/trend_costo")
def api_trend_costo():
    """Trend del NOSTRO costo (kodice.wap_ricalc): confronta il costo dell'ultimo mese con costo>0
    e quello di ~N mesi prima; evidenzia gli articoli oltre la soglia % (in aumento o in calo).
    E' l'equivalente di /api/costi_wap ma sul costo calcolato da noi, non sul WAP di Mago."""
    try:
        mesi = int(request.args.get("mesi", 3))
    except Exception:
        mesi = 3
    if mesi not in (1, 2, 3, 6):
        mesi = 3
    try:
        soglia = float(request.args.get("soglia", 10))
    except Exception:
        soglia = 10.0
    return jsonify({
        "mesi": mesi, "soglia": soglia,
        "righe": righe("""
            WITH r AS (
                SELECT Item, Mese, WAPCost_ricalc AS costo, QtaFin
                FROM kodice.wap_ricalc WHERE Anno = 2026 AND WAPCost_ricalc > 0
            ),
            nuovo AS (
                SELECT Item, Mese, costo, QtaFin FROM (
                    SELECT Item, Mese, costo, QtaFin, ROW_NUMBER() OVER (PARTITION BY Item ORDER BY Mese DESC) rn FROM r
                ) t WHERE rn = 1
            ),
            rif AS (
                SELECT n.Item, x.costo AS costo_rif, x.Mese AS mese_rif
                FROM nuovo n
                CROSS APPLY (SELECT TOP 1 costo, Mese FROM r
                             WHERE r.Item = n.Item AND r.Mese <= n.Mese - :mesi ORDER BY Mese DESC) x
            )
            SELECT TOP 600 n.Item, i.Description AS descr,
                   CAST(n.QtaFin AS DECIMAL(18,2)) AS giacenza,
                   CAST(rif.costo_rif AS DECIMAL(18,4)) AS costo_rif, rif.mese_rif,
                   CAST(n.costo AS DECIMAL(18,4)) AS costo_attuale, n.Mese AS mese_attuale,
                   CAST(n.costo - rif.costo_rif AS DECIMAL(18,4)) AS delta,
                   CAST(100.0*(n.costo - rif.costo_rif)/NULLIF(rif.costo_rif,0) AS DECIMAL(9,1)) AS pct
            FROM nuovo n JOIN rif ON rif.Item = n.Item
            LEFT JOIN KODICEBAGNO_4.dbo.MA_Items i ON LTRIM(RTRIM(i.Item)) = n.Item
            WHERE ABS(100.0*(n.costo - rif.costo_rif)/NULLIF(rif.costo_rif,0)) >= :soglia
            ORDER BY ABS(100.0*(n.costo - rif.costo_rif)/NULLIF(rif.costo_rif,0)) DESC
        """, mesi=mesi, soglia=soglia)
    })


@app.get("/api/diagnosi_articolo")
def api_diagnosi_articolo():
    """Perche' un articolo non ha costo certificato nel mese: se e' un KIT esplode la distinta
    (kodice.vw_distinta) e mostra, per ogni componente, il costo certificato o l'eccezione
    (foglia OK vs foglia rotta). Se non e' un kit, mostra l'eccezione dell'articolo stesso."""
    item = (request.args.get("item") or "").strip()
    a = int(request.args.get("anno", 0)); m = int(request.args.get("mese", 0))
    comp = righe("""
        SELECT d.Component AS componente, it.Description AS descr, CAST(d.Qty AS DECIMAL(18,3)) AS qty,
               CAST(cam.Costo AS DECIMAL(18,4)) AS costo, cam.Completo AS completo,
               e.TipoEccezione AS ecc_tipo, e.Dettaglio AS ecc_dettaglio
        FROM kodice.vw_distinta d
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items it ON LTRIM(RTRIM(it.Item)) = LTRIM(RTRIM(d.Component))
        LEFT JOIN kodice.costi_articolo_mese cam ON LTRIM(RTRIM(cam.Item)) = LTRIM(RTRIM(d.Component)) AND cam.Anno = :a AND cam.Mese = :m
        LEFT JOIN kodice.costi_eccezioni e ON LTRIM(RTRIM(e.Item)) = LTRIM(RTRIM(d.Component)) AND e.Anno = :a AND e.Mese = :m AND e.Stato = 'APERTA'
        WHERE LTRIM(RTRIM(d.BOM)) = :it
        ORDER BY d.Component
    """, it=item, a=a, m=m)
    ecc = righe("""
        SELECT TipoEccezione AS tipo, ComponenteColpevole AS colpevole, Dettaglio AS dettaglio
        FROM kodice.costi_eccezioni
        WHERE LTRIM(RTRIM(Item)) = :it AND Anno = :a AND Mese = :m AND Stato = 'APERTA'
        ORDER BY CASE WHEN ComponenteColpevole = '' THEN 0 ELSE 1 END
    """, it=item, a=a, m=m)
    kit_ecc = next((e for e in ecc if not e["colpevole"]), None)
    return jsonify({"item": item, "is_kit": len(comp) > 0, "kit_eccezione": kit_ecc,
                    "eccezioni": ecc, "componenti": comp})


@app.get("/api/kit_giacenza")
def api_kit_giacenza():
    """KIT con giacenza a magazzino (DA VERIFICARE): un kit non dovrebbe essere a stock —
    a magazzino ci vanno i componenti, non l'assemblato. Kit = articolo presente come BOM
    in kodice.vw_distinta; giacenza dall'ultimo mese del ricalcolo kodice.wap_ricalc."""
    return jsonify(righe("""
        WITH kit AS (SELECT DISTINCT LTRIM(RTRIM(BOM)) AS Item FROM kodice.vw_distinta),
        ult AS (
            SELECT Item, QtaFin, WAPCost_ricalc FROM (
                SELECT Item, QtaFin, WAPCost_ricalc,
                       ROW_NUMBER() OVER (PARTITION BY Item ORDER BY Anno DESC, Mese DESC) rn
                FROM kodice.wap_ricalc
            ) t WHERE rn = 1
        )
        SELECT k.Item, it.Description AS descr,
               CAST(u.QtaFin AS DECIMAL(18,2)) AS giacenza,
               CAST(u.WAPCost_ricalc AS DECIMAL(18,4)) AS costo,
               (SELECT COUNT(*) FROM kodice.vw_distinta d WHERE LTRIM(RTRIM(d.BOM)) = k.Item) AS n_componenti
        FROM kit k
        JOIN ult u ON u.Item = k.Item
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items it ON LTRIM(RTRIM(it.Item)) = k.Item
        WHERE u.QtaFin > 0.001
        ORDER BY u.QtaFin DESC
    """))


# Catalogo degli oggetti SQL documentati. La definizione viene letta LIVE dal DB
# (OBJECT_DEFINITION per proc/viste; colonne per le tabelle), quindi non puo' divergere.
OGGETTI_SQL = [
    {"gruppo": "1 · Estrazione (Mago → src)", "nome": "dbo.usp_load_righe_vendita", "tipo": "proc",
     "spieg": "Estrae le righe di vendita VALIDE da Mago in src.righe_vendita, alla grana riga/articolo. "
              "Replica i filtri dell'estrattore Qlik (fatture +, note credito −, esclusi resi/sostituzioni/"
              "intercompany/VENAUTO/ProjectCode 3-4-5), applica il segno a ricavo e quantita', fa il TRIM dei "
              "codici, e SPALMA le spese di trasporto recuperate (righe SPESEDITRASPORTO + ShippingCharges) sui "
              "prodotti in proporzione all'imponibile, per confrontabilita' tra marketplace."},
    {"gruppo": "2 · Motore costi (kodice)", "nome": "kodice.vw_costo_sorgente", "tipo": "view",
     "spieg": "ADATTATORE azienda: normalizza la sorgente Mago (MA_ItemsWAP, riga totale Storage='') nel contratto "
              "atteso dal motore: Item, Anno, Mese, Costo. E' l'INPUT grezzo (prima di risalita/kit/bonifica)."},
    {"gruppo": "2 · Motore costi (kodice)", "nome": "kodice.vw_distinta", "tipo": "view",
     "spieg": "ADATTATORE azienda: distinta base dei kit da MA_BillOfMaterialsComp (BOM, Component, Qty), per "
              "l'esplosione dei kit nel motore."},
    {"gruppo": "2 · Motore costi (kodice)", "nome": "core.usp_prepara_costi", "tipo": "proc",
     "spieg": "MOTORE di costo (parametrico per schema azienda, idempotente sul mese). Legge vw_costo_sorgente + "
              "vw_distinta, risolve la RISALITA MESE (ultimo costo <= competenza), ESPLODE i kit ricorsivamente "
              "fino alle foglie, CERTIFICA i costi validi in costi_articolo_mese e manda le anomalie in "
              "costi_eccezioni (stato APERTA/RISOLTA). Aggiorna prep_controllo_mesi."},
    {"gruppo": "2 · Motore costi (kodice)", "nome": "kodice.costi_articolo_mese", "tipo": "table",
     "spieg": "OUTPUT CERTIFICATO del motore: un costo per (Item, Anno, Mese) con risalita e kit gia' risolti, "
              "flag Completo, MeseCostoUsato (mese effettivo del costo). E' la fonte del componente COSTO_VENDUTO."},
    {"gruppo": "2 · Motore costi (kodice)", "nome": "kodice.costi_eccezioni", "tipo": "table",
     "spieg": "REGISTRO ANOMALIE del motore: COSTO_MANCANTE / KIT_INCOMPLETO / COSTO_NON_VALIDO, con componente "
              "colpevole, dettaglio e ciclo di vita (APERTA/RISOLTA). E' la base della sezione Anomalie."},
    {"gruppo": "2 · Motore costi (kodice)", "nome": "kodice.prep_controllo_mesi", "tipo": "table",
     "spieg": "Stato di avanzamento del motore per mese: n. articoli/kit, eccezioni aperte, stato (PRONTO/CON_ANOMALIE)."},
    {"gruppo": "3 · Componenti", "nome": "dbo.usp_comp_COSTO_VENDUTO", "tipo": "proc",
     "spieg": "Componente di livello 1. Importo = quantita × costo CERTIFICATO dell'articolo per il mese "
              "(JOIN a kodice.costi_articolo_mese, codice trimmato). Scrive SOLO le proprie righe in "
              "core.componente_riga; gli articoli senza costo certificato non producono costo (sono anomalie)."},
    {"gruppo": "4 · Assemblaggio", "nome": "dbo.usp_build_fatto_riga", "tipo": "proc",
     "spieg": "Costruisce core.fatto_riga: somma i componenti per LIVELLO leggendo il registro cfg.componenti "
              "(dichiarativo) e calcola i margini cumulativi MdC I/II/III. Aggiungere un componente non richiede "
              "di toccare questa procedura."},
    {"gruppo": "5 · Presentazione (viste per i consumatori)", "nome": "pres.conto_economico_riga", "tipo": "view",
     "spieg": "Vista per Qlik: una riga per riga-documento con ricavo, MdC I/II/III, MdC1% e tipo_articolo."},
    {"gruppo": "5 · Presentazione (viste per i consumatori)", "nome": "pres.componente_riga", "tipo": "view",
     "spieg": "Dettaglio elementare per componente (importo + origine), per ispezione/correzione di una voce."},
    {"gruppo": "5 · Presentazione (viste per i consumatori)", "nome": "pres.controllo_componenti", "tipo": "view",
     "spieg": "Sintesi per componente (n. righe, totale, range): per validare ogni voce senza guardare le altre."},
    {"gruppo": "6 · Valorizzazione magazzino (ricalcolo WAP)", "nome": "kodice.wap_ricalc", "tipo": "table",
     "spieg": "RICALCOLO PARALLELO del WAP (NON tocca MA_ItemsWAP di Mago). Una riga per (Item, Anno, Mese): "
              "ricostruisce il costo medio ponderato di periodo mese per mese con roll-forward. MA_ItemsWAP di Mago "
              "ha spesso QUANTITA' sbagliate (va negativa -> il costo crolla a 0); qui apertura = giacenza fisica reale "
              "(KLProgUbicazioni ATRI + MA_ItemsBalances depositi <>ATRI) valorizzata al WAPCost di risalita, poi "
              "carichi/scarichi dai movimenti. Tiene DUE BUCKET ValPuro/ValOneri: lo split costo d'acquisto vs ONERI "
              "ACCESSORI (dazi/import, causali AGGDAZI/IMPORT) somma sempre al WAPCost. Colonne WAPCost_Mago/Delta = controllo."},
    {"gruppo": "6 · Valorizzazione magazzino (ricalcolo WAP)", "nome": "kodice.usp_ricalc_wap", "tipo": "proc",
     "spieg": "Motore del ricalcolo (parametrico @Anno, @MeseMax). Costruisce il seed (giacenza/costo d'apertura) e "
              "cicla i mesi: per ogni mese media ponderata di periodo = (val.iniziale + acquisti) / (qta iniziale + "
              "acquisti), separando puro e oneri; il mese successivo parte dai valori di fine. Movimenti classificati "
              "per WAPMovementType (acquisti/vendite/resi; trasferimenti e 'ignora' esclusi). Gli articoli in "
              "kodice.articoli_esclusi_costo (voci di servizio) sono esclusi dal ricalcolo."},
    {"gruppo": "6 · Valorizzazione magazzino (ricalcolo WAP)", "nome": "kodice.articoli_esclusi_costo", "tipo": "table",
     "spieg": "Codici di SERVIZIO/non-prodotto da NON valorizzare (es. SPESEDITRASPORTO = 'Spese di trasporto'): hanno "
              "movimenti che generano quantita' negative e non sono merce. Esclusi dal ricalcolo WAP, quindi anche da "
              "vw_costo_eff, vw_qualita_costo e dal report inventario. Per escluderne altri si aggiunge una riga qui."},
    {"gruppo": "6 · Valorizzazione magazzino (ricalcolo WAP)", "nome": "kodice.vw_costo_eff", "tipo": "view",
     "spieg": "COSTO UNITARIO EFFICACE per articolo, scelto col METODO DI MAGO in base al ValuationType "
              "(11272206 = MPP/WAP, 11272194 = MEDIO annuale). MPP: ultimo costo del ricalcolo ad Aprile 2026 "
              "(RICALCOLO_APR, con split puro/oneri) -> altrimenti ultimo WAPCost>0 storico (RISALITA_WAP, la stessa "
              "risalita del report Mago). MEDIO: media ANNUALE 'alla Mago' = (apertura + acquisti PURI)/(qta totale), "
              "SENZA oneri (il medio non e' salvato in Mago, si ricalcola) -> ripiego MA_ItemsBalances.LastCost. "
              "Regola 'il costo sopravvive a giacenza 0': si cerca sempre l'ultimo costo>0. Usata per valorizzare "
              "l'inventario di bilancio (src/genera_report_inventario.py): i kit con la distinta esplosa sui costi "
              "efficaci; i pochi articoli senza alcun costo SQL (imballaggi interni EPAL) col prezzo del report Mago; "
              "dove il nostro costo indipendente diverge molto dal prezzo Mago la riga e' segnalata come prezzo sospetto."},
    {"gruppo": "7 · Qualita' del dato", "nome": "kodice.vw_qualita_costo", "tipo": "view",
     "spieg": "INDICI DI QUALITA' del costo per (Item,Anno,Mese): calcola i flag (Q1 scostamento vs WAP Mago, "
              "Q2 WAP Mago azzerato=informativo, Q3 oneri spariti, Q4 valuta non convertita, Q5 salto costo MoM, "
              "Q6 acquisto a prezzo anomalo, Q9 quantita' negativa, Q11 causale non mappata), assegna un LIVELLO "
              "ROSSO (bloccante: Q4/Q9) / GIALLO (da rivedere) / VERDE, e lo stato di certificazione. Alimenta la "
              "tab 'Certificazione costi' (scorecard + indice qualita' = % valore magazzino VERDE o certificato)."},
    {"gruppo": "7 · Qualita' del dato", "nome": "kodice.qualita_certificazione", "tipo": "table",
     "spieg": "Tabella di STATO della certificazione (l'unica scrivibile dalla dashboard): per (Item,Anno,Mese) il "
              "revisore marca CERTIFICATO / ACCETTATO_CON_NOTA / DA_CORREGGERE_ALGORITMO / IGNORATO, con nota, utente "
              "e data. La vista vw_qualita_costo la legge per mostrare cosa e' ancora aperto."},
]


def _ddl_tabella(c, nome_qualificato: str) -> str:
    schema, tab = nome_qualificato.split(".")
    cols = c.execute(text("""
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=:s AND TABLE_NAME=:t ORDER BY ORDINAL_POSITION
    """), {"s": schema, "t": tab}).fetchall()
    out = [f"CREATE TABLE {nome_qualificato} ("]
    defs = []
    for cn, dt, ln, pr, sc, nul in cols:
        tipo = dt
        if ln is not None:
            tipo = f"{dt}({'MAX' if ln == -1 else ln})"
        elif dt in ("decimal", "numeric"):
            tipo = f"{dt}({pr},{sc})"
        defs.append(f"    {cn} {tipo} {'NULL' if nul == 'YES' else 'NOT NULL'}")
    out.append(",\n".join(defs))
    out.append(");")
    return "\n".join(out)


@app.get("/api/qualita")
def api_qualita():
    """Indici di qualita' del costo (kodice.vw_qualita_costo) per il periodo + scorecard."""
    anno = int(request.args.get("anno"))
    mese = int(request.args.get("mese"))
    score = righe("SELECT Livello, COUNT(*) AS n FROM kodice.vw_qualita_costo "
                  "WHERE Anno=:a AND Mese=:m GROUP BY Livello", a=anno, m=mese)
    # indice sintetico = % del VALORE di magazzino (QtaFin*costo) gia' VERDE o certificato
    val = righe("""
        SELECT SUM(ISNULL(QtaFin,0)*ISNULL(WAPCost_ricalc,0)) AS val_tot,
               SUM(CASE WHEN Livello='VERDE' OR Stato IN ('CERTIFICATO','ACCETTATO_CON_NOTA')
                        THEN ISNULL(QtaFin,0)*ISNULL(WAPCost_ricalc,0) ELSE 0 END) AS val_ok
        FROM kodice.vw_qualita_costo WHERE Anno=:a AND Mese=:m AND QtaFin>0""", a=anno, m=mese)
    lista = righe("""
        SELECT q.Item, i.Description AS descr, q.Livello, q.Flags,
               q.WAPCost_ricalc, q.WAPCost_Mago, q.QtaFin, q.OneriUnit, q.Stato, q.Nota
        FROM kodice.vw_qualita_costo q
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items i ON LTRIM(RTRIM(i.Item)) = q.Item
        WHERE q.Anno=:a AND q.Mese=:m AND q.Livello <> 'VERDE'
        ORDER BY CASE q.Livello WHEN 'ROSSO' THEN 0 ELSE 1 END, q.Item""", a=anno, m=mese)
    return jsonify({"score": score, "valore": (val[0] if val else {}), "lista": lista})


@app.post("/api/certifica")
def api_certifica():
    """Scrive lo stato di certificazione (unica scrittura della dashboard) su kodice.qualita_certificazione."""
    d = request.get_json(force=True)
    p = {"item": d["item"], "anno": int(d["anno"]), "mese": int(d["mese"]),
         "stato": d["stato"], "nota": d.get("nota", ""), "utente": d.get("utente", "dashboard")}
    if p["stato"] in ("", "DA_CERTIFICARE"):   # "riapri": elimina lo stato
        with engine.begin() as c:
            c.execute(text("DELETE FROM kodice.qualita_certificazione "
                           "WHERE Item=:item AND Anno=:anno AND Mese=:mese"), p)
        return jsonify({"ok": True, "riaperto": True})
    with engine.begin() as c:
        c.execute(text("""
            MERGE kodice.qualita_certificazione AS t
            USING (SELECT :item AS Item, :anno AS Anno, :mese AS Mese) AS s
              ON t.Item=s.Item AND t.Anno=s.Anno AND t.Mese=s.Mese
            WHEN MATCHED THEN UPDATE SET Stato=:stato, Nota=:nota, Utente=:utente, DataStato=SYSDATETIME()
            WHEN NOT MATCHED THEN INSERT (Item,Anno,Mese,Stato,Nota,Utente,DataStato)
                 VALUES (:item,:anno,:mese,:stato,:nota,:utente,SYSDATETIME());"""), p)
    return jsonify({"ok": True})


@app.get("/api/costo_dettaglio")
def api_costo_dettaglio():
    """Scheda 'come si forma il costo' di un articolo: roll mensile del WAP + movimenti (acquisti, valuta/cambio, oneri, rettifiche)."""
    item = request.args.get("item")
    anno = int(request.args.get("anno", 2026))
    roll = righe("""
        SELECT Mese, QtaIniz, ValPuroIniz, ValOneriIniz, QtaAcq, ValAcqPuro, ValAcqOneri,
               QtaVend, QtaResi, QtaRettTrasf, QtaFin, PuroUnit, OneriUnit, WAPCost_ricalc, WAPCost_Mago
        FROM kodice.wap_ricalc WHERE Item=:i AND Anno=:a ORDER BY Mese""", i=item, a=anno)
    eff = righe("SELECT Fonte, CostoEff, PuroUnit, OneriUnit, ValuationType FROM kodice.vw_costo_eff WHERE Item=:i", i=item)
    # Fornitore preferenziale: SOLO anagrafica di tipo FORNITORE (in MA_CustSupp ci sono anche i clienti;
    # alcuni codici, es. 9998, esistono con entrambi i tipi). CustSuppType 3211265 = fornitore, 3211264 = cliente.
    # + costo di RIACQUISTO ad oggi = StandardPrice del fornitore preferenziale (MA_ItemSuppliers,
    #   chiavi Item+Supplier), NETTO sconti: Mago collassa la formula sconti in Discount1+Discount2
    #   (es. "60+25+5" -> D1=60, D2=28.75), quindi netto = StandardPrice*(1-D1/100)*(1-D2/100).
    #   E' in valuta del fornitore (colonna Currency, spesso USD): mostrato con la valuta, non convertito.
    forn = righe("""
        SELECT TOP 1 LTRIM(RTRIM(g.Supplier)) AS Supplier, cs.CompanyName,
               isup.StandardPrice AS riacquisto_lordo,
               isup.StandardPrice * (1 - ISNULL(isup.Discount1,0)/100.0) * (1 - ISNULL(isup.Discount2,0)/100.0) AS riacquisto,
               LTRIM(RTRIM(isup.DiscountFormula)) AS riacquisto_sconti,
               LTRIM(RTRIM(isup.Currency)) AS riacquisto_valuta,
               LTRIM(RTRIM(isup.SupplierCode)) AS cod_fornitore,
               -- netto sconti convertito in EUR col cambio BCE corrente (per le valute estere)
               CASE WHEN isup.Currency IS NULL OR LTRIM(RTRIM(isup.Currency)) IN ('','EUR')
                     THEN isup.StandardPrice * (1 - ISNULL(isup.Discount1,0)/100.0) * (1 - ISNULL(isup.Discount2,0)/100.0)
                    WHEN cv.CambioPerEur > 0
                     THEN isup.StandardPrice * (1 - ISNULL(isup.Discount1,0)/100.0) * (1 - ISNULL(isup.Discount2,0)/100.0) / cv.CambioPerEur
                    ELSE NULL END AS riacquisto_eur,
               CONVERT(varchar(10), cv.Data, 103) AS cambio_data
        FROM KODICEBAGNO_4.dbo.MA_ItemsGoodsData g
        LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSupp cs ON cs.CustSupp = g.Supplier AND cs.CustSuppType = 3211265
        LEFT JOIN KODICEBAGNO_4.dbo.MA_ItemSuppliers isup
               ON LTRIM(RTRIM(isup.Item)) = LTRIM(RTRIM(g.Item)) AND LTRIM(RTRIM(isup.Supplier)) = LTRIM(RTRIM(g.Supplier))
        LEFT JOIN kodice.vw_cambio_corrente cv ON cv.Valuta = LTRIM(RTRIM(isup.Currency))
        WHERE LTRIM(RTRIM(g.Item)) = :i AND g.Supplier IS NOT NULL AND g.Supplier <> ''""", i=item)
    # Distinta esplosa: se l'articolo e' un KIT non ha WAP/movimenti propri, il costo nasce dai componenti.
    # Costo del componente = NOSTRO ricalcolo (wap_ricalc) dell'anno della scheda, ultimo mese con costo>0
    # (es. scheda 2025 -> chiusura ricalcolata 2025: e' il valore giusto per l'APERTURA del 2026).
    # Ripiego sul COSTO EFFICACE (vw_costo_eff) se per quell'anno non c'e' ricalcolo.
    kit = righe("""
        SELECT LTRIM(RTRIM(b.Component)) AS Item, i.Description AS descr, b.Qty,
               COALESCE(wr.WAPCost_ricalc, ce.CostoEff) AS costo, wr.Mese AS mese_costo,
               CAST(CASE WHEN wr.WAPCost_ricalc IS NULL AND ce.CostoEff IS NOT NULL THEN 1 ELSE 0 END AS bit) AS da_efficace
        FROM KODICEBAGNO_4.dbo.MA_BillOfMaterialsComp b
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items i ON LTRIM(RTRIM(i.Item)) = LTRIM(RTRIM(b.Component))
        LEFT JOIN kodice.vw_costo_eff ce ON LTRIM(RTRIM(ce.Item)) = LTRIM(RTRIM(b.Component))
        OUTER APPLY (SELECT TOP 1 WAPCost_ricalc, Mese FROM kodice.wap_ricalc
                     WHERE LTRIM(RTRIM(Item)) = LTRIM(RTRIM(b.Component)) AND Anno = :a AND WAPCost_ricalc > 0
                     ORDER BY Mese DESC) wr
        WHERE LTRIM(RTRIM(b.BOM)) = :i
        ORDER BY b.Component""", i=item, a=anno)
    mov = righe("""
        SELECT MONTH(h.PostingDate) AS Mese, h.InvRsn, h.WAPMovementType, h.Currency, h.Fixing,
               SUM(d.Qty) AS qty, SUM(d.LineAmount) AS lineamt,
               SUM(d.LineAmount * CASE WHEN h.Currency NOT IN ('','EUR') AND h.Fixing > 0 THEN h.Fixing ELSE 1 END) AS eur
        FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = d.EntryId
        WHERE LTRIM(RTRIM(d.Item)) = :i AND YEAR(h.PostingDate) = :a
        GROUP BY MONTH(h.PostingDate), h.InvRsn, h.WAPMovementType, h.Currency, h.Fixing
        ORDER BY MONTH(h.PostingDate), h.InvRsn""", i=item, a=anno)
    # Indicatori di qualita' dell'ultimo mese disponibile (per mostrarli in testata coi numeri che li generano).
    qual = righe("""
        SELECT TOP 1 Mese, Livello, Flags, PuroUnit, OneriUnit, WAPCost_ricalc, WAPCost_Mago, RiacquistoEur,
               PuroAcqUnit, Q1_scost_mago, Q12_scost_riacq
        FROM kodice.vw_qualita_costo WHERE Item = :i AND Anno = :a ORDER BY Mese DESC""", i=item, a=anno)
    return jsonify({"roll": roll, "eff": (eff[0] if eff else None), "mov": mov,
                    "fornitore": (forn[0] if forn else None), "kit": kit,
                    "qualita": (qual[0] if qual else None)})


@app.get("/api/quadratura_materiale")
def api_quadratura_materiale():
    """QUADRATURA CONTABILE del materiale per il periodo: Acquisti (contabilita' generale, conti
    mappati in kodice.conti_quadratura) + Rimanenze iniziali - Rimanenze finali (nostra valorizzazione
    wap_ricalc) = Consumo contabile, da confrontare col nostro Sigma costo del venduto (core.fatto_riga)."""
    anno = int(request.args.get("anno", 2026))
    mda = int(request.args.get("mese_da", 1))
    mmax = righe("SELECT MAX(mese) AS m FROM core.fatto_riga WHERE anno=:a", a=anno)
    ma = int(request.args.get("mese_a") or (mmax[0]["m"] if mmax and mmax[0]["m"] else 12))
    dett = righe("""
        SELECT q.Account, q.Ruolo, q.Nota,
               ROUND(SUM(CASE WHEN g.DebitCreditSign=4980736 THEN g.Amount ELSE -g.Amount END),2) AS importo
        FROM kodice.conti_quadratura q
        JOIN KODICEBAGNO_4.dbo.MA_JournalEntriesGLDetail g ON g.Account = q.Account
        WHERE q.Componente='MATERIALE' AND YEAR(g.AccrualDate)=:a AND MONTH(g.AccrualDate) BETWEEN :mda AND :ma
        GROUP BY q.Account, q.Ruolo, q.Nota ORDER BY q.Account""", a=anno, mda=mda, ma=ma)
    # Rimanenze NOSTRE (valorizzazione wap_ricalc) a inizio e fine periodo
    rim = righe("""
        SELECT (SELECT SUM(ValPuroIniz+ValOneriIniz) FROM kodice.wap_ricalc WHERE Anno=:a AND Mese=:mda) AS rim_iniz,
               (SELECT SUM(ValPuroFin+ValOneriFin)   FROM kodice.wap_ricalc WHERE Anno=:a AND Mese=:ma)  AS rim_fin
    """, a=anno, mda=mda, ma=ma)
    # Rimanenze CONTABILI (saldi di bilancio MA_ChartOfAccountsBalances, conti RIMANENZE):
    #   apertura = BalanceType 3145728; saldo a fine mese = apertura + movimenti progressivi (3145730) fino al mese.
    rim_c = righe("""
        SELECT
          (SELECT ISNULL(SUM(b.Debit-b.Credit),0) FROM KODICEBAGNO_4.dbo.MA_ChartOfAccountsBalances b
             JOIN kodice.conti_quadratura q ON q.Account=b.Account AND q.Componente='MATERIALE' AND q.Ruolo='RIMANENZE'
            WHERE b.FiscalYear=:a AND b.BalanceType=3145728) AS rim_iniz,
          (SELECT ISNULL(SUM(b.Debit-b.Credit),0) FROM KODICEBAGNO_4.dbo.MA_ChartOfAccountsBalances b
             JOIN kodice.conti_quadratura q ON q.Account=b.Account AND q.Componente='MATERIALE' AND q.Ruolo='RIMANENZE'
            WHERE b.FiscalYear=:a AND (b.BalanceType=3145728 OR (b.BalanceType=3145730 AND b.BalanceMonth BETWEEN 1 AND :ma))) AS rim_fin
    """, a=anno, ma=ma)
    cogs = righe("SELECT SUM(ricavo_netto-mdc1) AS cogs FROM core.fatto_riga WHERE anno=:a AND mese BETWEEN :mda AND :ma",
                 a=anno, mda=mda, ma=ma)
    fnum = lambda v: float(v or 0)
    acq = sum(fnum(r["importo"]) for r in dett if r["Ruolo"] == "ACQUISTO")
    oneri = sum(fnum(r["importo"]) for r in dett if r["Ruolo"] == "ONERE_ACQUISTO")
    rin_n = fnum(rim[0]["rim_iniz"] if rim else 0); rfin_n = fnum(rim[0]["rim_fin"] if rim else 0)
    rin_c = fnum(rim_c[0]["rim_iniz"] if rim_c else 0); rfin_c = fnum(rim_c[0]["rim_fin"] if rim_c else 0)
    nostro = fnum(cogs[0]["cogs"] if cogs else 0)
    consumo = acq + oneri + rin_c - rfin_c   # consumo CONTABILE (rimanenze a bilancio)
    return jsonify({"anno": anno, "mese_da": mda, "mese_a": ma,
                    "acquisti": acq, "oneri": oneri,
                    "rim_iniz_nostra": rin_n, "rim_fin_nostra": rfin_n,
                    "rim_iniz_cont": rin_c, "rim_fin_cont": rfin_c,
                    "consumo": consumo, "nostro": nostro, "delta": consumo - nostro,
                    "dettaglio": [r for r in dett if r["Ruolo"] != "RIMANENZE"]})


def _ric_periodo():
    anno = int(request.args.get("anno", 2026))
    mda = int(request.args.get("mese_da", 1))
    mmax = righe("SELECT MAX(mese) AS m FROM core.fatto_riga WHERE anno=:a", a=anno)
    ma = int(request.args.get("mese_a") or (mmax[0]["m"] if mmax and mmax[0]["m"] else 12))
    return anno, mda, ma


def _fine_periodo(anno, ma):
    """Ultimo giorno del mese di chiusura del periodo (per la competenza degli ordini non spediti)."""
    return datetime.date(anno, ma, calendar.monthrange(anno, ma)[1])


# Ordini FATTURATI NON SPEDITI alla chiusura del periodo (competenza). Logica e razionale in
# sql/verifiche/ordini_non_evasi.sql. Oracolo = VwKLStatoOrdini (CompletamenteConsegnato='No'); per
# competenza si aggiungono gli ordini ricevuti entro la chiusura ma spediti DOPO (DataSpedizione>fine);
# si tengono solo gli ordini con FATTURA collegata (B2C: NrOrdine=stringa SaleDocId; B2B: ordine->fattura
# via MA_CrossReferences, anche col doppio salto via DDT), escludendo gli ordini non ancora fatturati.
_ORD_NON_SPEDITI_FROM = """
  FROM kodice.vw_ordini_non_evasi v
  WHERE v.DataOrdine <= :fine
    AND (v.CompletamenteConsegnato = 'No' OR v.DataSpedizione > :fine)
    AND ( EXISTS(SELECT 1 FROM KODICEBAGNO_4.dbo.MA_SaleDoc d
                 WHERE CAST(d.SaleDocId AS varchar(21)) = v.NrOrdine AND d.DocumentType IN (3407878,3407874))
       OR EXISTS(SELECT 1 FROM KODICEBAGNO_4.dbo.MA_CrossReferences x
                 JOIN KODICEBAGNO_4.dbo.MA_SaleDoc d ON d.SaleDocId = x.DerivedDocID AND d.DocumentType IN (3407878,3407874)
                 WHERE x.OriginDocID = v.IdOrdine)
       OR EXISTS(SELECT 1 FROM KODICEBAGNO_4.dbo.MA_CrossReferences x1
                 JOIN KODICEBAGNO_4.dbo.MA_CrossReferences x2 ON x2.OriginDocID = x1.DerivedDocID
                 JOIN KODICEBAGNO_4.dbo.MA_SaleDoc d ON d.SaleDocId = x2.DerivedDocID AND d.DocumentType IN (3407878,3407874)
                 WHERE x1.OriginDocID = v.IdOrdine) )
"""


# Legame carico merce -> bolla -> fattura (con AccrualDate) MATERIALIZZATO in kodice.carico_fattura
# (proc kodice.usp_build_carico_fattura, file sql/verifiche/link_documenti.sql): cosi' l'endpoint non rifa'
# il grafo CrossReferences a ogni caricamento. Colonne: MovEntryId, MovDate, ValPuro (carico merce prodotti),
# FattId, FattAccrual (competenza contabile della fattura, NULL = nessuna fattura). Bucket per FattAccrual.

# Giroconti/scritture sui conti materiale SENZA documento dietro (la fattura giustifica il conto; queste no):
# registrazioni su 06011000/oneri il cui movimento contabile non si lega ad alcun MA_PurchaseDoc.
# Solo conti MERCE (Ruolo='ACQUISTO': 06011000/02): qui "nessun MA_PurchaseDoc dietro" = davvero giroconto manuale.
# Gli oneri (trasporti/dazi) sono spesso registrati con doc NON-acquisto -> li lasciamo a oneri/valutazione residua.
_GL_NODOC_WHERE = """q.Componente='MATERIALE' AND q.Ruolo='ACQUISTO'
          AND YEAR(g.AccrualDate)=:a AND MONTH(g.AccrualDate) BETWEEN :d AND :h AND pd.PurchaseDocId IS NULL"""


def _ric_acq(anno, mda, ma):
    """Scompone la competenza acquisti (nostro carico merce vs acquisti registrati in contabilita') in voci
    parlanti: ricevuto-non-fatturato (fattura dopo il periodo / assente), fatturato in periodo precedente,
    giroconti senza documento. Vedi _ACQ_CTE."""
    fn = lambda v: float(v or 0)
    ini = datetime.date(anno, mda, 1); fin1 = _fine_periodo(anno, ma) + datetime.timedelta(days=1)
    b = righe("""
        SELECT ISNULL(SUM(CASE WHEN FattAccrual IS NULL THEN ValPuro END),0) nofatt,
               ISNULL(SUM(CASE WHEN FattAccrual < :ini THEN ValPuro END),0) prec,
               ISNULL(SUM(CASE WHEN FattAccrual >= :fin1 THEN ValPuro END),0) dopo
        FROM kodice.carico_fattura
        WHERE Anno=:a AND MovDate>=:ini AND MovDate<:fin1""", a=anno, ini=ini, fin1=fin1)[0]
    glnodoc = fn(righe("""SELECT ISNULL(SUM(CASE WHEN g.DebitCreditSign=4980736 THEN g.Amount ELSE -g.Amount END),0) v
        FROM kodice.conti_quadratura q
        JOIN KODICEBAGNO_4.dbo.MA_JournalEntriesGLDetail g ON g.Account=q.Account
        JOIN KODICEBAGNO_4.dbo.MA_JournalEntries je ON je.JournalEntryId=g.JournalEntryId
        LEFT JOIN KODICEBAGNO_4.dbo.MA_PurchaseDoc pd ON pd.PurchaseDocId=je.CRRefID
        WHERE """ + _GL_NODOC_WHERE + " OPTION (MAXDOP 1)", a=anno, d=mda, h=ma)[0]["v"])
    return {"prec": fn(b["prec"]), "dopo": fn(b["dopo"]), "nofatt": fn(b["nofatt"]), "glnodoc": glnodoc}


@app.get("/api/riconciliazione_cogs")
def api_riconciliazione_cogs():
    """Prospetto di riconciliazione: dal costo del venduto CDG (abbinato al fatturato) al CONSUMO materie
    a bilancio (Acquisti GL +/- variazione rimanenze), esplicitando ogni componente (sfasamento spedizione-
    fattura, rettifiche, resi, ricevuto-non-fatturato, drift apertura). Identita' garantita per costruzione."""
    anno, mda, ma = _ric_periodo()
    fn = lambda v: float(v or 0)
    # SOLO PRODOTTI: gli imballaggi (ItemType 997) entrano a magazzino ma in contabilita' sono costo, non merce.
    # La riconciliazione del COGS si fa prodotti-contro-prodotti (vedi kodice.vw_classe_articolo).
    cogs = fn(righe("""SELECT SUM(f.ricavo_netto-f.mdc1) v FROM core.fatto_riga f
                    LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=f.codice_articolo
                    WHERE f.anno=:a AND f.mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""",
                    a=anno, d=mda, h=ma)[0]["v"])
    w = righe("""SELECT SUM(w.QtaVend*w.WAPCost_ricalc) vend, SUM(w.QtaResi*w.WAPCost_ricalc) resi,
                        SUM(w.QtaRettTrasf*w.WAPCost_ricalc) rett, SUM(w.ValAcqPuro+w.ValAcqOneri) acq,
                        SUM(w.QtaTrasfFBA*w.WAPCost_ricalc) trasf
                 FROM kodice.wap_ricalc w LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item
                 WHERE w.Anno=:a AND w.Mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""", a=anno, d=mda, h=ma)[0]
    vend = fn(w["vend"]); resi = fn(w["resi"]); rett = fn(w["rett"]); acq = fn(w["acq"]); trasf_fba = fn(w["trasf"])
    # Rettifiche scomposte in POSITIVE (rientri/aumenti) e NEGATIVE (consumi: imballaggi/perdite), dai 507 grezzi
    # valorizzati come la riga (WAPCost_ricalc). rett_p+rett_n riproducono il netto rett (P − N).
    pn = righe("""SELECT
        ISNULL(SUM(CASE WHEN h.InvRsn IN ('KLRI-P-A','RI-POS') THEN d.Qty*ISNULL(wr.WAPCost_ricalc,0) ELSE 0 END),0) p,
        ISNULL(SUM(CASE WHEN h.InvRsn IN ('KLRI-N-A','RI-NEG','KLR-FORA') THEN d.Qty*ISNULL(wr.WAPCost_ricalc,0) ELSE 0 END),0) n
        FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId=d.EntryId
        LEFT JOIN kodice.wap_ricalc wr ON wr.Item=LTRIM(RTRIM(d.Item)) AND wr.Anno=:a AND wr.Mese=MONTH(h.PostingDate)
        LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
        WHERE h.WAPMovementType=2032533507 AND h.InvRsn IN ('KLRI-P-A','RI-POS','KLRI-N-A','RI-NEG','KLR-FORA')
          AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO' AND h.CancelPhase1='0' AND h.CancelPhase2='0'
          AND YEAR(h.PostingDate)=:a AND MONTH(h.PostingDate) BETWEEN :d AND :h""", a=anno, d=mda, h=ma)[0]
    rett_p = fn(pn["p"]); rett_n = fn(pn["n"])
    rin_n = fn(righe("""SELECT SUM(w.ValPuroIniz+w.ValOneriIniz) v FROM kodice.wap_ricalc w
        LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item
        WHERE w.Anno=:a AND w.Mese=:d AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""", a=anno, d=mda)[0]["v"])
    rfin_n = fn(righe("""SELECT SUM(w.ValPuroFin+w.ValOneriFin) v FROM kodice.wap_ricalc w
        LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item
        WHERE w.Anno=:a AND w.Mese=:h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""", a=anno, h=ma)[0]["v"])
    gl_acq = fn(righe("""SELECT ISNULL(SUM(CASE WHEN g.DebitCreditSign=4980736 THEN g.Amount ELSE -g.Amount END),0) v
        FROM kodice.conti_quadratura q JOIN KODICEBAGNO_4.dbo.MA_JournalEntriesGLDetail g ON g.Account=q.Account
        WHERE q.Componente='MATERIALE' AND q.Ruolo IN ('ACQUISTO','ONERE_ACQUISTO')
          AND YEAR(g.AccrualDate)=:a AND MONTH(g.AccrualDate) BETWEEN :d AND :h""", a=anno, d=mda, h=ma)[0]["v"])
    rb = righe("""SELECT
        (SELECT ISNULL(SUM(b.Debit-b.Credit),0) FROM KODICEBAGNO_4.dbo.MA_ChartOfAccountsBalances b
           JOIN kodice.conti_quadratura q ON q.Account=b.Account AND q.Componente='MATERIALE' AND q.Ruolo='RIMANENZE'
          WHERE b.FiscalYear=:a AND b.BalanceType=3145728) ini,
        (SELECT ISNULL(SUM(b.Debit-b.Credit),0) FROM KODICEBAGNO_4.dbo.MA_ChartOfAccountsBalances b
           JOIN kodice.conti_quadratura q ON q.Account=b.Account AND q.Componente='MATERIALE' AND q.Ruolo='RIMANENZE'
          WHERE b.FiscalYear=:a AND (b.BalanceType=3145728 OR (b.BalanceType=3145730 AND b.BalanceMonth BETWEEN 1 AND :h))) fin
    """, a=anno, h=ma)[0]
    rin_b = fn(rb["ini"]); rfin_b = fn(rb["fin"])
    # Rimanenze IMBALLAGGI dal NOSTRO ricalcolo (il bilancio non separa prodotti/imballaggi: il conto rimanenze e'
    # unico). Le scorporo dal saldo contabile -> il confronto resta prodotti-contro-prodotti e la differenza di
    # valutazione finisce TUTTA sui prodotti (come da indicazione). + carico imballaggi per il pannello separato.
    imb = righe("""SELECT
        ISNULL((SELECT SUM(w.ValPuroIniz+w.ValOneriIniz) FROM kodice.wap_ricalc w JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item AND ca.Classe='IMBALLAGGIO' WHERE w.Anno=:a AND w.Mese=:d),0) ini,
        ISNULL((SELECT SUM(w.ValPuroFin+w.ValOneriFin) FROM kodice.wap_ricalc w JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item AND ca.Classe='IMBALLAGGIO' WHERE w.Anno=:a AND w.Mese=:h),0) fin,
        ISNULL((SELECT SUM(w.ValAcqPuro+w.ValAcqOneri) FROM kodice.wap_ricalc w JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item AND ca.Classe='IMBALLAGGIO' WHERE w.Anno=:a AND w.Mese BETWEEN :d AND :h),0) carico
        """, a=anno, d=mda, h=ma)[0]
    imb_iniz = fn(imb["ini"]); imb_fin = fn(imb["fin"]); imb_carico = fn(imb["carico"])
    rin_b = rin_b - imb_iniz; rfin_b = rfin_b - imb_fin       # rimanenze contabili SOLO PRODOTTI
    # Scomposizione della "competenza acquisti" (gl_acq − acq) in voci parlanti (vedi _ric_acq), SOLO PRODOTTI:
    acq_oneri = fn(righe("""SELECT SUM(w.ValAcqOneri) v FROM kodice.wap_ricalc w
        LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item
        WHERE w.Anno=:a AND w.Mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""", a=anno, d=mda, h=ma)[0]["v"])
    gl_oneri = fn(righe("""SELECT ISNULL(SUM(CASE WHEN g.DebitCreditSign=4980736 THEN g.Amount ELSE -g.Amount END),0) v
        FROM kodice.conti_quadratura q JOIN KODICEBAGNO_4.dbo.MA_JournalEntriesGLDetail g ON g.Account=q.Account
        WHERE q.Componente='MATERIALE' AND q.Ruolo='ONERE_ACQUISTO'
          AND YEAR(g.AccrualDate)=:a AND MONTH(g.AccrualDate) BETWEEN :d AND :h""", a=anno, d=mda, h=ma)[0]["v"])
    ab = _ric_acq(anno, mda, ma)
    ricnf_tot = gl_acq - acq                                   # totale competenza acquisti = −(acq − gl_acq)
    oneri_contrib = gl_oneri - acq_oneri                       # oneri: registrati in GL − nostro carico
    consumo_fisico = rin_n + acq - rfin_n
    consumo_bil = gl_acq + rin_b - rfin_b
    # Scomposizione dello sfasamento spedizione/fattura dal LINK materializzato (kodice.vendite_link):
    #  - apertura: spedito nel periodo ma fatturato PRIMA (arretrato d'inizio anno lavorato)
    #  - residuo : spedito su DDT/ordine ma fattura non (ancora) emessa nel periodo (differita B2B in attesa)
    #  - sost    : sostituzioni gratuite (DDT senza fattura): COSTO del venduto a ricavo 0 = perdita
    _ini = datetime.date(anno, mda, 1)
    _fin1 = _fine_periodo(anno, ma) + datetime.timedelta(days=1)
    vlb = righe("""SELECT
        ISNULL(SUM(CASE WHEN vl.Modo NOT IN ('SOSTITUZIONE','SOLO_ORDINE','NESSUN_ORDINE') AND vl.InternalOrdNo NOT LIKE '1300%' AND vl.FatturaDate < :ini
                        THEN d.Qty*ISNULL(wr.WAPCost_ricalc,0) ELSE 0 END),0) apertura,
        ISNULL(SUM(CASE WHEN vl.Modo IN ('SOLO_ORDINE','NESSUN_ORDINE') AND vl.InternalOrdNo NOT LIKE '1300%' THEN d.Qty*ISNULL(wr.WAPCost_ricalc,0) ELSE 0 END),0) residuo,
        ISNULL(SUM(CASE WHEN vl.Modo='SOSTITUZIONE' OR vl.InternalOrdNo LIKE '1300%' THEN d.Qty*ISNULL(wr.WAPCost_ricalc,0) ELSE 0 END),0) sost
        FROM kodice.vendite_link vl
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d ON d.EntryId=vl.MovEntryId
        LEFT JOIN kodice.wap_ricalc wr ON wr.Item=LTRIM(RTRIM(d.Item)) AND wr.Anno=:a AND wr.Mese=MONTH(vl.MovDate)
        LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
        WHERE vl.MovDate>=:ini AND vl.MovDate<:fin1 AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""", a=anno, ini=_ini, fin1=_fin1)[0]
    sped_ap = fn(vlb["apertura"]); sped_res = fn(vlb["residuo"]); sped_sost = fn(vlb["sost"])
    r = lambda x: round(x, 2)
    # NESSUN PLUG: ogni componente e' MISURATA da fonte. Il ponte NON e' forzato a zero: cio' che le componenti
    # misurate non spiegano resta una riga esplicita "DIFFERENZA NON GIUSTIFICATA" (= Y − X − Σcomponenti), che
    # PUO' essere diversa da zero. Tipicamente e' la differenza di valorizzazione (nostro ricalcolo vs costo
    # certificato vs contabilita') ancora da dettagliare per articolo.
    # Righe ordinate per CERTEZZE DECRESCENTI (l'utente le legge dall'alto: prima le validate/chiare,
    # poi quelle ancora da verificare, infine quelle inaffidabili da link/da dettagliare).
    comp = [
        # -- alta certezza: validate e comprese --
        {"k": "rim_iniz", "label": "+ Differenza valutazione rimanenze INIZIALI (contabili − nostre)", "val": r(rin_b - rin_n), "drill": False},
        {"k": "rim_fin", "label": "+ Differenza valutazione rimanenze FINALI (nostre − contabili)", "val": r(rfin_n - rfin_b), "drill": False},
        {"k": "resi", "label": "− Resi da clienti (rientro merce, non in contabilità costi)", "val": r(-resi), "drill": True},
        {"k": "rettneg", "label": "+ Rettifiche/consumi non-vendita (perdite, inventari di fine mese)", "val": r(rett_n), "drill": True},
        {"k": "rettpos", "label": "− Rettifiche positive (rientri / aumenti d'inventario)", "val": r(-rett_p), "drill": True},
        {"k": "fba", "label": "± Trasferimenti FBA Amazon (sbilancio gambe ATRI↔Amazon)", "val": r(-trasf_fba), "drill": True},
        {"k": "ricnf_nofatt", "label": "+ Ricevuto NON fatturato: bolle fornitore ancora da fatturare (nessuna fattura)", "val": r(-ab["nofatt"]), "drill": True},
        # -- media certezza: chiare ma da verificare --
        {"k": "ricnf_dopo", "label": "+ Ricevuto e fatturato DOPO il periodo (fatture fornitori di giugno+) — da verificare", "val": r(-ab["dopo"]), "drill": True},
        {"k": "glnodoc", "label": "± Registrazioni a conto materiale SENZA documento (giroconti contabili: es. \"merci in transito\")", "val": r(ab["glnodoc"]), "drill": True},
        {"k": "ricnf_oneri", "label": "± Oneri accessori d'acquisto (GL dazi/trasporti import − nostro carico) — da approfondire", "val": r(oneri_contrib), "drill": True},
        {"k": "ricnf_prec", "label": "− Acquisti: merce ricevuta nel periodo, fattura registrata in periodo precedente (cross-anno reale dic→gen)", "val": r(-ab["prec"]), "drill": True},
        {"k": "uscita_ddt", "label": "+ Merce uscita NON in COGS: B2C cross-anno (ricevuta dic, spedita gen) + B2B fattura differita / ordini annullati", "val": r(sped_ap + sped_res), "drill": True},
    ]
    spiegato = cogs + sum(c["val"] for c in comp)
    non_giust = consumo_bil - spiegato                     # NON forzato a zero: e' il vero scarto non spiegato
    righe_out = ([{"n": 1, "k": "cogs", "label": "Costo del venduto CDG — X (abbinato al fatturato, incl. sostituzioni gratuite a ricavo 0)", "val": r(cogs), "tot": True, "drill": True}]
                 + [dict(c, n=i + 2) for i, c in enumerate(comp)]
                 + [{"n": len(comp) + 2, "k": "spiegato", "label": "= Totale spiegato (X + componenti misurate)", "val": r(spiegato), "tot": True},
                    {"n": len(comp) + 3, "k": "non_giust", "label": "≠ DIFFERENZA NON GIUSTIFICATA (Y − spiegato) — scarto reale, da dettagliare", "val": r(non_giust), "drill": False},
                    {"n": len(comp) + 4, "k": "bilancio", "label": "= Consumo materie a CONTABILITÀ — Y (Acquisti GL ± Δrimanenze)", "val": r(consumo_bil), "tot": True}])
    ord_ns = righe("SELECT COUNT(*) n " + _ORD_NON_SPEDITI_FROM, fine=_fine_periodo(anno, ma))[0]["n"]
    return jsonify({"anno": anno, "mese_da": mda, "mese_a": ma, "righe": righe_out,
                    "contabile": {"acquisti": r(gl_acq), "rim_iniz": r(rin_b), "rim_fin": r(rfin_b),
                                  "var_rim": r(rin_b - rfin_b), "consumo": r(consumo_bil)},
                    "nostro": {"acquisti_carico": r(acq), "rim_iniz": r(rin_n), "rim_fin": r(rfin_n),
                               "var_rim": r(rin_n - rfin_n)},
                    "imballaggi": {"rim_iniz": r(imb_iniz), "rim_fin": r(imb_fin), "carico": r(imb_carico),
                                   "var_rim": r(imb_iniz - imb_fin)},
                    "ord_non_spediti": ord_ns})


@app.get("/api/riconciliazione_drill")
def api_riconciliazione_drill():
    """Drill-down di una riga del prospetto: lista articoli/documenti che la compongono."""
    anno, mda, ma = _ric_periodo()
    k = request.args.get("k", "")
    fn = lambda v: float(v or 0)

    def _con_resto(rows, totale):
        # Garantisce che il dettaglio sommi ESATTAMENTE alla riga del ponte: i primi (per impatto) + una riga
        # "residuo" che assorbe la coda troncata e gli arrotondamenti. Poi separa i segni: prima i −, poi i +.
        resto = round(fn(totale) - sum(fn(x.get("valore")) for x in rows), 2)
        if abs(resto) >= 0.005:
            extra = {c: None for c in (rows[0].keys() if rows else ["Item", "valore"])}
            extra["Item"] = "— residuo (coda troncata + arrotondamenti) —"
            extra["valore"] = resto
            rows = rows + [extra]
        for x in rows:                              # uniforma a float (evita Decimal->stringa in JSON)
            x["valore"] = round(fn(x.get("valore")), 2)
        return sorted(rows, key=lambda x: (0 if x["valore"] < 0 else 1, -abs(x["valore"])))

    if k == "cogs":
        tot = fn(righe("""SELECT SUM(f.ricavo_netto-f.mdc1) v FROM core.fatto_riga f
                       LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=f.codice_articolo
                       WHERE f.anno=:a AND f.mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""",
                       a=anno, d=mda, h=ma)[0]["v"])
        rows = righe("""SELECT TOP 300 f.codice_articolo AS Item, SUM(f.quantita) AS qta,
            ROUND(SUM(f.ricavo_netto-f.mdc1),2) AS valore FROM core.fatto_riga f
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=f.codice_articolo
            WHERE f.anno=:a AND f.mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
            GROUP BY f.codice_articolo HAVING SUM(f.ricavo_netto-f.mdc1)<>0
            ORDER BY ABS(SUM(f.ricavo_netto-f.mdc1)) DESC""", a=anno, d=mda, h=ma)
        return jsonify(_con_resto(rows, tot))
    if k == "fisico":
        # Costo del venduto FISICO = scarico vendite 506 per articolo, ESCLUSO il trasferimento FBA (cliente 70209),
        # valorizzato allo STESSO costo della riga (WAPCost_ricalc). Top 300 + residuo -> somma = riga esatta.
        tot = fn(righe("""SELECT SUM(w.QtaVend*w.WAPCost_ricalc) v FROM kodice.wap_ricalc w
                       LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item
                       WHERE w.Anno=:a AND w.Mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""",
                       a=anno, d=mda, h=ma)[0]["v"])
        rows = righe("""
            SELECT TOP 300 LTRIM(RTRIM(d.Item)) AS Item, ROUND(SUM(d.Qty),0) AS qta_spedita,
                   ROUND(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)),2) AS valore
            FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId=d.EntryId
            LEFT JOIN kodice.wap_ricalc wr ON wr.Item=LTRIM(RTRIM(d.Item)) AND wr.Anno=:a AND wr.Mese=MONTH(h.PostingDate)
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
            WHERE h.WAPMovementType=2032533506 AND h.CustSupp<>'70209' AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
              AND h.CancelPhase1='0' AND h.CancelPhase2='0'
              AND YEAR(h.PostingDate)=:a AND MONTH(h.PostingDate) BETWEEN :d AND :h
            GROUP BY LTRIM(RTRIM(d.Item)) HAVING ABS(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)))>0.5
            ORDER BY ABS(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0))) DESC""", a=anno, d=mda, h=ma)
        return jsonify(_con_resto(rows, tot))
    if k == "sped":
        # spedito-non-fatturato PER ARTICOLO, ribaltando i componenti sul KIT venduto:
        # spedito (scarico 506) vs fatturato-equivalente (vendita diretta + come componente di kit venduti).
        return jsonify(righe("""
            WITH ship AS (
                SELECT LTRIM(RTRIM(d.Item)) AS Item, SUM(d.Qty) AS qty, MAX(cm.Costo) AS costo
                FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
                JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId=d.EntryId
                LEFT JOIN kodice.costi_articolo_mese cm ON cm.Item=LTRIM(RTRIM(d.Item)) AND cm.Anno=:a AND cm.Mese=MONTH(h.PostingDate)
                LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
                WHERE h.WAPMovementType=2032533506 AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
                  AND h.CancelPhase1='0' AND h.CancelPhase2='0'
                  AND YEAR(h.PostingDate)=:a AND MONTH(h.PostingDate) BETWEEN :d AND :h
                GROUP BY LTRIM(RTRIM(d.Item))),
            fr AS (SELECT f.codice_articolo AS cod, SUM(f.quantita) AS q FROM core.fatto_riga f
                   LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=f.codice_articolo
                   WHERE f.anno=:a AND f.mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
                   GROUP BY f.codice_articolo),
            own AS (SELECT cod AS Item, q FROM fr),
            kitimp AS (SELECT dd.Component AS Item, SUM(dd.Qty*fr.q) AS q FROM kodice.vw_distinta dd JOIN fr ON fr.cod=dd.BOM GROUP BY dd.Component)
            SELECT TOP 300 s.Item, ROUND(s.qty,0) AS spedito_qta,
                   ROUND(ISNULL(o.q,0)+ISNULL(ki.q,0),0) AS fatturato_qta_incl_kit,
                   ROUND((s.qty-(ISNULL(o.q,0)+ISNULL(ki.q,0)))*ISNULL(s.costo,0),2) AS differenza
            FROM ship s LEFT JOIN own o ON o.Item=s.Item LEFT JOIN kitimp ki ON ki.Item=s.Item
            WHERE ABS((s.qty-(ISNULL(o.q,0)+ISNULL(ki.q,0)))*ISNULL(s.costo,0))>1
            ORDER BY ABS((s.qty-(ISNULL(o.q,0)+ISNULL(ki.q,0)))*ISNULL(s.costo,0)) DESC""", a=anno, d=mda, h=ma))
    if k == "fba":
        # CONTROLLO trasferimenti FBA: per articolo, gamba USCITA (scarico ATRI -> cliente 70209) vs
        # gamba CARICO (CAR-AMA su deposito Amazon). Mostra solo gli articoli NON sincronizzati = lo sbilancio
        # (trasferimenti incompleti / sfasati nel tempo) da evidenziare. Fonte: kodice.vw_fba_movimenti (leggera).
        # Valore allo STESSO costo della riga (WAPCost_ricalc, per item/mese) e col SEGNO della riga
        # (riga = −trasf_fba): valore = −Σ[(carico − uscita) × WAPCost]. Nessun filtro sulle qty (una gamba
        # puo' cadere in un mese diverso → qty pari ma valore ≠0): si tengono tutti gli item con valore ≠ 0,
        # cosi' il dettaglio somma ESATTAMENTE alla riga. Ordinato per segno (− poi +).
        fba_tot = fn(righe("""SELECT -SUM(w.QtaTrasfFBA*w.WAPCost_ricalc) v FROM kodice.wap_ricalc w
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item
            WHERE w.Anno=:a AND w.Mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""", a=anno, d=mda, h=ma)[0]["v"])
        return jsonify(_con_resto(righe("""
            SELECT Item, ROUND(uscita_atri,0) AS uscita_atri, ROUND(carico_amazon,0) AS carico_amazon,
                   ROUND(carico_amazon-uscita_atri,0) AS differenza_qta, ROUND(valore,2) AS valore
            FROM (
                SELECT v.Item,
                       SUM(CASE WHEN v.Gamba='USCITA_ATRI'   THEN v.Qta ELSE 0 END) AS uscita_atri,
                       SUM(CASE WHEN v.Gamba='CARICO_AMAZON' THEN v.Qta ELSE 0 END) AS carico_amazon,
                       -SUM(CASE WHEN v.Gamba='CARICO_AMAZON' THEN v.Qta ELSE -v.Qta END * ISNULL(wr.WAPCost_ricalc,0)) AS valore
                FROM kodice.vw_fba_movimenti v
                LEFT JOIN kodice.wap_ricalc wr ON wr.Item=v.Item AND wr.Anno=v.Anno AND wr.Mese=v.Mese
                LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=v.Item
                WHERE v.Anno=:a AND v.Mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
                GROUP BY v.Item
            ) t
            WHERE ABS(valore) >= 0.005
            ORDER BY ABS(valore) DESC""", a=anno, d=mda, h=ma), fba_tot))
    if k in ("rettneg", "rettpos"):
        # Rettifiche scomposte per SEGNO (507), valorizzate allo STESSO costo della riga (WAPCost_ricalc).
        # rettneg = consumi/uscite non-vendita (imballaggi/perdite): contributo + al consumo.
        # rettpos = rientri/aumenti d'inventario: contributo − al consumo (valore negato).
        # Ogni drill e' MONOSEGNO e somma ESATTAMENTE alla sua riga. CAR-AMA escluso (e' nel trasferimento FBA).
        causali = "'KLRI-N-A','RI-NEG','KLR-FORA'" if k == "rettneg" else "'KLRI-P-A','RI-POS'"
        seg = "" if k == "rettneg" else "-"
        tot = fn(righe(f"""SELECT ISNULL({seg}SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)),0) v
            FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId=d.EntryId
            LEFT JOIN kodice.wap_ricalc wr ON wr.Item=LTRIM(RTRIM(d.Item)) AND wr.Anno=:a AND wr.Mese=MONTH(h.PostingDate)
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
            WHERE h.WAPMovementType=2032533507 AND h.InvRsn IN ({causali}) AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
              AND h.CancelPhase1='0' AND h.CancelPhase2='0'
              AND YEAR(h.PostingDate)=:a AND MONTH(h.PostingDate) BETWEEN :d AND :h""", a=anno, d=mda, h=ma)[0]["v"])
        rows = righe(f"""SELECT h.InvRsn AS causale, LTRIM(RTRIM(d.Item)) AS Item, ROUND(SUM(d.Qty),0) AS qta,
            ROUND({seg}SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)),2) AS valore
            FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId=d.EntryId
            LEFT JOIN kodice.wap_ricalc wr ON wr.Item=LTRIM(RTRIM(d.Item)) AND wr.Anno=:a AND wr.Mese=MONTH(h.PostingDate)
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
            WHERE h.WAPMovementType=2032533507 AND h.InvRsn IN ({causali}) AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
              AND h.CancelPhase1='0' AND h.CancelPhase2='0'
              AND YEAR(h.PostingDate)=:a AND MONTH(h.PostingDate) BETWEEN :d AND :h
            GROUP BY h.InvRsn, LTRIM(RTRIM(d.Item))
            ORDER BY ABS(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0))) DESC""", a=anno, d=mda, h=ma)
        return jsonify(_con_resto(rows, tot))
    if k == "resi":
        # RESI (509): contribuiscono col segno − al consumo (rientro merce) -> valore negato per sommare alla riga.
        resi_tot = fn(righe("""SELECT -SUM(w.QtaResi*w.WAPCost_ricalc) v FROM kodice.wap_ricalc w
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=w.Item
            WHERE w.Anno=:a AND w.Mese BETWEEN :d AND :h AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'""", a=anno, d=mda, h=ma)[0]["v"])
        return jsonify(_con_resto(righe("""SELECT h.InvRsn AS causale, LTRIM(RTRIM(d.Item)) AS Item,
            ROUND(SUM(d.Qty),0) AS qta, ROUND(-SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)),2) AS valore
            FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId=d.EntryId
            LEFT JOIN kodice.wap_ricalc wr ON wr.Item=LTRIM(RTRIM(d.Item)) AND wr.Anno=:a AND wr.Mese=MONTH(h.PostingDate)
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
            WHERE h.WAPMovementType=2032533509 AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
              AND h.CancelPhase1='0' AND h.CancelPhase2='0'
              AND YEAR(h.PostingDate)=:a AND MONTH(h.PostingDate) BETWEEN :d AND :h
            GROUP BY h.InvRsn, LTRIM(RTRIM(d.Item))
            ORDER BY ABS(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0))) DESC""", a=anno, d=mda, h=ma), resi_tot))
    if k in ("ricnf_prec", "ricnf_dopo", "ricnf_nofatt"):
        # Carico merce per FORNITORE dalla tabella materializzata kodice.carico_fattura, bucket per AccrualDate
        # della fattura: prec=competenza prima del periodo; dopo=dopo il periodo; nofatt=nessuna fattura collegata.
        ini = datetime.date(anno, mda, 1); fin1 = _fine_periodo(anno, ma) + datetime.timedelta(days=1)
        cond = {"ricnf_prec": "cf.FattAccrual < :ini",
                "ricnf_dopo": "cf.FattAccrual >= :fin1",
                "ricnf_nofatt": "cf.FattAccrual IS NULL"}[k]
        rows = righe("""
            SELECT TOP 300 ISNULL(cs.CompanyName, h.CustSupp) AS fornitore,
                   CONVERT(varchar(10), cf.MovDate, 103) AS carico,
                   CONVERT(varchar(10), cf.FattAccrual, 103) AS fattura_competenza,
                   ROUND(-SUM(cf.ValPuro), 2) AS valore
            FROM kodice.carico_fattura cf
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId=cf.MovEntryId
            LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSupp cs ON cs.CustSupp=h.CustSupp
            WHERE cf.Anno=:a AND cf.MovDate>=:ini AND cf.MovDate<:fin1 AND """ + cond + """
            GROUP BY ISNULL(cs.CompanyName, h.CustSupp), cf.MovDate, cf.FattAccrual
            ORDER BY ABS(SUM(cf.ValPuro)) DESC""", a=anno, ini=ini, fin1=fin1)
        ab = _ric_acq(anno, mda, ma)
        tot = {"ricnf_prec": -ab["prec"], "ricnf_dopo": -ab["dopo"], "ricnf_nofatt": -ab["nofatt"]}[k]
        return jsonify(_con_resto(rows, tot))
    if k == "glnodoc":
        # Le RIGHE DI REGISTRAZIONE sui conti materiale che NON hanno un documento (fattura) dietro: giroconti
        # manuali (es. "merci in transito"). DARE positivo / AVERE negativo: sommano al netto della riga.
        rows = righe("""SELECT CONVERT(varchar(10), CAST(g.PostingDate AS date),103) AS data,
                NULLIF(je.DocNo,'') AS documento, g.AccRsn AS causale, q.Account AS conto,
                CAST(g.Notes AS nvarchar(200)) AS note,
                ROUND(CASE WHEN g.DebitCreditSign=4980736 THEN g.Amount ELSE -g.Amount END,2) AS valore
            FROM kodice.conti_quadratura q
            JOIN KODICEBAGNO_4.dbo.MA_JournalEntriesGLDetail g ON g.Account=q.Account
            JOIN KODICEBAGNO_4.dbo.MA_JournalEntries je ON je.JournalEntryId=g.JournalEntryId
            LEFT JOIN KODICEBAGNO_4.dbo.MA_PurchaseDoc pd ON pd.PurchaseDocId=je.CRRefID
            WHERE """ + _GL_NODOC_WHERE + """
            ORDER BY ABS(g.Amount) DESC OPTION (MAXDOP 1)""", a=anno, d=mda, h=ma)
        return jsonify(_con_resto(rows, _ric_acq(anno, mda, ma)["glnodoc"]))
    if k == "ricnf_oneri":
        # ELENCO DOCUMENTI oneri in contabilita' (dazi/trasporti import, competenza AccrualDate): per ognuno la
        # data di COMPETENZA, quella di REGISTRAZIONE, conto, riferimento spedizione, importo. Sono i documenti che
        # il magazzino deve spalmare nello STESSO mese; 'reg_fuori_mese=SI' = registrato in un mese diverso dalla
        # competenza (candidato da sistemare in Mago).
        rows = righe("""SELECT
                CONVERT(varchar(10), CAST(g.AccrualDate AS date), 103) AS competenza,
                CONVERT(varchar(10), CAST(g.PostingDate AS date), 103) AS registrazione,
                CASE WHEN MONTH(g.AccrualDate)<>MONTH(g.PostingDate) OR YEAR(g.AccrualDate)<>YEAR(g.PostingDate) THEN 'SI' ELSE '' END AS reg_fuori_mese,
                q.Account AS conto, g.AccRsn AS causale, NULLIF(je.DocNo,'') AS documento,
                LEFT(CAST(g.Notes AS nvarchar(120)),40) AS riferimento,
                ROUND(SUM(CASE WHEN g.DebitCreditSign=4980736 THEN g.Amount ELSE -g.Amount END),2) AS importo
            FROM kodice.conti_quadratura q
            JOIN KODICEBAGNO_4.dbo.MA_JournalEntriesGLDetail g ON g.Account=q.Account
            JOIN KODICEBAGNO_4.dbo.MA_JournalEntries je ON je.JournalEntryId=g.JournalEntryId
            WHERE q.Componente='MATERIALE' AND q.Ruolo='ONERE_ACQUISTO'
              AND YEAR(g.AccrualDate)=:a AND MONTH(g.AccrualDate) BETWEEN :d AND :h
            GROUP BY g.AccrualDate, g.PostingDate, q.Account, g.AccRsn, je.DocNo, CAST(g.Notes AS nvarchar(120))
            ORDER BY g.AccrualDate DESC, ABS(SUM(CASE WHEN g.DebitCreditSign=4980736 THEN g.Amount ELSE -g.Amount END)) DESC""",
            a=anno, d=mda, h=ma)
        return jsonify(rows)
    if k == "apert":
        # articoli con maggior scostamento di valore d'apertura nostro vs ultimo costo Mago (proxy del drift)
        return jsonify(righe("""SELECT TOP 300 w.Item, ROUND(w.QtaIniz,0) AS giacenza,
            ROUND(w.WAPCost_ricalc,2) AS costo_nostro, ROUND(w.WAPCost_Mago,2) AS costo_mago,
            ROUND(w.QtaIniz*(ISNULL(w.WAPCost_Mago,0)-w.WAPCost_ricalc),2) AS differenza
            FROM kodice.wap_ricalc w WHERE w.Anno=:a AND w.Mese=:d AND w.QtaIniz>0 AND w.WAPCost_Mago IS NOT NULL
              AND ABS(w.QtaIniz*(ISNULL(w.WAPCost_Mago,0)-w.WAPCost_ricalc))>1
            ORDER BY ABS(w.QtaIniz*(ISNULL(w.WAPCost_Mago,0)-w.WAPCost_ricalc)) DESC""", a=anno, d=mda))
    if k == "ordnonsped":
        # Ordini FATTURATI non ancora spediti alla chiusura del periodo (competenza). Lista navigabile:
        # ogni riga e' un ordine con fattura emessa ma merce non uscita dal magazzino al fine periodo.
        return jsonify(righe("""
            SELECT TOP 600 CONVERT(varchar(10), v.DataOrdine, 103) AS data, v.NrOrdine AS ordine,
                   v.RagioneSocialeCliente AS cliente, v.GruppoCliente AS canale,
                   v.NrRighe AS righe, v.QtaOrdinata AS qta_ordinata,
                   CASE WHEN v.CompletamenteConsegnato = 'No' THEN 'backlog' ELSE 'spedito dopo chiusura' END AS stato
            """ + _ORD_NON_SPEDITI_FROM + " ORDER BY v.DataOrdine DESC", fine=_fine_periodo(anno, ma)))
    if k == "uscita_ddt":
        # DETTAGLIO DOCUMENTO PER DOCUMENTO (DDT, ordine, cliente, data, fattura agganciata, valore):
        # cosi' si possono aprire in Mago e verificare uno per uno. Top 300 + residuo (somma alla riga).
        ini = datetime.date(anno, mda, 1); fin1 = _fine_periodo(anno, ma) + datetime.timedelta(days=1)
        tot = fn(righe("""SELECT ISNULL(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)),0) v
            FROM kodice.vendite_link vl
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d ON d.EntryId=vl.MovEntryId
            LEFT JOIN kodice.wap_ricalc wr ON wr.Item=LTRIM(RTRIM(d.Item)) AND wr.Anno=:a AND wr.Mese=MONTH(vl.MovDate)
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
            WHERE vl.MovDate>=:ini AND vl.MovDate<:fin1 AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
              AND vl.InternalOrdNo NOT LIKE '1300%'
              AND ((vl.Modo NOT IN ('SOSTITUZIONE','SOLO_ORDINE','NESSUN_ORDINE') AND vl.FatturaDate < :ini)
                   OR vl.Modo IN ('SOLO_ORDINE','NESSUN_ORDINE'))""", a=anno, ini=ini, fin1=fin1)[0]["v"])
        rows = righe("""
            SELECT TOP 300 CONVERT(varchar(10),vl.MovDate,103) AS spedito, mh.DocNo AS ddt,
                   vl.InternalOrdNo AS ordine, ISNULL(cs.CompanyName, mh.CustSupp) AS cliente,
                   fd.DocNo AS fattura, CONVERT(varchar(10),vl.FatturaDate,103) AS data_fattura,
                   CASE WHEN vl.Modo='NESSUN_ORDINE' THEN 'senza ordine'
                        WHEN vl.Modo='SOLO_ORDINE' THEN 'fattura non agganciata (B2B differita o link mancato)'
                        ELSE 'fatturato in periodo precedente' END AS tipo,
                   ROUND(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)),2) AS valore
            FROM kodice.vendite_link vl
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries mh ON mh.EntryId=vl.MovEntryId
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d ON d.EntryId=vl.MovEntryId
            LEFT JOIN KODICEBAGNO_4.dbo.MA_SaleDoc fd ON fd.SaleDocId=vl.FatturaId
            LEFT JOIN kodice.wap_ricalc wr ON wr.Item=LTRIM(RTRIM(d.Item)) AND wr.Anno=:a AND wr.Mese=MONTH(vl.MovDate)
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item=LTRIM(RTRIM(d.Item))
            LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSupp cs ON cs.CustSupp=mh.CustSupp
            WHERE vl.MovDate>=:ini AND vl.MovDate<:fin1 AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO'
              AND vl.InternalOrdNo NOT LIKE '1300%'
              AND ((vl.Modo NOT IN ('SOSTITUZIONE','SOLO_ORDINE','NESSUN_ORDINE') AND vl.FatturaDate < :ini)
                   OR vl.Modo IN ('SOLO_ORDINE','NESSUN_ORDINE'))
            GROUP BY vl.MovDate, mh.DocNo, vl.InternalOrdNo, ISNULL(cs.CompanyName, mh.CustSupp), fd.DocNo, vl.FatturaDate, vl.Modo
            ORDER BY ABS(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0))) DESC""", a=anno, ini=ini, fin1=fin1)
        return jsonify(_con_resto(rows, tot))
    if k == "sost":
        # SINTESI sostituzioni gratuite per cliente/canale (chi ha ricevuto merce gratis).
        ini = datetime.date(anno, mda, 1); fin1 = _fine_periodo(anno, ma) + datetime.timedelta(days=1)
        return jsonify(righe("""
            SELECT ISNULL(cs.CompanyName, h.CustSupp) AS cliente, COUNT(DISTINCT vl.MovEntryId) AS movimenti,
                   ROUND(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)),2) AS valore
            FROM kodice.vendite_link vl
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = vl.MovEntryId
            JOIN KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d ON d.EntryId = vl.MovEntryId
            LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSupp cs ON cs.CustSupp = h.CustSupp
            LEFT JOIN kodice.wap_ricalc wr ON wr.Item = LTRIM(RTRIM(d.Item)) AND wr.Anno = :a AND wr.Mese = MONTH(vl.MovDate)
            LEFT JOIN kodice.vw_classe_articolo ca ON ca.Item = LTRIM(RTRIM(d.Item))
            WHERE (vl.Modo = 'SOSTITUZIONE' OR vl.InternalOrdNo LIKE '1300%') AND ISNULL(ca.Classe,'PRODOTTO')='PRODOTTO' AND vl.MovDate >= :ini AND vl.MovDate < :fin1
            GROUP BY ISNULL(cs.CompanyName, h.CustSupp)
            HAVING ABS(SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0))) > 0.005
            ORDER BY SUM(d.Qty*ISNULL(wr.WAPCost_ricalc,0)) DESC""", a=anno, ini=ini, fin1=fin1))
    return jsonify([])


@app.get("/api/cerca_articolo")
def api_cerca_articolo():
    """Ricerca articolo (codice o descrizione) per aprire la scheda costo di QUALUNQUE articolo."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    like = f"%{q}%"
    return jsonify(righe("""
        SELECT TOP 25 LTRIM(RTRIM(Item)) AS Item, Description AS descr
        FROM KODICEBAGNO_4.dbo.MA_Items
        WHERE Item LIKE :like OR Description LIKE :like
        ORDER BY Item""", like=like))


@app.get("/api/bonifica")
def api_bonifica():
    """Candidati alla bonifica dell'apertura 2026 (kodice.vw_bonifica_apertura)."""
    return jsonify(righe("""
        SELECT Item, Descrizione, Categoria, InRicalcolo, Giacenza, CostoSeed, NostroDic2025, MagoDic2025,
               LastCost, OverrideSuggerito, OverrideAttuale, OverrideFonte
        FROM kodice.vw_bonifica_apertura
        ORDER BY CASE WHEN OverrideAttuale IS NULL THEN 0 ELSE 1 END,
                 InRicalcolo, CASE Categoria WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END,
                 Giacenza*ISNULL(OverrideSuggerito,0) DESC"""))


@app.post("/api/bonifica_certifica")
def api_bonifica_certifica():
    """Certifica (o rimuove) il valore d'apertura forzato in kodice.wap_apertura_override (Anno 2026)."""
    d = request.get_json(force=True)
    item = d["item"]
    if d.get("rimuovi"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM kodice.wap_apertura_override WHERE Item=:i AND Anno=2026"), {"i": item})
        return jsonify({"ok": True, "rimosso": True})
    p = {"i": item, "c": float(d["costo"]), "f": d.get("fonte", "CERTIFICATO"),
         "n": d.get("nota", ""), "u": d.get("utente", "dashboard")}
    with engine.begin() as c:
        c.execute(text("""
            MERGE kodice.wap_apertura_override AS t
            USING (SELECT :i AS Item, 2026 AS Anno) s ON t.Item=s.Item AND t.Anno=s.Anno
            WHEN MATCHED THEN UPDATE SET CostoPuroUnit=:c, Fonte=:f, Nota=:n, Utente=:u, DataStato=SYSDATETIME()
            WHEN NOT MATCHED THEN INSERT (Item,Anno,CostoPuroUnit,Fonte,Nota,Utente,DataStato)
                 VALUES (:i,2026,:c,:f,:n,:u,SYSDATETIME());"""), p)
    return jsonify({"ok": True})


@app.post("/api/bonifica_certifica_suggeriti")
def api_bonifica_certifica_suggeriti():
    """Certifica IN BLOCCO tutti i candidati 'A' che hanno gia' un valore proposto (LastCost/WAP Mago):
    sono articoli con giacenza ma senza valore nostro, per cui esiste comunque un costo plausibile.
    Restano da fare a mano solo quelli SENZA alcun costo (OverrideSuggerito=0 -> da definire)."""
    with engine.begin() as c:
        n = c.execute(text("""
            INSERT INTO kodice.wap_apertura_override (Item, Anno, CostoPuroUnit, Fonte, Nota, Utente, DataStato)
            SELECT Item, 2026, OverrideSuggerito, 'AUTO_SUGGERITO', 'Bulk: valore proposto (LastCost/WAP)', 'dashboard', SYSDATETIME()
            FROM kodice.vw_bonifica_apertura
            WHERE Categoria='A' AND OverrideSuggerito > 0 AND OverrideAttuale IS NULL""")).rowcount
    return jsonify({"ok": True, "certificati": n})


@app.post("/api/rilancia_ricalcolo")
def api_rilancia_ricalcolo():
    """Rilancia il ricalcolo WAP per l'anno e PROPAGA al costo del venduto: il solo usp_ricalc_wap
    aggiorna wap_ricalc ma NON costi_articolo_mese/fatto_riga; senza la propagazione la bonifica non
    si vedrebbe nel COGS. Quindi per ogni mese gia' elaborato (presente in core.fatto_riga) rigenera
    costi (usp_prepara_costi) -> componenti attivi -> build."""
    anno = int((request.get_json(force=True) or {}).get("anno", 2026))
    with engine.begin() as c:
        c.exec_driver_sql(f"EXEC kodice.usp_ricalc_wap @Anno = {anno};")
        mesi = [r[0] for r in c.exec_driver_sql(
            f"SELECT DISTINCT mese FROM core.fatto_riga WHERE anno = {anno} ORDER BY mese")]
        attivi = [r[0] for r in c.exec_driver_sql(
            "SELECT codice_componente FROM cfg.componenti WHERE attivo=1 ORDER BY livello, codice_componente")]
        for m in mesi:
            c.exec_driver_sql(f"EXEC core.usp_prepara_costi @schema_azienda='kodice', @anno={anno}, @mese={m};")
            for cod in attivi:
                c.exec_driver_sql(f"EXEC dbo.usp_comp_{cod} @anno={anno}, @mese={m};")
            c.exec_driver_sql(f"EXEC dbo.usp_build_fatto_riga @anno={anno}, @mese={m};")
    return jsonify({"ok": True, "anno": anno, "mesi": mesi})


@app.post("/api/elabora_mese")
def api_elabora_mese():
    """Elabora un mese: prepara i costi (motore) -> carica le vendite da Mago -> componenti attivi -> assembla
    core.fatto_riga. NON rilancia il ricalcolo WAP (annuale, ha il suo pulsante). Stesso ordine di run_pipeline."""
    d = request.get_json(force=True) or {}
    anno = int(d.get("anno", 2026)); mese = int(d.get("mese", 1))
    passi = []
    with engine.begin() as c:
        c.exec_driver_sql(f"EXEC core.usp_prepara_costi @schema_azienda='kodice', @anno={anno}, @mese={mese};")
        passi.append("prepara_costi")
        c.exec_driver_sql(f"EXEC dbo.usp_load_righe_vendita @anno={anno}, @mese={mese};")
        passi.append("load_righe_vendita")
        attivi = [r[0] for r in c.exec_driver_sql(
            "SELECT codice_componente FROM cfg.componenti WHERE attivo=1 ORDER BY livello, codice_componente")]
        for cod in attivi:
            c.exec_driver_sql(f"EXEC dbo.usp_comp_{cod} @anno={anno}, @mese={mese};")
            passi.append(f"comp_{cod}")
        c.exec_driver_sql(f"EXEC dbo.usp_build_fatto_riga @anno={anno}, @mese={mese};")
        passi.append("build_fatto_riga")
        n = list(c.exec_driver_sql(
            f"SELECT COUNT(*) FROM core.fatto_riga WHERE anno={anno} AND mese={mese}"))[0][0]
    return jsonify({"ok": True, "anno": anno, "mese": mese, "passi": passi, "righe": n})


@app.get("/api/stato_mese")
def api_stato_mese():
    """Stato di consolidamento del costo del mese (CONSOLIDATO / IN_FORMAZIONE)."""
    a = int(request.args.get("anno")); m = int(request.args.get("mese"))
    r = righe("SELECT Stato, Utente, CONVERT(varchar(16), DataStato, 120) AS DataStato, Nota "
              "FROM kodice.costo_mese_stato WHERE Anno=:a AND Mese=:m", a=a, m=m)
    return jsonify(r[0] if r else {"Stato": "IN_FORMAZIONE"})


@app.post("/api/consolida_mese")
def api_consolida_mese():
    """Consolida (o riapre) i costi del mese: l'amministrazione certifica che tutti i documenti sono caricati."""
    d = request.get_json(force=True); a = int(d["anno"]); m = int(d["mese"])
    if d.get("riapri"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM kodice.costo_mese_stato WHERE Anno=:a AND Mese=:m"), {"a": a, "m": m})
        return jsonify({"ok": True, "riaperto": True})
    p = {"a": a, "m": m, "u": d.get("utente", "dashboard"), "n": d.get("nota", "")}
    with engine.begin() as c:
        c.execute(text("""
            MERGE kodice.costo_mese_stato AS t USING (SELECT :a AS Anno, :m AS Mese) s
              ON t.Anno=s.Anno AND t.Mese=s.Mese
            WHEN MATCHED THEN UPDATE SET Stato='CONSOLIDATO', Utente=:u, Nota=:n, DataStato=SYSDATETIME()
            WHEN NOT MATCHED THEN INSERT (Anno,Mese,Stato,Utente,Nota,DataStato)
                 VALUES (:a,:m,'CONSOLIDATO',:u,:n,SYSDATETIME());"""), p)
    return jsonify({"ok": True})


# Espressioni di dimensione del CE (condivise tra /api/ce e /api/ce_drill).
CE_DIMS = {
    "canale":        "CASE WHEN sd.CustSupp='135774' THEN 'SAVINI' ELSE COALESCE(NULLIF(LTRIM(RTRIM(ctg.Notes)),''), opt.Category, '(n/d)') END",
    "dipartimento":  "CASE WHEN ctg.Notes IN ('BTOB tradizionale','Professionale','Intercompany Savini') THEN 'BTOB' ELSE 'ONLINE' END",
    "cliente":       "COALESCE(cs.CompanyName, sd.CustSupp)",
    "agente":        "COALESCE(sp.Name, opt.Salesperson, '(n/d)')",
    "tipo_articolo": "COALESCE(ity.Description, it.ItemType, '(n/d)')",
    "linea_articolo":"COALESCE(hc.Description, it.HomogeneousCtg, '(n/d)')",
    "mese":          "RIGHT('0'+CAST(f.mese AS varchar(2)),2)",
}
CE_JOINS = """
        FROM core.fatto_riga f
        JOIN      KODICEBAGNO_4.dbo.MA_SaleDoc sd  ON sd.SaleDocId = f.sale_doc_id
        LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSuppCustomerOptions opt ON opt.Customer = sd.CustSupp AND opt.CustSuppType = sd.CustSuppType
        LEFT JOIN KODICEBAGNO_4.dbo.MA_CustomerCtg ctg ON ctg.Category = opt.Category
        LEFT JOIN KODICEBAGNO_4.dbo.MA_CustSupp cs  ON cs.CustSupp = sd.CustSupp AND cs.CustSuppType = sd.CustSuppType
        LEFT JOIN KODICEBAGNO_4.dbo.MA_SalesPeople sp ON sp.Salesperson = opt.Salesperson
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items it ON LTRIM(RTRIM(it.Item)) = f.codice_articolo
        LEFT JOIN KODICEBAGNO_4.dbo.MA_ItemTypes ity ON ity.CodeType = it.ItemType
        LEFT JOIN KODICEBAGNO_4.dbo.MA_HomogeneousCtg hc ON hc.Category = it.HomogeneousCtg
"""


@app.get("/api/ce")
def api_ce():
    """Conto Economico per dimensione (alla Qlik). Fatturato + Materiale (nostro costo) + Margine,
    con la dimensione scelta (Canale=categoria cliente, Dipartimento, Cliente, Agente, Tipo/Linea Articolo, Mese)."""
    anno = int(request.args.get("anno"))
    mese = request.args.get("mese")
    dim = request.args.get("dim", "canale")
    col = CE_DIMS.get(dim, CE_DIMS["canale"])
    params = {"anno": anno}
    wmese = ""
    if mese and mese not in ("0", ""):
        wmese = " AND f.mese = :mese"; params["mese"] = int(mese)
    rows = righe(f"""
        SELECT {col} AS dim,
               SUM(f.ricavo_netto)            AS fatturato,
               SUM(f.ricavo_netto - f.mdc1)   AS materiale,
               SUM(f.mdc1)                    AS margine,
               COUNT(DISTINCT f.sale_doc_id)  AS n_ordini
        {CE_JOINS}
        WHERE f.anno = :anno {wmese}
        GROUP BY {col}
        ORDER BY SUM(f.ricavo_netto) DESC
    """, **params)
    return jsonify({"dim": dim, "righe": rows})


@app.get("/api/ce_drill")
def api_ce_drill():
    """Drill-down di una riga del CE sul CLIENTE: dato un valore di dimensione (es. Canale='(n/d)'),
    elenca i clienti che lo compongono con codice, categoria/notes (per capire perche' non agganciano un canale)."""
    anno = int(request.args.get("anno"))
    mese = request.args.get("mese")
    dim = request.args.get("dim", "canale")
    val = request.args.get("val", "")
    col = CE_DIMS.get(dim, CE_DIMS["canale"])
    params = {"anno": anno, "val": val}
    wmese = ""
    if mese and mese not in ("0", ""):
        wmese = " AND f.mese = :mese"; params["mese"] = int(mese)
    rows = righe(f"""
        SELECT COALESCE(cs.CompanyName, sd.CustSupp) AS cliente,
               LTRIM(RTRIM(sd.CustSupp))             AS codice,
               MAX(LTRIM(RTRIM(opt.Category)))       AS categoria,
               MAX(LTRIM(RTRIM(ctg.Notes)))          AS notes,
               SUM(f.ricavo_netto)            AS fatturato,
               SUM(f.ricavo_netto - f.mdc1)   AS materiale,
               SUM(f.mdc1)                    AS margine,
               COUNT(DISTINCT f.sale_doc_id)  AS n_ordini
        {CE_JOINS}
        WHERE f.anno = :anno {wmese} AND ({col}) = :val
        GROUP BY COALESCE(cs.CompanyName, sd.CustSupp), LTRIM(RTRIM(sd.CustSupp))
        ORDER BY SUM(f.ricavo_netto) DESC
    """, **params)
    return jsonify({"dim": dim, "val": val, "righe": rows})


@app.get("/api/raffronto_costo")
def api_raffronto_costo():
    """Raffronto del COSTO MATERIALE del mese, per articolo: nostro vs WAP Mago (risalita) vs
    'mensile Mago' (costomaterialemensile di KB_SaleDocDetailDatiAggiuntivi, quello usato da Qlik).
    Serve a capire DOVE nasce la differenza di totale tra il nostro CE e quello Qlik."""
    a = int(request.args.get("anno")); m = int(request.args.get("mese"))
    rows = righe("""
        WITH wap AS (
            SELECT Item, WAPCost FROM (
                SELECT LTRIM(RTRIM(Item)) AS Item, WAPCost,
                       ROW_NUMBER() OVER (PARTITION BY LTRIM(RTRIM(Item)) ORDER BY EndPeriodDate DESC) rn
                FROM KODICEBAGNO_4.dbo.MA_ItemsWAP
                WHERE Storage='' AND WAPCost>0 AND EndPeriodDate < DATEADD(MONTH,1,DATEFROMPARTS(:a,:m,1))
            ) t WHERE rn=1
        ),
        r AS (
            SELECT f.codice_articolo AS Item, f.quantita AS qta, (f.ricavo_netto - f.mdc1) AS mat_nostro,
                   ISNULL(da.costomaterialemensile,0) * CASE WHEN f.quantita < 0 THEN -1 ELSE 1 END AS mat_mago
            FROM core.fatto_riga f
            LEFT JOIN KODICEBAGNO_4.dbo.KB_SaleDocDetailDatiAggiuntivi da
                   ON da.SaleDocId = f.sale_doc_id AND da.Line = f.line
            WHERE f.anno = :a AND f.mese = :m
        )
        SELECT r.Item, i.Description AS descr,
               SUM(r.qta)                       AS qta,
               SUM(r.mat_nostro)                AS mat_nostro,
               SUM(r.qta) * MAX(w.WAPCost)      AS mat_wap,
               SUM(r.mat_mago)                  AS mat_mago,
               MAX(w.WAPCost)                   AS wap_unit
        FROM r
        LEFT JOIN wap w ON w.Item = r.Item
        LEFT JOIN KODICEBAGNO_4.dbo.MA_Items i ON LTRIM(RTRIM(i.Item)) = r.Item
        GROUP BY r.Item, i.Description
        ORDER BY ABS(SUM(r.mat_nostro) - SUM(r.mat_mago)) DESC
    """, a=a, m=m)
    return jsonify({"righe": rows})


@app.get("/api/sql")
def api_sql():
    res = []
    with engine.begin() as c:
        for o in OGGETTI_SQL:
            if o["tipo"] == "table":
                sql = _ddl_tabella(c, o["nome"])
            else:
                sql = c.execute(text("SELECT OBJECT_DEFINITION(OBJECT_ID(:n))"), {"n": o["nome"]}).scalar()
            res.append({**o, "sql": (sql or "-- (definizione non disponibile nel DB)").strip()})
    return jsonify(res)


@app.get("/")
def home():
    return Response(PAGINA, mimetype="text/html")


# ----------------------------------------------------------------------------- HTML
PAGINA = r"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CDG_QV · Esplora</title>
<style>
  :root{--paper:#f6f4ee;--ink:#211e1a;--muted:#6f675c;--line:#e2dcd0;--card:#fffdf8;
        --ok:#2f7d52;--okbg:#e7f1ea;--warn:#9a5a1e;--warnbg:#f6ecdd;--bad:#a23b2c;--badbg:#f6e3df;--accent:#3a6ea5;}
  *{box-sizing:border-box} body{margin:0;background:var(--paper);color:var(--ink);
     font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.45}
  code{font-family:ui-monospace,Consolas,monospace}
  header{padding:16px 24px;border-bottom:1px solid var(--line);background:var(--card);display:flex;
         align-items:center;gap:18px;flex-wrap:wrap}
  header h1{font-family:Georgia,serif;font-size:20px;margin:0}
  select,input{font:inherit;border:1px solid var(--line);border-radius:8px;padding:7px 9px;background:#fff}
  .tabs{display:flex;gap:6px;margin-left:auto}
  .tab{padding:7px 14px;border-radius:8px;border:1px solid var(--line);background:#fff;cursor:pointer;font-size:14px}
  .tab.on{background:var(--accent);color:#fff;border-color:transparent}
  main{max-width:1200px;margin:0 auto;padding:20px 24px 60px}
  .app{display:flex;align-items:flex-start}
  .side{width:210px;flex:0 0 210px;border-right:1px solid var(--line);background:var(--card);padding:8px;min-height:calc(100vh - 66px)}
  .side .navg{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:12px 8px 4px}
  .side .sec{padding:8px 10px;border-radius:8px;cursor:pointer;font-size:13.5px;margin-bottom:2px}
  .side .sec:hover{background:#efeae0}
  .side .sec.on{background:var(--accent);color:#fff}
  .side .sec.todo{color:var(--muted)}
  .content{flex:1;min-width:0;max-width:1180px;padding:16px 24px 60px}
  .subtabs{display:flex;gap:6px;margin:0 0 16px;flex-wrap:wrap}
  .subtab{padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:#fff;cursor:pointer;font-size:13px}
  .subtab.on{background:var(--accent);color:#fff;border-color:transparent}
  .banner{padding:10px 14px;border-radius:10px;margin:0 0 14px;font-size:13.5px;border:1px solid var(--line)}
  .banner.ok{background:var(--okbg);border-color:#bfe0cb}
  .banner.warn{background:var(--warnbg);border-color:#e6d3b3}
  .expbtn{position:absolute;top:8px;right:10px;z-index:6;font:inherit;font-size:11px;line-height:1;
          border:1px solid var(--line);background:#fff;border-radius:6px;padding:3px 8px;cursor:pointer;
          color:var(--muted);box-shadow:0 1px 2px rgba(0,0,0,.07);opacity:.7;transition:opacity .12s,color .12s}
  .expbtn:hover{opacity:1;color:var(--accent);background:#eef2f7}
  .row{display:grid;grid-template-columns:380px 1fr;gap:20px}
  @media(max-width:900px){.row{grid-template-columns:1fr}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;position:relative}
  .panel h2{font-family:Georgia,serif;font-size:16px;margin:0 0 10px}
  table{border-collapse:collapse;width:100%;font-size:13px}
  th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
  th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
  td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  table.sticky thead th{position:sticky;top:0;background:var(--card);box-shadow:0 1px 0 var(--line);z-index:2}
  .doc{cursor:pointer} .doc:hover{background:#efeae0}
  .doc.sel{background:var(--okbg)}
  .pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;background:#efeae0;color:var(--muted)}
  .pill.warn{background:var(--warnbg);color:var(--warn)} .pill.bad{background:var(--badbg);color:var(--bad)}
  .pill.ok{background:var(--okbg);color:var(--ok)}
  .lista{max-height:70vh;overflow:auto}
  .muted{color:var(--muted)} .neg{color:var(--bad)}
  .diagbox{margin-top:8px;padding:8px;border:1px dashed var(--line);border-radius:8px;background:#fafafa}
  .ln{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:10px;background:#fff}
  .ln .h{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
  .ln .art{font-weight:600} .ln .desc{color:var(--muted);font-size:12.5px}
  .comp{margin-top:8px;border-top:1px dashed var(--line);padding-top:8px}
  .cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;min-width:170px}
  .kpi .v{font-size:22px;font-family:Georgia,serif} .kpi .l{font-size:12px;color:var(--muted)}
  .kpi.bad .v{color:var(--bad)}
  h3.sec{font-family:Georgia,serif;font-size:15px;margin:18px 0 6px}
  pre{background:#1f1d1a;color:#e9e4d8;padding:14px;border-radius:8px;overflow:auto;font-size:12.5px;margin:8px 0 0;max-height:440px}
  details.sqlo{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:10px}
  details.sqlo summary{cursor:pointer;font-weight:600}
  details.sqlo .sp{color:var(--muted);font-size:13px;margin:6px 0 0}
  .grp{font-family:Georgia,serif;font-size:16px;margin:20px 0 8px}
  .badge{font-size:11px;color:var(--muted);font-weight:400;margin-left:8px;text-transform:uppercase}
  tr.drill{cursor:pointer} tr.drill:hover{background:#efeae0}
  tr.det>td{background:#faf8f2;padding:0;border-bottom:2px solid var(--line)}
  .dbox{padding:10px 14px}
  details.sez{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:12px;overflow:hidden}
  details.sez>summary{cursor:pointer;padding:13px 16px;font-family:Georgia,serif;font-size:15.5px;list-style:none}
  details.sez>summary::-webkit-details-marker{display:none}
  details.sez>summary:before{content:'\25B8  ';color:var(--muted)}
  details.sez[open]>summary:before{content:'\25BE  '}
  details.sez>summary:hover{background:#efeae0}
  details.sez .panel{border:none;border-top:1px solid var(--line);border-radius:0;margin:0;max-height:60vh;overflow:auto}
  .cnt{font-family:system-ui;font-size:12.5px;color:var(--muted);font-weight:400;margin-left:8px}
</style></head>
<body>
<header>
  <h1>CDG_QV · Esplora</h1>
  <label>Periodo <select id="periodo"></select></label>
</header>
<div class="app">
  <nav class="side">
    <div class="navg">Conto economico</div>
    <div class="sec" data-s="ce" onclick="sezione('ce')">Riepilogo CE</div>
    <div class="sec on" data-s="ricavi" onclick="sezione('ricavi')">Ricavi</div>
    <div class="sec" data-s="materiali" onclick="sezione('materiali')">Costo dei materiali</div>
    <div class="sec" data-s="riconc" onclick="sezione('riconc')">Riconciliazione ↔ Co.Ge.</div>
    <div class="sec todo" data-s="commerciali" onclick="sezione('commerciali')">Costi commerciali</div>
    <div class="sec todo" data-s="trasporti" onclick="sezione('trasporti')">Costi di trasporto</div>
    <div class="sec todo" data-s="imballi" onclick="sezione('imballi')">Imballi</div>
    <div class="sec todo" data-s="finanziari" onclick="sezione('finanziari')">Costi finanziari</div>
    <div class="sec todo" data-s="resi" onclick="sezione('resi')">Resi da clienti</div>
    <div class="sec todo" data-s="recuperi" onclick="sezione('recuperi')">Recuperi forn./trasp.</div>
    <div class="navg">Raffronto (non nel CE)</div>
    <div class="sec" data-s="wap" onclick="sezione('wap')">MA_ItemsWAP</div>
    <div class="navg">Strumenti</div>
    <div class="sec" data-s="sql" onclick="sezione('sql')">Documentazione SQL</div>
  </nav>
  <div class="content">
    <section id="sec-ce" class="sez-main" style="display:none">
      <h2 class="grp">Conto economico · riepilogo</h2>
      <div id="ce"><p class="muted">Carico…</p></div>
    </section>

    <section id="sec-ricavi" class="sez-main">
      <h2 class="grp">Ricavi · documenti di vendita (Mago)</h2>
      <div id="v-doc">
        <div class="row">
          <div class="panel">
            <h2>Documenti</h2>
            <input id="q" placeholder="cerca per n° documento o cliente…" style="width:100%;margin-bottom:10px" oninput="cercaDeb()">
            <div class="lista"><table><thead><tr><th>Doc</th><th>Cliente</th><th class="num">Ricavo</th><th></th></tr></thead><tbody id="docs"></tbody></table></div>
          </div>
          <div class="panel"><h2>Dettaglio documento</h2><div id="dett"><p class="muted">Scegli un documento a sinistra.</p></div></div>
        </div>
      </div>
    </section>

    <section id="sec-riconc" class="sez-main" style="display:none">
      <h2 class="grp">Riconciliazione · costo del venduto CdG ↔ Contabilità</h2>
      <div id="riconc"><p class="muted">Carico…</p></div>
    </section>

    <section id="sec-materiali" class="sez-main" style="display:none">
      <h2 class="grp">Costo dei materiali · nostro costo (fonte principale)</h2>
      <p class="muted" style="margin-top:-4px">Basato sul ricalcolo parallelo mensile (<code>kodice.wap_ricalc</code> / <code>vw_costo_eff</code>). MA_ItemsWAP non è più la fonte: resta solo come <strong>raffronto</strong> (sezione dedicata a sinistra).</p>
      <p style="margin:2px 0 8px">
        <a href="#" onclick="elaboraMese();return false" style="display:inline-block;padding:5px 12px;background:#2f7d52;color:#fff;border-radius:5px;text-decoration:none;font-weight:600">▶ Elabora questo mese</a>
        <a href="#" onclick="rilanciaRic();return false" style="display:inline-block;padding:5px 12px;background:#2f5d8a;color:#fff;border-radius:5px;text-decoration:none;font-weight:600;margin-left:6px">↻ Ricalcola WAP anno in corso</a>
        <span class="muted"> &nbsp;<strong>Elabora</strong> = carica vendite + prepara costi + assembla il mese selezionato. <strong>Ricalcola WAP</strong> = ricostruisce i costi di magazzino dell'anno (dopo le bonifiche).</span></p>
      <div id="mesebanner"></div>
      <div class="subtabs">
        <div class="subtab on" data-sv="qual" onclick="sottoVista('qual')">Certificazione qualità</div>
        <div class="subtab" data-sv="bonifica" onclick="sottoVista('bonifica')">Bonifica apertura</div>
        <div class="subtab" data-sv="trend" onclick="sottoVista('trend')">Trend del costo</div>
        <div class="subtab" data-sv="raffronto" onclick="sottoVista('raffronto')">Raffronto vs Mago</div>
      </div>
      <div id="v-qual"><div id="qual"><p class="muted">Carico…</p></div></div>
      <div id="v-bonifica" style="display:none"><div id="bonifica"><p class="muted">Carico…</p></div></div>
      <div id="v-trend" style="display:none"><div id="trend"><p class="muted">Carico…</p></div></div>
      <div id="v-raffronto" style="display:none"><div id="raffronto"><p class="muted">Carico…</p></div></div>
    </section>

    <section id="sec-wap" class="sez-main" style="display:none">
      <h2 class="grp">MA_ItemsWAP · raffronto (NON usato nel conto economico)</h2>
      <p class="muted" style="margin-top:-4px">Dati del WAP di Mago, tenuti solo per <strong>confronto</strong>: nel conto economico il costo dei materiali userà il <strong>nostro</strong> calcolo. Anche le anomalie del motore attuale (basato su MA_ItemsWAP) sono qui finché non spostiamo il COGS sul nuovo costo.</p>
      <div class="subtabs">
        <div class="subtab on" data-sw="costi" onclick="sottoWap('costi')">Trend WAP Mago</div>
        <div class="subtab" data-sw="anom" onclick="sottoWap('anom')">Anomalie motore (attuale)</div>
      </div>
      <div id="v-costi"><div id="costi"><p class="muted">Carico…</p></div></div>
      <div id="v-anom" style="display:none"><div id="anom"><p class="muted">Carico…</p></div></div>
    </section>

    <section id="sec-commerciali" class="sez-main" style="display:none"><h2 class="grp">Costi commerciali</h2>
      <div class="panel"><p class="muted">🔧 In costruzione — provvigioni e costi variabili di vendita (MdC II): estrazione, attribuzione alla riga, certificazione.</p></div></section>
    <section id="sec-trasporti" class="sez-main" style="display:none"><h2 class="grp">Costi di trasporto</h2>
      <div class="panel"><p class="muted">🔧 In costruzione — costi di trasporto (MdC III). Funzione dedicata: <strong>gestione fatture vettori</strong> (DB <code>trasporti</code>, <code>KB_FattureVettori…</code>) con <strong>riconciliazione</strong> spedizioni/fatture.</p></div></section>
    <section id="sec-imballi" class="sez-main" style="display:none"><h2 class="grp">Imballi</h2>
      <div class="panel"><p class="muted">🔧 In costruzione — costi di imballaggio attribuibili.</p></div></section>
    <section id="sec-finanziari" class="sez-main" style="display:none"><h2 class="grp">Costi finanziari</h2>
      <div class="panel"><p class="muted">🔧 In costruzione — oneri finanziari.</p></div></section>
    <section id="sec-resi" class="sez-main" style="display:none"><h2 class="grp">Resi da clienti</h2>
      <div class="panel"><p class="muted">🔧 In costruzione — gestione e impatto dei resi da cliente.</p></div></section>
    <section id="sec-recuperi" class="sez-main" style="display:none"><h2 class="grp">Recuperi su fornitori/trasportatori</h2>
      <div class="panel"><p class="muted">🔧 In costruzione — ciò che si ribalta su fornitori/vettori per i reclami dei clienti.</p></div></section>

    <section id="sec-sql" class="sez-main" style="display:none">
      <h2 class="grp">Documentazione SQL</h2>
      <p class="muted" style="margin-top:0">Tutte le SQL del flusso, con spiegazione e <strong>definizione letta dal vivo dal database</strong> (non puo' divergere dal codice eseguito). Include il <strong>motore costi</strong> (kodice), versionato anche in <code>sql/motore/</code>.</p>
      <div id="sqlbox"><p class="muted">Carico…</p></div>
    </section>
  </div>
</div>
<script>
const $=s=>document.querySelector(s);
// Se l'app e' pubblicata sotto un sotto-percorso IIS (es. /tools/cdg/), antepone il prefisso a
// tutte le chiamate /api/... cosi' il reverse-proxy le instrada correttamente. A radice = nessun effetto.
const API = location.pathname.replace(/\/+$/,'');
const _origFetch = window.fetch.bind(window);
window.fetch = (u, o) => _origFetch((typeof u === 'string' && u.indexOf('/api/') === 0) ? API + u : u, o);
const eur=x=>(x==null?"—":Number(x).toLocaleString("it-IT",{minimumFractionDigits:2,maximumFractionDigits:2})+" €");
const num=x=>(x==null?"—":Number(x).toLocaleString("it-IT"));
let PER=null, SEL=null, PERIODI=[], SEZ='ricavi', SUBV='qual', SUBW='costi';

async function j(u){ const r=await fetch(u); return r.json(); }

// ---- Export Excel: un'icona su ogni tabella (auto, anche per quelle caricate dopo) ----
function nomeExport(){ const s=document.querySelector('.side .sec.on'); return 'CDG_'+((s?s.textContent:'export').trim().replace(/[^A-Za-z0-9]+/g,'_')); }
function tableToXls(tbl){
  const clone=tbl.cloneNode(true);
  clone.querySelectorAll('.noexp,.expbtn,a').forEach(e=>{ if(e.tagName==='A'){ e.replaceWith(document.createTextNode(e.textContent)); } else { e.remove(); } });
  const html='<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8">'
    +'<style>table,th,td{border:1px solid #ccc;border-collapse:collapse;padding:3px 6px;font-family:Arial;font-size:11px}</style></head>'
    +'<body>'+clone.outerHTML+'</body></html>';
  const blob=new Blob(['﻿'+html],{type:'application/vnd.ms-excel'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download=nomeExport()+'.xls'; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
function aggiungiExport(tbl){
  // niente icona sulle tabelle ANNIDATE (drill cliente, scheda costo, ...): il loro
  // contenitore viene rimosso/ricreato e il pulsante resterebbe orfano (duplicati).
  if(tbl.parentElement && tbl.parentElement.closest('table')) return;
  if(tbl.dataset.exp) return; tbl.dataset.exp='1';
  const btn=document.createElement('button'); btn.className='expbtn'; btn.title='Esporta la tabella in Excel';
  btn.textContent='↓ Excel';
  btn._tbl=tbl;   // riferimento alla tabella: per rimuovere il pulsante se la tabella sparisce
  btn.onclick=(e)=>{ e.preventDefault(); e.stopPropagation(); tableToXls(tbl); };
  // Ancora al PANNELLO (position:relative) in alto a destra: fuori dal flusso, niente colonna vuota.
  const panel=tbl.closest('.panel');
  if(panel){ panel.appendChild(btn); }
  else { let anchor=tbl; const wrap=tbl.closest('div[style*="overflow"]'); if(wrap) anchor=wrap;
         if(anchor.parentNode) anchor.parentNode.insertBefore(btn, anchor); }
}
function scanExport(){
  // rimuovi i pulsanti orfani (tabella non più nel DOM) per evitare duplicati accumulati
  document.querySelectorAll('.expbtn').forEach(b=>{ if(!b._tbl || !document.body.contains(b._tbl)) b.remove(); });
  document.querySelectorAll('.content table').forEach(aggiungiExport);
}
let _scanT;
function avviaExport(){ const o=new MutationObserver(()=>{ clearTimeout(_scanT); _scanT=setTimeout(scanExport,150); }); o.observe(document.body,{childList:true,subtree:true}); scanExport(); }

async function init(){
  const ps=await j("/api/periodi");
  const sel=$("#periodo");
  sel.innerHTML=ps.map(p=>`<option value="${p.anno}-${p.mese}">${p.anno}-${String(p.mese).padStart(2,'0')} · ${eur(p.ricavo)} · MdC I ${eur(p.mdc1)}</option>`).join("");
  if(ps.length){ sel.value=`${ps[ps.length-1].anno}-${ps[ps.length-1].mese}`; }
  PERIODI=ps; sel.onchange=onPeriodo;
  const h=location.hash.replace('#','');
  if(['qual','bonifica','trend'].includes(h)){ SUBV=h; sezione('materiali'); }
  else if(['anom','costi'].includes(h)){ SUBW=h; sezione('wap'); }
  else if(['ce','ricavi','materiali','riconc','wap','sql','commerciali','trasporti','imballi','finanziari','resi','recuperi'].includes(h)) sezione(h);
  else sezione('ricavi');
  avviaExport();
}
function periodo(){ const [a,m]=$("#periodo").value.split("-"); return {a:+a,m:+m}; }
function onPeriodo(){ SEL=null; cerca();
  if(SEZ==='ce') caricaCE();
  if(SEZ==='materiali'){ aggiornaStatoMese(); caricaSub(); }
  if(SEZ==='riconc') caricaRiconc();
  if(SEZ==='wap') sottoWap(SUBW);
}
function sezione(s){
  SEZ=s; location.hash=s;
  document.querySelectorAll('.side .sec').forEach(e=>e.classList.toggle('on',e.dataset.s===s));
  document.querySelectorAll('.sez-main').forEach(e=>e.style.display=(e.id==='sec-'+s)?'':'none');
  if(s==='ce') caricaCE();
  else if(s==='ricavi') cerca();
  else if(s==='materiali'){ aggiornaStatoMese(); sottoVista(SUBV); }
  else if(s==='riconc') caricaRiconc();
  else if(s==='wap') sottoWap(SUBW);
  else if(s==='sql') caricaSql();
}
async function caricaRiconc(){
  const {a,m}=periodo();
  const d=await j(`/api/riconciliazione_cogs?anno=${a}&mese_da=1&mese_a=${m}`);
  window._ricP={a,m};
  const co=d.contabile||{}, no=d.nostro||{};
  const gv=k=>((d.righe||[]).find(x=>x.k===k)||{}).val||0;
  const _X=gv('cogs'), _Y=gv('bilancio');
  let h=`<p class="muted">Periodo <strong>${a}</strong> (mesi 1–${m}, progressivo). Riconciliazione tra <strong>COGS calcolato (X)</strong> e <strong>consumo materie a contabilità (Y)</strong>: ogni scarto è una riga spiegata, i drill ▸ sommano al centesimo.</p>`;
  h+=`<div class="banner ok" style="font-size:13.5px;margin-bottom:10px">COGS calcolato <strong>X = ${eur(_X)}</strong> &nbsp;·&nbsp; Consumo contabile <strong>Y = ${eur(_Y)}</strong> &nbsp;·&nbsp; <strong>X − Y = ${eur(_X-_Y)}</strong> &nbsp;→&nbsp; spiegato dalle componenti qui sotto.</div>`;
  h+=`<div class="row" style="grid-template-columns:1fr 330px">`;
  h+=`<div class="panel"><h2>Da COGS (X) a Consumo contabile (Y)</h2><table><tbody>`;
  (d.righe||[]).forEach(x=>{
    const tot=x.tot?'font-weight:700;border-top:2px solid var(--line);background:#faf8f2':'';
    h+=`<tr class="${x.drill?'drill':''}" style="${tot}" ${x.drill?`onclick="ricDrill('${x.k}')"`:''}>
        <td>${x.drill?'<span class="muted">▸</span> ':'&nbsp;&nbsp;'}${esc(x.label)}</td>
        <td class="num">${eur(x.val)}</td></tr>
        <tr class="det" id="ricdet_${x.k}" style="display:none"><td colspan="2"><div class="dbox" id="ricbox_${x.k}"></div></td></tr>`;
  });
  h+=`</tbody></table></div>`;
  const imb=d.imballaggi||{};
  h+=`<div class="panel"><h2>Dati contabili (raffronto) — solo PRODOTTI</h2><table><tbody>
      <tr><td>Acquisti (GL 06011000 + oneri)</td><td class="num">${eur(co.acquisti)}</td></tr>
      <tr><td>+ Rimanenze iniziali (bilancio − imballaggi)</td><td class="num">${eur(co.rim_iniz)}</td></tr>
      <tr><td>− Rimanenze finali (bilancio − imballaggi)</td><td class="num">${eur(co.rim_fin)}</td></tr>
      <tr style="font-weight:700;border-top:2px solid var(--line)"><td>= Consumo materie (bilancio)</td><td class="num">${eur(co.consumo)}</td></tr>
      </tbody></table>
      <p class="muted" style="margin-top:12px;font-size:12px">Rimanenze <strong>nostre</strong> prodotti (ricalcolo): iniz ${eur(no.rim_iniz)} · fin ${eur(no.rim_fin)}<br>Carico merce prodotti nostro: ${eur(no.acquisti_carico)}</p>
      <div class="banner ok" style="margin-top:10px;font-size:12.5px">Ponte <strong>prodotti-contro-prodotti</strong>: gli imballaggi (ItemType 997) sono esclusi da carico, rimanenze e COGS. Ogni scarto è una <strong>riga spiegata</strong>; validazione implicita del costo del venduto.</div>
      </div></div>`;
  h+=`<div class="panel" style="margin-top:14px">
      <h2>Imballaggi — traccia separata (esclusi dal COGS prodotti)</h2>
      <p class="muted">Gli imballaggi entrano a magazzino col carico ma in contabilità sono <strong>costo</strong> (conto 06021505), non merce. Tenuti fuori dalla riconciliazione del costo del venduto. Valori dal nostro ricalcolo (il bilancio non separa il conto rimanenze).</p>
      <table><tbody>
      <tr><td>Rimanenze iniziali imballaggi</td><td class="num">${eur(imb.rim_iniz)}</td></tr>
      <tr><td>Rimanenze finali imballaggi</td><td class="num">${eur(imb.rim_fin)}</td></tr>
      <tr><td>Carico imballaggi nel periodo</td><td class="num">${eur(imb.carico)}</td></tr>
      <tr style="font-weight:700;border-top:2px solid var(--line)"><td>Consumo imballaggi (Δrim + carico)</td><td class="num">${eur((imb.rim_iniz||0)-(imb.rim_fin||0)+(imb.carico||0))}</td></tr>
      </tbody></table></div>`;
  h+=`<div class="panel" style="margin-top:14px">
      <h2>Ordini fatturati non ancora spediti alla chiusura</h2>
      <p class="muted">Competenza al periodo (mesi 1–${m}/${a}): <strong>${(d.ord_non_spediti||0).toLocaleString('it-IT')}</strong> ordini con <strong>fattura</strong> emessa (COGS registrato) ma merce non ancora uscita dal magazzino al ${m}/${a}. Per competenza include anche gli ordini spediti <em>dopo</em> la chiusura; esclude gli ordini non ancora fatturati (tipicamente B2B aperti). Fonte: <code>VwKLStatoOrdini</code> (<code>CompletamenteConsegnato='No'</code> + spediti post-chiusura). <a href="#" onclick="ricDrill('ordnonsped');return false">▸ elenco documenti</a></p>
      <div id="ricdet_ordnonsped" style="display:none;margin-top:8px"><div class="dbox" id="ricbox_ordnonsped"></div></div>
      </div>`;
  $("#riconc").innerHTML=h;
}
async function ricDrill(k){
  const row=document.getElementById('ricdet_'+k), box=document.getElementById('ricbox_'+k);
  if(!row) return;
  if(row.style.display!=='none'){ row.style.display='none'; return; }
  document.querySelectorAll('#riconc tr.det').forEach(e=>e.style.display='none');
  row.style.display=''; box.innerHTML='<p class="muted">Carico…</p>';
  const {a,m}=window._ricP;
  const d=await j(`/api/riconciliazione_drill?k=${k}&anno=${a}&mese_da=1&mese_a=${m}`);
  if(!d || !d.length){ box.innerHTML='<p class="muted">Nessun dettaglio per questa voce.</p>'; return; }
  const cols=Object.keys(d[0]);
  const isEuro=c=>/val|cost|cogs|scaric|differ|importo|riacq|magazz|document/i.test(c);
  const fmt=(c,v)=> (typeof v==='number') ? (isEuro(c)?eur(v):Number(v).toLocaleString('it-IT')) : esc(String(v==null?'':v));
  let t=`<table class="sticky"><thead><tr>${cols.map(c=>`<th class="${typeof d[0][c]==='number'?'num':''}">${esc(c)}</th>`).join('')}</tr></thead><tbody>`;
  t+=d.map(r=>`<tr>${cols.map(c=>`<td class="${typeof r[c]==='number'?'num':''}">${fmt(c,r[c])}</td>`).join('')}</tr>`).join('');
  t+=`</tbody></table>`;
  box.innerHTML=`<div style="max-height:48vh;overflow:auto">${t}</div>`;
}
async function aggiornaStatoMese(){
  const {a,m}=periodo(); const mm=`${a}-${String(m).padStart(2,'0')}`;
  const s=await j(`/api/stato_mese?anno=${a}&mese=${m}`);
  if(s.Stato==='CONSOLIDATO'){
    $("#mesebanner").innerHTML=`<div class="banner ok">${dot('#2f7d52')}<strong>Costi ${mm} CONSOLIDATI</strong> — certificato il ${esc(s.DataStato||'')}${s.Utente?' da '+esc(s.Utente):''}. Base solida per le vendite del mese successivo. <a href="#" onclick="consolidaMese(1);return false">riapri</a></div>`;
  } else {
    $("#mesebanner").innerHTML=`<div class="banner warn">${dot('#e0a800')}<strong>Costi ${mm} IN FORMAZIONE — stima incompleta</strong> (documenti del mese non ancora tutti caricati). <a href="#" onclick="consolidaMese(0);return false"><strong>Consolida prezzi del mese</strong></a></div>`;
  }
}
async function consolidaMese(riapri){
  const {a,m}=periodo(); const mm=`${a}-${String(m).padStart(2,'0')}`;
  if(!riapri && !confirm('Confermi che TUTTI i documenti di '+mm+' sono caricati e consolidi i costi del mese?')) return;
  await fetch('/api/consolida_mese',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify(riapri?{anno:a,mese:m,riapri:1}:{anno:a,mese:m})});
  aggiornaStatoMese();
}
function sottoVista(v){
  SUBV=v;
  document.querySelectorAll('#sec-materiali .subtab').forEach(e=>e.classList.toggle('on',e.dataset.sv===v));
  ['qual','bonifica','trend','raffronto'].forEach(x=>{ const el=$('#v-'+x); if(el) el.style.display=(x===v)?'':'none'; });
  if(v==='qual') caricaQual(); else if(v==='bonifica') caricaBonifica();
  else if(v==='trend') caricaTrend(); else if(v==='raffronto') caricaRaffronto();
}
async function caricaRaffronto(){
  const {a,m}=periodo();
  const d=await j(`/api/raffronto_costo?anno=${a}&mese=${m}`);
  const R=d.righe||[];
  const tot=R.reduce((s,r)=>({n:s.n+Number(r.mat_nostro||0),w:s.w+Number(r.mat_wap||0),mg:s.mg+Number(r.mat_mago||0)}),{n:0,w:0,mg:0});
  let h=`<div class="cards">
    <div class="kpi"><div class="v">${eur(tot.n)}</div><div class="l">Materiale NOSTRO</div></div>
    <div class="kpi"><div class="v">${eur(tot.mg)}</div><div class="l">Materiale "mensile Mago" (Qlik)</div></div>
    <div class="kpi ${Math.abs(tot.n-tot.mg)>1?'bad':''}"><div class="v">${(tot.n-tot.mg>=0?'+':'')+eur(tot.n-tot.mg)}</div><div class="l">Δ nostro − Qlik</div></div>
    <div class="kpi"><div class="v">${eur(tot.w)}</div><div class="l">Materiale a WAP Mago</div></div></div>
  <p class="muted">Periodo ${a}-${String(m).padStart(2,'0')}. Confronto del costo del venduto per articolo: <strong>nostro</strong> (ricalcolo) vs <strong>WAP</strong> Mago (risalita) vs <strong>"mensile Mago"</strong> (<code>costomaterialemensile</code>, quello che alimenta il CE Qlik). Ordinato per impatto del Δ nostro−Qlik. Il totale Δ spiega lo scostamento di Materiale tra i due CE.</p>`;
  const ucost=(mat,q)=> q?mat/q:null;
  h+=`<div class="panel" style="padding:0"><div style="max-height:62vh;overflow:auto"><table class="sticky"><thead><tr>
    <th>Articolo</th><th>Descrizione</th><th class="num">Q.tà</th>
    <th class="num">C.unit nostro</th><th class="num">C.unit WAP</th><th class="num">C.unit Mago-mens.</th>
    <th class="num">Mat. nostro</th><th class="num">Mat. WAP</th><th class="num">Mat. Qlik</th><th class="num">Δ nostro−Qlik</th></tr></thead><tbody>`;
  h+=R.slice(0,400).map(r=>{ const dl=Number(r.mat_nostro||0)-Number(r.mat_mago||0);
    return `<tr><td><a href="#" onclick="costoDett('${esc(r.Item)}',this);return false"><code>${esc(r.Item)}</code></a></td>
      <td>${esc((r.descr||'').slice(0,38))}</td><td class="num">${num(r.qta)}</td>
      <td class="num">${eur(ucost(r.mat_nostro,r.qta))}</td><td class="num">${r.wap_unit!=null?eur(r.wap_unit):'—'}</td>
      <td class="num">${eur(ucost(r.mat_mago,r.qta))}</td>
      <td class="num">${eur(r.mat_nostro)}</td><td class="num">${r.mat_wap!=null?eur(r.mat_wap):'—'}</td><td class="num">${eur(r.mat_mago)}</td>
      <td class="num" style="font-weight:600;color:${Math.abs(dl)<0.5?'inherit':(dl>0?'var(--bad)':'#1a7f37')}">${(dl>=0?'+':'')+eur(dl)}</td></tr>`;
  }).join("") || `<tr><td colspan="10" class="muted">Nessun dato.</td></tr>`;
  h+=`</tbody></table></div></div>`;
  $("#raffronto").innerHTML=h;
}
function caricaSub(){ sottoVista(SUBV); }
function sottoWap(v){
  SUBW=v;
  document.querySelectorAll('#sec-wap .subtab').forEach(e=>e.classList.toggle('on',e.dataset.sw===v));
  ['costi','anom'].forEach(x=>{ const el=$('#v-'+x); if(el) el.style.display=(x===v)?'':'none'; });
  if(v==='costi') caricaCosti(); else if(v==='anom') caricaAnom();
}
let CEDIM='canale', CEANNO=true;
function setCeDim(d){ CEDIM=d; caricaCE(); }
function setCeAnno(v){ CEANNO=v; caricaCE(); }
async function caricaCE(){
  const {a,m}=periodo(); const mm=String(m).padStart(2,'0');
  const d=await j(`/api/ce?anno=${a}${CEANNO?'':'&mese='+m}&dim=${CEDIM}`);
  const tot=d.righe.reduce((s,r)=>({f:s.f+Number(r.fatturato||0),ma:s.ma+Number(r.materiale||0),mg:s.mg+Number(r.margine||0)}),{f:0,ma:0,mg:0});
  const pct=v=>tot.f?(100*v/tot.f).toFixed(1)+'%':'0%';
  const dims=[['canale','Canale'],['dipartimento','Dipartimento'],['cliente','Cliente'],['agente','Agente'],['tipo_articolo','Tipo Articolo'],['linea_articolo','Linea Articolo'],['mese','Mese']];
  const etich=dims.find(x=>x[0]===CEDIM)[1];
  let h=`<div class="cards">
    <div class="kpi"><div class="v">${eur(tot.f)}</div><div class="l">Fatturato</div></div>
    <div class="kpi"><div class="v">${eur(tot.ma)}</div><div class="l">Materiale (nostro costo)</div></div>
    <div class="kpi"><div class="v">${eur(tot.mg)}</div><div class="l">Margine</div></div>
    <div class="kpi"><div class="v">${pct(tot.mg)}</div><div class="l">% Margine</div></div></div>
  <p class="muted">Periodo: `
    + `<a href="#" onclick="setCeAnno(false);return false" style="margin:0 4px;${!CEANNO?'font-weight:700;text-decoration:underline':''}">Mese ${a}-${mm}</a>·`
    + `<a href="#" onclick="setCeAnno(true);return false" style="margin:0 4px;${CEANNO?'font-weight:700;text-decoration:underline':''}">Anno intero ${a}</a>`
    + ` &nbsp;|&nbsp; Dimensione: `
    + dims.map(x=>`<a href="#" onclick="setCeDim('${x[0]}');return false" style="margin:0 4px;${CEDIM===x[0]?'font-weight:700;text-decoration:underline':''}">${x[1]}</a>`).join('·')
    + `. <em>Materiale</em> = nostro costo certificato (ricalcolo WAP). Imballi/Trasporto/Commerciali/Finanziari in costruzione (verranno dalle stesse fonti del CE Qlik).</p>`;
  h+=`<div class="panel" style="padding:0"><div style="max-height:62vh;overflow:auto"><table class="sticky"><thead><tr>
    <th>${etich}</th><th class="num">Fatturato</th><th class="num">Materiale</th><th class="num">% Mat</th><th class="num">Imballi</th>
    <th class="num">Trasporto</th><th class="num">Commerciali</th><th class="num">Finanziari</th>
    <th class="num">Margine</th><th class="num">% Margine</th><th class="num">Nr. Ordini</th></tr></thead><tbody>`;
  h+=`<tr style="font-weight:700;background:#efeae0"><td>Totali</td><td class="num">${eur(tot.f)}</td><td class="num">${eur(tot.ma)}</td>
    <td class="num">${pct(tot.ma)}</td><td class="num muted">—</td><td class="num muted">—</td><td class="num muted">—</td><td class="num muted">—</td>
    <td class="num">${eur(tot.mg)}</td><td class="num">${pct(tot.mg)}</td><td class="num"></td></tr>`;
  h+=d.righe.map(r=>`<tr><td>${CEDIM!=='cliente'
        ? `<a href="#" onclick="ceDrill(this);return false" data-val="${esc(String(r.dim==null?'(n/d)':r.dim)).replace(/"/g,'&quot;')}" title="drill-down sui clienti">${esc(r.dim||'(n/d)')}</a>`
        : esc(r.dim||'(n/d)')}</td>
    <td class="num">${eur(r.fatturato)}</td><td class="num">${eur(r.materiale)}</td>
    <td class="num">${r.fatturato?(100*Number(r.materiale)/Number(r.fatturato)).toFixed(1):'0'}%</td>
    <td class="num muted">—</td><td class="num muted">—</td><td class="num muted">—</td><td class="num muted">—</td>
    <td class="num">${eur(r.margine)}</td><td class="num">${r.fatturato?(100*Number(r.margine)/Number(r.fatturato)).toFixed(1):'0'}%</td>
    <td class="num">${num(r.n_ordini)}</td></tr>`).join("") || `<tr><td colspan="11" class="muted">Nessun dato.</td></tr>`;
  h+=`</tbody></table></div></div>`;
  $("#ce").innerHTML=h;
}
async function ceDrill(el){
  const tr=el.closest('tr'), nxt=tr.nextElementSibling;
  if(nxt && nxt.classList.contains('cedrill')){ nxt.remove(); return; }
  const val=el.dataset.val;
  const {a,m}=periodo();
  const d=await j(`/api/ce_drill?anno=${a}${CEANNO?'':'&mese='+m}&dim=${CEDIM}&val=${encodeURIComponent(val)}`);
  const R=d.righe||[];
  let inner=`<div style="padding:6px 10px"><strong>${R.length}</strong> clienti in «${esc(val)}»`;
  if(CEDIM==='canale' && val==='(n/d)') inner+=` <span class="muted">— non agganciano alcun canale: manca la categoria cliente o la sua mappatura (Raggrupp_Categorie). La colonna Categoria/Notes mostra il perché.</span>`;
  inner+=`<table style="margin-top:6px"><thead><tr><th>Cliente</th><th>Codice</th><th>Categoria</th><th>Notes (Raggr.)</th>
     <th class="num">Fatturato</th><th class="num">Materiale</th><th class="num">% Mat</th><th class="num">Margine</th><th class="num">Nr. Ord.</th></tr></thead><tbody>`;
  inner+=R.map(x=>`<tr><td>${esc(x.cliente||'')}</td><td><code>${esc(x.codice||'')}</code></td>
     <td>${x.categoria?esc(x.categoria):'<span class="muted">—</span>'}</td>
     <td>${x.notes?esc(x.notes):'<span class="muted">—</span>'}</td>
     <td class="num">${eur(x.fatturato)}</td><td class="num">${eur(x.materiale)}</td>
     <td class="num">${x.fatturato?(100*Number(x.materiale)/Number(x.fatturato)).toFixed(1):'0'}%</td>
     <td class="num">${eur(x.margine)}</td><td class="num">${num(x.n_ordini)}</td></tr>`).join("")
     || `<tr><td colspan="9" class="muted">Nessun cliente.</td></tr>`;
  inner+=`</tbody></table></div>`;
  const det=document.createElement('tr'); det.className='cedrill';
  const td=document.createElement('td'); td.colSpan=tr.children.length; td.style.background='#f6f3ec'; td.innerHTML=inner;
  det.appendChild(td); tr.after(det);
}

let deb;
function cercaDeb(){ clearTimeout(deb); deb=setTimeout(cerca,250); }
async function cerca(){
  const {a,m}=periodo(); const q=encodeURIComponent($("#q").value);
  const ds=await j(`/api/documenti?anno=${a}&mese=${m}&q=${q}`);
  $("#docs").innerHTML=ds.map(d=>`<tr class="doc ${SEL===d.sale_doc_id?'sel':''}" onclick="apri(${d.sale_doc_id})">
     <td><strong>${d.docno||d.sale_doc_id}</strong><br><span class="muted">${d.cust||''}</span></td>
     <td>${(d.cliente||'').slice(0,28)}${d.senza_costo>0?` <span class="pill warn">${d.senza_costo} senza costo</span>`:''}</td>
     <td class="num">${eur(d.ricavo)}<br><span class="muted">MdC ${eur(d.mdc1)}</span></td>
     <td>›</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Nessun documento.</td></tr>`;
}
async function apri(doc){
  SEL=doc; cerca();
  const {a,m}=periodo();
  const d=await j(`/api/documento?anno=${a}&mese=${m}&doc=${doc}`);
  const t=d.testata||{};
  let h=`<p><strong>Doc ${t.DocNo||doc}</strong> · ${t.CompanyName||t.CustSupp||''} · ${t.data||''}</p>`;
  d.linee.forEach((l,i)=>{
    const senza = !l.componenti.some(c=>c.codice_componente==='COSTO_VENDUTO');
    h+=`<div class="ln"><div class="h">
        <div><span class="art">${l.codice_articolo||'—'}</span> <span class="pill">${l.tipo_articolo||''}</span>
          <div class="desc">${(l.descr||'').slice(0,70)}</div></div>
        <div style="text-align:right"><div>Q.tà ${num(l.quantita)} · Ricavo <strong>${eur(l.ricavo)}</strong></div>
          <div class="muted">MdC I ${eur(l.mdc1)}</div></div></div>`;
    h+=`<div class="comp"><table><thead><tr><th>Componente</th><th>Liv.</th><th class="num">Importo</th><th>Origine</th></tr></thead><tbody>`;
    if(l.componenti.length){
      l.componenti.forEach(c=>{ h+=`<tr><td>${c.descrizione||c.codice_componente}</td><td>${c.livello||'—'}</td>
        <td class="num ${c.segno<0?'neg':''}">${c.segno<0?'−':''}${eur(c.importo)}</td><td class="muted">${c.origine||''}</td></tr>`; });
    } else { h+=`<tr><td colspan="4" class="muted">Nessun componente.</td></tr>`; }
    if(senza) h+=`<tr><td colspan="4"><span class="pill warn">articolo senza costo certificato</span>
        <a href="#" onclick="diagArt('${(l.codice_articolo||'').replace(/'/g,"\\'")}',${i});return false" style="margin-left:8px">🔍 perché?</a>
        <div id="diag${i}" class="diagbox" style="display:none"></div></td></tr>`;
    h+=`</tbody></table></div></div>`;
  });
  $("#dett").innerHTML=h;
}
async function diagArt(item, i){
  const box=document.getElementById('diag'+i);
  if(box.dataset.loaded){ box.style.display = box.style.display==='none'?'':'none'; return; }
  box.style.display=''; box.innerHTML='<p class="muted">Carico…</p>';
  const {a,m}=periodo();
  const d=await j(`/api/diagnosi_articolo?item=${encodeURIComponent(item)}&anno=${a}&mese=${m}`);
  let h='';
  if(!d.is_kit){
    h=`<p class="muted">Articolo a magazzino (non kit): il costo non è certificato per ${a}-${String(m).padStart(2,'0')}.`
      + (d.kit_eccezione?` — <span class="pill warn">${esc(d.kit_eccezione.tipo)}: ${esc(d.kit_eccezione.dettaglio||'')}</span>`:'')
      + `</p>`;
  } else {
    h=`<p>Distinta · ${d.componenti.length} componenti`
      + (d.kit_eccezione?` — <span class="pill warn">${esc(d.kit_eccezione.tipo)}: ${esc(d.kit_eccezione.dettaglio||'')}</span>`:'')
      + `</p><table><thead><tr><th>Componente</th><th>Descrizione</th><th class="num">Q.tà</th><th class="num">Costo</th><th>Stato</th></tr></thead><tbody>`;
    h+=d.componenti.map(c=>{
      const ok = c.costo!=null && Number(c.costo)>0 && !c.ecc_tipo;
      const stato = ok
        ? `<span class="pill" style="background:#dff3e4;color:#1a7f37">OK</span>`
        : `<span class="pill warn">${c.ecc_tipo?esc(c.ecc_tipo)+(c.ecc_dettaglio?' · '+esc(c.ecc_dettaglio):''):'costo mancante / 0'}</span>`;
      return `<tr${ok?'':' style="background:#fff7f0"'}><td><code>${esc(c.componente)}</code></td>
        <td>${esc((c.descr||'').slice(0,40))}</td><td class="num">${num(c.qty)}</td>
        <td class="num">${c.costo!=null?eur(c.costo):'—'}</td><td>${stato}</td></tr>`;
    }).join("");
    h+=`</tbody></table>`;
  }
  box.innerHTML=h; box.dataset.loaded='1';
}

let COSTIMESI=6;
function setCostiMesi(m){ COSTIMESI=m; caricaCosti(); }
async function caricaCosti(){
  const d=await j(`/api/costi_wap?mesi=${COSTIMESI}`);
  const su =d.righe.filter(x=>Number(x.delta)>0).sort((a,b)=>Number(b.pct)-Number(a.pct));
  const giu=d.righe.filter(x=>Number(x.delta)<0).sort((a,b)=>Number(a.pct)-Number(b.pct));
  let h=`<div class="cards">
    <div class="kpi bad"><div class="v">${num(su.length)}</div><div class="l">costo in AUMENTO</div></div>
    <div class="kpi"><div class="v">${num(giu.length)}</div><div class="l">costo in CALO</div></div>
    <div class="kpi"><div class="v">${COSTIMESI} mesi</div><div class="l">orizzonte confronto</div></div></div>`;
  h+=`<p class="muted">WAPCost: ultimo periodo vs ~${COSTIMESI} mesi prima (ordinati per Δ%). Orizzonte: `
     + [1,3,6,12].map(m=>`<a href="#costi" onclick="setCostiMesi(${m});return false" style="margin:0 4px;${m===COSTIMESI?'font-weight:700;text-decoration:underline':''}">${m} mesi</a>`).join('·')
     + `</p>`;
  const col = x => Number(x)>0 ? 'color:var(--bad)' : 'color:#1a7f37';
  const tabella=(arr,titolo)=>{
    let t=`<details class="sez" open><summary>${titolo}<span class="cnt">${arr.length}</span></summary><div class="panel"><table>`
      + `<thead><tr><th>Articolo</th><th>Descrizione</th><th class="num">Giac.</th><th class="num">Costo ${COSTIMESI}m fa</th>`
      + `<th class="num">Costo attuale</th><th class="num">Δ</th><th class="num">Δ%</th></tr></thead><tbody>`;
    t+= arr.slice(0,250).map(x=>`<tr>
        <td><code>${esc(x.Item)}</code></td>
        <td>${esc((x.descr||'').slice(0,46))}</td>
        <td class="num">${num(x.giacenza)}</td>
        <td class="num">${eur(x.costo_rif)}<br><span class="muted" style="font-size:11px">${x.data_rif||''}</span></td>
        <td class="num">${eur(x.costo_attuale)}<br><span class="muted" style="font-size:11px">${x.data_attuale||''}</span></td>
        <td class="num" style="${col(x.delta)}">${Number(x.delta)>0?'+':''}${eur(x.delta)}</td>
        <td class="num" style="${col(x.pct)};font-weight:600">${Number(x.pct)>0?'+':''}${x.pct}%</td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">Nessun articolo.</td></tr>`;
    return t+`</tbody></table></div></details>`;
  };
  h+= tabella(su,"📈 Costo in AUMENTO") + tabella(giu,"📉 Costo in CALO");
  $("#costi").innerHTML=h;
}
let TRENDMESI=3, TRENDSOGLIA=10;
function setTrend(mesi,soglia){ if(mesi!=null)TRENDMESI=mesi; if(soglia!=null)TRENDSOGLIA=soglia; caricaTrend(); }
async function caricaTrend(){
  const d=await j(`/api/trend_costo?mesi=${TRENDMESI}&soglia=${TRENDSOGLIA}`);
  const su=d.righe.filter(x=>Number(x.delta)>0).sort((a,b)=>Number(b.pct)-Number(a.pct));
  const giu=d.righe.filter(x=>Number(x.delta)<0).sort((a,b)=>Number(a.pct)-Number(b.pct));
  const M=['','Gen','Feb','Mar','Apr','Mag','Giu','Lug','Ago','Set','Ott','Nov','Dic'];
  let h=`<div class="cards">
    <div class="kpi bad"><div class="v">${num(su.length)}</div><div class="l">costo in AUMENTO</div></div>
    <div class="kpi"><div class="v">${num(giu.length)}</div><div class="l">costo in CALO</div></div>
    <div class="kpi"><div class="v">${TRENDMESI} mesi</div><div class="l">orizzonte</div></div>
    <div class="kpi"><div class="v">&ge; ${TRENDSOGLIA}%</div><div class="l">soglia evidenziata</div></div></div>
  <p class="muted">Variazione del <strong>nostro costo</strong> (wap_ricalc): ultimo mese vs ~${TRENDMESI} mesi prima, oltre soglia → da verificare. Orizzonte: `
     + [1,2,3,6].map(m=>`<a href="#" onclick="setTrend(${m},null);return false" style="margin:0 4px;${m===TRENDMESI?'font-weight:700;text-decoration:underline':''}">${m}m</a>`).join('·')
     + ` · Soglia: ` + [5,10,20,30].map(s=>`<a href="#" onclick="setTrend(null,${s});return false" style="margin:0 4px;${s===TRENDSOGLIA?'font-weight:700;text-decoration:underline':''}">${s}%</a>`).join('·') + `</p>`;
  const col=x=>Number(x)>0?'color:var(--bad)':'color:#1a7f37';
  const tab=(arr,tit)=>{
    let t=`<details class="sez" open><summary>${tit}<span class="cnt">${arr.length}</span></summary><div class="panel" style="padding:0"><div style="max-height:60vh;overflow:auto"><table class="sticky">`
      +`<thead><tr><th>Articolo</th><th>Descrizione</th><th class="num">Giac.</th><th class="num">Costo ~${TRENDMESI}m fa</th><th class="num">Costo attuale</th><th class="num">Δ</th><th class="num">Δ%</th></tr></thead><tbody>`;
    t+=arr.slice(0,400).map(x=>`<tr>
        <td><a href="#" onclick="costoDett('${esc(x.Item)}',this);return false"><code>${esc(x.Item)}</code></a></td>
        <td>${esc((x.descr||'').slice(0,44))}</td>
        <td class="num">${num(x.giacenza)}</td>
        <td class="num">${eur(x.costo_rif)}<br><span class="muted" style="font-size:11px">${M[x.mese_rif]||''}</span></td>
        <td class="num">${eur(x.costo_attuale)}<br><span class="muted" style="font-size:11px">${M[x.mese_attuale]||''}</span></td>
        <td class="num" style="${col(x.delta)}">${Number(x.delta)>0?'+':''}${eur(x.delta)}</td>
        <td class="num" style="${col(x.pct)};font-weight:600">${Number(x.pct)>0?'+':''}${x.pct}%</td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">Nessun articolo oltre soglia.</td></tr>`;
    return t+`</tbody></table></div></div></details>`;
  };
  h+= tab(su,"📈 Costo in AUMENTO") + tab(giu,"📉 Costo in CALO");
  $("#trend").innerHTML=h;
}
const dot=c=>`<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${c};margin-right:5px;vertical-align:middle"></span>`;
async function caricaQual(){
  const {a,m}=periodo();
  const d=await j(`/api/qualita?anno=${a}&mese=${m}`);
  const cnt=l=>{const x=d.score.find(s=>s.Livello===l);return x?x.n:0;};
  const verde=cnt('VERDE'), giallo=cnt('GIALLO'), rosso=cnt('ROSSO');
  const v=d.valore||{}; const idx=(v.val_tot&&v.val_tot>0)?(100*v.val_ok/v.val_tot):100;
  let h=`<div class="cards">
    <div class="kpi"><div class="v">${idx.toFixed(1)}%</div><div class="l">Indice qualità (valore certificato)</div></div>
    <div class="kpi"><div class="v" style="color:#1a7f37">${num(verde)}</div><div class="l">${dot('#2f7d52')}OK (automatico)</div></div>
    <div class="kpi"><div class="v" style="color:#b8780a">${num(giallo)}</div><div class="l">${dot('#e0a800')}Warning · da rivedere</div></div>
    <div class="kpi bad"><div class="v">${num(rosso)}</div><div class="l">${dot('#c0392b')}Errore · bloccante</div></div></div>
  <p class="muted">Periodo ${a}-${String(m).padStart(2,'0')}. Clicca un <strong>codice articolo</strong> per vedere come si forma il costo. <em>Q2 (WAP Mago azzerato)</em> è solo informativo: Mago è rotto e il nostro costo è quello buono.</p>`;
  h+=`<div class="panel" style="margin-bottom:16px">
    <h2 style="margin-bottom:8px">Scheda costo · cerca QUALUNQUE articolo (anche OK)</h2>
    <input id="acerca" placeholder="codice o descrizione…" autocomplete="off" oninput="cercaArtDeb()" style="width:60%">
    <div id="aris"></div><div id="ascheda" style="margin-top:8px"></div></div>`;
  const pill=l=> l==='ROSSO'?dot('#c0392b')+'<strong style="color:#c0392b">Errore</strong>'
                 :(l==='GIALLO'?dot('#e0a800')+'<strong style="color:#b8780a">Warning</strong>':dot('#2f7d52')+'OK');
  h+=`<div class="panel"><table><thead><tr><th>Articolo</th><th>Descrizione</th><th>Livello</th><th>Indici attivi</th>
      <th class="num">Nostro costo</th><th class="num">WAP Mago</th><th class="num">Q.tà</th><th>Stato / azione</th></tr></thead><tbody>`;
  h+= d.lista.map(x=>{
     const id=esc(x.Item);
     const az = x.Stato
        ? `<span class="pill ok">${esc(x.Stato)}</span> · <a href="#" onclick="cert('${id}','',0);return false" class="muted">riapri</a>`
        : `<a href="#" onclick="cert('${id}','CERTIFICATO',0);return false">Certifica</a> · `
          +`<a href="#" onclick="cert('${id}','DA_CORREGGERE_ALGORITMO',1);return false" style="color:var(--warn)">Segnala</a> · `
          +`<a href="#" onclick="cert('${id}','IGNORATO',1);return false" class="muted">Ignora</a>`;
     return `<tr><td><a href="#" onclick="costoDett('${id}',this);return false" title="come si forma il costo"><code>${id}</code></a></td>
        <td>${esc((x.descr||'').slice(0,40))}</td>
        <td>${pill(x.Livello)}</td><td style="font-size:12px">${esc((x.Flags||'').replace(/,/g,' · '))}</td>
        <td class="num">${eur(x.WAPCost_ricalc)}</td><td class="num">${x.WAPCost_Mago?eur(x.WAPCost_Mago):'—'}</td>
        <td class="num">${num(x.QtaFin)}</td><td style="font-size:12px">${az}</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="muted">Nessun caso da rivedere.</td></tr>`;
  h+=`</tbody></table></div>`;
  $("#qual").innerHTML=h;
}
async function cert(item, stato, chiediNota){
  let nota='';
  if(chiediNota){ const r=prompt('Nota per "'+stato+'" (facoltativa):'); if(r===null) return; nota=r; }
  const {a,m}=periodo();
  await fetch('/api/certifica',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({item, anno:a, mese:m, stato: stato||'DA_CERTIFICARE', nota})});
  caricaQual();
}
const MESI=['','Gen','Feb','Mar','Apr','Mag','Giu','Lug','Ago','Set','Ott','Nov','Dic'];
function movLabel(t,rsn){
  if(t===2032533505) return 'Acquisto (+)';
  if(t===2032533506) return 'Vendita (−)';
  if(t===2032533509) return 'Reso (+)';
  if(['CAR-AMA','KLRI-P-A','RI-POS'].includes(rsn)) return 'Rettif./Carico (+)';
  if(['KLRI-N-A','RI-NEG','KLR-FORA'].includes(rsn)) return 'Rettif./Scarico (−)';
  return 'Ignorato';
}
async function costoDett(item, el, anno){
  const tr=el.closest('tr'), nxt=tr.nextElementSibling;
  if(nxt && nxt.classList.contains('det')){ nxt.remove(); return; }
  const a = anno || periodo().a;
  const d=await j(`/api/costo_dettaglio?item=${encodeURIComponent(item)}&anno=${a}`);
  const det=document.createElement('tr'); det.className='det';
  const td=document.createElement('td'); td.colSpan=tr.children.length;
  td.innerHTML=`<div class="dbox" style="font-size:12px;color:var(--muted);margin-bottom:-6px">Movimenti e roll del <strong>${a}</strong></div>`+renderCostoDett(d, item, a);
  det.appendChild(td); tr.after(det);
}
async function caricaBonifica(){
  const d=await j('/api/bonifica');
  const imp=x=>Number(x.Giacenza||0)*Number(x.OverrideSuggerito||0);
  const cert=d.filter(x=>x.OverrideAttuale!=null);
  const daDef=d.filter(x=>x.OverrideAttuale==null && x.Categoria==='A' && !(x.OverrideSuggerito>0)); // nessun costo: decisione manuale
  const sugg=d.filter(x=>x.OverrideAttuale==null && x.Categoria==='A' && x.OverrideSuggerito>0);      // valore proposto: bulk
  const catB=d.filter(x=>x.OverrideAttuale==null && x.Categoria==='B');
  const suggImp=sugg.reduce((s,x)=>s+imp(x),0);
  // default: mostra solo cio' che richiede una SCELTA (da definire + scostamenti + gia certificati). I 'suggeriti' stanno dietro al bottone.
  const vis = window._bonifTutti ? d : d.filter(x=> x.OverrideAttuale!=null || x.Categoria==='B' || (x.Categoria==='A' && !(x.OverrideSuggerito>0)));
  let h=`<div class="cards">
    <div class="kpi"><div class="v" style="color:#c0392b">${num(daDef.length)}</div><div class="l">da definire (manuale)</div></div>
    <div class="kpi"><div class="v" style="color:#b8780a">${num(sugg.length)}</div><div class="l">valore proposto (bulk)</div></div>
    <div class="kpi"><div class="v">${num(catB.length)}</div><div class="l">${dot('#e0a800')}B · scostamento</div></div>
    <div class="kpi"><div class="v" style="color:#1a7f37">${num(cert.length)}</div><div class="l">${dot('#2f7d52')}certificati</div></div></div>
  <p class="muted">Universo = <strong>tutto il magazzino</strong> (giacenza Mago, ogni deposito). Non lavori 500 righe a mano: <strong>${num(daDef.length)} richiedono una tua scelta</strong> (giacenza ma nessun costo da nessuna fonte); gli altri hanno gia' un valore proposto. Clicca il <strong>codice</strong> per i movimenti. Poi <a href="#" onclick="rilanciaRic();return false"><strong>↻ Applica bonifica</strong></a>.</p>`;
  if(sugg.length) h+=`<div class="panel" style="background:#fff8e6;border:1px solid #e0a800;margin-bottom:8px">
    <strong>${num(sugg.length)} articoli</strong> con giacenza senza valore nostro ma un <strong>costo proposto</strong> (LastCost/WAP Mago) · impatto ~${eur(suggImp)}.
    <a href="#" onclick="certSuggeritiBulk(${sugg.length});return false"><strong>✓ Certifica tutti i suggeriti</strong></a></div>`;
  h+=`<p style="margin:4px 0"><a href="#" onclick="window._bonifTutti=${window._bonifTutti?'false':'true'};caricaBonifica();return false">${window._bonifTutti?'« mostra solo da decidere':`mostra tutti i ${num(d.length)} candidati »`}</a></p>`;
  h+=`<div class="panel" style="padding:0"><div style="max-height:72vh;overflow:auto"><table class="sticky"><thead><tr><th>Articolo</th><th>Descrizione</th><th>Cat.</th><th class="num">Giac.</th>
      <th class="num">Impatto €</th><th class="num">Nostro 2025</th><th class="num">LastCost</th>
      <th>Valore d'apertura</th><th>Azione</th></tr></thead><tbody>`;
  h+= vis.map(x=>{
    const id=esc(x.Item);
    const cat = x.Categoria==='A'?dot('#c0392b')+'A':(x.Categoria==='B'?dot('#e0a800')+'B':'—');
    const certified = x.OverrideAttuale!=null;
    const val = certified? x.OverrideAttuale : x.OverrideSuggerito;
    const iid='bo_'+id.replace(/[^a-zA-Z0-9]/g,'_');
    const az = certified
       ? `<span class="pill ok">certificato</span> · <a href="#" onclick="certifBon('${id}',0,1);return false" class="muted">rimuovi</a>`
       : `<a href="#" onclick="certifBon('${id}',document.getElementById('${iid}').value,0);return false">Certifica</a>`;
    const manc = x.InRicalcolo===0 ? ` <span class="pill" style="background:#c0392b;color:#fff;font-size:10px">MANCANTE</span>` : '';
    return `<tr><td><a href="#" onclick="costoDett('${id}',this,2025);return false" title="movimenti 2025"><code>${id}</code></a>${manc}</td>
       <td>${esc((x.Descrizione||'').slice(0,32))}</td><td>${cat}</td>
       <td class="num">${num(x.Giacenza)}</td>
       <td class="num"><strong>${eur(imp(x))}</strong></td>
       <td class="num">${x.NostroDic2025?eur(x.NostroDic2025):'—'}</td>
       <td class="num">${x.LastCost?eur(x.LastCost):'—'}</td>
       <td><input id="${iid}" value="${val!=null?Number(val).toFixed(2):''}" style="width:78px;text-align:right" ${certified?'disabled':''}></td>
       <td style="font-size:12px;white-space:nowrap">${az}</td></tr>`;
  }).join("") || `<tr><td colspan="9" class="muted">Nessun candidato.</td></tr>`;
  h+=`</tbody></table></div></div>`;
  $("#bonifica").innerHTML=h;
}
async function certSuggeritiBulk(n){
  if(!confirm('Certifico '+n+' articoli col valore proposto (LastCost / WAP Mago)?\\nResteranno da fare a mano solo quelli senza alcun costo. Potrai sempre rivedere i singoli.')) return;
  const r=await (await fetch('/api/bonifica_certifica_suggeriti',{method:'POST'})).json();
  alert('Certificati '+r.certificati+' articoli. Ora applica la bonifica (rilancia ricalcolo) per vederli nelle rimanenze.');
  caricaBonifica();
}
async function certifBon(item, costo, rimuovi){
  if(!rimuovi){
    const v=Number(String(costo).replace(',','.'));
    if(!v || isNaN(v)){ alert('Inserisci un valore numerico valido.'); return; }
    await fetch('/api/bonifica_certifica',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({item, costo:v, fonte:'CERTIFICATO'})});
  } else {
    await fetch('/api/bonifica_certifica',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({item, rimuovi:1})});
  }
  caricaBonifica();
}
async function elaboraMese(){
  const {a,m}=periodo();
  if(!confirm(`Elaboro il mese ${a}-${String(m).padStart(2,'0')}? Carico vendite da Mago, preparo i costi e assemblo i margini (qualche secondo).`)) return;
  let r=null;
  try{ const resp=await fetch('/api/elabora_mese',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({anno:a,mese:m})}); r=await resp.json(); }catch(e){ r=null; }
  if(r&&r.ok){ alert(`Mese ${a}-${String(m).padStart(2,'0')} elaborato: ${r.righe} righe in fatto_riga.\nPassi: ${r.passi.join(' → ')}`); }
  else { alert('Elaborazione non riuscita: controlla il log del backend.'); }
  aggiornaStatoMese(); sottoVista(SUBV||'qual');
}
async function rilanciaRic(){
  if(!confirm('Rilancio il ricalcolo WAP 2026 con le aperture certificate e ricostruisco il costo del venduto dei mesi già elaborati? (qualche secondo)')) return;
  let r=null;
  try{ const resp=await fetch('/api/rilancia_ricalcolo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({anno:2026})}); r=await resp.json(); }catch(e){ r=null; }
  alert(r&&r.ok ? `Ricalcolo 2026 completato e costo del venduto aggiornato per i mesi: ${(r.mesi||[]).join(', ')||'(nessuno elaborato)'}.` : 'Ricalcolo non riuscito: controlla il log del backend.');
  aggiornaStatoMese(); sottoVista(SUBV||'qual');
}
let acdeb;
function cercaArtDeb(){ clearTimeout(acdeb); acdeb=setTimeout(cercaArt,250); }
async function cercaArt(){
  const q=$("#acerca").value.trim();
  if(q.length<2){ $("#aris").innerHTML=''; return; }
  const rs=await j(`/api/cerca_articolo?q=${encodeURIComponent(q)}`);
  $("#aris").innerHTML = rs.map(r=>`<div class="doc" style="padding:4px 6px" onclick="mostraScheda('${esc(r.Item)}')">
      <code>${esc(r.Item)}</code> <span class="muted">${esc((r.descr||'').slice(0,64))}</span></div>`).join("")
    || `<div class="muted" style="padding:4px 6px">Nessun articolo.</div>`;
}
async function mostraScheda(item){
  $("#aris").innerHTML=''; $("#acerca").value=item;
  $("#ascheda").innerHTML='<p class="muted">Carico…</p>';
  const {a}=periodo();
  const d=await j(`/api/costo_dettaglio?item=${encodeURIComponent(item)}&anno=${a}`);
  $("#ascheda").innerHTML='<p style="margin:6px 0"><strong>Articolo</strong> <code>'+esc(item)+'</code></p>'+renderCostoDett(d, item, a);
}
function kitToBonifica(item, somma){
  const inp=document.getElementById('bo_'+item.replace(/[^a-zA-Z0-9]/g,'_'));
  if(!inp){ alert('Apri questa scheda dal tab "Bonifica apertura" per proporre il valore come apertura.'); return; }
  inp.value=Number(somma).toFixed(2); inp.focus();
  inp.style.background='#fff7d6'; setTimeout(()=>inp.style.background='',1200);
}
function renderCostoDett(d, item, anno){
  const e=d.eff;
  let h=`<div class="dbox">`;
  h+=`<p><strong>Costo efficace</strong>: ${e?eur(e.CostoEff):'—'}`
     +(e?` <span class="pill">${esc(e.Fonte)}</span> &nbsp; puro ${eur(e.PuroUnit)} + oneri ${eur(e.OneriUnit)}`:'')+`</p>`;
  if(d.fornitore){ const noi=String(d.fornitore.Supplier||'').trim()==='9998';
    h+=`<p style="margin-top:-6px"><strong>Fornitore preferenziale</strong>: ${esc(d.fornitore.CompanyName||d.fornitore.Supplier)}${d.fornitore.CompanyName?` <span class="muted">(${esc(d.fornitore.Supplier)})</span>`:''}${noi?` <span class="pill">noi · assemblaggio interno</span>`:''}</p>`;
    const ria=d.fornitore.riacquisto, lordo=d.fornitore.riacquisto_lordo;
    if(ria!=null && Number(ria)>0){ const cur=(d.fornitore.riacquisto_valuta||'').trim();
      const fmt=v=> (cur && cur!=='EUR') ? Number(v).toLocaleString('it-IT',{minimumFractionDigits:2,maximumFractionDigits:2})+' '+cur : eur(v);
      const scontato = lordo!=null && Math.abs(Number(lordo)-Number(ria))>0.005;
      const dettSconti = scontato ? ` <span class="muted">(lordo ${fmt(lordo)}${d.fornitore.riacquisto_sconti?` − sconti ${esc(d.fornitore.riacquisto_sconti)}`:''})</span>` : '';
      const estera = cur && cur!=='EUR';
      const eurConv = (estera && d.fornitore.riacquisto_eur!=null)
          ? ` ≈ <strong>${eur(d.fornitore.riacquisto_eur)}</strong> <span class="muted">(cambio BCE${d.fornitore.cambio_data?' '+esc(d.fornitore.cambio_data):''})</span>` : '';
      h+=`<p style="margin-top:-6px"><strong>Costo di riacquisto</strong> (listino fornitore, netto sconti): ${fmt(ria)}${eurConv}${dettSconti} <span class="muted">— prezzo d'acquisto attuale dal fornitore preferenziale</span></p>`;
    }
  }
  // Indicatori di qualita' (ultimo mese) coi NUMERI che li generano.
  if(d.qualita){ const q=d.qualita;
      const liv = q.Livello==='ROSSO' ? dot('#c0392b')+'<strong style="color:#c0392b">Errore</strong>'
                : q.Livello==='GIALLO' ? dot('#e0a800')+'<strong style="color:#b8780a">Warning</strong>'
                : dot('#2f7d52')+'OK';
      h+=`<div style="margin:6px 0;padding:6px 10px;background:#faf7f0;border:1px solid var(--line);border-radius:8px;font-size:12px">
        <strong>Indicatori di qualità</strong> (mese ${MESI[q.Mese]||q.Mese}): ${liv}
        <span class="muted"> &nbsp; WAP puro ${eur(q.PuroUnit)} · oneri ${eur(q.OneriUnit)}${q.PuroAcqUnit!=null?` · ultimo acq. puro ${eur(q.PuroAcqUnit)}`:''} · WAP Mago ${q.WAPCost_Mago?eur(q.WAPCost_Mago):'azzerato'} · listino riacquisto ${q.RiacquistoEur!=null?eur(q.RiacquistoEur):'—'}</span>`;
      if(q.Q12_scost_riacq && Number(q.RiacquistoEur)>0 && q.PuroAcqUnit!=null){ const dl=Number(q.PuroAcqUnit)-Number(q.RiacquistoEur);
        h+=`<br><span style="color:#b8780a">▸ Q12 scostamento riacquisto: ultimo acquisto puro <strong>${eur(q.PuroAcqUnit)}</strong> vs listino <strong>${eur(q.RiacquistoEur)}</strong> → Δ ${dl>=0?'+':''}${eur(dl)} (${(100*dl/Number(q.RiacquistoEur)).toFixed(0)}%)</span>`; }
      if(Number(q.Q1_scost_mago)>0 && Number(q.WAPCost_Mago)>0){
        h+=`<br><span class="muted">▸ Q1 scost. vs WAP Mago: nostro ${eur(q.WAPCost_ricalc)} vs Mago ${eur(q.WAPCost_Mago)}</span>`; }
      if(q.Flags) h+=`<br><span class="muted">flag: ${esc(q.Flags.replace(/,/g,' · '))}</span>`;
      h+=`</div>`;
  }
  if((d.kit||[]).length){
    const somma=d.kit.reduce((s,k)=>s+Number(k.Qty||0)*Number(k.costo||0),0);
    h+=`<div class="panel" style="background:#eef3ee;border-color:#cfe0cf;margin:8px 0">
      <p style="margin:0 0 6px"><strong>Articolo KIT</strong> — il costo nasce <strong>esplodendo la distinta</strong> sui costi dei
      componenti (nostro ricalcolo ${anno||''}). <span class="muted">Clicca un <strong>componente</strong> per vederne il dettaglio con i movimenti del ${anno||'periodo'}.</span></p>
      <table><thead><tr><th>Componente</th><th>Descrizione</th><th class="num">Q.tà</th><th class="num">Costo unit.</th><th class="num">Costo × q.tà</th></tr></thead><tbody>`;
    h+= d.kit.map(k=>`<tr><td><a href="#" onclick="costoDett('${esc(k.Item)}',this,${anno||'undefined'});return false" title="movimenti ${anno||''}"><code>${esc(k.Item)}</code></a></td>
      <td>${esc((k.descr||'').slice(0,42))}</td><td class="num">${num(k.Qty)}</td>
      <td class="num">${k.costo!=null?eur(k.costo)+(k.da_efficace?' <span class="muted" title="costo efficace (anno scheda non ancora preparato dal motore)">eff.</span>':''):'<span class="muted">— (nessun costo)</span>'}</td>
      <td class="num">${k.costo!=null?eur(Number(k.Qty||0)*Number(k.costo)):'—'}</td></tr>`).join("");
    h+=`<tr style="font-weight:700;background:#dfeadf"><td colspan="4">Costo del kit (somma componenti)`
      +(item?` <a href="#" onclick="kitToBonifica('${esc(item)}',${somma});return false" style="font-weight:600;font-size:12px;margin-left:8px">→ usa ${eur(somma)} come valore d'apertura</a>`:'')
      +`</td><td class="num">${eur(somma)}</td></tr>`;
    h+=`</tbody></table></div>`;
  }
  h+=`<h3 class="sec">Formazione del WAP mese per mese</h3>
      <table><thead><tr><th>Mese</th><th class="num">Q.tà iniz</th><th class="num">Acq. q</th><th class="num">Acq. puro</th>
      <th class="num">Acq. oneri</th><th class="num">Vend.</th><th class="num">Resi</th><th class="num">Rett./Trasf</th>
      <th class="num">Q.tà fin</th><th class="num">Costo puro</th><th class="num">Costo oneri</th><th class="num">WAP nostro</th><th class="num">WAP Mago</th></tr></thead><tbody>`;
  h+= d.roll.map(r=>`<tr><td>${MESI[r.Mese]}</td>
      <td class="num">${num(r.QtaIniz)}</td>
      <td class="num">${r.QtaAcq?num(r.QtaAcq):''}</td>
      <td class="num">${r.ValAcqPuro?eur(r.ValAcqPuro):''}</td>
      <td class="num">${r.ValAcqOneri?eur(r.ValAcqOneri):''}</td>
      <td class="num">${r.QtaVend?'−'+num(r.QtaVend):''}</td>
      <td class="num">${r.QtaResi?'+'+num(r.QtaResi):''}</td>
      <td class="num">${r.QtaRettTrasf?(Number(r.QtaRettTrasf)>0?'+':'')+num(r.QtaRettTrasf):''}</td>
      <td class="num ${Number(r.QtaFin)<0?'neg':''}">${num(r.QtaFin)}</td>
      <td class="num">${eur(r.PuroUnit)}</td>
      <td class="num">${eur(r.OneriUnit)}</td>
      <td class="num"><strong>${eur(r.WAPCost_ricalc)}</strong></td>
      <td class="num muted">${r.WAPCost_Mago?eur(r.WAPCost_Mago):'—'}</td></tr>`).join("");
  h+=`</tbody></table>`;
  h+=`<h3 class="sec">Movimenti dell'anno — <span style="color:var(--ok)">★ = determinano il costo</span> (apertura + acquisti/oneri/cambi)</h3>`;
  h+=`<table><thead><tr><th>Mese</th><th>Causale</th><th>Trattamento</th><th class="num">Valuta</th><th class="num">Cambio</th>
      <th class="num">Q.tà</th><th class="num">Importo doc</th><th class="num">Importo €</th></tr></thead><tbody>`;
  const op=(d.roll||[]).find(r=>r.Mese===1) || (d.roll||[])[0];
  if(op){
    h+=`<tr style="font-weight:600;background:#eef3ee"><td>${MESI[op.Mese]||'—'}</td><td>★ APERTURA</td>
        <td>Valori inizio periodo</td><td class="num">—</td><td class="num">—</td>
        <td class="num">${num(op.QtaIniz)}</td><td class="num">—</td>
        <td class="num">${eur((op.ValPuroIniz||0)+(op.ValOneriIniz||0))}</td></tr>`;
  }
  h+= (d.mov||[]).map(mo=>{ const det = mo.WAPMovementType===2032533505;
      return `<tr${det?' style="font-weight:600;background:#eef3ee"':''}><td>${det?'★ ':''}${MESI[mo.Mese]}</td>
        <td><code>${esc(mo.InvRsn)}</code></td><td>${movLabel(mo.WAPMovementType,mo.InvRsn)}</td>
        <td class="num">${esc((mo.Currency||'').trim()||'EUR')}</td>
        <td class="num">${mo.Fixing?Number(mo.Fixing).toFixed(5):''}</td>
        <td class="num">${num(mo.qty)}</td><td class="num">${eur(mo.lineamt)}</td><td class="num">${eur(mo.eur)}</td></tr>`;
  }).join("");
  if(!op && !(d.mov||[]).length) h+=`<tr><td colspan="8" class="muted">Nessun dato nell'anno.</td></tr>`;
  h+=`</tbody></table>`;
  return h+`</div>`;
}
const esc=s=>(s==null?"":String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"));
let SQLLOADED=false;
async function caricaSql(){
  if(SQLLOADED) return;            // documentazione statica: carico una volta sola
  const items=await j("/api/sql");
  let h="", grp=null;
  items.forEach(o=>{
    if(o.gruppo!==grp){ grp=o.gruppo; h+=`<div class="grp">${esc(grp)}</div>`; }
    h+=`<details class="sqlo"><summary><code>${esc(o.nome)}</code><span class="badge">${esc(o.tipo)}</span></summary>
        <p class="sp">${esc(o.spieg)}</p><pre><code>${esc(o.sql)}</code></pre></details>`;
  });
  $("#sqlbox").innerHTML=h;
  SQLLOADED=true;
}
function rigaDrill(code, descr, cols, fn){
  fn = fn || 'drill';
  const tail = cols.map(c=>`<td class="num">${c}</td>`).join("");
  return `<tr class="drill" onclick="${fn}(this,'${code}')"><td style="width:18px">▸</td>`
       + `<td><code>${esc(code)}</code></td><td>${esc((descr||'').slice(0,55))}</td>${tail}</tr>`
       + `<tr class="det" style="display:none"><td></td><td colspan="${2+cols.length}"><div class="dbox muted">…</div></td></tr>`;
}
async function _toggle(tr, url, render){
  const det=tr.nextElementSibling, box=det.querySelector('.dbox');
  if(det.style.display==='none'){
    det.style.display=''; tr.firstElementChild.textContent='▾';
    if(!det.dataset.loaded){ const d=await j(url); box.classList.remove('muted'); box.innerHTML=render(d); det.dataset.loaded='1'; }
  } else { det.style.display='none'; tr.firstElementChild.textContent='▸'; }
}
function drill(tr,item){ const {a,m}=periodo(); _toggle(tr,`/api/anomalia?anno=${a}&mese=${m}&item=${encodeURIComponent(item)}`, renderDettaglio); }
function drillF(tr,comp){ const {a,m}=periodo(); _toggle(tr,`/api/foglia?anno=${a}&mese=${m}&comp=${encodeURIComponent(comp)}`, renderFoglia); }
function renderFoglia(d){
  let h=`<p>Foglia <code>${esc(d.comp)}</code> · impatta <strong>${d.kit.length}</strong> articoli (kit). Sanando il suo costo si risolvono tutti.</p>
    <table><thead><tr><th>Articolo impattato</th><th>Descrizione</th><th>Stato</th></tr></thead><tbody>`;
  h+=d.kit.map(k=>`<tr><td><code>${esc(k.Item)}</code></td><td>${esc((k.descr||'').slice(0,50))}</td>
        <td>${k.venduto?'<span class="pill bad">venduto</span>':'<span class="pill">non venduto</span>'}</td></tr>`).join("");
  h+=`</tbody></table>`; return h;
}
function renderDettaglio(d){
  let h="";
  if(d.costo){ const k=d.costo;
    h+=`<p>Costo certificato: <strong>${k.TipoArticolo}</strong> · ${k.Costo!=null?eur(k.Costo):'—'} · usato dal mese <strong>${k.MeseCostoUsato||'—'}</strong>`
      + (k.NComponentiTotali?` · kit ${k.NComponentiValidi}/${k.NComponentiTotali} componenti validi`:``) + `</p>`;
  } else { h+=`<p class="neg">Nessun costo certificato per questo articolo nel mese (→ ricavo senza costo).</p>`; }
  if(d.eccezioni.length){
    h+=`<table><thead><tr><th>Tipo</th><th>Componente colpevole</th><th>Dettaglio</th></tr></thead><tbody>`;
    h+=d.eccezioni.map(e=>`<tr><td><span class="pill warn">${esc(e.TipoEccezione)}</span></td>
        <td>${e.ComponenteColpevole?`<code>${esc(e.ComponenteColpevole)}</code>`:'—'} <span class="muted">${esc((e.colpevole_descr||'').slice(0,42))}</span></td>
        <td class="muted">${esc(e.Dettaglio||'')}</td></tr>`).join("");
    h+=`</tbody></table>`;
  } else { h+=`<p class="muted">Nessuna eccezione registrata per questo articolo.</p>`; }
  return h;
}
async function caricaAnom(){
  const {a,m}=periodo();
  const [d,fg,kit]=await Promise.all([ j(`/api/anomalie?anno=${a}&mese=${m}`), j(`/api/foglie?anno=${a}&mese=${m}`), j(`/api/kit_giacenza`) ]);
  const totRic=d.venduti_senza_costo.reduce((s,x)=>s+Number(x.ricavo||0),0);
  let h=`<div class="cards">
    <div class="kpi bad"><div class="v">${num(d.venduti_senza_costo.length)}</div><div class="l">articoli MERCE senza costo</div></div>
    <div class="kpi bad"><div class="v">${eur(totRic)}</div><div class="l">ricavo merce senza costo (MdC sovrastimato)</div></div>
    <div class="kpi"><div class="v">${num(d.costi_datati.length)}</div><div class="l">articoli con costo datato</div></div>
    <div class="kpi"><div class="v">${num(fg.length)}</div><div class="l">componenti foglia anomali</div></div>
    <div class="kpi ${kit.length?'bad':''}"><div class="v">${num(kit.length)}</div><div class="l">kit a magazzino (da verificare)</div></div>`;
  d.eccezioni_sintesi.forEach(e=>{ h+=`<div class="kpi"><div class="v">${num(e.n)}</div><div class="l">${esc(e.TipoEccezione)} · ${esc(e.Stato)}</div></div>`; });
  h+=`</div>`;
  h+=`<p class="muted">Sezioni richiudibili: apri quella che ti serve.</p>`;

  const sez=(titolo,cnt,corpo)=>`<details class="sez"><summary>${titolo}<span class="cnt">${cnt}</span></summary><div class="panel">${corpo}</div></details>`;
  const vuoto=(cs,txt)=>`<tr><td colspan="${cs}" class="muted">${txt}</td></tr>`;
  let t;

  // Foglie anomale: priorita' = piu' trasversali (impattano piu' kit)
  t=`<table><thead><tr><th></th><th>Componente</th><th>Descrizione</th><th class="num">Kit impattati</th><th class="num">di cui venduti</th><th class="num">Tipo</th></tr></thead><tbody>`
    + (fg.map(x=>rigaDrill(x.comp,x.descr,[num(x.kit_impattati),num(x.kit_venduti_impattati),'<span class="pill warn">'+esc(x.tipo)+'</span>'],'drillF')).join("") || vuoto(6,"Nessuna foglia anomala."))
    + `</tbody></table>`;
  h+=sez("Componenti FOGLIA anomali — parti da questi (sanando un materiale risolvi piu' kit)", `${fg.length} foglie`, t);

  // Merce venduta senza costo
  t=`<table><thead><tr><th></th><th>Articolo</th><th>Descrizione</th><th class="num">Righe</th><th class="num">Doc.</th><th class="num">Ricavo</th></tr></thead><tbody>`
    + (d.venduti_senza_costo.map(x=>rigaDrill(x.codice_articolo,x.descr,[num(x.righe),num(x.documenti),eur(x.ricavo)])).join("") || vuoto(6,"Nessuno: tutti i prodotti venduti hanno un costo."))
    + `</tbody></table>`;
  h+=sez("Articoli MERCE venduti senza costo certificato — clicca per la causa", `${d.venduti_senza_costo.length} articoli · ${eur(totRic)} (servizi esclusi)`, t);

  // Costi datati
  t=`<table><thead><tr><th></th><th>Articolo</th><th>Descrizione</th><th class="num">Costo del</th><th class="num">Mesi fa</th><th class="num">Righe</th><th class="num">Ricavo</th></tr></thead><tbody>`
    + (d.costi_datati.map(x=>rigaDrill(x.codice_articolo,x.descr,[x.mese_costo||'—',num(x.mesi_indietro),num(x.righe),eur(x.ricavo)])).join("") || vuoto(7,"Nessuno: tutti i costi sono del mese di competenza."))
    + `</tbody></table>`;
  h+=sez("Costi DATATI — costo preso da un mese precedente (possibile non aggiornato)", `${d.costi_datati.length} articoli`, t);

  // Eccezioni piatte
  t=`<table><thead><tr><th>Articolo</th><th>Tipo</th><th>Componente colpevole</th><th>Dettaglio</th></tr></thead><tbody>`
    + (d.eccezioni.map(e=>`<tr><td><code>${esc(e.Item)}</code></td><td><span class="pill warn">${esc(e.TipoEccezione)}</span></td><td><code>${esc(e.ComponenteColpevole||'')}</code></td><td class="muted">${esc(e.Dettaglio||'')}</td></tr>`).join("") || vuoto(4,"Nessuna eccezione aperta."))
    + `</tbody></table><p class="muted" style="padding:8px 14px;margin:0">Correzioni in Mago + <code>EXEC core.usp_prepara_costi @schema_azienda='kodice', @anno=…, @mese=…</code></p>`;
  h+=sez("Eccezioni del motore (vista piatta)", `${d.eccezioni.length}${d.eccezioni.length>=500?'+ (prime 500)':''}`, t);

  // Kit a magazzino (da verificare): un kit non dovrebbe essere a stock
  t=`<table><thead><tr><th>Articolo</th><th>Descrizione</th><th class="num">Giacenza</th><th class="num">Costo ric.</th><th class="num">N. comp.</th></tr></thead><tbody>`
    + (kit.map(x=>`<tr><td><code>${esc(x.Item)}</code></td><td>${esc((x.descr||'').slice(0,50))}</td><td class="num">${num(x.giacenza)}</td><td class="num">${eur(x.costo)}</td><td class="num">${num(x.n_componenti)}</td></tr>`).join("") || vuoto(5,"Nessun kit a magazzino. 🎉"))
    + `</tbody></table><p class="muted" style="padding:8px 14px;margin:0">Un kit non dovrebbe essere a stock: a magazzino vanno i <strong>componenti</strong>, non l'assemblato. Verificare anagrafica/giacenza di questi articoli.</p>`;
  h+=sez("⚠️ Kit a magazzino (DA VERIFICARE)", `${kit.length}`, t);

  $("#anom").innerHTML=h;
}
init();
</script>
</body></html>"""


if __name__ == "__main__":
    import os
    porta = int(os.getenv("CDG_PORT", "8765"))   # 5000 e' riservata su alcuni Windows; override con CDG_PORT
    host = os.getenv("CDG_HOST", "127.0.0.1")    # 127.0.0.1 = solo locale (IIS fa reverse-proxy); 0.0.0.0 = tutta la rete
    print(f"CDG_QV Esplora ->  http://{host}:{porta}   (Ctrl+C per fermare)")
    try:
        # server di produzione multi-utente (dietro IIS/ARR)
        from waitress import serve
        serve(app, host=host, port=porta, threads=8)
    except ImportError:
        app.run(host=host, port=porta, debug=False, threaded=True)
