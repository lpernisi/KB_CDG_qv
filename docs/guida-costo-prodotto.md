# CDG · Sezione "Costo dei materiali" — guida alla lettura

*Per Direzione e Amministrazione. Dashboard: `https://pricing.kvstore.online/tools/cdg/` → sezione **Costo dei materiali**.*

## Da dove arriva il costo (in breve)
Non usiamo più il costo "grezzo" del gestionale (che su molti articoli era inaffidabile: quantità che vanno
negative, costi che si azzerano). Lo **ricalcoliamo mese per mese** dai movimenti reali di magazzino:

- partiamo dalla **giacenza reale** di inizio anno e dal suo costo;
- aggiungiamo gli **acquisti** del periodo (media ponderata), **convertendo le valute estere** (USD…) in euro al
  cambio del documento;
- teniamo separati il **costo puro d'acquisto** e gli **oneri accessori** (dazi, trasporti su acquisto, import);
- se in un mese non ci sono acquisti, il costo dell'ultimo mese valido viene **mantenuto** (non si azzera).

Il risultato è un **costo unitario per articolo e per mese** su cui poggia tutto il Conto Economico (Materiale →
Margine). I kit sono valorizzati esplodendo la distinta sui costi dei componenti.

## Come si legge la sezione (gli strumenti)
In alto, la sezione mostra il **periodo selezionato** e una **fascia di stato del mese** (vedi §"Consolidamento").
Sotto, le viste:

**1) Certificazione qualità** — è il "semaforo" del dato. Ogni articolo del mese è classificato:
- 🟢 **OK** — costo coerente, nessun problema: certificabile in automatico.
- 🟡 **Warning** — da rivedere (es. il costo è cambiato molto rispetto al mese prima, oppure mancano gli oneri).
- 🔴 **Errore** — bloccante (es. una valuta non convertita, o una quantità incoerente): il costo non è affidabile
  finché non si risolve.

  In alto un **Indice di qualità** (% del valore di magazzino già a posto o certificato). La lista mostra solo i casi
  🟡/🔴 con il motivo. Per ognuno si può **Certificare** (lo accetto), **Segnalare** (c'è un problema da sistemare nel
  calcolo) o **Ignorare** (caso noto). *Cliccando il codice articolo* si apre la **scheda costo** (sotto).

**2) Bonifica apertura** — è un'attività **una-tantum**: alcuni articoli partivano il 2026 con un valore d'apertura
errato (eredità del vecchio costo del gestionale). Qui si forza il valore corretto, verificandolo. Man mano che si 
certificano, questa lista si **esaurisce**.

**3) Trend del costo** — evidenzia gli articoli il cui **costo è salito o sceso** oltre una soglia % (regolabile)
negli ultimi mesi: servono per individuare variazioni anomale da verificare con l'ufficio acquisti, o per avere un 
alert che potrebbe richiedere o suggerire un intervento sui prezzi di vendita.

**4) Raffronto vs Mago** — il confronto che serve quando *"i totali con Qlik non tornano"*. Per ogni articolo del
periodo mette in fila tre costi del venduto: **nostro** (ricalcolo), **WAP Mago** (il vecchio costo di magazzino) e
**"mensile Mago"** (`costomaterialemensile`, cioè la voce che oggi alimenta il Conto Economico di Qlik). In alto i
totali con il **Δ nostro − Qlik**: è esattamente lo scostamento della voce *Materiale* tra il nostro CE e quello di
Qlik. La tabella è ordinata per impatto del Δ, così si vede subito **quali articoli** spiegano la differenza (e
cliccando il codice si apre la scheda costo per capire il perché).

**5) Scheda costo (clic sul codice articolo)** — mostra *come si è formato* il costo: il **fornitore preferenziale**
(presa solo dall'anagrafica di tipo *fornitore*; i **kit** assemblati in casa hanno come fornitore "KB Srl" = noi,
segnalato con *"assemblaggio interno"*), il roll mese per mese (giacenza, acquisti puro/oneri, vendite, costo) e
l'elenco dei **movimenti** (acquisti, cambi, oneri, rettifiche) con evidenziati ★ quelli che **determinano** il costo.
- Per i **kit** la scheda non mostra movimenti propri (un kit non si muove a magazzino): al loro posto compare la
  **distinta esplosa** con il costo di ciascun componente e la **somma = costo del kit**. È normale, quindi, che per
  un kit "Costo efficace" e il roll WAP risultino vuoti: il costo arriva dai componenti.

È lo strumento per capire un numero prima di certificarlo.

## Il consolidamento mensile (azione dell'Amministrazione)
In cima alla sezione, per il mese selezionato, una fascia indica:
- 🟡 **IN FORMAZIONE — stima incompleta**: non tutti i documenti del mese sono ancora caricati → i costi possono
  ancora cambiare.
- 🟢 **CONSOLIDATO**: l'Amministrazione ha confermato che **tutti i documenti del mese sono caricati**. Da quel
  momento i costi del mese sono la **base solida** per valorizzare le vendite del mese successivo.

**Istruzione operativa:** a fine mese, quando l'Amministrazione ha registrato tutte le fatture d'acquisto/oneri del
periodo, verifica i costi che potrebbero essere anomali, e una volta che i costi sono corretti 
preme **"Consolida prezzi del mese"**. Prima di allora i numeri vanno letti come **stima provvisoria**.

## Come interpretare i numeri (regole pratiche)
- Il **Materiale** nel Conto Economico (sezione *Riepilogo CE*, per Canale/Cliente/…) usa **questo** costo, non più
  quello del gestionale. Nel Riepilogo CE si può scegliere **Mese** oppure **Anno intero** (totale progressivo
  dell'anno, non solo il mese filtrato) e c'è la colonna **% Mat** = incidenza del costo materiale sul fatturato.
- Quando i totali **non quadrano con Qlik**, usa la vista **Raffronto vs Mago** (sopra): il *Δ nostro − Qlik* totale è
  lo scostamento di *Materiale*, e la lista per articolo dice da dove arriva.
- Un mese **non consolidato** = stima: il margine può ancora migliorare/peggiorare quando arrivano gli ultimi oneri.
- Articoli 🔴/🟡 non certificati = il loro costo è **da verificare**: il margine su quelle righe è indicativo.
- Un costo che diverge molto dal gestionale **non è un errore nostro**: spesso il gestionale è fermo/azzerato e il
  nostro è quello corretto (lo si vede nella scheda costo).

## Glossario
- **Materiale / Costo del venduto**: costo d'acquisto dei prodotti venduti.
- **Oneri accessori**: dazi, import, trasporti sull'acquisto — capitalizzati nel costo.
- **Costo efficace**: il costo unitario "buono" usato per valorizzare, con i ripieghi quando manca il dato.
- **Risalita**: se in un mese manca il costo, si usa l'ultimo costo valido precedente.
- **Consolidato**: il mese è chiuso lato documenti → costi definitivi.
- **Kit**: articolo composto da più componenti; non ha un costo di magazzino proprio, lo si ottiene sommando i costi
  dei componenti (distinta esplosa). I kit hanno come "fornitore" noi stessi (KB Srl, cod. 9998).
- **WAP / WAP Mago**: il vecchio costo medio di magazzino del gestionale, ormai usato **solo come raffronto**.
