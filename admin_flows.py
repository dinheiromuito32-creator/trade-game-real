"""
admin_flows.py

Fluxos exclusivos de admin:
  1. Aprovação de bónus referral — lista de pendentes por DM, botões
     Aprovar/Rejeitar, crédito direto no Firestore via Admin SDK.
  2. Aprovação de sinais antes da publicação — revisão e confirmação
     de sinais gerados pelo signals_engine.
  3. Aprovação de remessas internacionais — pedido do user, admin
     aprova/rejeita depois de processar o envio pelo canal externo
     (mesma lógica do RemessaTab do app).
  4. Confirmação de depósitos de cripto — o admin valida o TXID contra
     um block explorer manualmente (não há verificação on-chain
     automática nesta arquitetura) e informa o valor líquido em STN
     antes de aprovar (mesma lógica do CryptoDepositTab do app).

O Admin SDK ignora as Firestore Rules, o que é correto aqui porque
este código corre no servidor do bot (não no browser do user). É o
mesmo modelo que o AdminTab do app usa — confiança baseada em quem
executa o código, não apenas nas Rules.
"""

import logging
import os
from datetime import datetime, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CallbackQueryHandler
from google.cloud.firestore_v1 import Increment as firestore_increment

from firestore_client import get_db
from account_linking import get_chat_id_for_uid
import formatters
import config
import fx

logger = logging.getLogger("cless_bot.admin_flows")

CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

# Prefixos dos callback_data dos botões inline
CB_BONUS_APPROVE = "bonus_approve:"
CB_BONUS_REJECT  = "bonus_reject:"
CB_SIGNAL_APPROVE = "signal_approve:"
CB_SIGNAL_REJECT  = "signal_reject:"
CB_REMIT_APPROVE = "remit_approve:"
CB_REMIT_REJECT  = "remit_reject:"
CB_CRYPTODEP_REJECT = "cryptodep_reject:"


# ══════════════════════════════════════
# BÓNUS REFERRAL
# ══════════════════════════════════════

async def send_pending_bonuses_to_admins(bot: Bot) -> None:
    """
    Lê users com referralEarnings > 0 que ainda não foram aprovados,
    e envia a lista para cada admin com botões de aprovação por utilizador.
    Chamado automaticamente pelo scheduler (ex: diariamente às 09:00) ou
    pelo comando /admin no bot.
    """
    db = get_db()

    # Buscar users com ganhos pendentes não processados
    pending_docs = (
        db.collection("users")
        .where("referralEarnings", ">", 0)
        .where("referralBonusPaid", "==", False)
        .stream()
    )
    items = []
    for doc in pending_docs:
        data = doc.to_dict()
        items.append({
            "uid": doc.id,
            "name": data.get("name", "Utilizador"),
            "amount": data.get("referralEarnings", 0.0),
            "referralCount": data.get("referralCount", 0),
        })

    if not items:
        for admin_id in config.ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text="✅ Sem bónus referral pendentes de aprovação.",
                )
            except Exception as e:
                logger.warning(f"Não foi possível contactar admin {admin_id}: {e}")
        return

    summary = formatters.referral_bonus_pending(items)

    # Para cada item, manda um botão individual — mais fácil de gerir
    # do que aprovar/rejeitar toda a lista de uma vez.
    for item in items:
        uid = item["uid"]
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Aprovar", callback_data=f"{CB_BONUS_APPROVE}{uid}"),
                InlineKeyboardButton("❌ Rejeitar", callback_data=f"{CB_BONUS_REJECT}{uid}"),
            ]
        ])
        msg = (
            f"💰 *Bónus Referral — {item['name']}*\n"
            f"Montante: *{item['amount']:,.0f} STN*\n"
            f"Referidos: {item['referralCount']}\n"
            f"`{uid}`"
        )
        for admin_id in config.ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=msg,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            except Exception as e:
                logger.warning(f"Não foi possível contactar admin {admin_id}: {e}")


async def handle_bonus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler dos botões Aprovar/Rejeitar bónus referral.
    Registado no bot como CallbackQueryHandler.
    """
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id
    if not config.is_admin(chat_id):
        await query.edit_message_text("⛔ Sem permissão.")
        return

    data = query.data

    if data.startswith(CB_BONUS_APPROVE):
        uid = data[len(CB_BONUS_APPROVE):]
        await _approve_bonus(uid, query)

    elif data.startswith(CB_BONUS_REJECT):
        uid = data[len(CB_BONUS_REJECT):]
        await _reject_bonus(uid, query)


async def _approve_bonus(uid: str, query) -> None:
    db = get_db()
    try:
        user_ref = db.collection("users").document(uid)
        user_doc = user_ref.get()
        if not user_doc.exists:
            await query.edit_message_text(f"⚠️ User `{uid}` não encontrado.")
            return

        data = user_doc.to_dict()
        amount = data.get("referralEarnings", 0.0)
        name = data.get("name", "Utilizador")

        # Transação atómica: crédita o bónus no stnBal e marca como pago.
        # O Admin SDK permite escrever diretamente, ignorando as Rules
        # (comportamento correto para operações de servidor).
        transaction = db.transaction()

        @db.transaction()
        def approve_in_transaction(transaction, user_ref):
            snapshot = user_ref.get(transaction=transaction)
            current_data = snapshot.to_dict()
            new_bal = (current_data.get("stnBal", 0.0) or 0.0) + amount
            transaction.update(user_ref, {
                "stnBal": new_bal,
                "referralEarnings": 0.0,  # reset após pagamento
                "referralBonusPaid": True,
                "lastBonusPaidAt": datetime.now(timezone.utc),
            })

        approve_in_transaction(user_ref)

        await query.edit_message_text(
            f"✅ Bónus de *{amount:,.0f} STN* aprovado para *{name}*.",
            parse_mode="Markdown",
        )
        logger.info(f"Bónus referral aprovado: uid={uid} amount={amount}")

        # Notificar o user via Telegram (se tiver a conta vinculada)
        user_chat_id = get_chat_id_for_uid(uid)
        if user_chat_id:
            try:
                await query.get_bot().send_message(
                    chat_id=user_chat_id,
                    text=formatters.referral_bonus_approved_dm(amount),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Não foi possível notificar user {uid}: {e}")

    except Exception as e:
        logger.error(f"Erro ao aprovar bónus de {uid}: {e}")
        await query.edit_message_text(f"⛔ Erro ao processar: {e}")


async def _reject_bonus(uid: str, query) -> None:
    db = get_db()
    try:
        user_doc = db.collection("users").document(uid).get()
        name = user_doc.to_dict().get("name", uid) if user_doc.exists else uid
        # Rejeitar: apenas marca como revisto, não credita, não apaga
        # os ganhos (fica para histórico, admin pode rever mais tarde).
        db.collection("users").document(uid).update({
            "referralBonusRejectedAt": datetime.now(timezone.utc),
        })
        await query.edit_message_text(
            f"❌ Bónus de *{name}* rejeitado (ganhos mantidos para revisão).",
            parse_mode="Markdown",
        )
        logger.info(f"Bónus referral rejeitado: uid={uid}")
    except Exception as e:
        logger.error(f"Erro ao rejeitar bónus de {uid}: {e}")
        await query.edit_message_text(f"⛔ Erro: {e}")


# ══════════════════════════════════════
# SINAIS — APROVAÇÃO ADMIN
# ══════════════════════════════════════

async def send_signal_for_approval(bot: Bot, signal: dict, signal_id: str) -> None:
    """
    Envia um sinal detetado pelo engine para os admins aprovarem antes
    de publicar no canal. Chamado pelo signals_engine.
    """
    preview_msg = formatters.signal_admin_preview(signal, signal_id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Publicar", callback_data=f"{CB_SIGNAL_APPROVE}{signal_id}"),
            InlineKeyboardButton("❌ Descartar", callback_data=f"{CB_SIGNAL_REJECT}{signal_id}"),
        ]
    ])
    for admin_id in config.ADMIN_TELEGRAM_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=preview_msg,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning(f"Não foi possível enviar sinal ao admin {admin_id}: {e}")


async def handle_signal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler dos botões Publicar/Descartar sinal.
    Registado no bot como CallbackQueryHandler.
    """
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id
    if not config.is_admin(chat_id):
        await query.edit_message_text("⛔ Sem permissão.")
        return

    data = query.data

    if data.startswith(CB_SIGNAL_APPROVE):
        signal_id = data[len(CB_SIGNAL_APPROVE):]
        await _publish_signal(signal_id, query)

    elif data.startswith(CB_SIGNAL_REJECT):
        signal_id = data[len(CB_SIGNAL_REJECT):]
        await _discard_signal(signal_id, query)


async def _publish_signal(signal_id: str, query) -> None:
    if not CHANNEL_ID:
        await query.edit_message_text("⚠️ TELEGRAM_CHANNEL_ID não configurado.")
        return
    db = get_db()
    try:
        doc = db.collection("botSignals").document(signal_id).get()
        if not doc.exists:
            await query.edit_message_text("⚠️ Sinal não encontrado (já foi processado?).")
            return
        signal = doc.to_dict()

        # Publicar no canal
        public_msg = formatters.signal_public(signal)
        await query.get_bot().send_message(
            chat_id=CHANNEL_ID,
            text=public_msg,
            parse_mode="Markdown",
        )

        # Atualizar estado no Firestore
        db.collection("botSignals").document(signal_id).update({
            "status": "published",
            "published_at": datetime.now(timezone.utc),
            "published_by": query.from_user.id,
        })

        # Enviar também em DM a users com /sinais on (opt-in)
        await _push_signal_to_subscribers(query.get_bot(), public_msg)

        await query.edit_message_text(
            f"✅ Sinal publicado no canal e enviado aos subscritores.",
        )
        logger.info(f"Sinal {signal_id} publicado.")
    except Exception as e:
        logger.error(f"Erro ao publicar sinal {signal_id}: {e}")
        await query.edit_message_text(f"⛔ Erro: {e}")


async def _discard_signal(signal_id: str, query) -> None:
    db = get_db()
    try:
        db.collection("botSignals").document(signal_id).update({
            "status": "rejected",
            "rejected_at": datetime.now(timezone.utc),
            "rejected_by": query.from_user.id,
        })
        await query.edit_message_text("❌ Sinal descartado.")
        logger.info(f"Sinal {signal_id} descartado.")
    except Exception as e:
        logger.error(f"Erro ao descartar sinal {signal_id}: {e}")
        await query.edit_message_text(f"⛔ Erro: {e}")


async def resend_pending_signals(bot: Bot) -> int:
    """
    Reenvia todos os sinais com status 'pending' aos admins — útil quando
    um sinal fica "enterrado" no histórico do chat e o admin quer vê-los
    todos de relance, sem precisar de fazer scroll para trás. Chamado
    pelo botão "📡 Sinais pendentes" do /admin.
    Devolve quantos sinais estavam pendentes.
    """
    db = get_db()
    docs = (
        db.collection("botSignals")
        .where("status", "==", "pending")
        .stream()
    )
    count = 0
    for doc in docs:
        signal = doc.to_dict()
        await send_signal_for_approval(bot, signal, doc.id)
        count += 1
    return count


# ══════════════════════════════════════
# REMESSAS INTERNACIONAIS — APROVAÇÃO ADMIN
# ══════════════════════════════════════

async def send_pending_remittances_to_admins(bot: Bot) -> int:
    """
    Lê remessas com status 'pending' e envia a cada admin, com botões
    Aprovar/Rejeitar. Chamado pelo comando /admin (botão "Remessas
    pendentes") ou diretamente por /remessaspendentes.
    Devolve quantas remessas estavam pendentes.
    """
    db = get_db()
    docs = (
        db.collection("remittances")
        .where("status", "==", "pending")
        .stream()
    )
    count = 0
    for doc in docs:
        rem = doc.to_dict()
        rem_id = doc.id
        preview_msg = formatters.remessa_admin_preview(rem, rem_id)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Aprovar", callback_data=f"{CB_REMIT_APPROVE}{rem_id}"),
                InlineKeyboardButton("❌ Rejeitar", callback_data=f"{CB_REMIT_REJECT}{rem_id}"),
            ]
        ])
        for admin_id in config.ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id, text=preview_msg,
                    parse_mode="Markdown", reply_markup=keyboard,
                )
            except Exception as e:
                logger.warning(f"Não foi possível enviar remessa {rem_id} ao admin {admin_id}: {e}")
        count += 1

    if count == 0:
        for admin_id in config.ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text="✅ Sem remessas pendentes.")
            except Exception as e:
                logger.warning(f"Não foi possível contactar admin {admin_id}: {e}")
    return count


async def handle_remittance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler dos botões Aprovar/Rejeitar de remessa. Regista no bot
    como CallbackQueryHandler."""
    query = update.callback_query
    await query.answer()

    if not config.is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Sem permissão.")
        return

    data = query.data

    if data.startswith(CB_REMIT_APPROVE):
        rem_id = data[len(CB_REMIT_APPROVE):]
        await _approve_remittance(rem_id, query)

    elif data.startswith(CB_REMIT_REJECT):
        rem_id = data[len(CB_REMIT_REJECT):]
        await _reject_remittance(rem_id, query)


async def _approve_remittance(rem_id: str, query) -> None:
    """
    Espelha fbApproveRemittance() do index.html:
    - marca a remessa como 'confirmed'
    - regista a transação (type: remittance)
    - se a direção for 'receber', credita o saldo do user na moeda
      pedida (em 'enviar' o saldo já tinha sido reservado no momento
      do pedido, então não há nada a creditar aqui)
    """
    db = get_db()
    try:
        rem_ref = db.collection("remittances").document(rem_id)
        rem_doc = rem_ref.get()
        if not rem_doc.exists:
            await query.edit_message_text("⚠️ Remessa não encontrada (já foi processada?).")
            return
        rem = rem_doc.to_dict()
        if rem.get("status") != "pending":
            await query.edit_message_text(f"⚠️ Esta remessa já foi processada (status: {rem.get('status')}).")
            return

        direction = rem.get("direction", "enviar")
        cur = rem.get("cur", "EUR")
        amount = float(rem.get("amount", 0.0))
        fee = float(rem.get("fee", 0.0))
        uid = rem.get("uid")

        now = datetime.now(timezone.utc)
        rem_ref.update({
            "status": "confirmed",
            "approvedBy": query.from_user.id,
            "approvedAt": now,
        })

        taxa_stn = fee if cur == "STN" else fee * fx.fx_mid_rate(cur, "STN")
        db.collection("transactions").document().set({
            "uid": uid, "type": "remittance", "direction": direction, "cur": cur,
            "amount": amount, "fee": fee, "taxaSTN": taxa_stn,
            "country": rem.get("country", ""), "approvedBy": query.from_user.id,
            "ts": now,
        })

        if direction == "receber" and uid:
            field = fx.FX_BAL_FIELD.get(cur, "stnBal")
            net = amount - fee
            db.collection("users").document(uid).update({field: firestore_increment(net)})

        await query.edit_message_text(
            f"✅ Remessa aprovada — {fx.fmt_cur(cur, amount)} ({direction}).",
        )
        logger.info(f"Remessa {rem_id} aprovada: uid={uid} direction={direction} amount={amount} {cur}")

        chat_id = get_chat_id_for_uid(uid) if uid else None
        if chat_id:
            try:
                await query.get_bot().send_message(
                    chat_id=chat_id,
                    text=formatters.remessa_approved_dm(direction, cur, amount),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Não foi possível notificar user {uid} sobre remessa: {e}")

    except Exception as e:
        logger.error(f"Erro ao aprovar remessa {rem_id}: {e}")
        await query.edit_message_text(f"⛔ Erro ao processar: {e}")


async def _reject_remittance(rem_id: str, query) -> None:
    """
    Espelha fbRejectRemittance(): se a direção era 'enviar', devolve o
    saldo reservado (amount+fee) ao user — em 'receber' não há nada
    reservado para devolver, porque o saldo só seria creditado na
    aprovação.
    """
    db = get_db()
    try:
        rem_ref = db.collection("remittances").document(rem_id)
        rem_doc = rem_ref.get()
        if not rem_doc.exists:
            await query.edit_message_text("⚠️ Remessa não encontrada.")
            return
        rem = rem_doc.to_dict()
        if rem.get("status") != "pending":
            await query.edit_message_text(f"⚠️ Esta remessa já foi processada (status: {rem.get('status')}).")
            return

        direction = rem.get("direction", "enviar")
        cur = rem.get("cur", "EUR")
        amount = float(rem.get("amount", 0.0))
        fee = float(rem.get("fee", 0.0))
        uid = rem.get("uid")
        now = datetime.now(timezone.utc)

        rem_ref.update({
            "status": "rejected",
            "approvedBy": query.from_user.id,
            "approvedAt": now,
        })

        if direction == "enviar" and uid:
            field = fx.FX_BAL_FIELD.get(cur, "stnBal")
            db.collection("users").document(uid).update({field: firestore_increment(amount + fee)})

        await query.edit_message_text("❌ Remessa rejeitada.")
        logger.info(f"Remessa {rem_id} rejeitada: uid={uid}")

        chat_id = get_chat_id_for_uid(uid) if uid else None
        if chat_id:
            try:
                await query.get_bot().send_message(
                    chat_id=chat_id,
                    text=formatters.remessa_rejected_dm(direction, cur, amount),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Não foi possível notificar user {uid} sobre rejeição: {e}")

    except Exception as e:
        logger.error(f"Erro ao rejeitar remessa {rem_id}: {e}")
        await query.edit_message_text(f"⛔ Erro: {e}")


# ══════════════════════════════════════
# DEPÓSITO DE CRIPTO — CONFIRMAÇÃO ADMIN
# ══════════════════════════════════════
#
# IMPORTANTE: ao contrário do bónus e da remessa, este fluxo não pode
# ser aprovado com um único clique — o admin precisa de abrir o block
# explorer, confirmar o TXID, e SÓ DEPOIS sabe o valor exato em STN a
# creditar (o preço da cripto no momento da confirmação é o que conta,
# não o do momento do pedido). Por isso, o botão inline só cobre a
# REJEIÇÃO; a aprovação é feita com o comando /confirmarcripto
# <ID> <valorSTN>, depois de o admin validar manualmente. Mesma lógica
# de fbConfirmCryptoDeposit() no index.html.

async def send_pending_crypto_deposits_to_admins(bot: Bot) -> int:
    """Lê depósitos de cripto com status 'pending' e envia a cada admin.
    Devolve quantos estavam pendentes."""
    db = get_db()
    docs = (
        db.collection("cryptoDeposits")
        .where("status", "==", "pending")
        .stream()
    )
    count = 0
    for doc in docs:
        dep = doc.to_dict()
        dep_id = doc.id
        preview_msg = formatters.crypto_deposit_admin_preview(dep, dep_id)
        cur = fx.CRYPTO_ADDRESSES.get(dep.get("coin", ""), {})
        explorer_url = f"{cur.get('explorer','')}{dep.get('txid','')}" if cur.get("explorer") else None
        keyboard_rows = []
        if explorer_url:
            keyboard_rows.append([InlineKeyboardButton("🔍 Ver no Block Explorer", url=explorer_url)])
        keyboard_rows.append([InlineKeyboardButton("❌ Rejeitar", callback_data=f"{CB_CRYPTODEP_REJECT}{dep_id}")])
        keyboard = InlineKeyboardMarkup(keyboard_rows)
        for admin_id in config.ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id, text=preview_msg,
                    parse_mode="Markdown", reply_markup=keyboard,
                )
            except Exception as e:
                logger.warning(f"Não foi possível enviar depósito {dep_id} ao admin {admin_id}: {e}")
        count += 1

    if count == 0:
        for admin_id in config.ADMIN_TELEGRAM_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text="✅ Sem depósitos de cripto pendentes.")
            except Exception as e:
                logger.warning(f"Não foi possível contactar admin {admin_id}: {e}")
    return count


async def handle_crypto_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do botão Rejeitar de depósito cripto. A aprovação passa
    pelo comando /confirmarcripto, não por callback."""
    query = update.callback_query
    await query.answer()

    if not config.is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Sem permissão.")
        return

    data = query.data
    if data.startswith(CB_CRYPTODEP_REJECT):
        dep_id = data[len(CB_CRYPTODEP_REJECT):]
        await _reject_crypto_deposit(dep_id, query)


async def confirm_crypto_deposit(dep_id: str, admin_chat_id: int, amount_stn_gross: float) -> dict:
    """
    Chamado pelo comando /confirmarcripto <ID> <valorSTN>. Espelha
    fbConfirmCryptoDeposit(): calcula a taxa de 1% (mesma taxa dos
    outros depósitos), credita o líquido em STN, regista a transação,
    e notifica o user por DM.

    Devolve um dict {ok: bool, message: str, ...} em vez de levantar
    exceção — o comando no bot.py decide como mostrar o resultado.
    """
    db = get_db()
    dep_ref = db.collection("cryptoDeposits").document(dep_id)
    dep_doc = dep_ref.get()
    if not dep_doc.exists:
        return {"ok": False, "message": "Depósito não encontrado."}

    dep = dep_doc.to_dict()
    if dep.get("status") != "pending":
        return {"ok": False, "message": f"Este depósito já foi processado (status: {dep.get('status')})."}

    uid = dep.get("uid")
    coin = dep.get("coin", "—")
    if not uid:
        return {"ok": False, "message": "Depósito sem uid associado — não é possível creditar."}

    fee = amount_stn_gross * 0.01  # mesma taxa de 1% dos outros depósitos
    amount_net = amount_stn_gross - fee
    now = datetime.now(timezone.utc)

    dep_ref.update({
        "status": "confirmed",
        "confirmedBy": admin_chat_id,
        "confirmedAt": now,
        "amountSTN": amount_stn_gross,
        "fee": fee,
        "amountNet": amount_net,
    })
    db.collection("users").document(uid).update({"stnBal": firestore_increment(amount_net)})
    db.collection("transactions").document().set({
        "uid": uid, "type": "deposit", "method": "crypto", "coin": coin,
        "network": dep.get("network", ""), "txid": dep.get("txid", ""),
        "amount": amount_stn_gross, "fee": fee, "amountNet": amount_net,
        "confirmedBy": admin_chat_id, "depositId": dep_id, "ts": now,
    })

    logger.info(f"Depósito cripto {dep_id} confirmado: uid={uid} coin={coin} amountNet={amount_net} STN")

    # A notificação ao user é enviada pelo próprio comando no bot.py
    # (confirmarcripto_command), que já tem acesso a context.bot — esta
    # função devolve só os dados necessários (chat_id, coin, amount_net)
    # em vez de tentar mandar a mensagem a partir daqui.
    chat_id = get_chat_id_for_uid(uid)

    return {
        "ok": True, "message": "Depósito confirmado.", "uid": uid, "coin": coin,
        "amount_net": amount_net, "chat_id": chat_id,
    }


async def _reject_crypto_deposit(dep_id: str, query) -> None:
    db = get_db()
    try:
        dep_ref = db.collection("cryptoDeposits").document(dep_id)
        dep_doc = dep_ref.get()
        if not dep_doc.exists:
            await query.edit_message_text("⚠️ Depósito não encontrado.")
            return
        dep = dep_doc.to_dict()
        if dep.get("status") != "pending":
            await query.edit_message_text(f"⚠️ Este depósito já foi processado (status: {dep.get('status')}).")
            return

        dep_ref.update({
            "status": "rejected",
            "confirmedBy": query.from_user.id,
            "confirmedAt": datetime.now(timezone.utc),
        })
        await query.edit_message_text("❌ Depósito rejeitado.")
        logger.info(f"Depósito cripto {dep_id} rejeitado.")

        uid = dep.get("uid")
        chat_id = get_chat_id_for_uid(uid) if uid else None
        if chat_id:
            try:
                await query.get_bot().send_message(
                    chat_id=chat_id,
                    text=formatters.crypto_deposit_rejected_dm(dep.get("coin", "—")),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Não foi possível notificar user {uid} sobre rejeição: {e}")

    except Exception as e:
        logger.error(f"Erro ao rejeitar depósito {dep_id}: {e}")
        await query.edit_message_text(f"⛔ Erro: {e}")


# ══════════════════════════════════════
# POST LIVRE NO CANAL
# ══════════════════════════════════════

async def post_to_channel(bot: Bot, text: str) -> bool:
    """
    Publica texto livre no canal principal — usado pelo comando /postar,
    para o admin conseguir publicar algo na hora sem sair do Telegram
    do bot para o canal diretamente.
    """
    if not CHANNEL_ID:
        logger.error("post_to_channel: TELEGRAM_CHANNEL_ID não configurado.")
        return False
    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")
        return True
    except Exception as e:
        logger.error(f"Erro ao publicar no canal: {e}")
        return False


async def _push_signal_to_subscribers(bot: Bot, msg: str) -> None:
    """Envia o sinal em DM a todos os users com notificações ativas."""
    db = get_db()
    try:
        subs = (
            db.collection("telegramLinks")
            .where("signalsEnabled", "==", True)
            .stream()
        )
        for sub in subs:
            chat_id = sub.to_dict().get("chat_id")
            if not chat_id:
                continue
            try:
                await bot.send_message(
                    chat_id=chat_id, text=msg, parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"DM de sinal falhou para chat_id={chat_id}: {e}")
    except Exception as e:
        logger.error(f"Erro ao enviar sinal para subscritores: {e}")
