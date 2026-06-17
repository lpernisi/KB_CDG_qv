-- =============================================================================
-- 04_struttura_commerciali.sql  (eseguire su CDG_QV)
-- -----------------------------------------------------------------------------
-- TABELLA DI ATTRIBUZIONE delle COMMISSIONI marketplace (componente PROVVIGIONI).
-- Stesso criterio a CASCATA della stima trasporto (cfg.trasporto_stima_peso):
-- si imposta DA UI, si inseriscono solo le ECCEZIONI e per il resto valgono i jolly '*'.
--
-- Si gestisce per:
--   * canale        : la CATEGORIA cliente (codice Mago, es. '31'=Amazon, '43'=Leroy Merlin,
--                     'BTOB', 'B2B0415'=Bricoman) o '*' (qualsiasi). E' la stessa chiave che la
--                     riga di vendita porta con se' (con il caso speciale Bricoman). 'marketplace'
--                     e' solo l'etichetta leggibile.
--   * area          : 'ITALIA' | nome Paese (es. 'FRANCIA') | 'ESTERO' generico | '*'  -> stessa
--                     geografia a cascata del trasporto (Paese specifico -> ESTERO -> '*').
--   * tipo_articolo : MA_Items.ItemType (come la stima trasporto su acquisti) o '*' (comune).
-- e si imposta:
--   * commissione_pct : % di commissione applicata al ricavo della riga.
--   * recupero_pct    : % di commissione STORNATA sulle note di credito di RESO (quanto il
--                       marketplace restituisce su un reso). Gli annullamenti/inevadibili/cambio
--                       documento (InvoicingAccGroup A/I/C) stornano SEMPRE il 100% (la vendita e'
--                       annullata) a prescindere da questo valore.
--   * valido_dal      : inizio validita'. Correggere una tariffa con una nuova data NON tocca i
--                       periodi gia' chiusi (per una riga si usa la tariffa valida alla sua data).
--
-- Risoluzione (la PIU' specifica e, a parita', la valido_dal piu' recente): canale esatto batte '*';
-- area: Paese batte macro batte '*'; tipo_articolo esatto batte '*'.
-- =============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'cfg') EXEC('CREATE SCHEMA cfg');
GO

IF OBJECT_ID(N'cfg.commissione_marketplace', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.commissione_marketplace (
        id              INT IDENTITY(1,1) NOT NULL,
        canale          NVARCHAR(40)  NOT NULL CONSTRAINT DF_cm_canale DEFAULT N'*',  -- categoria cliente (codice) o '*'
        marketplace     NVARCHAR(80)  NULL,                                           -- etichetta leggibile (Amazon, Leroy Merlin...)
        area            NVARCHAR(20)  NOT NULL CONSTRAINT DF_cm_area   DEFAULT N'*',  -- ITALIA | Paese | ESTERO | '*'
        tipo_articolo   NVARCHAR(20)  NOT NULL CONSTRAINT DF_cm_tipo   DEFAULT N'*',  -- MA_Items.ItemType o '*'
        commissione_pct DECIMAL(9,4)  NOT NULL CONSTRAINT DF_cm_comm   DEFAULT 0,
        recupero_pct    DECIMAL(9,4)  NOT NULL CONSTRAINT DF_cm_rec    DEFAULT 0,
        valido_dal      DATE          NOT NULL CONSTRAINT DF_cm_dal    DEFAULT '20200101',
        note            NVARCHAR(200) NULL,
        CONSTRAINT PK_cfg_commissione_marketplace PRIMARY KEY (id)
    );
END
GO

-- Seed dai valori LEGACY (kb_TabProvvigioniVendita): SOLO se la tabella e' vuota, cosi' un
-- ri-lancio della pipeline NON sovrascrive le tariffe inserite/corrette da UI. Tutte le righe
-- nascono generiche (area='*', tipo='*'): le eccezioni per Paese/Tipo si aggiungono dopo.
-- recupero_pct = 0: come il legacy, su un reso la commissione marketplace NON si recupera.
IF NOT EXISTS (SELECT 1 FROM cfg.commissione_marketplace)
    INSERT INTO cfg.commissione_marketplace (canale, marketplace, area, tipo_articolo, commissione_pct, recupero_pct, valido_dal, note)
    SELECT LTRIM(RTRIM(p.CategoriaCliente)), LTRIM(RTRIM(p.Descrizione)), N'*', N'*',
           p.CommissioniVendita, 0, '20200101', N'seed da kb_TabProvvigioniVendita (legacy)'
    FROM KODICEBAGNO_4.dbo.kb_TabProvvigioniVendita AS p
    WHERE p.CommissioniVendita IS NOT NULL;
GO
