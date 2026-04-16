import os
import json
import logging
from collections import defaultdict

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

# Опциональные URL для ссылок в подвале сообщения
GRAFANA_URL = os.environ.get('GRAFANA_URL', '')
ALERTMANAGER_URL = os.environ.get('ALERTMANAGER_URL', '')
PROMETHEUS_URL = os.environ.get('PROMETHEUS_URL', '')

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

# ---------- Форматирование одного алерта ----------
def format_single_alert(alert):
    """Форматирует один алерт в читаемый блок (без заголовка статуса)."""
    labels = alert.get('labels', {})
    annotations = alert.get('annotations', {})
    status = alert.get('status', 'unknown')
    alertname = labels.get('alertname', 'Без имени')
    severity = labels.get('severity', 'не указана')
    
    # Выбор эмодзи для severity
    severity_emoji = {
        'critical': '🚨',
        'warning': '⚠️',
        'info': 'ℹ️',
        'не указана': '🔔'
    }.get(severity, '🔔')
    
    summary = annotations.get('summary', 'Нет описания')
    description = annotations.get('description', '')
    runbook_url = annotations.get('runbook_url', '')
    # generator_url = alert.get('generatorURL', '')
    
    # Формируем блок
    lines = [
        "---",
        f"🪪 {alertname}",
        f"{severity_emoji} {severity.upper()} {severity_emoji}",
        f"📝 {summary}",
    ]
    if description:
        lines.append(f"📖 {description}")
    if runbook_url:
        lines.append(f"📔 {runbook_url}")
    
    # Вывод labels (исключаем служебные, если нужно)
    if labels:
        lines.append("🏷 Labels:")
        for key, value in labels.items():
            lines.append(f"  {key}: {value}")
    
    # generatorURL (ссылка на график в Prometheus)
    # if generator_url:
    #     lines.append(f"📊 Prometheus graph: {generator_url}")
    
    return "\n".join(lines)

def build_footer():
    """Формирует подвал с ссылками на Grafana, Alertmanager, Prometheus."""
    footer_parts = []
    if GRAFANA_URL:
        footer_parts.append(f"🛠 Grafana ({GRAFANA_URL})")
    if ALERTMANAGER_URL:
        footer_parts.append(f"💊 Alertmanager ({ALERTMANAGER_URL})")
    if PROMETHEUS_URL:
        footer_parts.append(f"💊 Prometheus ({PROMETHEUS_URL})")
    if not footer_parts:
        return ""
    return "\n\n" + " ".join(footer_parts)

def build_message_for_status(alerts, status):
    """
    Собирает сообщение для списка алертов с одинаковым статусом.
    Возвращает текст сообщения.
    """
    if not alerts:
        return None
    
    # Заголовок в зависимости от статуса
    if status == 'firing':
        header = "🔥 Alerts Firing 🔥\n"
    elif status == 'resolved':
        header = "✅ Alerts Resolved ✅\n"
    else:
        header = f"📢 Alerts ({status.upper()})\n"
    
    # Форматируем каждый алерт
    alert_blocks = [format_single_alert(alert) for alert in alerts]
    body = "\n".join(alert_blocks)
    
    # Добавляем подвал
    footer = build_footer()
    
    full_msg = header + body + footer
    
    # Ограничение длины сообщения (6000 символов)
    if len(full_msg) > 6000:
        full_msg = full_msg[:5997] + "..."
    return full_msg

# ---------- Отправка в Яндекс Мессенджер ----------
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
    # 1. Проверка Basic Auth
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

    # 3. Разбор тела запроса
    try:
        payload = request.get_json()
        if not payload:
            raise ValueError("Empty body")
    except Exception as e:
        logger.error(f"Invalid JSON: {e}")
        return jsonify({'error': 'Invalid JSON'}), 400

    if DEBUG:
        logger.debug(f"Received webhook payload (chat_id={chat_id}):\n{json.dumps(payload, indent=2, ensure_ascii=False)}")

    # 4. Группировка алертов по статусу
    alerts = payload.get('alerts', [])
    if not alerts:
        logger.info("No alerts in payload, sending empty notification")
        text = "✅ Нет активных оповещений"
        success, error_msg = send_to_yandex_messenger(chat_id, text)
        if success:
            return jsonify({'status': 'sent'}), 200
        else:
            return jsonify({'error': f'Yandex Messenger error: {error_msg}'}), 502

    # Группируем
    alerts_by_status = defaultdict(list)
    for alert in alerts:
        status = alert.get('status', 'unknown')
        alerts_by_status[status].append(alert)

    # Отправляем отдельное сообщение для каждого статуса
    all_success = True
    last_error = None
    for status, status_alerts in alerts_by_status.items():
        message = build_message_for_status(status_alerts, status)
        if not message:
            continue
        
        if DEBUG:
            logger.debug(f"Prepared message for status '{status}' (chat_id={chat_id}):\n{message}")
        
        success, error_msg = send_to_yandex_messenger(chat_id, message)
        if not success:
            all_success = False
            last_error = error_msg
            logger.error(f"Failed to send message for status {status}: {error_msg}")
    
    if all_success:
        return jsonify({'status': 'sent'}), 200
    else:
        return jsonify({'error': f'Yandex Messenger error: {last_error}'}), 502

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