# app.py
import os
import re
import unicodedata
from datetime import datetime, time
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from pyairtable import Table
from dotenv import load_dotenv

# ==========================
# CONFIGURAÇÕES INICIAIS
# ==========================
load_dotenv()

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
# FUNÇÕES AUXILIARES
# ==========================
def normalize(txt):
    if txt is None:
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
        return "+" + digits
    return "+" + digits

def parse_datetime_text(txt):
    txt = (txt or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(txt, fmt)
        except Exception:
            continue
    return None

def save_appointment_to_airtable(record):
    if table is None:
        return None, "Airtable não configurado."
    try:
        created = table.create(record)
        return created.get("id"), None
    except Exception as e:
        return None, str(e)

# ==========================
# FUNÇÃO DE HORÁRIOS DINÂMICOS
# ==========================
def get_available_hours(date_str):
    """Retorna os horários disponíveis conforme mês e dia da semana."""
    try:
        date_obj = datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        return None, "⚠️ Data inválida. Use o formato DD/MM/AAAA."

    weekday = date_obj.weekday()  # 0=segunda, 6=domingo
    month = date_obj.month

    if weekday == 6:
        return [], "❌ O estúdio não funciona aos domingos."

    # Outubro → começa 13:30 (exceto sábado)
    if month == 10:
        if weekday == 5:
            start_hour, end_hour = 8, 18
        elif weekday == 3:
            start_hour, end_hour = 13, 17
        else:
            start_hour, end_hour = 13, 18

    # Novembro em diante
    elif month >= 11:
        if weekday == 5:
            start_hour, end_hour = 8, 18
        elif weekday == 3:
            start_hour, end_hour = 8, 17
        else:
            start_hour, end_hour = 8, 18

    # Gera lista de horários
    hours = []
    for h in range(start_hour, end_hour + 1):
        hours.append(f"{h:02d}:00")

    message = f"🗓 *Escolha um horário disponível para {date_str}:*\n"
    for i, t in enumerate(hours, 1):
        message += f"{i}️⃣ {t}\n"
    message += "\n⏰ *Horários após 18:00 precisam ser confirmados com uma atendente.*"
    return hours, message

# ==========================
# FLUXO PRINCIPAL DO BOT
# ==========================
@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    raw_from = request.values.get("From", "")
    phone = extract_phone_number(raw_from)
    body_raw = (request.values.get("Body") or "").strip()
    body_norm = normalize(body_raw)

    sess = sessions.get(phone, {"state": "menu", "data": {}})
    state = sess["state"]

    print(f"📩 Mensagem de {phone}: {body_raw} | Estado: {state}")

    resp = MessagingResponse()
    reply = ""

    # MENU PRINCIPAL
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

    # MENU INTERAÇÕES
    if state == "menu":
        if body_norm in ["1", "agendamento", "agendar"]:
            sess = {"state": "ask_service", "data": {"phone": phone}}
            sessions[phone] = sess
            reply = (
                "✨ *Agendamento* ✨\n\n"
                "Serviços disponíveis:\n"
                "- Corte\n- Escova\n- Coloração\n- Mechas\n- Progressiva\n\n"
                "Digite o nome do serviço desejado:"
            )

        elif body_norm in ["2", "atendente"]:
            reply = "💁 Aguarde um momento, uma atendente irá te responder em breve."

        elif body_norm in ["3", "agenda"]:
            if table is None:
                reply = "Airtable não configurado."
            else:
                try:
                    formula = f"{{Phone}} = '{phone}'"
                    records = table.all(formula=formula)
                    if not records:
                        reply = "📅 Você não possui agendamentos."
                    else:
                        lines = ["📅 *Seus agendamentos:*"]
                        for r in records:
                            f = r.get("fields", {})
                            dt = f.get("DateTime", "sem data")
                            svc = f.get("Service", "—")
                            status = f.get("Status", "—")
                            lines.append(f"- {dt} | {svc} | {status}")
                        reply = "\n".join(lines)
                except Exception as e:
                    reply = f"Erro ao buscar agenda: {e}"

        elif body_norm in ["4", "manicure", "pedicure"]:
            sess = {"state": "ask_manicure_option", "data": {"phone": phone}}
            sessions[phone] = sess
            reply = "💅 Deseja *somente mão*, *somente pé* ou *mão e pé*?"

        else:
            reply = "❌ Opção inválida. Digite *menu* para ver novamente."

    elif state == "ask_service":
        if body_norm in SERVICES:
            sess["data"]["service"] = body_raw
            sess["state"] = "ask_date"
            sessions[phone] = sess
            reply = "📅 Informe a data (DD/MM/AAAA) do agendamento."
        else:
            reply = "❌ Serviço não reconhecido. Tente novamente ou digite *menu*."

    elif state == "ask_manicure_option":
        sess["data"]["service"] = body_raw
        sess["state"] = "ask_date"
        sessions[phone] = sess
        reply = "📅 Informe a data (DD/MM/AAAA) desejada."

    elif state == "ask_date":
        date_str = body_raw
        sess["data"]["date"] = date_str
        hours, msg = get_available_hours(date_str)
        if not hours:
            reply = msg
        else:
            sess["data"]["hours_list"] = hours
            sess["state"] = "ask_hour"
            sessions[phone] = sess
            reply = msg

    elif state == "ask_hour":
        sess["data"]["hour"] = body_raw
        sess["state"] = "confirm"
        sessions[phone] = sess
        reply = (
            f"✅ Confirme o agendamento:\n\n"
            f"💇 Serviço: {sess['data']['service']}\n"
            f"📅 Data: {sess['data']['date']}\n"
            f"⏰ Horário: {sess['data']['hour']}\n\n"
            f"Digite *sim* para confirmar ou *não* para cancelar."
        )

    elif state == "confirm":
        if "sim" in body_norm:
            record = {
                "Phone": phone,
                "Service": sess["data"]["service"],
                "DateTime": f"{sess['data']['date']} {sess['data']['hour']}",
                "Status": "Confirmado"
            }
            rec_id, err = save_appointment_to_airtable(record)
            if err:
                reply = f"Erro ao salvar no Airtable: {err}"
            else:
                reply = "✨ Agendamento confirmado com sucesso! 💖\n\nDigite *menu* para voltar."
            sessions[phone] = {"state": "menu", "data": {}}
        else:
            reply = "❌ Agendamento cancelado. Digite *menu* para retornar."
            sessions[phone] = {"state": "menu", "data": {}}

    else:
        reply = "⚠️ Não entendi... Vou te encaminhar para uma atendente 💁"
        sessions[phone] = {"state": "menu", "data": {}}

    resp.message(reply)
    return str(resp)


# ==========================
# EXECUÇÃO LOCAL
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)









