import os
import json
import logging
from functools import wraps

import requests
from flask import Flask, request, jsonify
from flask_basicauth import BasicAuth

# ---------- Настройки из переменных окружения ----------
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
CHAT_ID_HEADER = os.environ.get('CHAT_ID_HEADER', 'X-Chat-Id')
BASIC_AUTH_USER = os.environ.get('BASIC_AUTH_USER', '')
BASIC_AUTH_PASS = os.environ.get('BASIC_AUTH_PASS', '')
ENABLE_AUTH = bool(BASIC_AUTH_USER and BASIC_AUTH_PASS)
PORT = int(os.environ.get('PORT', 8080))
DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'

YANDEX_MESSENGER_URL = 'https://botapi.messenger.yandex.net/bot/v1/messages/sendText/'

# ---------- Настройка логирования ----------
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('alertmanager-webhook')

if DEBUG:
    logger.warning("DEBUG MODE ENABLED – webhook payloads and messages will be logged")

# ---------- Инициализация Flask и BasicAuth ----------
app = Flask(__name__)
if ENABLE_AUTH:
    app.config['BASIC_AUTH_USERNAME'] = BASIC_AUTH_USER
    app.config['BASIC_AUTH_PASSWORD'] = BASIC_AUTH_PASS
    basic_auth = BasicAuth(app)
    logger.info("Basic authentication enabled")
else:
    logger.info("Basic authentication disabled (credentials not set)")

# ---------- Вспомогательные функции ----------
def format_alert(alert):
    """Форматирует одно оповещение Alertmanager в строку."""
    labels = alert.get('labels', {})
    annotations = alert.get('annotations', {})
    status = alert.get('status', 'unknown')
    alert_name = labels.get('alertname', 'Без имени')
    severity = labels.get('severity', 'не указана')
    summary = annotations.get('summary', 'Нет описания')
    description = annotations.get('description', '')
    starts_at = alert.get('startsAt', '')
    return (
        f"🔔 **{alert_name}** (важность: {severity})\n"
        f"Статус: {status}\n"
        f"Описание: {summary}\n"
        f"{description}\n"
        f"Время: {starts_at}\n"
    )

def build_message_from_alerts(alerts, status):
    """Собирает итоговое сообщение из списка оповещений."""
    if not alerts:
        return "✅ Нет активных оповещений"
    
    header = f"🚨 **Alertmanager** – статус: {status.upper()}\n\n"
    body = "\n".join(format_alert(alert) for alert in alerts)
    full_msg = header + body
    if len(full_msg) > 6000:
        full_msg = full_msg[:5997] + "..."
    return full_msg

def send_to_yandex_messenger(chat_id, text):
    """Отправляет текстовое сообщение через API Яндекс Мессенджера."""
    headers = {
        'Authorization': f'OAuth {BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    payload = {
        'chat_id': chat_id,
        'text': text
    }
    try:
        resp = requests.post(YANDEX_MESSENGER_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get('ok'):
            logger.info(f"Message sent to chat {chat_id}, message_id={data.get('message_id')}")
            return True, None
        else:
            error = data.get('description', 'Unknown error')
            logger.error(f"Yandex API error: {error}")
            return False, error
    except requests.exceptions.RequestException as e:
        logger.exception("Failed to send message to Yandex Messenger")
        return False, str(e)

# ---------- Основной эндпоинт ----------
@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. Проверка Basic Auth (если включена)
    if ENABLE_AUTH:
        auth = request.authorization
        if not auth or auth.username != BASIC_AUTH_USER or auth.password != BASIC_AUTH_PASS:
            logger.warning("Unauthorized access attempt")
            return jsonify({'error': 'Unauthorized'}), 401

    # 2. Получение chat_id из заголовка
    chat_id = request.headers.get(CHAT_ID_HEADER)
    if not chat_id:
        logger.error(f"Missing required header: {CHAT_ID_HEADER}")
        return jsonify({'error': f'Missing {CHAT_ID_HEADER} header'}), 400

    # 3. Разбор тела запроса от Alertmanager
    try:
        payload = request.get_json()
        if not payload:
            raise ValueError("Empty body")
    except Exception as e:
        logger.error(f"Invalid JSON: {e}")
        return jsonify({'error': 'Invalid JSON'}), 400

    # ---- DEBUG: логируем исходный webhook ----
    if DEBUG:
        logger.debug(f"Received webhook payload (chat_id={chat_id}):\n{json.dumps(payload, indent=2, ensure_ascii=False)}")

    # 4. Извлечение данных об оповещениях
    status = payload.get('status', 'unknown')
    alerts = payload.get('alerts', [])
    if not alerts:
        logger.info("No alerts in payload, sending empty notification")
        text = "✅ Нет активных оповещений"
    else:
        text = build_message_from_alerts(alerts, status)

    # ---- DEBUG: логируем сообщение перед отправкой ----
    if DEBUG:
        logger.debug(f"Prepared message to send (chat_id={chat_id}):\n{text}")

    # 5. Отправка в Яндекс Мессенджер
    success, error_msg = send_to_yandex_messenger(chat_id, text)
    if success:
        return jsonify({'status': 'sent'}), 200
    else:
        return jsonify({'error': f'Yandex Messenger error: {error_msg}'}), 502

# ---------- Healthcheck ----------
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

# ---------- Запуск ----------
if __name__ == '__main__':
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is not set")
        exit(1)
    logger.info(f"Starting webhook receiver on port {PORT}, debug={DEBUG}")
    app.run(host='0.0.0.0', port=PORT)