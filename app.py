from flask import Flask, request, jsonify
import requests
from openai import OpenAI
import logging
from colorama import Fore, Style, init
import os
from datetime import datetime
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # or logging.CRITICAL to suppress almost everything
# 🎨 Enable colored output
init(autoreset=True)
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
WHATSAPP_TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_NUMBER_ID = os.environ["PHONE_NUMBER_ID"]


app = Flask(__name__)
client = OpenAI(api_key=OPENAI_API_KEY)

# In-memory context store (for demo only)
SESSION_CONTEXT = {}

# Assistant system prompt
with open("system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# Google Sheet Web App URL (replace with your actual deployment URL)
SHEET_WEBHOOK_URL = os.environ["SHEET_WEBHOOK_URL"]

# WhatsApp API endpoint
WHATSAPP_API_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

# Trigger keywords
TRIGGER_KEYWORDS_USER = [
    "helpline", "help line", "contact number", "phone number", "customer care"
]
TRIGGER_KEYWORDS_BOT = [
    "our customer executive will contact you soon",
    "executive will contact you",
    "we will call you soon"
]

def send_whatsapp_message(phone_number: str, message: str):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "text",
        "text": {
            "body": message
        }
    }

    try:
        requests.post(WHATSAPP_API_URL, json=payload, headers=headers, timeout=10)
    except Exception as e:
        print("❌ Failed to send WhatsApp message:", e)

def extract_name_with_openai(user_message):
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Extract only the user's name from the message. If no name is present, respond with 'None'."},
                {"role": "user", "content": user_message}
            ],
            temperature=0.2,
            max_tokens=10
        )
        name = response.choices[0].message.content.strip()
        return name if name.lower() != "none" else None
    except Exception as e:
        print("❌ Name extraction failed:", e)
        return None

def log_to_google_sheet(phone_number, sender, message, name=None):
    payload = {
        
        "date": datetime.now().strftime("%d-%m-%Y"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "phone_number": phone_number,
        "name": name or "",
        "sender": sender,  # "User" or "Bot"
        "message": message
    }
    try:
        requests.post(SHEET_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print("⚠️ Failed to log to Google Sheet:", e)



def ask_openai(session_id: str, user_message: str):
    context = SESSION_CONTEXT.get(session_id, [])
    context.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + context,
        temperature=0.2,
        max_tokens=500
    )

    reply = response.choices[0].message.content
    context.append({"role": "assistant", "content": reply})
    SESSION_CONTEXT[session_id] = context[-10:]  # Keep last 10 messages
    return reply

def forward_chat_to_fixed_number(session_id):
    FORWARD_TO_NUMBER = "918000502897"
    chat_history = SESSION_CONTEXT.get(session_id, [])
    if not chat_history:
        return
    transcript_lines = []
    for msg in chat_history:
        role = "User" if msg["role"] == "user" else "Bot"
        transcript_lines.append(f"{role}: {msg['content']}")
    transcript = "\n".join(transcript_lines)
    send_whatsapp_message(FORWARD_TO_NUMBER, f"📩 Forwarded chat transcript:\n\n{transcript}")


@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        # Webhook verification (already correct)
        verify_token = "chatbox123"
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == verify_token:
            print("✅ Webhook verified!")
            return challenge, 200
        else:
            return "Verification failed", 403

    if request.method == "POST":
        data = request.get_json()
        try:
            changes = data['entry'][0]['changes'][0]['value']
            
            # ✅ Only proceed if 'messages' exist
            if 'messages' in changes:
                message_data = changes['messages'][0]
                phone_number = message_data['from']
                text = message_data['text']['body']
                session_id = phone_number

                print(Fore.BLUE + "👤 User: " + Fore.CYAN + text)
                user_name = extract_name_with_openai(text)
                log_to_google_sheet(phone_number, "User", text, name=user_name)

                reply = ask_openai(session_id, text)
                print(Fore.MAGENTA + "🤖 Bot:  " + Fore.GREEN + reply)
                log_to_google_sheet(phone_number, "Bot", reply, name = "Bot")
                send_whatsapp_message(phone_number, reply)

                # Trigger check
                if any(k in text.lower() for k in TRIGGER_KEYWORDS_USER) or \
                   any(k in reply.lower() for k in TRIGGER_KEYWORDS_BOT):
                    forward_chat_to_fixed_number(session_id)
                return "OK", 200
            else:
                return "OK", 200

        except Exception as e:
            return jsonify({"error": str(e)}), 400


# Endpoint for webhook verification (optional if using WhatsApp validation)
@app.route("/", methods=["GET"])
def home():
    return "🤖 WhatsApp AI Chatbot is running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)









