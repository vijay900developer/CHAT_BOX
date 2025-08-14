from flask import Flask, request, jsonify
import requests
from openai import OpenAI
import logging
import json
from colorama import Fore, Style, init
import os
import re
from datetime import datetime, timedelta
from dateutil import parser
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # or logging.CRITICAL to suppress almost everything
# 🎨 Enable colored output
init(autoreset=True)
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
WHATSAPP_TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_NUMBER_ID = os.environ["PHONE_NUMBER_ID"]
SALES_SHEET_URL = os.environ["SALES_SHEET_URL"]
ADMIN_NUMBERS = os.environ.get("ADMIN_NUMBERS", "").split(",")  # e.g. "919999999999,918888888888"


app = Flask(__name__)
client = OpenAI(api_key=OPENAI_API_KEY)

# In-memory context store (for demo only)
SESSION_CONTEXT = {}

# Assistant system prompt
with open("system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# Assistant Personal-Prompt
with open("personal_prompt.txt", "r", encoding="utf-8") as f:
    PERSONAL_PROMPT = f.read()

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

def fetch_sales_data():
    try:
        return requests.get(SALES_SHEET_URL, timeout=10).json()
    except Exception as e:
        return {"error": str(e)}

def extract_filters(user_message):
    """
    Use AI to extract filters (date, showroom, product) from user message.
    """
    prompt = f"""
    You are an assistant that extracts filters from sales queries.
    User message: "{user_message}"

    Return a JSON with possible keys:
    - "date" (string in dd/mm/yyyy if exact date, or natural text like "yesterday", "12 August", "last month")
    - "showroom" (string like "Bikaner", "Jaipur", etc. or null)
    - "product" (string like "Blazer", "Kurta", etc. or null)

    If something is not mentioned, keep it null.
    """

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    filters = json.loads(response.choices[0].message.content.strip())
    return filters

from dateutil import parser

def normalize_date(date_text):
    if not date_text:
        return None
    date_text = date_text.lower().strip()

    if date_text == "yesterday":
        return (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    elif date_text == "today":
        return datetime.now().strftime("%d/%m/%Y")
    else:
        try:
            parsed = parser.parse(date_text, dayfirst=True)
            return parsed.strftime("%d/%m/%Y")
        except:
            return None

def apply_filters(sales_data, filters):
    query_date = normalize_date(filters.get("date"))
    showroom = filters.get("showroom")
    product = filters.get("product")

    filtered_rows = []
    total = 0

    for row in sales_data:
        row_date = row.get("Bill_Date")
        row_showroom = row.get("Showroom")
        row_product = row.get("Product")
        amount = float(row.get("Net_Amount", 0))

        if (not query_date or row_date == query_date) \
           and (not showroom or row_showroom.lower() == showroom.lower()) \
           and (not product or row_product.lower() == product.lower()):
            total += amount
            filtered_rows.append(row)

    return total, filtered_rows

def ask_sales_ai(user_message):
    try:
        sales_data = requests.get(SALES_SHEET_URL).json()

        filters = extract_filters(user_message)
        total, rows = apply_filters(sales_data, filters)

        ai_prompt = f"""
        User asked: {user_message}
        Filters applied: {filters}
        Matching entries: {json.dumps(rows, indent=2)}
        Total sales = ₹{total}

        Explain the result in a clear way for the user.
        """

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": ai_prompt}],
            temperature=0
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print("❌ Sales AI error:", e)
        return "Sorry, I couldn't fetch or analyze the sales data."



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
    FORWARD_TO_NUMBER = "918000502897"
    chat_history = SESSION_CONTEXT.get(session_id, [])
    if not chat_history:
        return

    summary = summarize_chat_with_openai(chat_history)
    customer_name = extract_name_with_openai("\n".join([m["content"] for m in chat_history if m["role"] == "user"]))
    customer_number = extract_number_with_openai(chat_history)

    if not customer_number:
        customer_number = user_whatsapp_number  # fallback to WhatsApp sender number

    message = (
        f"📩 Customer Query Summary:\n{summary}\n\n"
        f"👤 Name: {customer_name or 'Not provided'}\n"
        f"📞 Contact: {customer_number}"
    )

    send_whatsapp_message(FORWARD_TO_NUMBER, message)



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

                # ✅ If message is from ADMIN_NUMBER → Personal sales bot
                if phone_number in ADMIN_NUMBERS:
                    sales_data = fetch_sales_data()
                    if "error" in sales_data:
                        reply = f"❌ Could not fetch sales data: {sales_data['error']}"
                    else:
                        # AI gets sales data + user query
                        reply = ask_sales_ai(text)
                        send_whatsapp_message(phone_number, reply)
                        return "OK", 200
                else: 
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)























