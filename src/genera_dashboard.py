"""
genera_dashboard.py
===================
Genera dashboard/index.html dalla FONTE del repo:
  - dashboard/manifest.yaml      (spiegazioni in italiano, una per componente)
  - sql/components/*.sql          (il testo SQL REALE delle procedure)
  - sql/ddl/02_seed_componenti.sql (registro: livello, segno, attivo)
  - dashboard/caso_test.json      (documento di esempio per il pannello di test)
  - dashboard/risultati_caso.json (opzionale: risultati ufficiali dal DB)

La dashboard mostra, per ogni componente, la spiegazione ACCANTO al codice reale:
cosi' un non tecnico legge "da dove prende i dati e con che logica", e puo'
confrontarlo col codice eseguito. Il generatore segnala se manifest, registro e
file .sql non sono allineati.

Lancio:  python src/genera_dashboard.py
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import yaml

# Cartella radice del progetto (due livelli sopra questo file).
ROOT = Path(__file__).resolve().parent.parent

DASH = ROOT / "dashboard"


def leggi_registro_dal_seed() -> dict:
    """
    Estrae (livello, segno, attivo) dal file di seed del registro, senza DB.
    Il seed e' la dichiarazione versionata: e' la fonte giusta per una dashboard
    di revisione del codice.
    """
    testo = (ROOT / "sql/ddl/02_seed_componenti.sql").read_text(encoding="utf-8")
    # Ogni tupla: (N'CODICE', N'descr', N'modo', livello, segno, attivo, N'note')
    pattern = re.compile(
        r"\(N'([A-Z_]+)'\s*,\s*N'.*?'\s*,\s*N'.*?'\s*,\s*(\d+|NULL)\s*,\s*(-?\d+)\s*,\s*(\d+)\s*,",
        re.DOTALL,
    )
    reg = {}
    for m in pattern.finditer(testo):
        codice, livello, segno, attivo = m.groups()
        reg[codice] = {
            "livello": None if livello == "NULL" else int(livello),
            "segno": int(segno),
            "attivo": bool(int(attivo)),
        }
    return reg


def controlla_allineamento(componenti: list, registro: dict) -> list:
    """Confronta manifest, registro e file .sql; ritorna la lista di avvisi."""
    avvisi = []
    codici_manifest = {c["codice"] for c in componenti}
    codici_registro = set(registro.keys())
    file_sql = {p.name for p in (ROOT / "sql/components").glob("*.sql")}

    for c in componenti:
        # Il file SQL dichiarato esiste?
        if not (ROOT / c["file_sql"]).exists():
            avvisi.append(f"{c['codice']}: file SQL mancante ({c['file_sql']}).")
        # La procedura nel file ha il nome atteso?
        else:
            testo = (ROOT / c["file_sql"]).read_text(encoding="utf-8")
            if f"usp_comp_{c['codice']}" not in testo:
                avvisi.append(f"{c['codice']}: nel file SQL non trovo la procedura usp_comp_{c['codice']}.")

    for codice in codici_registro - codici_manifest:
        avvisi.append(f"{codice}: nel registro ma SENZA spiegazione nel manifest.")
    for codice in codici_manifest - codici_registro:
        avvisi.append(f"{codice}: nel manifest ma NON nel registro (cfg.componenti).")

    return avvisi


def carica_json(percorso: Path):
    """Carica un file JSON se esiste, altrimenti None."""
    if percorso.exists():
        return json.loads(percorso.read_text(encoding="utf-8"))
    return None


# ----------------------------- Generazione HTML -----------------------------

LIVELLO_ROMANO = {1: "I", 2: "II", 3: "III"}


def card_componente(c: dict, reg: dict) -> str:
    """Costruisce la 'scheda' HTML di un componente: spiegazione + SQL reale."""
    livello = reg.get("livello")
    attivo = reg.get("attivo", False)
    segno = reg.get("segno")
    sql = html.escape((ROOT / c["file_sql"]).read_text(encoding="utf-8"))

    pill_liv = (f'<span class="pill liv liv{livello}">Margine {LIVELLO_ROMANO.get(livello, "—")}</span>'
                if livello else '<span class="pill liv struttura">Struttura</span>')
    pill_att = (f'<span class="pill {"on" if attivo else "off"}">'
                f'{"Attivo" if attivo else "Inattivo"}</span>')
    pill_seg = f'<span class="pill segno">{"costo (−)" if segno == -1 else "ricavo (+)"}</span>'

    return f"""
    <section class="card liv{livello or 0}" id="comp-{c['codice']}">
      <div class="card-head">
        <h3>{html.escape(c['titolo'])}</h3>
        <div class="pills">{pill_liv}{pill_att}{pill_seg}<span class="pill code">{html.escape(c['codice'])}</span></div>
      </div>
      <div class="spieg">
        <div><span class="lbl">Da dove prende i dati</span><p>{html.escape(c['sorgente'].strip())}</p></div>
        <div><span class="lbl">Con che logica</span><p>{html.escape(c['logica'].strip())}</p></div>
      </div>
      <details class="codice">
        <summary>Codice realmente eseguito · <code>{html.escape(c['file_sql'])}</code></summary>
        <pre><code>{sql}</code></pre>
      </details>
    </section>"""


def nav_voce(c: dict, reg: dict) -> str:
    livello = reg.get("livello")
    dot = "on" if reg.get("attivo") else "off"
    return (f'<a href="#comp-{c["codice"]}"><span class="dot {dot}"></span>'
            f'<span class="nv-liv liv{livello or 0}">{LIVELLO_ROMANO.get(livello, "S")}</span>'
            f'{html.escape(c["titolo"])}</a>')


def genera():
    manifest = yaml.safe_load((DASH / "manifest.yaml").read_text(encoding="utf-8"))
    componenti = manifest["componenti"]
    registro = leggi_registro_dal_seed()
    avvisi = controlla_allineamento(componenti, registro)
    caso = carica_json(DASH / "caso_test.json")
    risultati = carica_json(DASH / "risultati_caso.json")

    # Avvisi a console
    if avvisi:
        print("AVVISI di allineamento:")
        for a in avvisi:
            print("  - " + a)
    else:
        print("Allineamento OK: manifest, registro e file SQL coincidono.")

    # Dati per il pannello di test (simulazione lato browser)
    dati_js = json.dumps({
        "componenti": [
            {
                "codice": c["codice"], "titolo": c["titolo"],
                "livello": registro.get(c["codice"], {}).get("livello"),
                "segno": registro.get(c["codice"], {}).get("segno", -1),
                "attivo": registro.get(c["codice"], {}).get("attivo", False),
                "simulazione": c.get("simulazione", {"tipo": "nessuna"}),
            } for c in componenti
        ],
        "caso": caso,
        "risultati": risultati,
    }, ensure_ascii=False)

    banner = ""
    if avvisi:
        voci = "".join(f"<li>{html.escape(a)}</li>" for a in avvisi)
        banner = f'<div class="banner warn"><strong>Disallineamenti da sistemare:</strong><ul>{voci}</ul></div>'
    else:
        banner = '<div class="banner ok">Spiegazioni allineate al codice: manifest, registro e procedure SQL coincidono.</div>'

    cards = "".join(card_componente(c, registro.get(c["codice"], {})) for c in componenti)
    nav = "".join(nav_voce(c, registro.get(c["codice"], {})) for c in componenti)

    html_out = PAGINA.replace("{{BANNER}}", banner) \
                     .replace("{{NAV}}", nav) \
                     .replace("{{CARDS}}", cards) \
                     .replace("{{DATI}}", dati_js)

    out = DASH / "index.html"
    out.write_text(html_out, encoding="utf-8")
    print(f"\nDashboard generata: {out}")
    return out


# La pagina HTML (template). Lo stile e' definito qui; i dati vengono iniettati.
PAGINA = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CDG_QV · Dashboard estrazioni</title>
<style>
  :root{
    --paper:#f6f4ee; --ink:#211e1a; --muted:#6f675c; --line:#e2dcd0; --card:#fffdf8;
    --ok:#2f7d52; --okbg:#e7f1ea; --warn:#9a5a1e; --warnbg:#f6ecdd;
    --l1:#3a6ea5; --l2:#7a5ea7; --l3:#a55b3a; --struct:#8a8170;
    --accent:#3a6ea5;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--paper);color:var(--ink);
       font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5}
  code,pre{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
  header{padding:22px 28px;border-bottom:1px solid var(--line);background:var(--card)}
  header h1{font-family:Georgia,"Iowan Old Style",serif;font-size:24px;margin:0;letter-spacing:.2px}
  header p{margin:4px 0 0;color:var(--muted);font-size:14px}
  .wrap{display:grid;grid-template-columns:240px 1fr;gap:0;max-width:1180px;margin:0 auto}
  nav{position:sticky;top:0;align-self:start;padding:22px 14px;border-right:1px solid var(--line);height:100vh;overflow:auto}
  nav .t{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 10px 6px}
  nav a{display:flex;align-items:center;gap:8px;color:var(--ink);text-decoration:none;font-size:13.5px;
        padding:7px 6px;border-radius:7px}
  nav a:hover{background:#efeae0}
  nav .dot{width:7px;height:7px;border-radius:50%;flex:none}
  nav .dot.on{background:var(--ok)} nav .dot.off{background:#cfc7b8}
  nav .nv-liv{font-family:Georgia,serif;font-size:11px;width:22px;text-align:center;color:#fff;border-radius:4px;flex:none;padding:1px 0}
  main{padding:24px 28px 60px}
  .banner{padding:12px 16px;border-radius:10px;font-size:14px;margin-bottom:22px}
  .banner.ok{background:var(--okbg);color:var(--ok)}
  .banner.warn{background:var(--warnbg);color:var(--warn)}
  .banner ul{margin:8px 0 0;padding-left:18px}
  .liv1{--lc:var(--l1)} .liv2{--lc:var(--l2)} .liv3{--lc:var(--l3)} .liv0{--lc:var(--struct)}
  .nv-liv.liv1{background:var(--l1)} .nv-liv.liv2{background:var(--l2)} .nv-liv.liv3{background:var(--l3)} .nv-liv.liv0{background:var(--struct)}
  .card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--lc,var(--struct));
        border-radius:12px;padding:18px 20px;margin-bottom:18px}
  @media (prefers-reduced-motion: no-preference){
    .card{opacity:0;transform:translateY(8px);animation:rise .5s ease forwards}
    .card:nth-child(1){animation-delay:.02s}.card:nth-child(2){animation-delay:.06s}
    .card:nth-child(3){animation-delay:.10s}.card:nth-child(4){animation-delay:.14s}.card:nth-child(5){animation-delay:.18s}
  }
  @keyframes rise{to{opacity:1;transform:none}}
  @supports not (display:grid){
    .wrap{display:block} nav{height:auto;border-right:none;border-bottom:1px solid var(--line)}
  }
  .card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
  .card-head h3{font-family:Georgia,serif;font-size:18px;margin:0}
  .pills{display:flex;gap:6px;flex-wrap:wrap}
  .pill{font-size:11.5px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);background:#fff;color:var(--muted)}
  .pill.liv{color:#fff;border:none;background:var(--lc,var(--struct))}
  .pill.on{color:var(--ok);background:var(--okbg);border-color:transparent}
  .pill.off{color:var(--muted)}
  .pill.code{font-family:ui-monospace,monospace;color:var(--ink)}
  .spieg{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:14px}
  .spieg .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--accent);font-weight:600}
  .spieg p{margin:5px 0 0;font-size:14px}
  details.codice{margin-top:14px;border-top:1px dashed var(--line);padding-top:10px}
  details.codice summary{cursor:pointer;font-size:12.5px;color:var(--muted)}
  details.codice code{color:var(--ink)}
  details.codice pre{background:#1f1d1a;color:#e9e4d8;padding:14px;border-radius:8px;overflow:auto;font-size:12.5px;margin:10px 0 0;max-height:360px}
  @media(max-width:820px){.wrap{grid-template-columns:1fr}nav{display:none}.spieg{grid-template-columns:1fr}}

  /* Pannello caso di test */
  .test{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:26px}
  .test h2{font-family:Georgia,serif;font-size:19px;margin:0 0 4px}
  .test .nota{font-size:13px;color:var(--muted);margin:0 0 14px}
  .test .grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  .test table{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}
  .test th,.test td{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left}
  .test th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}
  .test input{width:90px;border:1px solid var(--line);border-radius:6px;padding:4px 6px;font:inherit}
  .test td.num,.test th.num{text-align:right;font-variant-numeric:tabular-nums}
  .btn{margin-top:12px;background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px 16px;font:inherit;cursor:pointer}
  .btn.sec{background:#fff;color:var(--ink);border:1px solid var(--line);margin-left:8px}
  .ris{margin-top:18px}
  .ris h3{font-family:Georgia,serif;font-size:16px;margin:0 0 6px}
  .tot{font-size:14px;margin-top:8px}
  .tag{display:inline-block;font-size:11px;background:#efeae0;color:var(--muted);padding:2px 8px;border-radius:999px;margin-left:8px}
  @media(max-width:820px){.test .grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>CDG_QV · Dashboard delle estrazioni</h1>
  <p>Ogni componente di costo, spiegato accanto al codice che lo calcola. Per chi non legge SQL, e per provare un caso di test.</p>
</header>
<div class="wrap">
  <nav>
    <p class="t">Componenti</p>
    <a href="#test"><span class="dot on"></span><span class="nv-liv liv0">▶</span>Caso di test</a>
    {{NAV}}
  </nav>
  <main>
    {{BANNER}}

    <section class="test" id="test">
      <h2>Caso di test</h2>
      <p class="nota">Inserisci un documento di esempio (righe + costi certificati) e premi <em>Esegui</em>.
        È una <strong>simulazione della logica dichiarata</strong> nel manifest: il calcolo ufficiale
        gira sul database con le procedure reali (<code>python src/esegui_caso_test.py</code>).
        Il <strong>costo</strong> arriva dal motore kodice (gia' con risalita mese e kit risolti);
        il <strong>ricavo</strong> di riga include gia' la quota di trasporto recuperato spalmata.</p>
      <div class="grid">
        <div>
          <strong>Righe di vendita</strong>
          <table id="t-righe"><thead><tr><th>Riga</th><th>Articolo</th><th class="num">Q.tà</th><th class="num">Ricavo</th><th></th></tr></thead><tbody></tbody></table>
          <button class="btn sec" onclick="aggiungiRiga()">+ riga</button>
        </div>
        <div>
          <strong>Costi certificati (kodice)</strong>
          <table id="t-costi"><thead><tr><th>Articolo</th><th class="num">Anno</th><th class="num">Mese</th><th class="num">Costo</th><th></th></tr></thead><tbody></tbody></table>
          <button class="btn sec" onclick="aggiungiCosto()">+ costo</button>
          <p class="nota" style="margin-top:10px">Competenza: <input id="c-anno" style="width:70px"> / <input id="c-mese" style="width:50px"></p>
        </div>
      </div>
      <button class="btn" onclick="esegui()">Esegui simulazione</button>
      <div class="ris" id="risultati"></div>
    </section>

    {{CARDS}}
  </main>
</div>

<script>
const DATI = {{DATI}};
const ROMANO = {1:"I",2:"II",3:"III"};

function eur(x){ return (x==null)?"—":x.toLocaleString("it-IT",{minimumFractionDigits:2,maximumFractionDigits:2})+" €"; }

// --- popola le tabelle editabili dal caso di test ---------------------------
function righeTbody(){ return document.querySelector("#t-righe tbody"); }
function costiTbody(){ return document.querySelector("#t-costi tbody"); }
function aggiungiRiga(r={line:"",codice_articolo:"",quantita:"",ricavo_netto:""}){
  const tr=document.createElement("tr");
  tr.innerHTML=`<td><input value="${r.line}" style="width:50px"></td><td><input value="${r.codice_articolo}"></td>
    <td class="num"><input class="num" value="${r.quantita}"></td><td class="num"><input class="num" value="${r.ricavo_netto}"></td>
    <td><button class="btn sec" onclick="this.closest('tr').remove()">×</button></td>`;
  righeTbody().appendChild(tr);
}
function aggiungiCosto(k={codice_articolo:"",anno:"",mese:"",costo:""}){
  const tr=document.createElement("tr");
  tr.innerHTML=`<td><input value="${k.codice_articolo}"></td><td class="num"><input class="num" value="${k.anno}" style="width:70px"></td>
    <td class="num"><input class="num" value="${k.mese}" style="width:50px"></td><td class="num"><input class="num" value="${k.costo}"></td>
    <td><button class="btn sec" onclick="this.closest('tr').remove()">×</button></td>`;
  costiTbody().appendChild(tr);
}
(function init(){
  const c=DATI.caso||{competenza:{anno:2026,mese:4},righe_vendita:[],costi_articolo:[]};
  document.getElementById("c-anno").value=c.competenza.anno;
  document.getElementById("c-mese").value=c.competenza.mese;
  (c.righe_vendita||[]).forEach(aggiungiRiga);
  (c.costi_articolo||[]).forEach(aggiungiCosto);
})();

function leggiRighe(){
  return [...righeTbody().querySelectorAll("tr")].map(tr=>{
    const i=tr.querySelectorAll("input");
    return {line:i[0].value, codice_articolo:i[1].value.trim(),
            quantita:parseFloat(i[2].value)||0, ricavo_netto:parseFloat(i[3].value)||0};
  }).filter(r=>r.codice_articolo);
}
function leggiCosti(){
  return [...costiTbody().querySelectorAll("tr")].map(tr=>{
    const i=tr.querySelectorAll("input");
    return {codice_articolo:i[0].value.trim(), anno:parseInt(i[1].value)||0,
            mese:parseInt(i[2].value)||0, costo:parseFloat(i[3].value)};
  }).filter(k=>k.codice_articolo);
}

// --- simulazione della logica dichiarata ------------------------------------
// Costo CERTIFICATO: il motore kodice ha gia' risolto risalita mese e kit, quindi
// si cerca il costo dell'articolo per il mese di competenza ESATTO (nessuna risalita qui).
function costoCertificato(art, costi, competenza){
  const c=costi.find(k=>k.codice_articolo===art && (k.anno*100+k.mese)===competenza);
  return c? c.costo : null;
}
function importoComponente(comp, riga, costi, competenza){
  const s=comp.simulazione||{tipo:"nessuna"};
  if(s.tipo==="costo_certificato"){ const k=costoCertificato(riga.codice_articolo,costi,competenza); return k==null?null:+(riga.quantita*k).toFixed(2); }
  if(s.tipo==="percentuale_ricavo"){ return +(riga.ricavo_netto*(s.parametro||0)).toFixed(2); }
  if(s.tipo==="per_unita"){ return +(riga.quantita*(s.parametro||0)).toFixed(2); }
  return undefined; // logica non definita -> non simulabile
}

function esegui(){
  const righe=leggiRighe(), costi=leggiCosti();
  const competenza=(parseInt(document.getElementById("c-anno").value)||0)*100+(parseInt(document.getElementById("c-mese").value)||0);
  const comps=DATI.componenti.filter(c=>c.attivo);

  // controllo per componente + dettaglio per riga
  const perRiga={}; righe.forEach(r=>perRiga[r.line]={ricavo:r.ricavo_netto,liv:{1:0,2:0,3:0}});
  const controllo=[];
  comps.forEach(c=>{
    let n=0,nz=0,tot=0,simulabile=true;
    righe.forEach(r=>{
      const imp=importoComponente(c,r,costi,competenza);
      if(imp===undefined){simulabile=false;return;}
      n++; if(imp==null)nz++; tot+=(imp||0);
      if(c.livello) perRiga[r.line].liv[c.livello]+=(imp||0)*c.segno;
    });
    controllo.push({codice:c.codice,titolo:c.titolo,livello:c.livello,simulabile,n,nz,tot});
  });

  // margini per riga
  const fatto=righe.map(r=>{
    const L=perRiga[r.line].liv;
    const mdc1=r.ricavo_netto+L[1], mdc2=mdc1+L[2], mdc3=mdc2+L[3];
    return {line:r.line,art:r.codice_articolo,ricavo:r.ricavo_netto,mdc1,mdc2,mdc3};
  });
  const T=(k)=>fatto.reduce((s,f)=>s+f[k],0);

  let h=`<h3>Controllo per componente <span class="tag">simulazione</span></h3>
    <table><thead><tr><th>Componente</th><th>Liv.</th><th class="num">Righe</th><th class="num">Senza importo</th><th class="num">Totale</th></tr></thead><tbody>`;
  controllo.forEach(c=>{
    h+=`<tr><td>${c.titolo}</td><td>${ROMANO[c.livello]||"—"}</td>
      <td class="num">${c.simulabile?c.n:"—"}</td><td class="num">${c.simulabile?c.nz:"logica non definita"}</td>
      <td class="num">${c.simulabile?eur(c.tot):"—"}</td></tr>`;
  });
  h+=`</tbody></table>
    <h3 style="margin-top:18px">Conto economico di riga</h3>
    <table><thead><tr><th>Riga</th><th>Articolo</th><th class="num">Ricavo</th><th class="num">MdC I</th><th class="num">MdC II</th><th class="num">MdC III</th></tr></thead><tbody>`;
  fatto.forEach(f=>{
    h+=`<tr><td>${f.line}</td><td>${f.art}</td><td class="num">${eur(f.ricavo)}</td>
      <td class="num">${eur(f.mdc1)}</td><td class="num">${eur(f.mdc2)}</td><td class="num">${eur(f.mdc3)}</td></tr>`;
  });
  h+=`</tbody></table>
    <p class="tot"><strong>Totali</strong> — MdC I: ${eur(T("mdc1"))} · MdC II: ${eur(T("mdc2"))} · MdC III: ${eur(T("mdc3"))}</p>`;

  if(DATI.risultati){
    h+=`<h3 style="margin-top:22px">Risultati ufficiali dal database <span class="tag">procedure reali</span></h3>
      <pre style="background:#1f1d1a;color:#e9e4d8;padding:12px;border-radius:8px;overflow:auto;font-size:12px">`
      +JSON.stringify(DATI.risultati,null,2)+`</pre>`;
  }
  document.getElementById("risultati").innerHTML=h;
}
esegui();
</script>
</body>
</html>"""


if __name__ == "__main__":
    genera()
