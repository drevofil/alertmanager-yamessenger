# Alertmanager Webhook → Яндекс Мессенджер

Адаптер для отправки оповещений из **Prometheus Alertmanager** в **Яндекс Мессенджер** (бот).  
Принимает webhook‑вызовы Alertmanager, форматирует их и отправляет текстовые сообщения в указанный чат или пользователю через Bot API.

## Возможности

- Поддержка **Basic‑аутентификации** (опционально)
- Передача `chat_id` через **HTTP заголовок** (позволяет использовать один экземпляр для разных чатов)
- **Debug‑режим** с логированием входящих webhook и исходящих сообщений
- Автоматическое ограничение длины сообщения (максимум 6000 символов)
- Healthcheck‑эндпоинт `/health`

## Требования

- Python 3.11+
- OAuth‑токен бота Яндекс Мессенджера
- Бот должен быть добавлен в целевой чат (или иметь право писать пользователю)

## Переменные окружения

| Переменная            | Обязательная | По умолчанию       | Описание                                                                 |
|-----------------------|--------------|--------------------|--------------------------------------------------------------------------|
| `BOT_TOKEN`           | ✅           | –                  | OAuth‑токен бота (начинается с `At`)                                     |
| `CHAT_ID_HEADER`      | ❌           | `X-Chat-Id`        | Имя HTTP‑заголовка, из которого читается `chat_id`                       |
| `BASIC_AUTH_USER`     | ❌           | –                  | Логин для Basic‑аутентификации (если задан, включается проверка)         |
| `BASIC_AUTH_PASS`     | ❌           | –                  | Пароль для Basic‑аутентификации                                          |
| `PORT`                | ❌           | `8080`             | Порт, на котором слушает приложение                                      |
| `DEBUG`               | ❌           | `false`            | При `true` в лог выводятся входящий webhook и отправляемое сообщение     |

## Установка и запуск

### 1. Локальный запуск (без Docker)

```bash
# Клонируйте репозиторий или сохраните app.py и requirements.txt
pip install -r requirements.txt

export BOT_TOKEN="AtXXXXXXXXXXX"
export BASIC_AUTH_USER="admin"      # опционально
export BASIC_AUTH_PASS="secret"
export DEBUG="false"

python app.py
```

### 2. Сборка Docker‑образа

```bash
docker build -t alertmanager-yamessenger .
```

### 3. Запуск в Docker

```bash
docker run -d --name alertmanager-yamessenger \
  -p 8080:8080 \
  -e BOT_TOKEN="AtXXXXXXXXXXX" \
  -e BASIC_AUTH_USER="admin" \
  -e BASIC_AUTH_PASS="secret" \
  alertmanager-yamessenger
```

### 4. Проверка работоспособности

```bash
curl http://localhost:8080/health
# Должен вернуть {"status":"ok"}
```

## Конфигурация Alertmanager

В файле `alertmanager.yml` для каждого receiver укажите:

- `url` – адрес вашего адаптера
- `http_config.headers` – добавьте заголовок с `chat_id` (или другим, переопределённым через `CHAT_ID_HEADER`)
- Рекомендуется включить `send_resolved: true`, чтобы получать уведомления о восстановлении

### Пример для двух разных чатов

```yaml
route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'default'
  routes:
    - match:
        team: 'alpha'
      receiver: 'team-alpha'
    - match:
        team: 'beta'
      receiver: 'team-beta'

receivers:
- name: 'team-alpha'
  webhook_configs:
  - url: 'http://alertmanager-webhook:8080/webhook'
    send_resolved: true
    http_config:
      headers:
        X-Chat-Id: '0/0/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'

- name: 'team-beta'
  webhook_configs:
  - url: 'http://alertmanager-webhook:8080/webhook'
    send_resolved: true
    http_config:
      headers:
        X-Chat-Id: '0/0/yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy'
```

### Если включена Basic‑аутентификация

Добавьте заголовок `Authorization`:

```yaml
http_config:
  headers:
    X-Chat-Id: '0/0/...'
    Authorization: 'Basic YWRtaW46c2VjcmV0'   # base64("admin:secret")
```

## Тестирование адаптера (без Alertmanager)

Отправьте тестовый webhook с помощью `curl`:

```bash
curl -X POST http://localhost:8080/webhook -H "Content-Type: application/json" -H "X-Chat-Id: 0/0/<uuid>" \
  -d '{
    "status": "resolved",
    "alerts": [
      {
        "labels": {"alertname": "High CPU", "severity": "critical"},
        "annotations": {"summary": "CPU usage > 90%", "description": "Instance web01"},
        "startsAt": "2025-02-16T10:00:00Z"
      }
    ]
  }'
```

Успешный ответ: `{"status":"sent"}`

## Режим DEBUG

Для диагностики проблем включите `DEBUG=true`.  
**Внимание!** В этом режиме в лог попадают **все данные** входящего webhook и текст отправляемого сообщения.  
Не используйте в production без крайней необходимости.

Пример лога:

```
2026-04-15 09:36:11,758 - alertmanager-webhook - DEBUG - Received webhook payload (chat_id=0/0/21b636e6-9899-4e88-a81d-d6cf82c7de94):
{
  "status": "firing",
  "alerts": [
    {
      "labels": {
        "alertname": "High CPU",
        "severity": "critical"
      },
      "annotations": {
        "summary": "CPU usage > 90%",
        "description": "Instance web01"
      },
      "startsAt": "2025-02-16T10:00:00Z"
    }
  ]
}
2026-04-15 09:36:11,758 - alertmanager-webhook - DEBUG - Prepared message to send (chat_id=0/0/21b636e6-9899-4e88-a81d-d6cf82c7de94):
🚨 **Alertmanager** – статус: FIRING

🔔 **High CPU** (важность: critical)
Статус: unknown
Описание: CPU usage > 90%
Instance web01
Время: 2025-02-16T10:00:00Z
```

## Формат сообщения

Сообщение формируется автоматически из полей Alertmanager:

- Заголовок с общим статусом (`firing` / `resolved`)
- Для каждого алерта:
  - Название (`alertname`)
  - Важность (`severity`)
  - Краткое описание (`summary`)
  - Детальное описание (`description`)
  - Время срабатывания (`startsAt`)

При необходимости вы можете изменить шаблон в функции `build_message_from_alerts` в `app.py`.

## Ограничения

- Максимальная длина сообщения – 6000 символов (ограничение API). Длинные сообщения обрезаются.
- Бот должен быть участником чата или иметь право писать пользователю.
- При использовании Basic‑аутентификации настоятельно рекомендуется включить TLS на уровне Ingress / reverse proxy.