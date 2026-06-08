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
              "per WAPMovementType (acquisti/vendite/resi; trasferimenti e 'ignora' esclusi)."},
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
    mov = righe("""
        SELECT MONTH(h.PostingDate) AS Mese, h.InvRsn, h.WAPMovementType, h.Currency, h.Fixing,
               SUM(d.Qty) AS qty, SUM(d.LineAmount) AS lineamt,
               SUM(d.LineAmount * CASE WHEN h.Currency NOT IN ('','EUR') AND h.Fixing > 0 THEN h.Fixing ELSE 1 END) AS eur
        FROM KODICEBAGNO_4.dbo.MA_InventoryEntriesDetail d
        JOIN KODICEBAGNO_4.dbo.MA_InventoryEntries h ON h.EntryId = d.EntryId
        WHERE LTRIM(RTRIM(d.Item)) = :i AND YEAR(h.PostingDate) = :a
        GROUP BY MONTH(h.PostingDate), h.InvRsn, h.WAPMovementType, h.Currency, h.Fixing
        ORDER BY MONTH(h.PostingDate), h.InvRsn""", i=item, a=anno)
    return jsonify({"roll": roll, "eff": (eff[0] if eff else None), "mov": mov})


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
        SELECT Item, Descrizione, Categoria, Giacenza, CostoSeed, NostroDic2025, MagoDic2025,
               LastCost, OverrideSuggerito, OverrideAttuale, OverrideFonte
        FROM kodice.vw_bonifica_apertura
        ORDER BY CASE WHEN OverrideAttuale IS NULL THEN 0 ELSE 1 END,
                 CASE Categoria WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END, Giacenza DESC"""))


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


@app.post("/api/rilancia_ricalcolo")
def api_rilancia_ricalcolo():
    """Rilancia il ricalcolo WAP per l'anno indicato (applica le aperture certificate)."""
    anno = int((request.get_json(force=True) or {}).get("anno", 2026))
    with engine.begin() as c:
        c.exec_driver_sql(f"EXEC kodice.usp_ricalc_wap @Anno = {anno};")
    return jsonify({"ok": True, "anno": anno})


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
  .row{display:grid;grid-template-columns:380px 1fr;gap:20px}
  @media(max-width:900px){.row{grid-template-columns:1fr}}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
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
  <div class="tabs">
    <div class="tab on" data-v="doc" onclick="vista('doc')">Documenti</div>
    <div class="tab" data-v="anom" onclick="vista('anom')">Anomalie</div>
    <div class="tab" data-v="costi" onclick="vista('costi')">Costi WAP</div>
    <div class="tab" data-v="qual" onclick="vista('qual')">Certificazione costi</div>
    <div class="tab" data-v="bonifica" onclick="vista('bonifica')">Bonifica apertura</div>
    <div class="tab" data-v="sql" onclick="vista('sql')">Documentazione SQL</div>
  </div>
</header>
<main>
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
  <div id="v-anom" style="display:none">
    <div id="anom"><p class="muted">Carico…</p></div>
  </div>
  <div id="v-costi" style="display:none">
    <div id="costi"><p class="muted">Carico…</p></div>
  </div>
  <div id="v-qual" style="display:none">
    <div id="qual"><p class="muted">Carico…</p></div>
  </div>
  <div id="v-bonifica" style="display:none">
    <div id="bonifica"><p class="muted">Carico…</p></div>
  </div>
  <div id="v-sql" style="display:none">
    <p class="muted" style="margin-top:0">Tutte le SQL del flusso, con spiegazione e <strong>definizione letta dal vivo dal database</strong> (non puo' divergere dal codice eseguito). Include il <strong>motore costi</strong> (kodice), versionato anche in <code>sql/motore/</code>.</p>
    <div id="sqlbox"><p class="muted">Carico…</p></div>
  </div>
</main>
<script>
const $=s=>document.querySelector(s);
const eur=x=>(x==null?"—":Number(x).toLocaleString("it-IT",{minimumFractionDigits:2,maximumFractionDigits:2})+" €");
const num=x=>(x==null?"—":Number(x).toLocaleString("it-IT"));
let PER=null, SEL=null;

async function j(u){ const r=await fetch(u); return r.json(); }

async function init(){
  const ps=await j("/api/periodi");
  const sel=$("#periodo");
  sel.innerHTML=ps.map(p=>`<option value="${p.anno}-${p.mese}">${p.anno}-${String(p.mese).padStart(2,'0')} · ${eur(p.ricavo)} · MdC I ${eur(p.mdc1)}</option>`).join("");
  if(ps.length){ sel.value=`${ps[ps.length-1].anno}-${ps[ps.length-1].mese}`; }
  sel.onchange=onPeriodo; onPeriodo();
  const h=location.hash.replace('#',''); if(['sql','anom','costi','qual','bonifica'].includes(h)) vista(h);
}
function periodo(){ const [a,m]=$("#periodo").value.split("-"); return {a:+a,m:+m}; }
function onPeriodo(){ SEL=null; cerca();
  if($("#v-anom").style.display!=="none") caricaAnom();
  if($("#v-qual").style.display!=="none") caricaQual();
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

function vista(v){
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("on",t.dataset.v===v));
  $("#v-doc").style.display = v==="doc"?"":"none";
  $("#v-anom").style.display = v==="anom"?"":"none";
  $("#v-costi").style.display = v==="costi"?"":"none";
  $("#v-qual").style.display = v==="qual"?"":"none";
  $("#v-bonifica").style.display = v==="bonifica"?"":"none";
  $("#v-sql").style.display = v==="sql"?"":"none";
  if(v==="anom") caricaAnom();
  if(v==="costi") caricaCosti();
  if(v==="qual") caricaQual();
  if(v==="bonifica") caricaBonifica();
  if(v==="sql") caricaSql();
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
  td.innerHTML=`<div class="dbox" style="font-size:12px;color:var(--muted);margin-bottom:-6px">Movimenti e roll del <strong>${a}</strong></div>`+renderCostoDett(d);
  det.appendChild(td); tr.after(det);
}
async function caricaBonifica(){
  const d=await j('/api/bonifica');
  const a=d.filter(x=>x.Categoria==='A'), b=d.filter(x=>x.Categoria==='B'), cert=d.filter(x=>x.OverrideAttuale!=null);
  let h=`<div class="cards">
    <div class="kpi"><div class="v">${num(a.length)}</div><div class="l">${dot('#c0392b')}A · apertura senza costo</div></div>
    <div class="kpi"><div class="v">${num(b.length)}</div><div class="l">${dot('#e0a800')}B · costo da certificare</div></div>
    <div class="kpi"><div class="v" style="color:#1a7f37">${num(cert.length)}</div><div class="l">${dot('#2f7d52')}certificati</div></div>
    <div class="kpi"><div class="v">${num(d.length-cert.length)}</div><div class="l">da certificare</div></div></div>
  <p class="muted">Bonifica dell'apertura 2026. Clicca il <strong>codice</strong> per vedere i <strong>movimenti 2025</strong> da cui nasce il valore proposto. Modifica il valore se serve, poi <em>Certifica</em>. Infine <a href="#" onclick="rilanciaRic();return false"><strong>↻ Applica bonifica (rilancia ricalcolo 2026)</strong></a>.</p>`;
  h+=`<div class="panel" style="padding:0"><div style="max-height:72vh;overflow:auto"><table class="sticky"><thead><tr><th>Articolo</th><th>Descrizione</th><th>Cat.</th><th class="num">Giac.</th>
      <th class="num">Costo seed</th><th class="num">Nostro 2025</th><th class="num">Mago Dic</th><th class="num">LastCost</th>
      <th>Valore d'apertura</th><th>Azione</th></tr></thead><tbody>`;
  h+= d.map(x=>{
    const id=esc(x.Item);
    const cat = x.Categoria==='A'?dot('#c0392b')+'A':(x.Categoria==='B'?dot('#e0a800')+'B':'—');
    const certified = x.OverrideAttuale!=null;
    const val = certified? x.OverrideAttuale : x.OverrideSuggerito;
    const iid='bo_'+id.replace(/[^a-zA-Z0-9]/g,'_');
    const az = certified
       ? `<span class="pill ok">certificato</span> · <a href="#" onclick="certifBon('${id}',0,1);return false" class="muted">rimuovi</a>`
       : `<a href="#" onclick="certifBon('${id}',document.getElementById('${iid}').value,0);return false">Certifica</a>`;
    return `<tr><td><a href="#" onclick="costoDett('${id}',this,2025);return false" title="movimenti 2025"><code>${id}</code></a></td>
       <td>${esc((x.Descrizione||'').slice(0,32))}</td><td>${cat}</td>
       <td class="num">${num(x.Giacenza)}</td>
       <td class="num">${x.CostoSeed?eur(x.CostoSeed):'—'}</td>
       <td class="num"><strong>${x.NostroDic2025?eur(x.NostroDic2025):'—'}</strong></td>
       <td class="num">${x.MagoDic2025?eur(x.MagoDic2025):'—'}</td>
       <td class="num">${x.LastCost?eur(x.LastCost):'—'}</td>
       <td><input id="${iid}" value="${val!=null?Number(val).toFixed(2):''}" style="width:78px;text-align:right" ${certified?'disabled':''}></td>
       <td style="font-size:12px;white-space:nowrap">${az}</td></tr>`;
  }).join("") || `<tr><td colspan="10" class="muted">Nessun candidato.</td></tr>`;
  h+=`</tbody></table></div></div>`;
  $("#bonifica").innerHTML=h;
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
async function rilanciaRic(){
  if(!confirm('Rilancio il ricalcolo 2026 con le aperture certificate? (qualche secondo)')) return;
  await fetch('/api/rilancia_ricalcolo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({anno:2026})});
  alert('Ricalcolo 2026 completato: i costi sono aggiornati con le aperture bonificate.');
  caricaBonifica();
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
  $("#ascheda").innerHTML='<p style="margin:6px 0"><strong>Articolo</strong> <code>'+esc(item)+'</code></p>'+renderCostoDett(d);
}
function renderCostoDett(d){
  const e=d.eff;
  let h=`<div class="dbox">`;
  h+=`<p><strong>Costo efficace</strong>: ${e?eur(e.CostoEff):'—'}`
     +(e?` <span class="pill">${esc(e.Fonte)}</span> &nbsp; puro ${eur(e.PuroUnit)} + oneri ${eur(e.OneriUnit)}`:'')+`</p>`;
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
    print(f"CDG_QV Esplora ->  http://127.0.0.1:{porta}   (Ctrl+C per fermare)")
    app.run(host="127.0.0.1", port=porta, debug=False)
