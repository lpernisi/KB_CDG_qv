# Estrattore Qlik Sense del team — script di load (riferimento per le dimensioni e i componenti CE)

Questo è lo **script di caricamento** dell'app Qlik Sense del team ("Estrattore KB"), fonte di verità della
**nomenclatura** e di **come si costruiscono le dimensioni di analisi** e i **componenti del Conto Economico**.
Serve come riferimento per arricchire il nostro modello CDG_QV in modo coerente con ciò che i colleghi già usano.
Vedi anche [[ce-nomenclatura-qlik]] e [[definizione-fatturato-mago]] (in memoria).

> NB: incollato dall'editor di caricamento Qlik; alcuni caratteri sono "mojibake" da encoding (es. `â¬`=€, `Ã¬`=ì,
> `QuantitÃ `=Quantità). La copia autorevole resta nell'app Qlik. Qui conta la LOGICA/mapping.

---

## 1. Mappa DIMENSIONI di analisi → sorgente Mago

| Dimensione (Qlik) | Campo / derivazione | Sorgente Mago |
|---|---|---|
| **Canale** | `Category` del cliente (con override `135774`→'SAVINI') | `MA_CustomerCtg.Category` via `MA_CustSuppCustomerOptions.Category` (cliente in `MA_CustSupp`, `CustSuppType=3211264`) |
| descrizioneCategoria | `MA_CustomerCtg.Description` | idem |
| Raggrupp_Categorie | `MA_CustomerCtg.Notes` | idem |
| **Dipartimento** (ONLINE/BTOB) | `ApplyMap('map_dipartipento', Notes)` — mappa INLINE su Raggrupp_Categorie | derivato dal Canale (Notes) |
| **Marchio** (MarchioVendita) | `Map_MarchioCliente` (B2B0415→'KE BAGNO') con fallback: Dipartimento BTOB→'KORICINI', altrimenti 'KIAMAMI VALENTINA' | regola |
| **Agente** (CodAgente/nomeAgente) | `MA_CustSuppCustomerOptions.Salesperson` → `MA_SalesPeople.Name`; anche a livello doc `MA_SaleDoc.Salesperson` | |
| **Nazione/Regione/Provincia Sped.** | indirizzi spedizione: `Nazione_Sped`=Country, `Prov_Sped`=County(IT)/Country, `Regione_Sped` via `regioni_province.csv` su County | `MA_CustSuppBranches` (+ `MA_SaleDocShipping.ShipToAddress`), chiave `CustSupp & '_' & Branch` |
| **Linea Articolo** | `HomogeneousCtg` → `MA_HomogeneousCtg.Description` (`map_linea_articolo`) | `MA_Items.HomogeneousCtg` |
| **Tipo Articolo** | `ItemType` → `MA_ItemTypes.Description` (`map_tipo_articolo`) | `MA_Items.ItemType` |
| **Tipo NC** | `InvoicingAccGroup` → `MA_AccountingGroups` (GroupCode→description) (`map_tipo_nota_credito`) | `MA_SaleDoc.InvoicingAccGroup` |
| **Tipo Documento** | `map_tipodoc`: 3407874/5/8=Fattura, 3407876=Accredito, 3407877=ResoCliente, 3407873=Sostituzione | `MA_SaleDoc.DocumentType` |
| **Tipo Vendita** | `IsGood` → 0=Servizio / 1=Merce (`map_tipo_vendita`) | `MA_Items.IsGood` |
| **Vettore** | `MA_SaleDocShipping.Carrier1` (`map_vettore` per SaleDocId) | |
| Negozio | `MA_SaleDoc.Pricelist` | |
| Fornitore (preferenziale) | `MA_ItemsGoodsData.Supplier` → `MA_CustSupp.CompanyName` (CustSuppType=3211265) | |
| Anno/Trimestre/Mese/Settimana/MeseGiorno | da `MA_SaleDoc.DocumentDate` (MasterCalendar) | |

## 2. FATTURATO (ricavo) — filtri/segno (conferma la nostra definizione)
Righe da `MA_SaleDocDetail.TaxableAmount`, con **segno** `Mult`: +1 fatture (3407874/5/8), +1 sostituzioni
(3407873, con regole su Category/ProjectCode/invrsn), **−1** note credito (3407876, '00'). Resi cliente (3407877)
esclusi dal ricavo (imponibile forzato a 0). Esclusi `KODICEFR/DE/ES` (+ `149449`,`24209` sulle sostituzioni),
`ProjectCode` 3/4/5, `taxjournal='VENAUTO'`. Spese: da `MA_SaleDocSummary` (TotSpese) e `ImpSpeseTrasportoRecuperate`.
→ coincide con `usp_load_righe_vendita` (vedi [[definizione-fatturato-mago]]).

## 3. COMPONENTI DI COSTO del CE — tutti in `KB_SaleDocDetailDatiAggiuntivi` (per SaleDocId+Line)
**SCOPERTA CHIAVE:** le voci di costo del CE (Materiale, Imballi, Trasporto, Commerciali, Finanziari) NON sono
calcolate in Qlik: sono **campi già pronti, per riga documento**, nella tabella `KB_SaleDocDetailDatiAggiuntivi`.
Campi rilevanti (dal `load DatiAggiuntivi`):

| Voce CE | Campi `KB_SaleDocDetailDatiAggiuntivi` |
|---|---|
| **Materiale** (costo del venduto) | `costomaterialemensile` (mensile) / `CostoMaterialeMedio` (medio) (+ oneri d'acquisto sotto) |
| oneri su acquisti (dentro Materiale) | `ImportoDazi`, `ImportoTrasportiSuAcquisti`, `ImportoLogistica` |
| **Imballi** | `ImportoImballi` |
| **Trasporto** | `ImportoSpeseTrasportoStimate` (alt: `ImportoSpeseTrasporto`), `ImportoSpeseTrasportoEffettive`, `ImpSpeseTrasportoRecuperate`, `tipoSpeseTrasporto`, `CostoSpedizioneAPeso`, `vettoreEffettivo` |
| **Commerciali** | `ImportoProvvigioni` (+`ImpProvvigioniAgenti`), `BonusClienteGDO`, `CostoChannelENgine` |
| **Finanziari** | `CostoIncasso`, `costointeressi`, `CostoAssicurazione` |
| Pubblicità | `ImportoPubblicita` |

Variabili Qlik (aggregati di esempio — le formule esatte delle colonne CE stanno nelle espressioni dei grafici):
```
costoDelVenduto = sum((costomaterialemensile + ImportoDazi + ImportoTrasportiSuAcquisti + ImportoLogistica + ImportoImballi) * Mult)
oneriCommerciali = sum((BonusClienteGDO + ImportoSpeseTrasporto + ImportoProvvigioni) * Mult)
```
Tabelle parametri di STIMA: `KB_TabSpeseTrasporti`, `kb_tabspesetraspAcquisto`, `KB_TabProvvigioniVendita`.

## 4. Implicazioni per CDG_QV (come rispecchiarlo)
- **Dimensioni**: arricchire `core.fatto_riga` (o una vista `pres`) con gli attributi sopra, riusando le STESSE
  sorgenti/mappe (Canale = `MA_CustomerCtg.Category` del cliente; Dipartimento dalla mappa su Notes; Spedizione da
  `MA_CustSuppBranches`; Linea/Tipo articolo; Tipo doc/NC; Vettore; Agente; Marchio).
- **Materiale**: usiamo il NOSTRO costo ricalcolato (`vw_costo_eff`/`wap_ricalc`) — più affidabile di
  `costomaterialemensile`/`CostoMaterialeMedio`. Questo è il valore aggiunto del CDG.
- **Imballi / Trasporto / Commerciali / Finanziari**: inizialmente si possono **leggere da
  `KB_SaleDocDetailDatiAggiuntivi`** (gli stessi numeri "stimati" del CE Qlik), per far quadrare la dashboard col CE
  noto; poi si raffinano per voce (es. Trasporto con la riconciliazione fatture vettori; Commerciali con le tabelle
  provvigioni). Così ogni sezione passa da "stima Qlik" a "certificata CDG".
- **Confronto anno-su-anno**: il CE Qlik mostra 2026 vs 2025; per replicarlo serve caricare anche il 2025.

---

## 5. Script Qlik completo (verbatim)

```text
///$tab Main
SET ThousandSep='.';
SET DecimalSep=',';
SET MoneyThousandSep='.';
SET MoneyDecimalSep=',';
SET MoneyFormat='#.##0,00 €;-#.##0,00 €';
SET TimeFormat='hh:mm:ss';
SET DateFormat='DD/MM/YYYY';
SET TimestampFormat='DD/MM/YYYY hh:mm:ss[.fff]';
SET FirstWeekDay=0; SET BrokenWeeks=0; SET ReferenceDay=4; SET FirstMonthOfYear=1;
SET CollationLocale='it-IT';
SET MonthNames='gen;feb;mar;apr;mag;giu;lug;ago;set;ott;nov;dic';

///$tab Connessione
ODBC CONNECT TO MAGO_Kodicebagno (XUserId is ..., XPassword is ...);
sql Split_Costi2024;

///$tab mapping
map_tipo_vendita:    Mapping LOAD * INLINE [ TipoMerce, TipoVendita
0, Servizio
1, Merce ];

map_costo_medio:     // Item -> CostoMedio = (InitialBookInvValue+PurchasesValue+ProducedValue)/(InitialBookInv+PurchasesQty+ProducedQty)
Mapping load Text(Item) As Item, CostoMedio;
sql Select Item, round(SUM(Initialbookinvvalue + PurchasesValue+ producedvalue)/Nullif(SUM(InitialBookInv+purchasesqty+ProducedQty),0),2) as CostoMedio
from MA_ItemsBalances where FiscalYear = year(GetDate()) and Variant ='' group by Item;

map_costo_standard:  Mapping load Text(Item) As Item, StandardCost as CostoStandard;
sql Select Item, StandardCost from MA_ItemsFiscalYearData where FiscalYear = year(GetDate());

map_last_cost:       Mapping load Text(Item) As Item, LastCost where rowno=1;
sql Select Row_Number() over (partition by Item order by LastCostUpdate desc) as rowno, Item, LastCost
from MA_ItemsBalances where FiscalYear = year(GetDate()) and Variant ='';

map_linea_articolo:  mapping load Category as LineaProdotto, Description as Linea_Articolo;
sql select Category, Description from MA_HomogeneousCtg;

map_tipo_articolo:   mapping load CodeType as ItemType, Description as Tipo_Articolo;
sql select CodeType, Description from MA_ItemTypes;

map_codfornitore:    Mapping load Text(Item) As Item, Supplier as codFornitore;
sql select Item, Supplier from MA_ItemsGoodsData;

ma_codArtFornitoreRaw:  SQL SELECT Item, Supplier, SupplierCode FROM MA_ItemSuppliers;
ma_codArtFornitore:     MAPPING LOAD Text(Item) & '|' & Text(Supplier) AS ChiaveAF, SupplierCode RESIDENT ma_codArtFornitoreRaw;

map_fornitore:       Mapping load CustSupp as codFornitore, CompanyName as NomeFornitore;
sql select CustSupp, CompanyName from MA_CustSupp where CustSuppType = 3211265;

map_dipartipento:    mapping LOAD * INLINE [ Raggrupp_Categorie,Dipartimento
Amazon, ONLINE
Hornbach, ONLINE
Carrefour, ONLINE
Kaufland, ONLINE
BTOB tradizionale,BTOB
CDiscount,ONLINE
E-Commerce,ONLINE
EBAY,ONLINE
Eprice,ONLINE
GDO CIIR,ONLINE
Houzz,ONLINE
Intercompany Savini,BTOB
Leroy Merlin,ONLINE
MANOMANO,ONLINE
Professionale,BTOB
Rakuten,ONLINE
Darty,ONLINE
Castorama, ONLINE
Bricoman, ONLINE
Bauhaus, ONLINE
OBI, ONLINE
Vente-unique, ONLINE
Conforama, ONLINE
BricoMarche, ONLINE
RueDuCommerce,ONLINE ];

Map_MarchioCliente:  MAPPING LOAD * INLINE [ CustSupp, MarchioVendita
B2B0415, KE BAGNO ];

map_tipo_sostituzione: Mapping LOAD * INLINE [ ProjectCode,TipoSostituzione
1, Ricambio per danno/rottura
0, Da Inserire
2, Ricambio per difetto
3, Suddivisione ordine con ricarico
4, Cambio articolo
5, Spostamenti
6, Danno Logistica
7, Fotografo
C, Cambio Documento ];

map_tipodoc:         Mapping LOAD * INLINE [ DocumentType, TipoDoc
3407873, Sostituzione
3407874, Fattura
3407875, Fattura
3407876, Accredito
3407877, ResoCliente
3407878, Fattura ];

map_vettore:         Mapping load SaleDocId, Carrier1 as Vettore;
sql select SaleDocId, Carrier1 from "MA_SaleDocShipping";

map_tipo_nota_credito: Mapping load GroupCode as tipoNC, description as tipoNotaCredito;
sql select GroupCode, description from MA_AccountingGroups;

map_lotto_minimo:    mapping load text(Item) & '_' & Supplier, MinOrderQty;
SELECT Item,Supplier, MinOrderQty From MA_ItemSuppliers where MinOrderQty > 0;

map_LeadTime:        mapping load CustSupp, Rangesum(leadtime, ExtraLeadtime) As GGLT;
Select CustSupp, leadtime, ExtraLeadtime From KB_DatiFornitore Where leadtime > 0 or ExtraLeadtime > 0;

map_homeDelivery:    Mapping load SaleDocId, ClienteHMD;
sql select customer as ClienteHMD, custsupp, SaleDocId from
( select saledocid,custsupp from ma_saledoc where documenttype = 3407874 and custsupp like'B2B%') as fatt
join ( select saleordid,customer,DerivedDocID from ma_saleord join
  (select max(origindocid) as origindocid, DerivedDocID from MA_CrossReferences where deriveddoctype= 27066387 and origindoctype= 27066372 group by DerivedDocID) as cros
  on saleordid =cros.origindocid) as ord on ord.deriveddocid=fatt.saledocid where Customer like 'HMD%';

Map_ResponsabileBtoB: Mapping Load * INLINE [ Regione, Responsabile_BtoB
Abruzzo, Andrea Blasioli
... (Sud/Centro = Andrea Blasioli; Nord/Toscana/Sardegna = Marco Villanova) ... ];

///$tab Customer_b
// regioni_province.csv (C:\Users\Public\Documents\) -> Area/Regione/DesProvincia/Provincia
TMP_CustSupp:  // cliente CustSuppType=3211264, con Canale/Dipartimento/Marchio/Agente
load CustSupp, CustSuppType, CompanyName, Email, Address, ZIPCode, City,
County as Provincia, Country as CodStato, Telephone1,
If(CustSupp like 'L*' or CustSupp like 'ML*', 'S', 'N') as [Cliente Logistica],
Salesperson as CodAgente, Name as nomeAgente,
If(CustSupp = '135774','SAVINI', Category) as Category,
Description as descrizioneCategoria,
Notes as Raggrupp_Categorie,
ApplyMap('map_dipartipento', Notes) As Dipartimento,
ApplyMap('Map_MarchioCliente', CustSupp,
   If(ApplyMap('map_dipartipento', Notes) = 'BTOB','KORICINI','KIAMAMI VALENTINA')) as MarchioVendita;
sql select CustSupp, c.CustSuppType, CompanyName, Email, Address, ZIPCode, City, County, Country, Telephone1,
opt.Salesperson, Name, opt.Category, ctg.Description, ctg.Notes
from MA_CustSupp as c
left join MA_CustSuppCustomerOptions as opt on opt.Customer=CustSupp and c.CustSuppType=opt.CustSuppType
left join MA_SalesPeople as agente on agente.Salesperson=opt.Salesperson
left Join MA_CustomerCtg as ctg on ctg.Category = opt.Category
where c.CustSuppType = 3211264;
// + join regioni_province per Area/Regione/DesProvincia
// MA_CustSupp finale: aggiunge respBtob = ApplyMap('Map_ResponsabileBtoB', Regione) se Dipartimento='BTOB'

///$tab Fatture  (3 blocchi concatenati con Mult: +1 fatture, +1 sostituzioni, -1 note credito)
Fatture: load DocumentType, ApplyMap('map_tipodoc',DocumentType) As TipoDoc, DocNo, CustSupp, Payment, Notes,
SaleDocId, ApplyMap('map_vettore',SaleDocId) As Vettore, ValueDate, Pricelist as Negozio, ProjectCode,
Applymap('map_tipo_sostituzione',ProjectCode) As TipoSostituzione, InvEntryId, Date(DocumentDate) As DocumentDate,
StoragePhase1 as deposito, CountryOfDestination as Stato, YEAR(DocumentDate) as AnnoDoc, Month(DocumentDate) as MeseDoc,
week(DocumentDate) as Settimana, QuarterName(DocumentDate) as Trimestre, InvoicingAccGroup as tipoNC,
Applymap('map_tipo_nota_credito',InvoicingAccGroup) As tipoNotaCredito, 1 as Mult, Name As nomeAgente;
SQL SELECT ... FROM "MA_SaleDoc" d left join MA_SalesPeople agente
WHERE DocumentType in ('3407874','3407875','3407878') and ProjectCode not in ('3','4','5')
and CustSupp not in ('KODICEFR','KODICEDE','KODICEES') and taxjournal <>'VENAUTO' and YEAR(DocumentDate)> Year(GetDate())-3;
// 2° blocco: sostituzioni 3407873 (Category<>'BTOB' & ProjectCode not in 3/4/5) OR (Category='BTOB' & invrsn like 'KLSOST%'), Mult=1
// 3° blocco: note credito 3407876 e '00' (resi cliente 3407877 ESCLUSI), Mult=-1
// + join MA_SaleDocSummary: TotaleDocumento/TotaleMerce/TotaleServizi/TotSpese (per NC/ResoCliente azzerati)
// + Dipartimento sulle fatture, Key_Spedizioni, IndirizziSpedizioni (MA_CustSuppBranches): Nazione/Regione/Prov/Citta/Cap _Sped

///$tab Righe Fatture
RigheFatture: left keep(Fatture) load SaleDocId & '_' & Line As Key_Dati_Agg, SaleDocId, Line, LineType, Description,
Qty as Qta, UnitValue, TaxableAmount as Imponibile, TaxCode, TotalAmount as Totale, Text(Item) As Item,
DiscountAmount as Sconto, AdditionalQty1 as Provvigioni;
SQL SELECT ... FROM "MA_SaleDocDetail" where DocumentType not in ('3407873','3407877');
// 2° blocco (sostituzioni/resi): Imponibile=0, LineCost as CostoVenduto

// *** COMPONENTI DI COSTO (per riga) ***
DatiAggiuntivi: load SaleDocId & '_' & Line As Key_Dati_Agg,
coalesce(ImportoProvvigioni,0)+coalesce(ImpProvvigioniAgenti,0) as ImportoProvvigioni,
ImportoSpeseTrasporto, ImportoDazi, ImportoTrasportiSuAcquisti, ImportoLogistica, ImportoImballi, ImportoPubblicita,
CostoMaterialeMedio, ImportoSpeseTrasportoEffettive, ImpSpeseTrasportoRecuperate, costomaterialemensile,
BonusClienteGDO, vettore as vettoreEffettivo, CostoAssicurazione, CostoChannelENgine, CostoIncasso, costointeressi,
CostoSpedizioneAPeso,
IF(IsNull(ImportoSpeseTrasportoStimate) OR ImportoSpeseTrasportoStimate = 0, ImportoSpeseTrasporto, ImportoSpeseTrasportoStimate) AS ImportoSpeseTrasportoStimate,
tipoSpeseTrasporto, if(ImportoSpeseTrasportoEffettive>0,'1','0') as PresenzaSpEffettive;
SQL SELECT ... FROM "KB_SaleDocDetailDatiAggiuntivi";

///$tab Item
Item: load Text(Item) As Item, ItemType, Description as descrizioneArticolo, IsGood as TipoMerce,
Applymap('map_tipo_vendita',IsGood) As TipoVendita, ApplyMap('map_costo_standard',Text(Item)) As CostoStandard,
ApplyMap('map_last_cost',Text(Item)) As LastCost, ApplyMap('map_linea_articolo',HomogeneousCtg) As LineaArticolo,
ApplyMap('map_tipo_articolo',ItemType) As Tipo_Articolo, ApplyMap('map_codfornitore',Text(Item)) As codFornitore,
Applymap('map_fornitore', ApplyMap('map_codfornitore',Text(Item))) As NomeFornitore,
HomogeneousCtg as LineaProdotto, SaleBarCode as CodEan, ProductCtg, ProductSubCtg;
sql select i.Item, i.ItemType, i.Description, i.IsGood, i.HomogeneousCtg, i.SaleBarCode, i.DescriptionText,
i.ProductCtg, i.ProductSubCtg, gd.Supplier from MA_Items i left join MA_ItemsGoodsData gd on i.item=gd.item;

///$tab variabili CE
let costoDelVenduto = 'sum((costomaterialemensile + ImportoDazi + ImportoTrasportiSuAcquisti + ImportoLogistica + ImportoImballi)*Mult)';
let oneriCommerciali = 'sum((BonusClienteGDO + ImportoSpeseTrasporto + ImportoProvvigioni)*Mult)';

///$tab Parametri Stima
// KB_TabSpeseTrasporti (per Negozio: SpeseTrasporto/Logistica/Imballi/Bonus)
// kb_tabspesetraspAcquisto (per TipoArticolo: SpTraspAcquisti/Dazi/SpeseTrasporto*)
// KB_TabProvvigioniVendita (per CategoriaCliente: CommissioniVendita/CostiChannelEngine/CostiAssicurazione/CostoInteressi)

///$tab Calendar  -> MasterCalendar (Anno/Mese/Settimana/Trimestre/YTD/RC12 da DocumentDate)
///$tab Magazzino -> Giacenze (MA_ItemsBalances), Costi (MA_ItemsWAP risalita), Movimenti (MA_InventoryEntries+Detail con segno causali), CostiStorico (MA_ItemsWAP)
///$tab Ordini Fornitori e Distinte base -> Distinte_Base (MA_BillOfMaterialsComp join MA_BillOfMaterials Disabled=0)
///$tab Store and Drop -> salva i QVD (Fatture, RigheFatture, DatiAggiuntivi, MA_CustSupp, Item, Giacenze, Costi, Movimenti, ...)
```

> Lo script completo originale (incl. blocchi Logistica/Partite/Saldi giornalieri/Giacenze progressive) è nell'app
> Qlik; qui sopra è riportata la struttura con i pezzi che servono al CDG (dimensioni, fatturato, componenti di costo).
