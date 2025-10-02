import os
import re
import unicodedata
import logging
from datetime import datetime, time
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from pyairtable import Table
from dotenv import load_dotenv

# Ativar logs detalhados
logging.basicConfig(level=logging.DEBUG)

load_dotenv()

# Config Airtable
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

# Serviços válidos
SERVICES = ["corte", "escova", "coloracao", "mechas", "progressiva", "manicure", "pedicure"]

# Saudações aceitas
GREETINGS = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "hello", "oi kelly", "kelly"]

# ==========================
# Funções auxiliares
# ==========================
def normalize(txt):
    if txt is None:
        return ""
    txt = txt.strip().lower()
    return ''.join(
        c for c in unicodedata.normalize('NFD', txt)
        if unicodedata.category(c) != 'Mn'
    )

def is_greeting(text_norm):
    return any(normalize(g) in text_norm for g in GREETINGS)

def extract_phone_number(raw_from):
    digits = re.sub(r'\D', '', raw_from or "")
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

# ==========================
# Rota de teste (navegador)
# ==========================
@app.route("/", methods=["GET"])
def home():
    logging.debug("Acessaram a rota raiz '/' no navegador.")
    return "🚀 Bot WhatsApp do Studio Kelly d’Paula está rodando!"

# ==========================
# Rota WhatsApp principal
# ==========================
@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    raw_from = request.values.get("From", "")
    phone = extract_phone_number(raw_from)
    body_raw = (request.values.get("Body") or "").strip()
    body_norm = normalize(body_raw)

    resp = MessagingResponse()
    reply = ""

    # Sessão do usuário
    sess = sessions.get(phone, {"state": "menu", "data": {}})
    state = sess["state"]

    # Debug logs
    logging.debug("📩 NOVA MENSAGEM")
    logging.debug(f"De: {phone}")
    logging.debug(f"Texto bruto: {body_raw}")
    logging.debug(f"Texto normalizado: {body_norm}")
    logging.debug(f"Estado atual: {state}")

    # ======== MENU PRINCIPAL ========
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
        state = "menu"

    elif state == "menu":
        if body_norm == "1" or body_norm.startswith("agend"):
            sess = {"state": "ask_service", "data": {"phone": phone}}
            sessions[phone] = sess
            reply = (
                "✨ *Agendamento* ✨\n\n"
                "Serviços disponíveis:\n- Corte\n- Escova\n- Coloração\n- Mechas\n- Progressiva\n\n"
                "Digite o nome do serviço que deseja (ex: Corte)."
            )
            state = "ask_service"

        elif body_norm == "2" or "atendente" in body_norm:
            reply = "💁 Aguarde só um instante, uma atendente irá te responder em breve."

        elif body_norm == "3" or "agenda" in body_norm:
            if table is None:
                reply = "Airtable não está configurado."
            else:
                formula = f"{{Phone}} = '{phone}'"
                try:
                    records = table.all(formula=formula)
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
            sessions[phone] = sess
            reply = "💅 *Manicure e Pedicure* 💅\n\nInforme a data e horário (dd/mm/aa hh:mm)."
            state = "ask_manicure_date"

        else:
            reply = "❌ Opção inválida. Digite *menu* para ver novamente."

    # ======== AGENDAMENTO (SERVIÇOS) ========
    elif state == "ask_service":
        if body_norm in SERVICES:
            sess["data"]["service"] = body_raw.strip().title()
            sess["state"] = "ask_date"
            sessions[phone] = sess
            reply = f"📅 Você escolheu *{sess['data']['service']}*.\nInforme a data e horário (dd/mm/aa hh:mm)."
            state = "ask_date"
        else:
            reply = (
                "❌ Serviço não reconhecido.\n\n"
                "Disponíveis:\n- Corte\n- Escova\n- Coloração\n- Mechas\n- Progressiva\n\n"
                "Digite exatamente como aparece acima ou *menu* para voltar."
            )

    elif state == "ask_date":
        dt = parse_datetime_text(body_raw)
        if not dt:
            reply = "❌ Formato inválido. Use dd/mm/aa hh:mm (ex: 05/10/25 14:00)."
        else:
            ok, msg = is_allowed_datetime(dt)
            if not ok:
                reply = msg
            else:
                sess["data"]["datetime"] = dt.strftime("%d/%m/%Y %H:%M")
                sessions[phone] = sess
                record = {
                    "Phone": phone,
                    "Service": sess["data"]["service"],
                    "DateTime": sess["data"]["datetime"],
                    "Status": "Pendente"
                }
                rec_id, err = save_appointment_to_airtable(record)
                if err:
                    reply = f"Erro ao salvar no Airtable: {err}"
                else:
                    reply = (
                        f"✅ Agendamento confirmado!\n\n"
                        f"📱 Telefone: {phone}\n"
                        f"💇 Serviço: {sess['data']['service']}\n"
                        f"📅 Data: {sess['data']['datetime']}\n\n"
                        f"Para voltar ao menu digite *menu*."
                    )
                sessions[phone] = {"state": "menu", "data": {}}

    # ======== FALLBACK ========
    else:
        if body_raw:
            reply = f"Você disse: {body_raw}"
        else:
            reply = "Oi! Manda uma mensagem para começar 🚀"

    # Debug final
    logging.debug(f"Novo estado: {state}")
    logging.debug(f"Resposta enviada: {reply}\n")

    resp.message(reply)
    return str(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)







