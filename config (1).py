"""
config.py

Carrega toda a configuração do bot a partir de variáveis de ambiente.
Centralizado aqui para nenhum outro módulo precisar de chamar
os.environ diretamente — facilita auditoria de quais segredos o
bot realmente usa.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cless_bot.config")


def _require(key: str) -> str:
    """Lê uma variável de ambiente obrigatória; falha cedo e com
    mensagem clara se faltar, em vez de um erro confuso mais tarde."""
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(
            f"Variável de ambiente obrigatória '{key}' não está definida. "
            f"Confirma o teu ficheiro .env (vê .env.example como referência)."
        )
    return value


TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")

APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://clesscripto.app")

# Lista de chat_ids do Telegram com permissão de admin (aprovar bónus,
# publicar sinais). Vazio é válido no arranque — o bot ainda funciona,
# só os comandos /admin ficam indisponíveis até alguém ser configurado.
_admin_ids_raw = os.environ.get("ADMIN_TELEGRAM_IDS", "")
ADMIN_TELEGRAM_IDS = {
    int(chat_id.strip())
    for chat_id in _admin_ids_raw.split(",")
    if chat_id.strip().isdigit()
}

if not ADMIN_TELEGRAM_IDS:
    logger.warning(
        "ADMIN_TELEGRAM_IDS está vazio — nenhum admin configurado. "
        "Os comandos administrativos (/admin) vão recusar todos os "
        "pedidos até definires pelo menos um chat_id."
    )


def is_admin(chat_id: int) -> bool:
    """Confere se um chat_id tem permissão de admin no bot."""
    return chat_id in ADMIN_TELEGRAM_IDS
