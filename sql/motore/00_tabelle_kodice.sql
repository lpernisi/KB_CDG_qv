-- =============================================================================
-- kodice.<tabelle motore>  —  MOTORE COSTI (snapshot dal DB, NON parte dello scaffold originale)
-- Estratto da CDG_QV per documentazione/versionamento. La fonte di verita' resta
-- l'oggetto nel database; la dashboard mostra la definizione LIVE.
-- =============================================================================


-- kodice.costi_articolo_mese
CREATE TABLE kodice.costi_articolo_mese (
    Item varchar(40) NOT NULL,
    Anno smallint NOT NULL,
    Mese tinyint NOT NULL,
    TipoArticolo varchar(12) NOT NULL,
    Costo decimal(18,4) NOT NULL,
    Completo bit NOT NULL,
    NComponentiTotali int NULL,
    NComponentiValidi int NULL,
    MeseCostoUsato date NULL,
    DataCalcolo datetime2 NOT NULL
);

-- kodice.costi_eccezioni
CREATE TABLE kodice.costi_eccezioni (
    Id bigint NOT NULL,
    Item varchar(40) NOT NULL,
    Anno smallint NOT NULL,
    Mese tinyint NOT NULL,
    TipoEccezione varchar(20) NOT NULL,
    ComponenteColpevole varchar(40) NOT NULL,
    Dettaglio varchar(200) NULL,
    Stato varchar(10) NOT NULL,
    DataRilevazione datetime2 NOT NULL,
    DataRisoluzione datetime2 NULL
);

-- kodice.prep_controllo_mesi
CREATE TABLE kodice.prep_controllo_mesi (
    Anno smallint NOT NULL,
    Mese tinyint NOT NULL,
    DataEsecuzione datetime2 NOT NULL,
    NArticoli int NOT NULL,
    NKit int NOT NULL,
    NEccezioniAperte int NOT NULL,
    Stato varchar(15) NOT NULL
);
