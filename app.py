import os
import json
import sqlite3
import requests
from datetime import datetime
from flask import Flask, request
from PIL import Image
import io
from google import genai
from google.genai import types

app = Flask(__name__)

# ==========================================
# 🔑 LE TUE 3 CHIAVI
# ==========================================
TELEGRAM_TOKEN = "8887911048:AAE47GwRRtZgfKJRNuemMNburSvbUIw3Xzk"
GEMINI_API_KEY = "AQ.Ab8RN6KQVTDOccGD68YJK0x5y2EUdfehtH1akUb-szWjhWt4Cw"
FOOTBALL_API_KEY = "ecc1dce996b099e002247afba8af1b44"

client = genai.Client(api_key=GEMINI_API_KEY)

def init_db():
    conn = sqlite3.connect("scommesse.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS schedine (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    importo REAL,
                    vincita_potenziale REAL,
                    stato TEXT DEFAULT 'IN_CORSO',
                    data_creazione TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS eventi (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedina_id INTEGER,
                    squadra_casa TEXT,
                    squadra_ospite TEXT,
                    mercato TEXT,
                    quota REAL,
                    stato TEXT DEFAULT 'IN_CORSO',
                    ultimo_risultato TEXT DEFAULT 'Non iniziata'
                )''')
    conn.commit()
    conn.close()

init_db()

def invia_messaggio(chat_id, testo):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": testo, "parse_mode": "Markdown"})

@app.route("/", methods=["POST"])
def webhook():
    try:
        update = request.json
        if not update or "message" not in update:
            return "OK", 200
        
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")

        if text == "/start":
            invia_messaggio(chat_id, "👋 *Bet Tracker Cloud Attivo!*\n\n📸 *Inviami lo screenshot* di una schedina per registrarla.\n\n📋 *Comandi:*\n• `/attive` - Schedine in corso\n• `/bilancio` - Cassa e profitto")
        elif text == "/attive":
            mostra_attive(chat_id)
        elif text == "/bilancio":
            mostra_bilancio(chat_id)
        elif "photo" in msg:
            invia_messaggio(chat_id, "🔍 Sto leggendo lo screenshot con l'AI...")
            file_id = msg["photo"][-1]["file_id"]
            elabora_screenshot(chat_id, file_id)
            
    except Exception as e:
        print(f"Errore webhook: {e}")
    return "OK", 200

def elabora_screenshot(chat_id, file_id):
    try:
        file_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        file_path = file_res["result"]["file_path"]
        img_bytes = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}").content
        
        img = Image.open(io.BytesIO(img_bytes))
        prompt = 'Analizza questo screenshot di una schedina calcistica. Restituisci SOLO un JSON valido con questa struttura: {"importo_giocato": 10.0, "vincita_potenziale": 50.0, "eventi": [{"squadra_casa": "Inter", "squadra_ospite": "Milan", "mercato": "Over 8.5 Corner", "quota": 1.80}]}. Normalizza i nomi delle squadre. Se importo o vincita non si leggono metti 0.'
        
        # Tentativi automatici in caso di sovraccarico temporaneo dei server Google
        response = None
        for tentativo in range(3):
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[img, prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                break
            except Exception as e:
                if tentativo == 2:
                    raise e

        dati = json.loads(response.text)
        
        conn = sqlite3.connect("scommesse.db")
        c = conn.cursor()
        c.execute("INSERT INTO schedine (chat_id, importo, vincita_potenziale, data_creazione) VALUES (?, ?, ?, ?)",
                  (chat_id, dati.get("importo_giocato", 0), dati.get("vincita_potenziale", 0), datetime.now().strftime("%d/%m/%Y %H:%M")))
        schedina_id = c.lastrowid
        
        riepilogo = f"✅ *Schedina #{schedina_id} Salvata!*\n💰 Puntata: {dati.get('importo_giocato', 0)}€ ➡️ Vincita: {dati.get('vincita_potenziale', 0)}€\n\n"
        for ev in dati.get("eventi", []):
            c.execute("INSERT INTO eventi (schedina_id, squadra_casa, squadra_ospite, mercato, quota) VALUES (?, ?, ?, ?, ?)",
                      (schedina_id, ev["squadra_casa"], ev["squadra_ospite"], ev["mercato"], ev.get("quota", 1.0)))
            riepilogo += f"⚽ *{ev['squadra_casa']} - {ev['squadra_ospite']}*\n   🎯 `{ev['mercato']}` (@{ev.get('quota', '-')})\n"
            
        conn.commit()
        conn.close()
        invia_messaggio(chat_id, riepilogo)
    except Exception as e:
        invia_messaggio(chat_id, f"❌ Errore lettura: {e}")

@app.route("/check-live", methods=["GET"])
def trigger_live():
    controlla_risultati_live()
    return "Controllo live eseguito", 200

def controlla_risultati_live():
    conn = sqlite3.connect("scommesse.db")
    c = conn.cursor()
    c.execute("""SELECT e.id, e.schedina_id, e.squadra_casa, e.squadra_ospite, e.mercato, s.chat_id 
                 FROM eventi e JOIN schedine s ON e.schedina_id = s.id 
                 WHERE e.stato = 'IN_CORSO' AND s.stato = 'IN_CORSO'""")
    eventi_attivi = c.fetchall()
    if not eventi_attivi:
        conn.close()
        return

    oggi = datetime.now().strftime("%Y-%m-%d")
    api_res = requests.get(f"https://v3.football.api-sports.io/fixtures?date={oggi}", headers={"x-apisports-key": FOOTBALL_API_KEY})
    partite = api_res.json().get("response", [])
    if not partite:
        conn.close()
        return

    for ev_id, s_id, casa, ospite, mercato, chat_id in eventi_attivi:
        match = next((m for m in partite if casa.lower()[:4] in m["teams"]["home"]["name"].lower() or ospite.lower()[:4] in m["teams"]["away"]["name"].lower()), None)
        if not match or match["fixture"]["status"]["short"] == "NS":
            continue

        f_id = match["fixture"]["id"]
        stats = requests.get(f"https://v3.football.api-sports.io/fixtures/statistics?fixture={f_id}", headers={"x-apisports-key": FOOTBALL_API_KEY}).json().get("response", [])
        events = requests.get(f"https://v3.football.api-sports.io/fixtures/events?fixture={f_id}", headers={"x-apisports-key": FOOTBALL_API_KEY}).json().get("response", [])

        prompt = f"""Valuta se la scommessa è VINTA, PERSA o IN_CORSO.
        Match: {casa} - {ospite} | Giocata: {mercato}
        Stato: {match['fixture']['status']['short']} ({match['fixture']['status']['elapsed']}') | Gol: {match['goals']['home']}-{match['goals']['away']}
        Stats: {json.dumps(stats)} | Eventi: {json.dumps(events)}
        Rispondi SOLO in JSON: {{"stato": "VINTA"|"PERSA"|"IN_CORSO", "riassunto": "es. 2-1 (70') | Corner: 9"}}"""

        try:
            resp = client.models.generate_content(model='gemini-2.5-flash', contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json"))
            verdetto = json.loads(resp.text)

            if verdetto["stato"] in ["VINTA", "PERSA"]:
                c.execute("UPDATE eventi SET stato=?, ultimo_risultato=? WHERE id=?", (verdetto["stato"], verdetto["riassunto"], ev_id))
                conn.commit()
                ic = "✅ *EVENTO PRESO!*" if verdetto["stato"] == "VINTA" else "❌ *EVENTO SALTATO!*"
                invia_messaggio(chat_id, f"{ic}\n🎟 Schedina #{s_id}\n⚽ *{casa} - {ospite}*\n🎯 `{mercato}`\n📊 Live: _{verdetto['riassunto']}_")
        except Exception:
            continue

    c.execute("SELECT id, chat_id FROM schedine WHERE stato='IN_CORSO'")
    for s_id, chat_id in c.fetchall():
        c.execute("SELECT stato FROM eventi WHERE schedina_id=?", (s_id,))
        stati = [r[0] for r in c.fetchall()]
        if "PERSA" in stati:
            c.execute("UPDATE schedine SET stato='PERSA' WHERE id=?", (s_id,))
            conn.commit()
            invia_messaggio(chat_id, f"💀 *La Schedina #{s_id} è purtroppo SALTATA.*")
        elif stati and all(s == "VINTA" for s in stati):
            c.execute("UPDATE schedine SET stato='VINTA' WHERE id=?", (s_id,))
            conn.commit()
            invia_messaggio(chat_id, f"🎉💰 *CASSA! Hai vinto la Schedina #{s_id}!* 💰🎉")
    conn.close()

def mostra_attive(chat_id):
    conn = sqlite3.connect("scommesse.db")
    c = conn.cursor()
    c.execute("SELECT id, squadra_casa, squadra_ospite, mercato, ultimo_risultato, stato FROM eventi WHERE schedina_id IN (SELECT id FROM schedine WHERE chat_id=? AND stato='IN_CORSO')", (chat_id,))
    righe = c.fetchall()
    conn.close()
    if not righe:
        invia_messaggio(chat_id, "📭 Nessuna schedina attiva.")
        return
    msg = "📋 *EVENTI IN CORSO:*\n\n"
    for r in righe:
        ic = "✅" if r[5] == "VINTA" else ("❌" if r[5] == "PERSA" else "⏳")
        msg += f"{ic} *#{r[0]}* | {r[1]}-{r[2]} (`{r[3]}`)\n   └ 📊 _{r[4]}_\n"
    invia_messaggio(chat_id, msg)

def mostra_bilancio(chat_id):
    conn = sqlite3.connect("scommesse.db")
    c = conn.cursor()
    c.execute("SELECT stato, COUNT(*), SUM(importo), SUM(vincita_potenziale) FROM schedine WHERE chat_id=? GROUP BY stato", (chat_id,))
    righe = c.fetchall()
    conn.close()
    speso, vinto, v, p = 0, 0, 0, 0
    for stato, cnt, imp, vinc in righe:
        speso += (imp or 0)
        if stato == "VINTA":
            vinto += (vinc or 0)
            v += cnt
        elif stato == "PERSA":
            p += cnt
    invia_messaggio(chat_id, f"📊 *BILANCIO CASSA*\n\n✅ Vinte: *{v}* | ❌ Perse: *{p}*\n💸 Giocato: *{speso:.2f}€*\n💰 Incassato: *{vinto:.2f}€*\n📈 Profitto: *{(vinto - speso):.2f}€*")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
