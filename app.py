from flask import Flask, request, jsonify
import requests
from openai import OpenAI
import logging
import json
from colorama import Fore, Style, init
import os
import re
import threading
import time
from datetime import datetime, timedelta
from dateutil import parser
LAST_MESSAGE_TIME = {}
INACTIVITY_TIMEOUT = 120
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # or logging.CRITICAL to suppress almost everything
# 🎨 Enable colored output
init(autoreset=True)
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
WHATSAPP_TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_NUMBER_ID = os.environ["PHONE_NUMBER_ID"]
FIX_PHONE_NUMBER = os.environ["FIX_PHONE_NUMBER"]

app = Flask(__name__)
client = OpenAI(api_key=OPENAI_API_KEY)

# In-memory context store (for demo only)
SESSION_CONTEXT = {}
LAST_MESSAGE_TIME = {}
INACTIVITY_TIMEOUT = 120

# Assistant system prompt
with open("system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()


# Google Sheet Web App URL 
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
    "we will call you soon",
    "Cityvibes team",
    "reach out to you shortly",
    "8290432222"
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

def extract_address_with_openai(user_message):
    """
    Extracts only the address from the user's message using OpenAI.
    If no address is found, returns None.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract only the address mentioned in the user's message. "
                        "If there is no address or it's unclear, respond strictly with 'None'. "
                        "Address may include house number, street, area, city, or postal code."
                    )
                },
                {"role": "user", "content": user_message}
            ],
            temperature=0.2,
            max_tokens=30
        )

        address = response.choices[0].message.content.strip()
        return address if address.lower() != "none" else None

    except Exception as e:
        print("❌ Address extraction failed:", e)
        return None

def log_to_google_sheet(phone_number, sender, message, name=None, address=None):
    payload = {
        "sheet": "Chat_Log",
        "date": datetime.now().strftime("%d-%m-%Y"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "phone_number": phone_number,
        "name": name or "",
        "address": address or "",
        "sender": sender,  # "User" or "Bot"
        "message": message
    }
    try:
        requests.post(SHEET_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print("⚠️ Failed to log to Google Sheet:", e)


def ask_openai(session_id: str, user_message: str):
    """
    Generate a bot reply using OpenAI, keeping context in memory only.
    """
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

def summarize_chat_with_openai(chat_history):
    """Generate a short summary of the customer's query."""
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Summarize the customer's query in 2-3 sentences."},
                *chat_history
            ],
            temperature=0.3,
            max_tokens=100
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("❌ Summary generation failed:", e)
        return "Summary not available."


def extract_number_with_openai(chat_history):
    """Extract phone number from chat history if mentioned by user."""
    try:
        combined_text = "\n".join([msg["content"] for msg in chat_history if msg["role"] == "user"])
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Extract only the phone number mentioned by the customer. If none, respond with 'None'."},
                {"role": "user", "content": combined_text}
            ],
            temperature=0.2,
            max_tokens=10
        )
        num = response.choices[0].message.content.strip()
        return None if num.lower() == "none" else num
    except Exception as e:
        print("❌ Number extraction failed:", e)
        return None


def forward_summary_to_fixed_number(session_id, user_whatsapp_number):
    """Forward chat summary + contact to CRM fixed number."""
    FORWARD_TO_NUMBER = FIX_PHONE_NUMBER
    chat_history = SESSION_CONTEXT.get(session_id, [])
    if not chat_history:
        return

    summary = summarize_chat_with_openai(chat_history)
    customer_name = extract_name_with_openai("\n".join([m["content"] for m in chat_history if m["role"] == "user"]))
    customer_address = extract_address_with_openai(
        "\n".join([m["content"] for m in chat_history if m["role"] == "user"])
    )
    customer_number = extract_number_with_openai(chat_history)

    if not customer_number:
        customer_number = user_whatsapp_number  # fallback to WhatsApp sender number

    message = (
        f"📩 Customer Query Summary:\n{summary}\n\n"
        f"👤 Name: {customer_name or 'Not provided'}\n"
        f"🏠 Address: {customer_address or 'Not provided'}\n"
        f"📞 Contact: {customer_number}"
    )

    send_whatsapp_message(FORWARD_TO_NUMBER, message)
    
def log_summary_to_google_sheet(session_id):
    """
    Log chat summary to Google Sheet.
    If summary is not provided, generate it from chat_history in SESSION_CONTEXT.
    """
    chat_history = SESSION_CONTEXT.get(session_id, [])
    
    if not chat_history:
        return
    # Generate summary from full chat history
    user_text = "\n".join([m["content"] for m in chat_history if m["role"] == "user"])
    name = extract_name_with_openai(user_text) or ""
    address = extract_address_with_openai(user_text) or ""
    customer_number = extract_number_with_openai(chat_history) or session_id

    payload = {
        "sheet": "Chat_Summary",
        "date": datetime.now().strftime("%d-%m-%Y"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "phone_number": customer_number,
        "name": name,
        "address": address,
        "summary": summary
    }

    try:
        requests.post(SHEET_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print("⚠️ Failed to log chat summary to Google Sheet:", e)


def start_inactivity_watcher():
    def watcher():
        while True:
            now = datetime.now()
            for phone, last_time in list(LAST_MESSAGE_TIME.items()):
                if (now - last_time).total_seconds() > INACTIVITY_TIMEOUT:
                    try:
                        context = SESSION_CONTEXT.get(phone, [])
                        if context:
                            log_summary_to_google_sheet(session_id=phone)
                            print(f"🕒 Auto-summarized chat for {phone}")
                            LAST_MESSAGE_TIME.pop(phone, None)
                            SESSION_CONTEXT.pop(phone, None)
                    except Exception as e:
                        print(f"⚠️ Error summarizing chat for {phone}: {e}")
            time.sleep(30)
    threading.Thread(target=watcher, daemon=True).start()


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
        logging.info(f"📩 Incoming data: {data}")
        try:
            changes = data['entry'][0]['changes'][0]['value']
            
            # ✅ Only proceed if 'messages' exist
            if 'messages' in changes:
                message_data = changes['messages'][0]
                # 🛡️ Ignore echo/self or non-text messages
                if (
                    message_data.get("from") == PHONE_NUMBER_ID
                    or message_data.get("from") == FIX_PHONE_NUMBER
                ):
                    print("⚠️ Ignoring self-sent or echo message.")
                    return "OK", 200

                if message_data.get("type") != "text":
                    print("⚠️ Ignoring non-text message.")
                    return "OK", 200
                phone_number = message_data['from']
                text = message_data['text']['body']
                session_id = phone_number
                
                print(Fore.BLUE + "👤 User: " + Fore.CYAN + text)
                customer_name = extract_name_with_openai(text)
                chat_history = SESSION_CONTEXT.get(session_id, [])
                LAST_MESSAGE_TIME[phone_number] = datetime.now()
                customer_address = extract_address_with_openai(
                    "\n".join([m["content"] for m in chat_history if m["role"] == "user"])
                )
                log_to_google_sheet(phone_number, "User", text, name=customer_name, address=customer_address)

                reply = ask_openai(session_id, text)
                print(Fore.MAGENTA + "🤖 Bot:  " + Fore.GREEN + reply)
                log_to_google_sheet(phone_number, "Bot", reply, name = "Bot")
                send_whatsapp_message(phone_number, reply)

                # ✅ Update chat_history with latest messages before summarizing
                chat_history = SESSION_CONTEXT.get(session_id, [])
                chat_history.append({"role": "user", "content": text})
                chat_history.append({"role": "assistant", "content": reply})
                SESSION_CONTEXT[session_id] = chat_history[-10:]  # Keep last 10 messages

    
                # Trigger check
                if any(k in text.lower() for k in TRIGGER_KEYWORDS_USER) or \
                   any(k in reply.lower() for k in TRIGGER_KEYWORDS_BOT):
                    forward_summary_to_fixed_number(session_id,phone_number)
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
    start_inactivity_watcher()  # ✅ auto-start background thread
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)













