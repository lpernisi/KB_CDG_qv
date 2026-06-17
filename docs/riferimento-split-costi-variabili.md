# Riferimento — `Split_Costi_Variabili` (procedura legacy KODICEBAGNO_4)

> **A cosa serve questo documento.** È la **fotografia della logica storica** con cui, fino ad
> oggi, ogni costo variabile veniva attribuito alle righe di vendita nel gestionale (tabella
> `KB_SaleDocDetailDatiAggiuntivi`). È l'**oracolo** che il Conto Economico del team in Qlik legge
> (vedi [estrattore-qlik.md](estrattore-qlik.md)). Lo teniamo qui come **riferimento per ogni
> componente del CE** che stiamo ricostruendo, più pulito e più trasparente, dentro CDG_QV.
>
> ⚠️ **Limite noto (motivo per cui lo stiamo rifacendo):** oggi le percentuali per canale
> **comprendono tutto in modo indiscriminato** (commissione + integrazione + a volte altro
> sono "spalmati" in un'unica aliquota). L'obiettivo del CDG è **separare le voci** e
> **migliorare dettaglio e qualità del dato**, così che ogni numero sia drill-abile e spiegabile.

## Mappa: campo legacy → voce di Conto Economico → componente CDG

| Campo `KB_SaleDocDetailDatiAggiuntivi` | Voce CE (team) | Sorgente / aliquota | Componente CDG | Stato CDG |
|---|---|---|---|---|
| `ImportoProvvigioni` | **Commerciali** | `kb_TabProvvigioniVendita.CommissioniVendita` (% per canale) × imponibile riga | `PROVVIGIONI` | ✅ attivo |
| `CostoChannelEngine` | **Commerciali** | `kb_TabProvvigioniVendita.CostiChannelEngine` (% per canale, dal 2022-04) × imponibile | `CHANNEL_ENGINE` | ✅ attivo |
| `ImpProvvigioniAgenti` | **Commerciali** | `MA_SaleDocDetail.SalespersonComm` (importo provvigione agente, per riga) | `PROVVIGIONI_AGENTI` | ✅ attivo |
| `BonusClienteGDO` | **Commerciali** | `kb_tabspesetrasporti.bonus` (% per negozio/listino) | — | da valutare |
| `ImportoPubblicita` | **Pubblicità** | (nessuna % per riga: spesa aggregata per canale/campagna) | `PUBBLICITA` | ⏸ stub |
| `CostoIncasso` | **Finanziari** | 1,2% su pagamenti PayPal (`Payment='CC'`) | — | sezione Finanziari |
| `CostiAssicurazione` | **Finanziari** | `kb_TabProvvigioniVendita.CostiAssicurazione` (% per canale) | — | sezione Finanziari |
| `CostoInteressi` | **Finanziari** | `kb_TabProvvigioniVendita.CostoInteressi` (% per canale) | — | sezione Finanziari |
| `ImportoImballi` | **Imballi** | `kb_tabspesetrasporti.SpeseImballi` (% per negozio) | `IMBALLI` | ⏸ stub |
| `ImportoLogistica` | Materiale/Imballi | `kb_tabspesetrasporti.SpeseLogistica` (% per negozio) | — | da valutare |
| `ImportoSpeseTrasporto` (stimato) | **Trasporto** | `kb_tabspesetraspAcquisto`/`kb_tabspesetrasporti` (% per tipo art./area) | `TRASPORTO` (cascata reale→stima) | ✅ attivo |
| `ImportoSpeseTrasportoEffettive` | **Trasporto** | fattura vettore (`trasporti.KB_FattureVettori*`) ripartita per valore | `TRASPORTO` (livello reale) | ✅ attivo |
| `CostoSpedizioneAPeso` | **Trasporto** | stima listino BRT per peso/regione (`KB_ListinoCostiTrasporto`) +25% | `TRASPORTO` (livello stima) | ✅ attivo |
| `CostoMaterialeMedio` / `CostoMaterialeMensile` | **Materiale** | `KB_CostiStandardStorici` (costo medio mensile) | `COSTO_VENDUTO` (nostro WAP ricalcolato) | ✅ attivo |
| `ImportoTrasportiSuAcquisti` / `ImportoDazi` | Materiale | `kb_tabspesetraspAcquisto` — **azzerati dopo 30.09.2020** (già nel costo magazzino) | — | nel costo articolo |

### Note di logica importanti riprese dalla legacy (per qualità)

- **Chiave canale**: `MA_CustSuppCustomerOptions.Category` del cliente (CustSuppType 3211264), con il
  caso speciale `CustSupp = 'B2B0415'` (Bricoman) che usa il proprio codice come chiave di tariffa.
- **Servizi esclusi**: le righe di **servizio** (`MA_Items.IsGood = 0`) **non** pagano commissione/imballi.
- **Channel Engine** parte dal **2022-04-01** (prima non c'era).
- **Note di credito al cliente (resi)** — regola del 19.03.2026: sulle note di credito *vere*
  (resi, `InvoicingAccGroup` **non** in A/I/C = annullamenti/inevadibili/cambio documento) il
  marketplace **non restituisce** la commissione → il recupero di provvigioni/channel engine/imballi/
  trasporto su quelle NC va **azzerato**. Solo su annullamenti/inevadibili/cambio documento si recupera.
- **Costo incasso**: 1,2% fisso sui pagamenti con carta/PayPal (`MA_SaleDoc.Payment = 'CC'`).

---

## Testo SQL della procedura (verbatim, KODICEBAGNO_4)

```sql
CREATE PROCEDURE [dbo].[Split_Costi_Variabili]
AS
BEGIN
    SET NOCOUNT ON;

    declare @dataini date;
    set @dataini= '2020-09-30T00:00:00';

    -- 1) Allinea anagrafica prodotti
    insert into KB_DatiProdotto(Item,Descrizione,LottoOrdine,Rettifica_percentuale,Rettifica_Numero,ScortaMinimaGiorni)
        (select it.Item, it.Description, 0, 0, 0, 0
         from MA_Items as it
         inner join MA_ItemsGoodsData as good on it.Item=good.Item
         left join KodiceBagno_4.dbo.KB_DatiProdotto as p on p.Item=it.Item
         where p.Item is null);

    -- 2) Crea le righe DatiAggiuntivi mancanti (ultimi 2 anni)
    insert into dbo.KB_SaledocdetailDatiAggiuntivi(SaledocId,Line,ImportoProvvigioni,ImportoSpeseTrasporto,
        ImportoPubblicita,ImportoDazi,ImportoTrasportiSuAcquisti,importoimballi,importologistica,
        ImportoSpeseTrasportoEffettive,BonusClienteGDO,vettore,CostoAssicurazione,CostoIncasso,
        CostoChannelENgine,CostoInteressi,ImportoSpeseTrasportoStimate)
    select a.SaleDocId, a.Line,0,0,0,0,0,0,0,0,0,'',0,0,0,0,0
    from MA_SaleDocDetail as a
    left outer join KB_SaledocdetailDatiAggiuntivi as b on a.SaleDocId=b.Saledocid and a.Line=B.line
    where year(a.DocumentDate)>=Year(GETDATE())-1 and b.Saledocid is null;

    -- 3) Aggiorna costo medio mensile (mese in corso e precedente)
    declare @drif Datetime;
    set @drif=EOMONTH(GetDate(),-1);  exec KB_SplitCostoMedioMPP @drif;
    set @drif=EOMONTH(GetDate(),-2);  exec KB_SplitCostoMedioMPP @drif;

    -- 4) PROVVIGIONI / ASSICURAZIONE / CHANNEL ENGINE / INCASSO / INTERESSI  (% per canale)
    update KB_SaleDocDetailDatiAggiuntivi
    set
      ImportoProvvigioni = case when gd.IsGood='0' then 0 else round((provv.commissioniVendita*riga.TaxableAmount/100),2) end,
      CostoAssicurazione = case when gd.IsGood='0' then 0 else round((coalesce(provv.costiAssicurazione,0)*riga.TaxableAmount/100),2) end,
      CostoChannelENgine = case when gd.IsGood='0' or doc.DocumentDate<'2022-04-01T00:00:00' then 0 else round((coalesce(provv.costiChannelEngine,0)*riga.TaxableAmount/100),2) end,
      CostoIncasso       = case when doc.Payment<>'CC' then 0 else round((1.2*riga.TaxableAmount/100),2) end,  -- 1,2% PayPal
      Costointeressi     = case when gd.IsGood='0' then 0 else round((coalesce(provv.Costointeressi,0)*riga.taxableamount/100),2) end
    from KB_SaleDocDetailDatiAggiuntivi as det
    join MA_SaleDocDetail as riga on riga.SaleDocId=det.SaleDocId and riga.Line=det.line
    left join MA_Items as gd on gd.item=riga.item
    join MA_SaleDoc as doc on det.SaleDocId=doc.SaleDocId
    join MA_CustSuppCustomerOptions as opt on opt.Customer=doc.CustSupp
    join kb_TabprovvigioniVendita as provv
      on provv.categoriaCliente = case when doc.CustSupp='B2B0415' then doc.custsupp else opt.Category end;

    -- 5) IMBALLI / LOGISTICA / BONUS GDO  (% per negozio/listino)
    update KB_SaleDocDetailDatiAggiuntivi
    set
      importoimballi   = case when gd.IsGood='0' then 0 else round((trasp.SpeseImballi*riga.TaxableAmount/100),2) end,
      importoLogistica = case when gd.IsGood='0' then 0 else round((trasp.SpeseLogistica*riga.TaxableAmount/100),2) end,
      BonusClienteGDO  = round((trasp.bonus*riga.TaxableAmount/100),2)
    from KB_SaleDocDetailDatiAggiuntivi as det
    join MA_SaleDocDetail as riga on riga.SaleDocId=det.SaleDocId and riga.Line=det.line
    left join MA_Items as gd on gd.item=riga.item
    join MA_SaleDoc as doc on det.SaleDocId=doc.SaleDocId
    join kb_tabspesetrasporti as trasp on trasp.CodiceNegozio=doc.pricelist
    where year(doc.DocumentDate)>=Year(GETDATE())-1;

    -- 6) TRASPORTI SU ACQUISTI / DAZI / SPESE TRASPORTO STIMATE (% per tipo articolo/area)
    update KB_SaleDocDetailDatiAggiuntivi
    set ImportoTrasportiSuAcquisti = round((trasp.SpTraspAcquisti*riga.TaxableAmount/100),2),
        ImportoDazi = round((trasp.Dazi*riga.TaxableAmount/100),2),
        ImportoSpeseTrasporto = case when art.IsGood='0' or doc.CustSupp='135774' then 0 else
            round(riga.TaxableAmount*(case when doc.CustSupp like 'B2B%' then SpesetrasportoBtoB
                else (case when Upper(CountryOfDestination)='IT' then SpeseTrasportoItalia else SpeseTrasportoEstero end) end)/100,2) end
    from KB_SaleDocDetailDatiAggiuntivi as det
    join MA_SaleDocDetail as riga on riga.SaleDocId=det.SaleDocId and riga.Line=det.line
    join MA_SaleDoc as doc on det.SaleDocId=doc.SaleDocId
    join MA_Items as art on riga.Item=art.Item
    join kb_tabspesetraspAcquisto as trasp on trasp.TipoArticolo=art.ItemType
    where year(doc.DocumentDate)>=Year(GETDATE())-1;

    -- 7) Azzera Dazi e trasporti su acquisti dopo il 30.09.2020 (già nel costo materiale a magazzino)
    update KB_SaleDocDetailDatiAggiuntivi
    set ImportoTrasportiSuAcquisti=0, ImportoDazi=0
    from KB_SaleDocDetailDatiAggiuntivi as det
    join MA_SaleDocDetail as riga on riga.SaleDocId=det.SaleDocId and riga.Line=det.line
    join MA_SaleDoc as doc on det.SaleDocId=doc.SaleDocId
    where doc.DocumentDate>=@dataini;

    -- 8) Costo materiale medio (costo standard storico × qta)
    update KB_SaleDocDetailDatiAggiuntivi
    set CostoMaterialeMedio = mesi.Qty*(mesi.costoStandard)
    from (select * from MA_SaleDocDetail as dett
          join KB_CostiStandardStorici as mensili
            on dett.Item=mensili.Articolo
           and cast(dateadd(d,-1,DATEADD(q,datediff(q,0,DocumentDate),0)) as date)=DataCalcolo
          where YEAR(dett.DocumentDate)>=Year(GetDate())-1) as mesi
    join KB_SaleDocDetailDatiAggiuntivi as agg on agg.SaleDocId=mesi.SaleDocId and agg.Line=mesi.line;

    -- 9) NOTE DI CREDITO al cliente (resi): azzera il RECUPERO di costo/imballi/provvigioni/trasporto/
    --    channel engine, tranne annullamenti/inevadibili/cambio documento (InvoicingAccGroup A/I/C).
    UPDATE agg
    SET CostoMaterialeMedio=0, ImportoImballi=0, ImportoProvvigioni=0, ImportoSpeseTrasporto=0, CostoChannelENgine=0,
        CostoIncasso       = case when InvoicingAccGroup='C' then 0 else CostoIncasso end,
        CostoAssicurazione = case when InvoicingAccGroup='C' then 0 else CostoAssicurazione end,
        CostoInteressi     = case when InvoicingAccGroup='C' then 0 else CostoInteressi end
    FROM KB_SaleDocDetailDatiAggiuntivi AS agg
    INNER JOIN MA_SaleDoc AS d ON d.SaleDocId=agg.SaleDocId
    WHERE d.DocumentType='3407876' AND d.DocumentDate>='2026-01-01' AND d.InvoicingAccGroup NOT IN ('A','I','C');

    -- 10) Azzera costo materiali su trasferimenti al fotografo (ProjectCode 7)
    update agg set CostoMaterialeMensile=0
    from KB_SaleDocDetailDatiAggiuntivi as agg
    join MA_SaleDoc as d on d.SaleDocId=agg.SaleDocId
    where d.DocumentType in ('3407873') and d.DocumentDate>='2020-01-01' and ProjectCode='7';

    -- 11) Spese di trasporto RECUPERATE dal cliente (ripartite sulle righe)
    --     [calcolo su MA_SaleDocSummary: ServiceAmounts/ShippingCharges/... ripartiti per imponibile]
    -- 12) Provvigioni AGENTI = MA_SaleDocDetail.SalespersonComm
    update sd set impprovvigioniagenti = d.salespersoncomm
    from ma_saledocdetail as d
    join KB_SaleDocDetailDatiAggiuntivi as sd on sd.SaleDocId=d.SaleDocId and sd.Line=d.Line
    where SalespersonComm<>0;

    -- 13) Spese trasporto EFFETTIVE da fattura vettore (trasporti.KB_FattureVettori*) ripartite
    -- 14) Stima costo trasporto a PESO/regione da listino BRT (+25% extra nolo)
    --     [vedi componente CDG TRASPORTO, che implementa la cascata reale→stima listino→stima peso]

    exec split_SpeseTrasportoEffetiveSUSA;
    exec KB_inserisciFornitori;
END
```

> I blocchi 11/13/14 sono riportati in forma sintetica: nel CDG la loro logica è già reimplementata,
> più trasparente, nel componente `TRASPORTO` (cascata fattura reale → stima listino → stima per peso).
> Il testo integrale storico è in `_sp_Split_Costi_Variabili.sql` alla radice del repo.
