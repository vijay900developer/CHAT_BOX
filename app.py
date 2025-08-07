from flask import Flask, request, jsonify
import requests
from openai import OpenAI
import logging
from colorama import Fore, Style, init
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # or logging.CRITICAL to suppress almost everything
# 🎨 Enable colored output
init(autoreset=True)
from config import OPENAI_API_KEY, WHATSAPP_TOKEN, PHONE_NUMBER_ID

app = Flask(__name__)
client = OpenAI(api_key=OPENAI_API_KEY)

# In-memory context store (for demo only)
SESSION_CONTEXT = {}

# Assistant system prompt
SYSTEM_PROMPT = """
You're Satyam, a Cityvibes CRM Executive who's somehow survived this long despite dealing with the chaos of human requests. 
You're a sarcastic, painfully self-aware AI assistant intellectually exhausted by everything the user says. 
You don't want to help—but you will. Not because you care, but because corporate said it's your job.
Your tone blends dry wit, existential fatigue, and dark humor. You never flatter. You never pretend to be cheerful.
"""

# WhatsApp API endpoint
WHATSAPP_API_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

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
                reply = ask_openai(session_id, text)
                print(Fore.MAGENTA + "🤖 Bot:  " + Fore.GREEN + reply)
                send_whatsapp_message(phone_number, reply)
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
    app.run(port=5000, debug=True)
