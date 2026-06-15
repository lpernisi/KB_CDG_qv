-- Classificatore PRODOTTO vs IMBALLAGGIO per articolo.
-- La riconciliazione del costo del venduto va fatta PRODOTTI-contro-PRODOTTI: gli imballaggi
-- entrano a magazzino (carico) ma in contabilita' sono COSTO (conto 06021505), non merce (06011000).
-- Classificatore = MA_Items.ItemType: '997' = imballaggi (CART*, PALLET*, SCATOLE SCA*...), il resto = prodotti.
-- Item trimmato per agganciarsi a wap_ricalc / dettagli movimento (che a volte hanno spazi in coda).
CREATE OR ALTER VIEW kodice.vw_classe_articolo AS
SELECT LTRIM(RTRIM(Item)) AS Item,
       CASE WHEN ItemType = '997' THEN 'IMBALLAGGIO' ELSE 'PRODOTTO' END AS Classe
FROM KODICEBAGNO_4.dbo.MA_Items;
