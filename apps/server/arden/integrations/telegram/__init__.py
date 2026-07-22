from arden.integrations.base import Integration, IntegrationField
from arden.integrations.telegram.notifier import TelegramNotifier

TELEGRAM = Integration(
    id="telegram",
    label="Telegram",
    service_fields=[
        IntegrationField("telegram_bot_token", "Telegram bot token", secret=True, env_var="TELEGRAM_BOT_TOKEN"),
    ],
    notifier_class=TelegramNotifier,
)
