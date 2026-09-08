"""
alerts.py

Listeners em tempo real (onSnapshot) via Firebase Admin SDK.
Monitoriza depósitos, levantamentos e KYC pendentes e envia
alertas imediatos ao admin via Telegram.

Também contém o job de resumo diário personalizado para cada
utilizador vinculado, e o job de alertas de preço.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from threading import Thread

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from firestore_client import get_db
import config

logger = logging.getLogger("cless_bot.alerts")


# ══════════════════════════════════════
# HELPERS
# ══════════════════════════════════════

def _fmt_stn(v: float) -> str:
    return f"{v:,.0f} STN"

def _fmt_ts(ts) -> str:
    if ts is None:
        return "—"
    try:
        if hasattr(ts, "ToDatetime"):
            dt = ts.ToDatetime()
        elif hasattr(ts, "toDatetime"):
            dt = ts.toDatetime()
        else:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc) if isinstance(ts, (int, float)) else ts
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return "—"


async def _notify_admins(bot: Bot, text: str, keyboard=None) -> None:
    """Envia mensagem a todos os admins configurados."""
    for admin_id in config.ADMIN_TELEGRAM_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning(f"Erro a notificar admin {admin_id}: {e}")


# ══════════════════════════════════════
# LISTENER DE DEPÓSITOS
# ══════════════════════════════════════

def start_deposit_listener(bot: Bot, loop: asyncio.AbstractEventLoop) -> None:
    """
    Escuta a coleção 'deposits' em tempo real.
    Quando aparece um novo documento com status='pending',
    notifica o admin imediatamente.
    """
    db = get_db()
    seen_ids: set[str] = set()

    # Pre-popular com os já existentes para não notificar os antigos ao arrancar
    existing = db.collection("deposits").where(
        "status", "==", "pending"
    ).stream()
    for doc in existing:
        seen_ids.add(doc.id)
    logger.info(f"Deposit listener: {len(seen_ids)} depósitos pendentes pré-carregados.")

    def on_snapshot(col_snapshot, changes, read_time):
        for change in changes:
            if change.type.name != "ADDED":
                continue
            doc_id = change.document.id
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            d = change.document.to_dict()
            if d.get("status") != "pending":
                continue

            amount   = d.get("amount", 0)
            name     = d.get("userName", "Desconhecido")
            method   = d.get("method", "—")
            sao_ref  = d.get("saoRef", "—")
            ts_label = _fmt_ts(d.get("ts"))

            text = (
                f"💰 *Novo Depósito Pendente!*\n\n"
                f"👤 Utilizador: *{name}*\n"
                f"💵 Valor: *{_fmt_stn(amount)}*\n"
                f"📱 Método: {method}\n"
                f"🔖 Ref SAO: `{sao_ref}`\n"
                f"🕐 Hora: {ts_label}\n\n"
                f"⚡ Confirma no painel admin do app para creditar o saldo."
            )
            asyncio.run_coroutine_threadsafe(
                _notify_admins(bot, text), loop
            )

    db.collection("deposits").on_snapshot(on_snapshot)
    logger.info("✅ Listener de depósitos activo.")


# ══════════════════════════════════════
# LISTENER DE LEVANTAMENTOS
# ══════════════════════════════════════

def start_withdrawal_listener(bot: Bot, loop: asyncio.AbstractEventLoop) -> None:
    """
    Escuta a coleção raiz 'withdrawals' em tempo real.
    Notifica o admin quando aparece um novo levantamento pendente.
    """
    db = get_db()
    seen_ids: set[str] = set()

    existing = db.collection("withdrawals").where(
        "status", "==", "pending"
    ).stream()
    for doc in existing:
        seen_ids.add(doc.id)
    logger.info(f"Withdrawal listener: {len(seen_ids)} levantamentos pré-carregados.")

    def on_snapshot(col_snapshot, changes, read_time):
        for change in changes:
            if change.type.name != "ADDED":
                continue
            doc_id = change.document.id
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            d = change.document.to_dict()
            if d.get("status") != "pending":
                continue

            amount  = d.get("amount", 0)
            fee     = d.get("fee", 0)
            net     = d.get("net", 0)
            email   = d.get("email", "—")
            method  = d.get("method", "—")
            dest    = d.get("destination", "—")
            ts_label = _fmt_ts(d.get("ts"))

            text = (
                f"🏦 *Pedido de Levantamento!*\n\n"
                f"👤 Email: `{email}`\n"
                f"💵 Valor bruto: *{_fmt_stn(amount)}*\n"
                f"💸 Taxa: {_fmt_stn(fee)}\n"
                f"✅ Valor líquido a enviar: *{_fmt_stn(net)}*\n"
                f"📱 Método: {method}\n"
                f"📍 Destino: `{dest}`\n"
                f"🕐 Hora: {ts_label}\n\n"
                f"⚡ Envia o pagamento e aprova no painel admin."
            )
            asyncio.run_coroutine_threadsafe(
                _notify_admins(bot, text), loop
            )

    db.collection("withdrawals").on_snapshot(on_snapshot)
    logger.info("✅ Listener de levantamentos activo.")


# ══════════════════════════════════════
# LISTENER DE KYC
# ══════════════════════════════════════

def start_kyc_listener(bot: Bot, loop: asyncio.AbstractEventLoop) -> None:
    """
    Escuta a coleção 'kycRequests' em tempo real.
    Notifica o admin quando aparece um novo pedido de KYC.
    """
    db = get_db()
    seen_ids: set[str] = set()

    existing = db.collection("kycRequests").where(
        "status", "==", "pending"
    ).stream()
    for doc in existing:
        seen_ids.add(doc.id)
    logger.info(f"KYC listener: {len(seen_ids)} pedidos KYC pré-carregados.")

    def on_snapshot(col_snapshot, changes, read_time):
        for change in changes:
            if change.type.name != "ADDED":
                continue
            doc_id = change.document.id
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            d = change.document.to_dict()
            if d.get("status") != "pending":
                continue

            name     = d.get("userName", "Desconhecido")
            email    = d.get("userEmail", d.get("email", "—"))
            ts_label = _fmt_ts(d.get("submittedAt"))

            text = (
                f"🪪 *Novo Pedido KYC!*\n\n"
                f"👤 Nome: *{name}*\n"
                f"📧 Email: `{email}`\n"
                f"🕐 Submetido: {ts_label}\n\n"
                f"📋 Acede ao painel admin do app para verificar os documentos e aprovar."
            )
            asyncio.run_coroutine_threadsafe(
                _notify_admins(bot, text), loop
            )

    db.collection("kycRequests").on_snapshot(on_snapshot)
    logger.info("✅ Listener de KYC activo.")


# ══════════════════════════════════════
# RESUMO DIÁRIO PERSONALIZADO
# ══════════════════════════════════════

async def send_daily_summary(bot: Bot) -> None:
    """
    Envia a cada utilizador vinculado um resumo matinal personalizado.
    Corre diariamente às 08:00 UTC.
    """
    from scheduler import _fetch_daily_leaders, _day_key, _week_key, _month_key

    db = get_db()
    now = datetime.now(timezone.utc)
    day_key   = f"day_{_day_key()}"

    # Buscar todos os utilizadores vinculados ao bot
    links = list(db.collection("telegramLinks")
                   .where("chat_id", "!=", None)
                   .stream())

    logger.info(f"Resumo diário: a enviar para {len(links)} utilizadores.")

    for link_doc in links:
        link = link_doc.to_dict()
        chat_id = link.get("chat_id")
        uid     = link.get("uid")
        if not chat_id or not uid:
            continue

        try:
            # Dados do utilizador
            user_doc = db.collection("users").document(uid).get()
            if not user_doc.exists:
                continue
            u = user_doc.to_dict()
            name    = u.get("name", "trader")
            stn_bal = u.get("stnBal", 0.0) or 0.0

            # Dados do leaderboard
            lb_doc = db.collection("leaderboard").document(uid).get()
            lb     = lb_doc.to_dict() if lb_doc.exists else {}
            pnl_total = lb.get("pnl", 0.0) or 0.0
            pnl_day   = lb.get(day_key, 0.0) or 0.0
            wins      = lb.get("wins", 0) or 0
            losses    = lb.get("losses", 0) or 0

            pnl_day_sign  = "+" if pnl_day >= 0 else ""
            pnl_total_sign = "+" if pnl_total >= 0 else ""
            pnl_emoji = "📈" if pnl_day >= 0 else "📉"

            # Posições abertas
            port = u.get("port", {}) or {}
            open_pos = [c for c, p in port.items() if p.get("qty", 0) > 0]
            pos_str = f"📂 Posições abertas: {', '.join(open_pos)}\n" if open_pos else ""

            # Saudação por hora
            hour = now.hour
            greeting = "🌅 Bom dia" if hour < 12 else ("🌆 Boa tarde" if hour < 18 else "🌙 Boa noite")

            msg = (
                f"{greeting}, *{name}*! 💎\n\n"
                f"📊 *O teu resumo de hoje:*\n\n"
                f"💰 Saldo disponível: *{stn_bal:,.0f} STN*\n"
                f"{pnl_emoji} PnL ontem: *{pnl_day_sign}{pnl_day:,.0f} STN*\n"
                f"📈 PnL total: *{pnl_total_sign}{pnl_total:,.0f} STN*\n"
                f"🏆 Trades: {wins + losses} ({wins}W / {losses}L)\n"
                f"{pos_str}\n"
                f"🚀 *O mercado está aberto. Boas oportunidades!*\n\n"
                f"_Usa /ranking para veres onde estás hoje._"
            )

            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
            await asyncio.sleep(0.3)  # rate limit

        except Exception as e:
            logger.warning(f"Resumo diário para {uid}: {e}")


# ══════════════════════════════════════
# ALERTAS DE PREÇO
# ══════════════════════════════════════

async def check_price_alerts(bot: Bot, prices: dict) -> None:
    """
    Verifica alertas de preço definidos pelos utilizadores.
    'prices' é um dict {coin: price_eur} obtido pela Binance.
    Chamado pelo job periódico do scheduler.
    """
    if not prices:
        return

    db = get_db()
    from google.cloud.firestore_v1 import FieldFilter

    try:
        alerts = list(
            db.collection("alerts")
            .where(filter=FieldFilter("triggered", "==", False))
            .stream()
        )
    except Exception as e:
        logger.warning(f"check_price_alerts: erro a ler alertas: {e}")
        return

    for alert_doc in alerts:
        a = alert_doc.to_dict()
        coin      = a.get("coin", "")
        target    = a.get("targetPrice", 0.0)
        direction = a.get("direction", "above")  # "above" ou "below"
        uid       = a.get("uid", "")

        current = prices.get(coin)
        if not current or not target:
            continue

        triggered = (
            (direction == "above" and current >= target) or
            (direction == "below" and current <= target)
        )
        if not triggered:
            continue

        # Marcar como disparado
        try:
            alert_doc.reference.update({"triggered": True, "triggeredAt": datetime.now(timezone.utc)})
        except Exception:
            pass

        # Notificar o utilizador
        from account_linking import get_chat_id_for_uid
        chat_id = get_chat_id_for_uid(uid)
        if not chat_id:
            continue

        dir_label = "subiu acima de" if direction == "above" else "desceu abaixo de"
        coin_emoji = {"BTC": "₿", "ETH": "⟠", "BNB": "🔶", "SOL": "◎",
                      "XRP": "✕", "ADA": "₳", "DOGE": "🐕", "AVAX": "🔺"}.get(coin, "🪙")

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔔 *Alerta de Preço Atingido!*\n\n"
                    f"{coin_emoji} *{coin}* {dir_label} *{target:,.2f} €*\n"
                    f"💹 Preço actual: *{current:,.2f} €*\n\n"
                    f"_Abre o app para agir agora._"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Alerta de preço para {uid}: {e}")


# ══════════════════════════════════════
# ARRANQUE DOS LISTENERS
# ══════════════════════════════════════

def start_all_listeners(bot: Bot, loop: asyncio.AbstractEventLoop) -> None:
    """
    Inicia todos os listeners em threads separadas (onSnapshot é blocking).
    Chamado uma vez no arranque do bot.
    """
    Thread(target=start_deposit_listener,    args=(bot, loop), daemon=True).start()
    Thread(target=start_withdrawal_listener, args=(bot, loop), daemon=True).start()
    Thread(target=start_kyc_listener,        args=(bot, loop), daemon=True).start()
    logger.info("🚀 Todos os listeners de alertas iniciados.")
