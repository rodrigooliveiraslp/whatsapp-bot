# app.py
import os
import re
from datetime import datetime, time
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from pyairtable import Table
from dotenv import load_dotenv

load_dotenv()  # carrega variáveis do .env local (apenas para testes locais)

# Config
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = os.getenv("AIRTABLE_TABLE_NAME", "Appointments")

# Inicializa Airtable
table = None
if AIRTABLE_API_KEY and AIRTABLE_BASE_ID:
    table = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)

app = Flask(__name__)

# Estados temporários por cliente (em memória)
# Para persistência, usar Airtable ou DB; aqui é simples para fluxo de conversa
sessions = {}

# Serviços válidos (você pode editar)
SERVICES = ["corte", "escova", "coloracao", "coloração", "mechas", "progressiva", "manicure", "pedicure"]

def extract_phone_number(raw_from):
    # Twilio envia "whatsapp:+5511999999999"
    digits = re.sub(r'\D', '', raw_from)
    # devolve no formato +551199999999
    if not digits.startswith("55"):
        # se não tiver código do Brasil, não alteramos — mas preferimos +55
        return "+" + digits
    return "+" + digits

def parse_datetime_text(txt):
    txt = txt.strip()
    # tenta vários formatos: dd/mm/yy HH:MM ou dd/mm/yyyy HH:MM ou dd/mm/yy
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%y", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(txt, fmt)
            # se não veio hora, manter hora 00:00
            if fmt in ("%d/%m/%y", "%d/%m/%Y"):
                return dt.replace(hour=0, minute=0)
            return dt
        except Exception:
            continue
    return None

def is_allowed_datetime(dt: datetime):
    # Não atende domingos:
    if dt.weekday() == 6:  # domingo = 6
        return False, "Desculpe, não atendemos aos domingos. Escolha outra data, por favor."
    # Não atende horário de almoço 12:00 - 13:30
    t = dt.time()
    if time(12,0) <= t < time(13,30):
        return False, "O horário de almoço (12:00 - 13:30) não está disponível. Escolha outro horário, por favor."
    # Pode adicionar checagem de passado:
    if dt < datetime.now():
        return False, "Essa data/hora já passou. Escolha uma data futura, por favor."
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
    body = (request.values.get("Body") or "").strip()

    # normalize lower for logic but keep original text in messages
    msg = body.lower()

    # prepare response
    resp = MessagingResponse()
    reply = ""

    sess = sessions.get(phone, {"state": "menu", "data": {}})
    state = sess["state"]

    # entrada inicial ou "menu"
    if state == "menu" and msg in ["oi", "olá", "menu", "hello", "1", "2", "3", "4"] and msg not in ["1","2","3","4"]:
        # caso cliente só diga oi/menu
        pass

    # Se quiser resetar
    if msg in ["menu", "oi", "olá", "início"]:
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

    # Main flow by state
    if state == "menu":
        # trata opções digitadas
        if msg == "1" or msg.startswith("agend"):
            sessions[phone] = {"state": "ask_service", "data": {"phone": phone}}
            reply = (
                "✨ *Agendamento* ✨\n\n"
                "Serviços disponíveis:\n- Corte\n- Escova\n- Coloração\n- Progressiva\n\n"
                "Digite o nome do serviço que deseja (ex: Corte)."
            )
        elif msg == "2" or "atendente" in msg:
            reply = "💁 Aguarde só um instante, uma atendente irá te responder em breve. Obrigada!"
        elif msg == "3" or "agenda" in msg:
            # buscar no airtable por Phone
            if table is None:
                reply = "Airtable não está configurado no servidor. Não consigo mostrar sua agenda agora."
            else:
                # fórmula Airtable para encontrar pelo campo Phone exato
                # assumimos que no Airtable gravamos Phone com +55... ou digitos.
                formula = f"{{Phone}} = '{phone}'"
                try:
                    records = table.all(formula=formula)
                    if not records:
                        reply = "📅 Você não tem agendamentos no nosso sistema."
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
        elif msg == "4" or "manicure" in msg:
            sessions[phone] = {"state": "ask_manicure_date", "data": {"phone": phone, "service": "Manicure/Pedicure"}}
            reply = "💅 *Manicure e Pedicure* 💅\n\nPor favor, escolha a data e horário (dd/mm/aa hh:mm). Lembre: não atendemos domingos, nem 12:00-13:30."
        else:
            reply = "Opção inválida. Digite *menu* para ver novamente."

    elif state == "ask_service":
        chosen = msg
        if chosen not in [s.lower() for s in SERVICES]:
            reply = ("Serviço não reconhecido. Serviços disponíveis:\n" + ", ".join(SERVICES) +
                     "\nPor favor digite exatamente um destes nomes.")
        else:
            sess["data"]["service"] = chosen.title()
            # se coloração, vamos perguntar cores depois
            if "color" in chosen or "colora" in chosen:
                sess["state"] = "ask_color_current"
                reply = "Qual a cor atual do cabelo?"
            else:
                sess["state"] = "ask_date"
                reply = "Ótimo. Agora informe a data e horário no formato dd/mm/aa hh:mm (ex: 02/10/25 14:00)."

    elif state == "ask_color_current":
        sess["data"]["color_current"] = body  # keep original case
        sess["state"] = "ask_color_desired"
        reply = "Qual a cor desejada?"

    elif state == "ask_color_desired":
        sess["data"]["color_desired"] = body
        sess["state"] = "ask_date"
        reply = "Perfeito. Agora informe a data e horário no formato dd/mm/aa hh:mm (ex: 02/10/25 14:00)."

    elif state in ["ask_date", "ask_manicure_date"]:
        dt = parse_datetime_text(body)
        if dt is None:
            reply = "Formato inválido. Envie a data no formato dd/mm/aa hh:mm (ex: 02/10/25 14:00)."
        else:
            ok, msg_err = is_allowed_datetime(dt)
            if not ok:
                reply = msg_err
            else:
                sess["data"]["datetime"] = dt
                sess["state"] = "confirm"
                svc = sess["data"].get("service", "Serviço")
                ccur = sess["data"].get("color_current", "")
                cdes = sess["data"].get("color_desired", "")
                summary_lines = [
                    "Confirme seu agendamento:",
                    f"Telefone: {phone}",
                    f"Serviço: {svc}",
                    f"Data/Hora: {dt.strftime('%d/%m/%Y %H:%M')}",
                ]
                if ccur:
                    summary_lines.append(f"Cor atual: {ccur}")
                if cdes:
                    summary_lines.append(f"Cor desejada: {cdes}")
                summary_lines.append("\nResponda *SIM* para confirmar ou *NÃO* para cancelar.")
                reply = "\n".join(summary_lines)

    elif state == "confirm":
        if msg in ["sim", "s", "confirmar"]:
            # salva no Airtable
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
            if err:
                reply = f"Desculpe, não consegui salvar o agendamento: {err}"
            else:
                reply = f"✅ Agendamento confirmado! Código: {rec_id}\nAgradecemos sua preferência. Se precisar alterar, responda *menu*."
            # limpa sessão
            sessions.pop(phone, None)
        else:
            reply = "Ok, sem problemas. Agendamento cancelado. Digite *menu* para voltar ao menu."
            sessions.pop(phone, None)

    else:
        reply = "Digite *menu* para começar."

    # atualiza sessão
    sessions[phone] = sess
    resp.message(reply)
    return str(resp)

if __name__ == "__main__":
    # para testes locais
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)


   

