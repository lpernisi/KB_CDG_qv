-- =============================================================================
-- Quadratura CONTABILE (top-down) dei costi: riscontro col bilancio di Mago.
-- -----------------------------------------------------------------------------
-- Secondo oracolo, a livello di PERIODO (oltre al controllo riga-per-riga): per il
-- MATERIALE vale l'identita'  Consumo = Acquisti + Rimanenze iniziali - Rimanenze finali,
-- dove Acquisti vengono dalla CONTABILITA' GENERALE (MA_JournalEntriesGLDetail, per conto)
-- e le Rimanenze dalla nostra valorizzazione (wap_ricalc). Deve dare ~lo stesso totale del
-- nostro Sigma costo del venduto (core.fatto_riga). Mappatura conti in tabella -> manutenibile,
-- riusabile per gli altri componenti (Commerciali, Trasporto, ...).
-- DebitCreditSign nel GL: 4980736 = DARE, 4980737 = AVERE (saldo conto costo = DARE - AVERE).
-- =============================================================================
USE CDG_QV;
GO

IF OBJECT_ID('kodice.conti_quadratura', 'U') IS NULL
CREATE TABLE kodice.conti_quadratura (
    Componente varchar(30)  NOT NULL,   -- MATERIALE, COMMERCIALI, TRASPORTO, ...
    Account    varchar(30)  NOT NULL,   -- conto di MA_ChartOfAccounts
    Ruolo      varchar(30)  NOT NULL,   -- ACQUISTO / ONERE_ACQUISTO / COSTO / ...
    Nota       varchar(200) NULL,
    CONSTRAINT PK_conti_quadratura PRIMARY KEY (Componente, Account)
);
GO

-- Seed MATERIALE (riconciliato gen-mag 2026 a +2.0% del nostro COGS).
MERGE kodice.conti_quadratura AS t
USING (VALUES
    ('MATERIALE','06011000','ACQUISTO',      'Acquisti merci'),
    ('MATERIALE','06011002','ACQUISTO',      'Anomalie fatture (acquisti)'),
    ('MATERIALE','06013000','ONERE_ACQUISTO','Dazi'),
    ('MATERIALE','06014000','ONERE_ACQUISTO','Spese di trasporto import'),
    ('MATERIALE','06014001','ONERE_ACQUISTO','Spese scarico container'),
    ('MATERIALE','06014002','ONERE_ACQUISTO','Spese soste container'),
    ('MATERIALE','06015000','ONERE_ACQUISTO','Costo trasporto acquisti/trasferimenti'),
    ('MATERIALE','06015001','ONERE_ACQUISTO','Costo trasporto trasf. Amazon logistica'),
    -- RIMANENZE (stato patrimoniale): saldo a bilancio (MA_ChartOfAccountsBalances), NON il GL cumulato.
    ('MATERIALE','00041000','RIMANENZE','Materie prime, sussidiarie e consumo'),
    ('MATERIALE','00041100','RIMANENZE','Prodotti in lavorazione, semilavorati'),
    ('MATERIALE','00041200','RIMANENZE','Lavori in corso su ordinazione'),
    ('MATERIALE','00041300','RIMANENZE','Prodotti finiti e merci')
) AS s (Componente, Account, Ruolo, Nota)
   ON t.Componente = s.Componente AND t.Account = s.Account
WHEN NOT MATCHED THEN INSERT (Componente, Account, Ruolo, Nota)
     VALUES (s.Componente, s.Account, s.Ruolo, s.Nota);
GO
