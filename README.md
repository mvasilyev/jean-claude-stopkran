# Stopkran

Telegram-бот для удалённого подтверждения действий Claude Code.

Когда Claude Code работает автономно (ночью, на удалённой машине), он запрашивает подтверждение опасных действий через терминал. Stopkran пересылает эти запросы в Telegram и возвращает решение обратно.

```
Claude Code → hook → Unix socket → daemon → Telegram → телефон / часы
                                           ← callback ←
```

## Требования

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Telegram-бот (создать через [@BotFather](https://t.me/BotFather))

## Быстрый старт

```bash
git clone <repo-url> && cd jean-claude-stopkran

# Установить зависимости, настроить токен, хук и автозапуск
make setup

# Запустить демон в фоне (launchd на macOS, systemd на Linux)
make start-bg

# Отправить /start боту в Telegram
```

Готово. Claude Code теперь пересылает запросы в Telegram.

## Make-команды

```
make help            Показать все команды
make install         Установить зависимости (uv sync)
make setup           Интерактивный мастер настройки
make start           Запустить демон (foreground)
make start-bg        Запустить демон (launchd / systemd)
make stop            Остановить демон
make restart         Перезапустить демон
make status          Показать статус: процесс, сокет, конфиг, хук, сервис
make logs            Следить за логами (tail -f)
make hook-install    Добавить хук в ~/.claude/settings.json
make hook-uninstall  Убрать хук из settings.json
make uninstall       Полное удаление: демон, хук, конфиг
```

## Как это работает

1. Claude Code вызывает действие, требующее подтверждения
2. Хук `stopkran_hook.py` получает запрос через stdin
3. Отправляет его демону через Unix socket (`/tmp/stopkran.sock`)
4. Демон шлёт сообщение в Telegram с кнопками **Allow** / **Deny**
5. Вы нажимаете кнопку — решение возвращается через сокет в Claude Code
6. Если не ответить за 300 секунд — автоматический deny

### Apple Watch и быстрые ответы

Кнопки Telegram не работают на Apple Watch. Вместо этого можно ответить текстом:

| Ответ | Действие |
|-------|----------|
| Да, yes, ок, ok, 👍, ✅ | Allow |
| Нет, no, 👎, ❌ | Deny |

Текстовый ответ применяется к самому старому ожидающему запросу.

### Graceful degradation

Если демон не запущен, хук завершается молча — Claude Code показывает обычный терминальный UI.

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Регистрация — первый пользователь становится владельцем |
| `/status` | Количество ожидающих запросов |

## Ручная настройка

Если не хотите использовать `make setup`:

### Конфиг

```bash
mkdir -p ~/.config/stopkran
cat > ~/.config/stopkran/config.json << 'EOF'
{
    "token": "YOUR_BOT_TOKEN",
    "chat_id": null,
    "timeout": 300
}
EOF
chmod 600 ~/.config/stopkran/config.json
```

`chat_id` заполнится автоматически после `/start` в Telegram.

### Хук

```bash
make hook-install
```

Или вручную — добавить в `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/stopkran_hook.py",
            "timeout": 330000
          }
        ]
      }
    ]
  }
}
```

> Таймаут хука (330с) должен быть больше таймаута демона (300с), чтобы Claude Code не убил хук раньше auto-deny.

## Файлы

| Файл | Описание |
|------|----------|
| `stopkran_daemon.py` | Демон: Telegram-бот + Unix socket сервер |
| `stopkran_hook.py` | Хук для Claude Code (только stdlib) |
| `stopkran_setup.py` | Интерактивная настройка |
| `com.stopkran.daemon.plist` | Шаблон launchd для macOS |
| `stopkran.service` | Шаблон systemd для Linux |
| `Makefile` | Команды управления |
| `pyproject.toml` | Зависимости (uv) |

## Устранение проблем

**Демон не запускается**
```bash
make status    # проверить статус
make logs      # посмотреть логи
```

**На Linux демон падает с `TimedOut` / `ConnectTimeout`**
- Скорее всего нужен прокси. systemd запускает сервисы в чистом окружении без переменных из шелла.
- Убедитесь, что `HTTP_PROXY` / `HTTPS_PROXY` выставлены в текущей сессии, и выполните `make start-bg` — он автоматически пропишет их в `.service` файл.

**Хук не срабатывает**
- Перезапустить Claude Code после изменения `settings.json`
- Проверить `make status` — хук должен быть установлен

**Кнопки в Telegram не реагируют**
- Проверить, что запущен только один инстанс демона (`make status`)
- При `409 Conflict` в логах — `make restart`

**Apple Watch не работает**
- Отвечать нужно простым текстовым сообщением, не reply
- Убедиться, что демон перезапущен после обновления кода
