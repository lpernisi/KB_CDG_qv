# CDG · Guida operativa — gestire i costi mese per mese

*Per l'Amministrazione. Dashboard: `https://pricing.kvstore.online/tools/cdg/` → sezione **Costo dei materiali**. Questa è la procedura da seguire d'ora in avanti per tenere i costi (e quindi i margini) affidabili.*

## Il ciclo in breve
1. **Una-tantum, all'inizio**: bonifica dei dati di partenza 2026 (i valori d'apertura ereditati dal gestionale).
2. **Ricalcolo** dell'anno in corso (un pulsante) → ricostruisce i costi mese per mese.
3. **Ogni mese, dal primo**: rivedi i casi segnalati, sistema, **Consolida i prezzi del mese**, passa al successivo.

I passi 1-2 si rifanno ogni volta che correggi un dato a monte; il passo 3 è il lavoro mensile ricorrente.

## Passo 0 — Bonifica dei dati di partenza (una-tantum)
Tab **Bonifica apertura**. Alcuni articoli partivano il 2026 con un valore d'apertura errato (eredità del vecchio
costo del gestionale): se l'apertura è sbagliata, l'errore si trascina su tutti i mesi.

- La lista propone i candidati con un **valore suggerito**. Clicca il **codice** per vedere i **movimenti 2025** da
  cui nasce il valore proposto e capire se è corretto.
- Se serve, **modifica** il valore, poi premi **Certifica** (lo fissa come apertura buona del 2026).
- Man mano che certifichi, la lista si **esaurisce**.

Quando hai sistemato le aperture, passa al ricalcolo.

## Passo 1 — Ricalcolo dell'anno in corso
In cima alla sezione **Costo dei materiali** c'è il pulsante **"↻ Ricalcola i costi dell'anno in corso"**.
Premendolo, il sistema riapplica le aperture bonificate e **ricostruisce il WAP mese per mese** (media ponderata con
riporto da un mese all'altro). Dura pochi secondi.

- È un ricalcolo **globale e unico**: ricostruisce tutti gli articoli e tutti i mesi insieme. Non esiste (di proposito)
  un ricalcolo del singolo mese o del singolo articolo isolato, perché ogni mese dipende dai precedenti: una sola
  passata aggiorna tutto.
- Per **vedere il risultato su un singolo articolo** dopo il ricalcolo, aprine la **scheda costo** (clic sul codice,
  in *Certificazione qualità* o dalla ricerca articolo): trovi il costo aggiornato e come si è formato.
- Rilancia il ricalcolo **ogni volta** che bonifichi un'apertura o correggi un dato di magazzino in Mago.

## Passo 2 — Verifica e consolidamento, mese per mese
Seleziona il **primo mese** dell'anno (selettore periodo in alto) e apri il tab **Certificazione qualità**.

- In alto l'**Indice di qualità** = % del valore di magazzino già a posto (verde) o certificato. L'obiettivo del mese
  è portarlo verso il 100%.
- La lista mostra **solo i casi da rivedere** con il loro livello e il motivo (gli indici attivi):
  - 🟢 **OK** — costo coerente: certificato in automatico, non richiede azione.
  - 🟡 **Warning** — da rivedere (es. il costo è cambiato molto rispetto al mese prima, o mancano gli oneri).
  - 🔴 **Errore** — bloccante (es. una valuta non convertita, o una quantità incoerente): il costo non è affidabile.
- Per ogni articolo da rivedere, **clicca il codice** → si apre la scheda costo (movimenti, oneri, eventuale distinta
  del kit). Capito il numero, scegli l'azione (vedi tabella sotto).

Quando hai sistemato i casi del mese **e l'Amministrazione ha caricato tutte le fatture d'acquisto/oneri del periodo**,
premi **"Consolida prezzi del mese"** (banner in cima): il mese diventa la **base solida** per valorizzare le vendite
del mese successivo. Poi passa al **mese successivo** e ripeti.

## Cosa fa ogni scelta sull'articolo (Certifica / Segnala / Ignora)
Le tre azioni sono un'**attestazione umana**: dicono *cosa hai deciso* su quel costo. **Non cambiano il numero** —
il numero si corregge o bonificando l'apertura, o sistemando il dato in Mago e **rilanciando il ricalcolo**.

- **Certifica** *(usala quando, vista la scheda, il costo è corretto)* → l'articolo viene marcato **CERTIFICATO** ed
  entra nell'**Indice di qualità** come "a posto". Resta in lista con l'etichetta dello stato e il link **riapri**.
- **Segnala** *(il costo è SBAGLIATO per un problema nel nostro calcolo/dato, non in Mago)* → marcato
  **DA_CORREGGERE_ALGORITMO** con la tua **nota**. **Non** conta nell'indice: resta una *to-do* per chi mantiene il
  motore di costo (la nota spiega cosa non torna). È il modo per dirci "qui il calcolo va aggiustato".
- **Ignora** *(caso noto e irrilevante: articolo dismesso, quantità nulla, ecc.)* → marcato **IGNORATO** con nota
  facoltativa. Esce dalla revisione e **non** conta come certificato: serve solo a togliere dalla lista ciò che non
  vale la pena guardare.
- **riapri** → annulla lo stato: l'articolo torna "da certificare" (se hai cambiato idea).

> Nota: l'indice *Q2 (WAP Mago azzerato)* è **solo informativo** — significa che il gestionale è azzerato su
> quell'articolo e il nostro costo è quello buono: di norma si può certificare.

## Promemoria
- **Ordine giusto**: prima bonifica apertura → poi ricalcolo → poi verifica/consolida i mesi in sequenza.
- **Un mese non consolidato** è una **stima**: il margine può ancora cambiare quando arrivano gli ultimi oneri.
- **Consolida solo quando i documenti del mese sono tutti caricati**: da quel momento quei costi alimentano il mese dopo.
- Dopo ogni correzione a monte (apertura o dato Mago), **rilancia il ricalcolo** per propagarla.
