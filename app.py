import os
import re
import unicodedata
from datetime import datetime, time
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from pyairtable import Table
from dotenv import load_dotenv

load_dotenv()

# Config
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME", "Appointments")

# Airtable
table = None
if AIRTABLE_API_KEY and AIRTABLE_BASE_ID:
    table = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

app = Flask(__name__)

# Sessões em memória
sessions = {}

# Serviços válidos (sempre em minúsculo e sem acento)
SERVICES = ["corte", "escova", "coloracao", "mechas", "progressiva", "manicure", "pedicure"]

# Saudações aceitas
GREETINGS = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "hello", "oi kelly", "kelly"]

def normalize(txt):
    """Remove acentos e deixa em minúsculo para comparação"""
    if txt is None:
        return ""
    txt = txt.strip().lower()
    return ''.join(
        c for c in unicodedata.normalize('NFD', txt)
        if unicodedata.category(c) != 'Mn'
    )

def is_greeting(text_norm):
    """Detecta se o texto normalizado contém uma saudação"""
    for g in GREETINGS:
        if normalize(g) in text_norm:
            return True
    return False

def extract_phone_number(raw_from):
    digits = re.sub(r'\D', '', raw_from or "")
    if not digits.startswith("55"):
        return "+" + digits
    return "+" + digits

def parse_datetime_text(txt):
    txt = (txt or "").strip()
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%y", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(txt, fmt)
            if fmt in ("%d/%m/%y", "%d/%m/%Y"):
                return dt.replace(hour=0, minute=0)
            return dt
        except Exception:
            continue
    return None

def is_allowed_datetime(dt: datetime):
    if dt.weekday() == 6:
        return False, "❌ Não atendemos aos domingos. Escolha outra data."
    t = dt.time()
    if time(12,0) <= t < time(13,30):
        return False, "❌ O horário de almoço (12:00 - 13:30) não está disponível."
    if dt < datetime.now():
        return False, "❌ Essa data/hora já passou. Escolha uma data futura."
    return True, None

def save_appointment_to_airtable(record):
    if table is None:
        return None, "Airtable não configurado."
    try:
        created = table.create(record)
        return created.get("id"), None
    except Exception as e:
        return None, str(e)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    raw_from = request.values.get("From", "")
    phone = extract_phone_number(raw_from)
    body_raw = (request.values.get("Body") or "").strip()
    body_norm = normalize(body_raw)

    resp = MessagingResponse()
    reply = ""

    # Se for saudação ou "menu" — sempre mostra o menu e reseta a sessão
    if body_norm == "menu" or is_greeting(body_norm):
        sessions[phone] = {"state": "menu", "data": {}}
        reply = (
            "🌸 *Seja bem-vinda ao Studio Kelly d’Paula* 🌸\n\n"
            "Escolha uma opção:\n"
            "1️⃣ *Agendamento*\n"
            		"2️⃣ *Agendamento com atendente*\n"
            		"3️⃣ *Ver minha agenda*\n"
            		"4️⃣ *Manicure e Pedicure*\n\n"
            		"Responda com o número (ex: 1) ou com a palavra."
        )
        resp.message(reply)
        return str(resp)

    # Pega sessão atual (se não existir, vai para menu)
    sess = sessions.get(phone, {"state": "menu", "data": {}})
    state = sess["state"]

    # FLUXO PRINCIPAL
    if state == "menu":
        if body_norm == "1" or body_norm.startswith("agend"):
            sess = {"state": "ask_service", "data": {"phone": phone}}
            reply = (
                "✨ *Agendamento* ✨\n\n"
                "Serviços disponíveis:\n- Corte\n- Escova\n- Coloração\n- Progressiva\n\n"
                "Digite o nome do serviço que deseja (ex: Corte)."
            )
        elif body_norm == "2" or "atendente" in body_norm:
            reply = "💁 Aguarde só um instante, uma atendente irá te responder em breve."
        elif body_norm == "3" or "agenda" in body_norm:
            if table is None:
                reply = "Airtable não está configurado."
            else:
                try:
                    records = table.all(formula=f"{{Phone}} = '{phone}'")
                    if not records:
                        reply = "📅 Você não tem agendamentos."
                    else:
                        lines = ["📅 Seus agendamentos:"]
                        for r in records:
                            f = r.get("fields", {})
                            dt = f.get("DateTime", "sem data")
                            svc = f.get("Service", "—")
                            status = f.get("Status", "—")
                            lines.append(f"- {dt} | {svc} | {status}")
                        reply = "\n".join(lines)
                except Exception as e:
                    reply = f"Erro ao buscar agenda: {e}"
        elif body_norm == "4" or "manicure" in body_norm:
            sess = {"state": "ask_manicure_date", "data": {"phone": phone, "service": "Manicure/Pedicure"}}
            reply = "💅 *Manicure e Pedicure* 💅\n\nInforme a data e horário (dd/mm/aa hh:mm)."
        elif body_norm in SERVICES:
            sess = {"state": "ask_date", "data": {"phone": phone, "service": body_raw.strip().title()}}
            reply = "Ótimo! Informe a data e horário no formato dd/mm/aa hh:mm."
        else:
            reply = "❌ Opção inválida. Digite *menu* para ver novamente."
        sessions[phone] = sess

    elif state == "ask_service":
        chosen = normalize(body_raw)
        if chosen not in SERVICES:
            reply = ("❌ Serviço não reconhecido.\n"
                     "Disponíveis:\n- " + "\n- ".join([s.title() for s in SERVICES]))
        else:
            sess["data"]["service"] = body_raw.strip().title()
            if "color" in chosen or "colora" in chosen:
                sess["state"] = "ask_color_current"
                reply = "Qual a cor atual do cabelo?"
            else:
                sess["state"] = "ask_date"
                reply = "Ótimo! Agora informe a data e horário no formato dd/mm/aa hh:mm."
        sessions[phone] = sess

    elif state == "ask_color_current":
        sess["data"]["color_current"] = body_raw.strip().title()
        sess["state"] = "ask_color_desired"
        reply = "Qual a cor desejada?"
        sessions[phone] = sess

    elif state == "ask_color_desired":
        sess["data"]["color_desired"] = body_raw.strip().title()
        sess["state"] = "ask_date"
        reply = "Perfeito. Agora informe a data e horário (dd/mm/aa hh:mm)."
        sessions[phone] = sess

    elif state in ["ask_date", "ask_manicure_date"]:
        dt = parse_datetime_text(body_raw)
        if dt is None:
            reply = "❌ Formato inválido. Use dd/mm/aa hh:mm."
        else:
            ok, msg_err = is_allowed_datetime(dt)
            if not ok:
                reply = msg_err
            else:
                sess["data"]["datetime"] = dt
                sess["state"] = "confirm"
                summary = [
                    "📌 Confirme seu agendamento:",
                    f"📱 Telefone: {phone}",
                    f"💇 Serviço: {sess['data'].get('service', 'Serviço')}",
                    f"📅 Data/Hora: {dt.strftime('%d/%m/%Y %H:%M')}",
                ]
                if "color_current" in sess["data"]:
                    summary.append(f"🎨 Cor atual: {sess['data']['color_current']}")
                if "color_desired" in sess["data"]:
                    summary.append(f"🎯 Cor desejada: {sess['data']['color_desired']}")
                summary.append("\nResponda *SIM* para confirmar ou *NÃO* para cancelar.")
                reply = "\n".join(summary)
        sessions[phone] = sess

    elif state == "confirm":
        if body_norm in ["sim", "s", "confirmar"]:
            data = sess["data"]
            record = {
                "Phone": phone,
                "Service": data.get("service", ""),
                "DateTime": data.get("datetime").isoformat() if data.get("datetime") else "",
                "ColorCurrent": data.get("color_current", ""),
                "ColorDesired": data.get("color_desired", ""),
                "Status": "Agendado"
            }
            rec_id, err = save_appointment_to_airtable(record)
            reply = f"✅ Agendamento confirmado!\nCódigo: {rec_id}\nObrigada pela preferência 💖" if not err else f"❌ Erro ao salvar: {err}"
            sessions.pop(phone, None)
        else:
            reply = "Agendamento cancelado. Digite *menu* para voltar."
            sessions.pop(phone, None)

    else:
        reply = "Digite *menu* para começar."

    resp.message(reply)
    return str(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)


