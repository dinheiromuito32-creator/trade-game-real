"""
account_linking.py

Implementa o fluxo de vínculo de conta por "link mágico", sem nunca
pedir password no Telegram (decisão de segurança documentada em
ARQUITETURA.md).

Fluxo:
1. /start → generate_link_token() cria um token único, válido 10 min,
   guarda em telegramLinks/{token} com status "pending".
2. Bot manda ao user: {APP_BASE_URL}/?t={token}
3. User abre no telemóvel (já logado no app), confirma com 1 toque.
   Essa confirmação é feita pelo PRÓPRIO APP (não por este bot) —
   o app escreve telegramLinks/{token}.status = "confirmed" e
   telegramLinks/{token}.uid = <uid do user>.
4. Este módulo expõe um listener (watch_link_confirmation) que o bot
   usa para detetar a confirmação em tempo real e finalizar o vínculo,
   criando telegramLinks/{uid} definitivo.

Nenhuma password passa pelo Telegram em momento algum.
"""

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

from google.cloud.firestore_v1 import DocumentSnapshot

from firestore_client import get_db

logger = logging.getLogger("cless_bot.account_linking")

TOKEN_LENGTH = 32
TOKEN_TTL_MINUTES = 30  # aumentado de 10 para 30 minutos
LINKS_COLLECTION = "telegramLinks"


def _generate_token() -> str:
    """Token aleatório, criptograficamente seguro — não é um UUID
    sequencial nem previsível, para que não dê para adivinhar tokens
    de outros utilizadores."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(TOKEN_LENGTH))


def generate_link_token(chat_id: int) -> tuple[str, str]:
    """
    Cria um novo token de vínculo pendente para este chat_id do Telegram.

    Devolve (token, link_url_path). O caller (bot.py) é responsável por
    compor a URL completa com APP_BASE_URL.
    """
    db = get_db()
    token = _generate_token()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=TOKEN_TTL_MINUTES)

    doc_ref = db.collection(LINKS_COLLECTION).document(token)
    doc_ref.set({
        "status": "pending",
        "chat_id": chat_id,
        "created_at": now,
        "expires_at": expires_at,
    })

    logger.info(f"Token de vínculo gerado para chat_id={chat_id}")
    return token, f"/?t={token}"


def is_token_expired(snapshot_data: dict) -> bool:
    """Confere se um documento de token já passou do prazo de validade.
    Tokens já confirmados nunca são considerados expirados."""
    if snapshot_data.get("status") == "confirmed":
        return False
    expires_at = snapshot_data.get("expires_at")
    if expires_at is None:
        return True
    now = datetime.now(timezone.utc)
    # Firestore pode devolver datetime com ou sem timezone
    if hasattr(expires_at, 'tzinfo') and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return now > expires_at


def get_link_status(token: str) -> dict | None:
    """
    Lê o estado atual de um token. Devolve None se o token não existir.
    Usado pelo bot para fazer polling leve enquanto espera confirmação,
    como alternativa/complemento ao listener em tempo real.
    """
    db = get_db()
    doc = db.collection(LINKS_COLLECTION).document(token).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def finalize_link(token: str) -> str | None:
    """
    Chamado depois de detetar que o app confirmou o vínculo
    (status == "confirmed" e uid populado no documento do token).

    Cria o documento definitivo telegramLinks/{uid} e apaga o token
    temporário. Devolve o uid vinculado, ou None se algo correu mal
    (token inválido, expirado, ou ainda não confirmado).
    """
    db = get_db()
    token_ref = db.collection(LINKS_COLLECTION).document(token)
    token_doc = token_ref.get()

    if not token_doc.exists:
        logger.warning(f"finalize_link: token '{token}' não encontrado.")
        return None

    data = token_doc.to_dict()

    if data.get("status") != "confirmed":
        logger.warning(
            f"finalize_link: token '{token}' ainda não está confirmado "
            f"(status atual: {data.get('status')})."
        )
        return None

    uid = data.get("uid")
    chat_id = data.get("chat_id")

    if not uid or not chat_id:
        logger.error(
            f"finalize_link: token '{token}' confirmado mas sem uid/chat_id "
            "completo — possível adulteração ou bug no app. A ignorar."
        )
        return None

    if is_token_expired(data):
        logger.warning(f"finalize_link: token '{token}' já expirou.")
        token_ref.delete()
        return None

    # Documento definitivo, indexado pelo uid (consultas futuras do bot
    # vão sempre por uid, nunca por token).
    final_ref = db.collection(LINKS_COLLECTION).document(uid)
    final_ref.set({
        "uid": uid,
        "chat_id": chat_id,
        "linked_at": datetime.now(timezone.utc),
    })

    # Remove o token temporário — uso único, não deve sobreviver.
    token_ref.delete()

    logger.info(f"Conta vinculada com sucesso: uid={uid} chat_id={chat_id}")
    return uid


def get_chat_id_for_uid(uid: str) -> int | None:
    """
    Lookup inverso: dado um uid do Cless Cripto, devolve o chat_id do
    Telegram vinculado, ou None se não houver vínculo. Usado por todos
    os outros módulos (rankings, sinais, autotrade) para saber para
    onde mandar mensagens a um user específico.
    """
    db = get_db()
    doc = db.collection(LINKS_COLLECTION).document(uid).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("chat_id")


def get_uid_for_chat_id(chat_id: int) -> str | None:
    """
    Lookup: dado um chat_id do Telegram, devolve o uid vinculado.
    Usa query simples (só um where) para evitar precisar de índice composto.
    Filtra documentos sem uid do lado do Python.
    """
    from google.cloud.firestore_v1 import FieldFilter
    db = get_db()
    try:
        query = (
            db.collection(LINKS_COLLECTION)
            .where(filter=FieldFilter("chat_id", "==", chat_id))
            .limit(10)  # apanha tokens pendentes e o definitivo
        )
        results = list(query.stream())
        for doc in results:
            data = doc.to_dict()
            uid = data.get("uid")
            # Só aceita documentos com uid definido e sem status pending
            if uid and data.get("status") != "pending":
                logger.info(f"get_uid_for_chat_id: chat_id={chat_id} → uid={uid}")
                return uid
        logger.warning(f"get_uid_for_chat_id: nenhum vínculo confirmado para chat_id={chat_id}")
        return None
    except Exception as e:
        logger.error(f"get_uid_for_chat_id erro: {e}")
        return None
