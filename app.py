# app.py
import os
import re
import unicodedata
from datetime import datetime, time
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from pyairtable import Table
from dotenv import load_dotenv

load_dotenv()

# ==========================
# Config Airtable
# ==========================
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME", "Appointments")

table = None
if AIRTABLE_API_KEY and AIRTABLE_BASE_ID:
    table = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

app = Flask(__name__)
sessions = {}

SERVICES = ["corte", "escova", "coloracao", "mechas", "progressiva", "manicure", "pedicure"]
GREETINGS = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "hello", "oi kelly", "kelly"]

# ==========================
# Funções auxiliares
# ==========================
def normalize(txt):
    if not txt:
        return ""
    txt = txt.strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')

def is_greeting(text_norm):
    for g in GREETINGS:
        if normalize(g) in text_norm:
            return True
    return False

def extract_phone_number(raw_from):
    digits = re.sub(r'\D', '', raw_from or "")
    if not digits.startswith("55"):
        digits = "55" + digits
    return "+" + digits

def parse_datetime_text(txt):
    txt = (txt or "").strip()
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(txt, fmt)
        except Exception:
            continue
    return None

def is_allowed_datetime(dt: datetime):
    if dt.weekday() == 6:
        return False, "❌ Não atendemos aos domingos. Escolha outra data."
    if time(12, 0) <= dt.time() < time(13, 30):
        return False, "❌ O horário de almoço (12:00 - 13:30) não está disponível."
    if dt < datetime.now():
        return False, "❌ Essa data/hora já passou. Escolha uma data futura."
    return True, None

def save_appointment_to_airtable(record):
    if not table:
        return None, "Airtable não configurado."
    try:
        created = table.create(record)
        return created.get("id"), None
    except Exception as e:
        return None, str(e)

# ==========================
# Rota principal WhatsApp
# ==========================
@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    raw_from = request.values.get("From", "")
    phone = extract_phone_number(raw_from)
    body_raw = (request.values.get("Body") or "").strip()
    msg = normalize(body_raw)

    resp = MessagingResponse()
    reply = ""

    # Carrega sessão atual (ou cria nova)
    sess = sessions.get(phone, {"state": "menu", "data": {}})
    state = sess["state"]

    print(f"📩 Nova mensagem de {phone}: {body_raw} | Estado: {state}")

    # ======== ABRE MENU COM SAUDAÇÃO ========
    if is_greeting(msg) or msg == "menu":
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

    # ======== MENU PRINCIPAL ========
    if state == "menu":
        if msg in ["1", "agendamento", "agendar"]:
            sess = {"state": "ask_service", "data": {"phone": phone}}
            sessions[phone] = sess
            reply = (
                "✨ *Agendamento* ✨\n\n"
                "Serviços disponíveis:\n"
                "- Corte\n- Escova\n- Coloração\n- Mechas\n- Progressiva\n\n"
                "Digite o nome do serviço que deseja (ex: Corte)."
            )

        elif msg in ["2", "atendente"]:
            reply = "💁 Aguarde um momento, uma atendente irá te responder em breve."

        elif msg in ["3", "agenda"]:
            if not table:
                reply = "⚠️ Airtable não configurado."
            else:
                formula = f"{{Phone}} = '{phone}'"
                try:
                    records = table.all(formula=formula)
                    if not records:
                        reply = "📅 Você não possui agendamentos."
                    else:
                        lines = ["📅 *Seus agendamentos:*"]
                        for r in records:
                            f = r.get("fields", {})
                            lines.append(f"- {f.get('DateTime', 'Data não informada')} | {f.get('Service', 'Serviço')} | {f.get('Status', '-')}")
                        reply = "\n".join(lines)
                except Exception as e:
                    reply = f"Erro ao buscar agenda: {e}"

        elif msg in ["4", "manicure", "pedicure"]:
            sess = {"state": "ask_manicure_date", "data": {"phone": phone, "service": "Manicure/Pedicure"}}
            sessions[phone] = sess
            reply = "💅 Informe a data e horário para o serviço (dd/mm/aa hh:mm)."

        else:
            reply = "❌ Opção inválida. Digite *menu* para voltar ao início."

    # ======== SERVIÇO ========
    elif state == "ask_service":
        if msg in SERVICES:
            sess["data"]["service"] = body_raw.title()
            sess["state"] = "ask_date"
            sessions[phone] = sess
            reply = f"📅 Você escolheu *{body_raw.title()}*.\nAgora informe a data e horário (dd/mm/aa hh:mm)."
        else:
            reply = (
                "❌ Serviço não reconhecido.\n"
                "Digite um dos seguintes:\n- " + "\n- ".join(s.title() for s in SERVICES)
            )

    # ======== DATA ========
    elif state in ["ask_date", "ask_manicure_date"]:
        dt = parse_datetime_text(body_raw)
        if not dt:
            reply = "❌ Formato inválido. Use dd/mm/aa hh:mm (ex: 05/10/25 14:00)."
        else:
            ok, msg_err = is_allowed_datetime(dt)
            if not ok:
                reply = msg_err
            else:
                sess["data"]["datetime"] = dt.strftime("%d/%m/%Y %H:%M")
                record = {
                    "Phone": phone,
                    "Service": sess["data"]["service"],
                    "DateTime": sess["data"]["datetime"],
                    "Status": "Agendado"
                }
                rec_id, err = save_appointment_to_airtable(record)
                if err:
                    reply = f"⚠️ Erro ao salvar: {err}"
                else:
                    reply = (
                        f"✅ *Agendamento confirmado!*\n\n"
                        f"💇 Serviço: {sess['data']['service']}\n"
                        f"📅 Data/Hora: {sess['data']['datetime']}\n"
                        f"📱 Telefone: {phone}\n\n"
                        f"Obrigada pela preferência 💖\nDigite *menu* para voltar."
                    )
                sessions[phone] = {"state": "menu", "data": {}}

    else:
        reply = "Digite *menu* para começar novamente."

    print(f"💬 Resposta enviada: {reply}\n")
    resp.message(reply)
    return str(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)








