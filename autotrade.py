"""
autotrade.py

Gestão completa do autotrade assistido:
  - Ativação/desativação pelo utilizador (/autotrade on/off)
  - Proposta de trade enviada por DM para confirmação
  - Execução no Firestore via Admin SDK (mesmo padrão do fbPlaceOrder do app)
  - Circuit breaker: pausa automática após 3 perdas seguidas
  - Saldo do autotrade fisicamente separado do stnBal de trading manual
    para evitar race conditions entre app e bot

Modelo: bot decide tudo (entry, size, SL/TP) → envia proposta ao user
→ user confirma com 1 clique → bot executa → SL/TP corre automaticamente
via sltp_engine.py sem nova confirmação.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta

from google.cloud import firestore as fs
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from firestore_client import get_db
from account_linking import get_chat_id_for_uid
import formatters

logger = logging.getLogger("cless_bot.autotrade")

# Constantes de gestão de risco — nunca alteradas pelo utilizador
MAX_POSITION_PCT  = 0.15   # máx 15% do allocatedSTN por trade
MAX_CONSEC_LOSSES = 3      # circuit breaker após 3 perdas seguidas
# Taxa de corretagem por tier — igual ao plano mostrado no app (Planos & Benefícios).
# Tiers guardados em users/{uid}.tier: "free" (default), "silver", "gold", "diamond".
TIER_FEE_RATES = {
    "free":    0.005,
    "silver":  0.003,
    "gold":    0.002,
    "diamond": 0.001,
}
FEE_DIAMOND       = 0.001  # mantido por compatibilidade — usar get_user_fee_rate() em vez disto
STN_EUR           = 24.5   # taxa de câmbio (mesma constante do app)
SPREAD            = 0.003  # 0.3% spread (mesma constante do app)

# Bónus mensal de performance — iguais ao app (MONTHLY_BONUS_THRESHOLD/AMOUNT no index.html)
MONTHLY_BONUS_THRESHOLD = 500
MONTHLY_BONUS_AMOUNT    = 100

# Validade da proposta — passado isto, o SL/TP calculados já podem não
# fazer sentido (o preço mexeu-se entretanto), por isso a aprovação deixa
# de ser aceite e a oportunidade "expira".
PROPOSAL_VALIDITY_MINUTES = 15

CB_AUTOTRADE_CONFIRM = "at_confirm:"
CB_AUTOTRADE_REJECT  = "at_reject:"


# ══════════════════════════════════════
# LEITURA DE ESTADO
# ══════════════════════════════════════

def get_autotrade_settings(uid: str) -> dict | None:
    """Lê as definições de autotrade de um utilizador. None = não ativado."""
    db = get_db()
    doc = db.collection("autoTradeSettings").document(uid).get()
    return doc.to_dict() if doc.exists else None


def get_active_autotrade_users() -> list[dict]:
    """
    Devolve lista de utilizadores com autotrade ativo e sem posição
    aberta de momento — prontos para receber uma nova proposta.
    """
    db = get_db()
    docs = (
        db.collection("autoTradeSettings")
        .where("active", "==", True)
        .where("status", "==", "awaiting_signal")
        .stream()
    )
    users = []
    for doc in docs:
        data = doc.to_dict()
        data["uid"] = doc.id
        users.append(data)
    return users


# ══════════════════════════════════════
# ATIVAÇÃO / DESATIVAÇÃO
# ══════════════════════════════════════

def activate_autotrade(uid: str, allocated_stn: float) -> bool:
    """
    Ativa o autotrade para um utilizador.
    Faz transferência atómica de stnBal → autoTradeSettings.allocatedSTN,
    para que o saldo do bot e o saldo de trading manual nunca colidam.
    Devolve True se OK, False se saldo insuficiente.
    """
    db = get_db()
    user_ref  = db.collection("users").document(uid)
    trade_ref = db.collection("autoTradeSettings").document(uid)

    @fs.transactional
    def _activate(transaction):
        user_snap = user_ref.get(transaction=transaction)
        if not user_snap.exists:
            raise ValueError("Utilizador não encontrado.")
        user_data = user_snap.to_dict()
        current_bal = user_data.get("stnBal", 0.0) or 0.0
        if current_bal < allocated_stn:
            raise ValueError(
                f"Saldo insuficiente: tens {current_bal:.0f} STN, "
                f"tentaste alocar {allocated_stn:.0f} STN."
            )
        # Debita do saldo principal
        transaction.update(user_ref, {"stnBal": current_bal - allocated_stn})
        # Cria/atualiza as definições de autotrade
        transaction.set(trade_ref, {
            "uid": uid,
            "active": True,
            "allocatedSTN": allocated_stn,
            "availableSTN": allocated_stn,
            "status": "awaiting_signal",
            "consecutiveLosses": 0,
            "totalTrades": 0,
            "totalPnlSTN": 0.0,
            "activated_at": datetime.now(timezone.utc),
        })

    try:
        transaction = db.transaction()
        _activate(transaction)
        logger.info(f"Autotrade ativado: uid={uid} allocated={allocated_stn}")
        return True
    except ValueError as e:
        logger.warning(f"activate_autotrade falhou para {uid}: {e}")
        raise
    except Exception as e:
        logger.error(f"Erro ao ativar autotrade de {uid}: {e}")
        return False


def deactivate_autotrade(uid: str) -> float:
    """
    Desativa o autotrade e devolve o saldo disponível restante ao stnBal
    principal. Devolve o montante devolvido (para informar o user).
    Posições abertas pelo bot são fechadas pelo sltp_engine separadamente.
    """
    db = get_db()
    user_ref  = db.collection("users").document(uid)
    trade_ref = db.collection("autoTradeSettings").document(uid)

    @fs.transactional
    def _deactivate(transaction):
        trade_snap = trade_ref.get(transaction=transaction)
        if not trade_snap.exists:
            return 0.0
        trade_data = trade_snap.to_dict()
        available  = trade_data.get("availableSTN", 0.0) or 0.0

        user_snap  = user_ref.get(transaction=transaction)
        current_bal = user_snap.to_dict().get("stnBal", 0.0) or 0.0 if user_snap.exists else 0.0

        # Devolve saldo disponível ao principal
        transaction.update(user_ref, {"stnBal": current_bal + available})
        transaction.update(trade_ref, {
            "active": False,
            "availableSTN": 0.0,
            "status": "inactive",
            "deactivated_at": datetime.now(timezone.utc),
        })
        return available

    try:
        transaction = db.transaction()
        returned = _deactivate(transaction)
        logger.info(f"Autotrade desativado: uid={uid} devolvido={returned}")
        return returned
    except Exception as e:
        logger.error(f"Erro ao desativar autotrade de {uid}: {e}")
        return 0.0


def _fee_rate_from_user_data(user_data: dict | None) -> float:
    """
    Deriva a taxa de corretagem a partir de um dict users/{uid} já lido
    (sem round-trip novo ao Firestore). Usada dentro da transação de
    execute_trade, que já lê este mesmo documento como user_snap.
    """
    tier = ((user_data or {}).get("tier", "free")) or "free"
    return TIER_FEE_RATES.get(tier.lower(), TIER_FEE_RATES["free"])


def get_user_fee_rate(uid: str) -> float:
    """
    Lê o tier atual do utilizador em users/{uid}.tier e devolve a taxa
    de corretagem correspondente (igual à tabela do app: Planos & Benefícios).
    Default 'free' se o campo não existir.

    Nota: dentro de execute_trade/_execute_trade_sync NÃO uses esta função —
    o documento users/{uid} já é lido na transação (user_snap); usa
    _fee_rate_from_user_data(user_data) para poupar um round-trip ao Firestore.
    Esta função fica disponível para quem precisar do fee_rate isoladamente,
    fora do caminho de execução do trade.
    """
    db = get_db()
    doc = db.collection("users").document(uid).get()
    tier = (doc.to_dict().get("tier", "free") if doc.exists else "free") or "free"
    return TIER_FEE_RATES.get(tier.lower(), TIER_FEE_RATES["free"])


# ══════════════════════════════════════
# PROPOSTA DE TRADE
# ══════════════════════════════════════

async def send_trade_proposal(bot: Bot, uid: str, signal: dict) -> str | None:
    """
    Calcula o tamanho da posição (máx 15% do allocatedSTN),
    guarda a proposta no Firestore e envia DM ao utilizador
    com botões Aprovar/Rejeitar.
    Devolve o proposal_id, ou None se não foi possível enviar.
    """
    settings = get_autotrade_settings(uid)
    if not settings or not settings.get("active"):
        return None

    available = settings.get("availableSTN", 0.0)
    size_stn  = min(available * MAX_POSITION_PCT, available)
    if size_stn < 10:
        logger.info(f"Proposta ignorada para {uid}: saldo disponível insuficiente ({available:.0f} STN)")
        return None

    entry_eur = signal.get("entry", 0.0)
    sl_eur    = signal.get("sl", 0.0)
    tp_eur    = signal.get("tp", 0.0)
    rr        = signal.get("rr", 0.0)
    coin      = signal.get("coin", "BTC")
    direction = signal.get("direction", "BUY")

    proposal_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=PROPOSAL_VALIDITY_MINUTES)

    db = get_db()
    db.collection("autoTradeProposals").document(proposal_id).set({
        "uid": uid,
        "coin": coin,
        "direction": direction,
        "entry": entry_eur,
        "sl": sl_eur,
        "tp": tp_eur,
        "rr": rr,
        "size_stn": size_stn,
        "allocated_stn": available,
        "status": "pending",
        "created_at": now,
        "expires_at": expires_at,
    })

    proposal = {
        "coin": coin, "direction": direction,
        "entry": entry_eur, "sl": sl_eur, "tp": tp_eur,
        "rr": rr, "size_stn": size_stn, "allocated_stn": available,
    }
    msg = formatters.signal_autotrade_proposal(proposal)
    msg += (
        f"\n\n⏰ _Válido por {PROPOSAL_VALIDITY_MINUTES} min "
        f"(até às {expires_at.strftime('%H:%M')} UTC) — o preço pode mudar, "
        f"não deixes a proposta a marinar!_ 😉"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Aprovar", callback_data=f"{CB_AUTOTRADE_CONFIRM}{proposal_id}"),
        InlineKeyboardButton("❌ Recusar", callback_data=f"{CB_AUTOTRADE_REJECT}{proposal_id}"),
    ]])

    chat_id = get_chat_id_for_uid(uid)
    if not chat_id:
        logger.warning(f"Proposta criada mas utilizador {uid} não tem chat_id vinculado.")
        return None

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        logger.info(f"Proposta {proposal_id} enviada ao user {uid}")
        return proposal_id
    except Exception as e:
        logger.error(f"Erro ao enviar proposta a {uid}: {e}")
        return None


# ══════════════════════════════════════
# EXECUÇÃO DA ORDEM
# ══════════════════════════════════════

def _execute_trade_sync(proposal_id: str) -> dict:
    """
    Toda a parte SÍNCRONA e bloqueante de execute_trade (leituras e
    escritas no Firestore) — corre numa thread à parte via
    asyncio.to_thread (chamado por execute_trade), para nunca bloquear
    o loop principal do bot enquanto está à espera da rede do Firestore.
    Sem isto, um clique em Aprovar "congelava" o bot inteiro por instantes
    — incluindo para OUTROS users, scheduler, etc., já que tudo corre
    num único loop assíncrono.
    """
    db = get_db()
    prop_ref = db.collection("autoTradeProposals").document(proposal_id)
    prop_doc = prop_ref.get()

    if not prop_doc.exists:
        return {"status": "not_found"}

    prop = prop_doc.to_dict()
    if prop.get("status") != "pending":
        return {"status": "already_processed"}

    expires_at = prop.get("expires_at")
    if expires_at and datetime.now(timezone.utc) > expires_at:
        prop_ref.update({"status": "expired"})
        return {"status": "expired", "coin": prop.get("coin", "")}

    uid       = prop["uid"]
    coin      = prop["coin"]
    direction = prop["direction"]
    entry_eur = prop["entry"]
    sl_eur    = prop["sl"]
    tp_eur    = prop["tp"]
    size_stn  = prop["size_stn"]

    # Preço de execução real (com spread de compra, igual ao app)
    buy_price  = entry_eur * (1 + SPREAD)
    sell_price = entry_eur * (1 - SPREAD)
    exec_price = buy_price if direction == "BUY" else sell_price

    position_id = str(uuid.uuid4())[:8]
    # Preenchido dentro de _execute() — coin_amt/fee_rate dependem do
    # user_data, que só é lido dentro da transação (ver nota abaixo).
    calc = {}

    try:
        settings_ref = db.collection("autoTradeSettings").document(uid)
        user_ref     = db.collection("users").document(uid)

        @fs.transactional
        def _execute(transaction):
            settings_snap = settings_ref.get(transaction=transaction)
            if not settings_snap.exists:
                raise ValueError("Definições de autotrade não encontradas.")
            settings_data = settings_snap.to_dict()
            available = settings_data.get("availableSTN", 0.0)

            # Gate crítico: mesmo que uma segunda proposta tenha sido
            # enviada (scan não refaz esta verificação a meio do loop de
            # moedas), a execução em si nunca pode abrir uma 2ª posição
            # enquanto a anterior ainda está aberta.
            if settings_data.get("status") == "in_position":
                raise ValueError("Já tens uma posição aberta — fecha-a primeiro (/autotrade fechar).")

            if available < size_stn:
                raise ValueError(f"Saldo insuficiente: {available:.0f} STN disponíveis.")

            # Lê o portfolio atual do user — para fundir com uma posição já
            # existente no mesmo formato que o app usa (fbPlaceOrder/placeShort),
            # e assim o gráfico/portfólio mostrarem a posição do bot também.
            user_snap = user_ref.get(transaction=transaction)
            user_data = user_snap.to_dict() if user_snap.exists else {}

            # fee_rate/coin_amt calculados aqui — reaproveitando o mesmo
            # user_snap que já tínhamos de ler na mesma, em vez de um
            # get_user_fee_rate(uid) à parte (era um 2º round-trip ao
            # Firestore no mesmo documento, só para ler o tier).
            fee_rate = _fee_rate_from_user_data(user_data)
            stn_after_fee = size_stn * (1 - fee_rate)
            coin_amt = stn_after_fee / STN_EUR / exec_price if exec_price else 0
            if coin_amt <= 0:
                raise ValueError("Quantidade calculada inválida.")
            calc["fee_rate"] = fee_rate
            calc["coin_amt"] = coin_amt

            # avgEntry guardado ao preço MID (sem spread) — igual ao app,
            # para o PnL não realizado mostrar 0.00% no instante da abertura.
            entry_mid = entry_eur
            user_updates = {}

            if direction == "BUY":
                port = dict(user_data.get("port", {}) or {})
                prev = port.get(coin) or {"qty": 0.0, "avgEntry": 0.0, "totalCostEUR": 0.0}
                new_qty  = prev.get("qty", 0.0) + coin_amt
                new_cost = prev.get("totalCostEUR", 0.0) + coin_amt * entry_mid
                port[coin] = {
                    "qty": new_qty,
                    "avgEntry": new_cost / new_qty if new_qty else 0.0,
                    "totalCostEUR": new_cost,
                    "sl": sl_eur,
                    "tp": tp_eur,
                }
                user_updates["port"] = port
            else:
                shorts = dict(user_data.get("shorts", {}) or {})
                prev = shorts.get(coin) or {"qty": 0.0, "avgEntry": 0.0, "totalMarginSTN": 0.0}
                new_qty = prev.get("qty", 0.0) + coin_amt
                avg_entry = (
                    (prev.get("avgEntry", 0.0) * prev.get("qty", 0.0) + entry_mid * coin_amt) / new_qty
                    if new_qty else entry_mid
                )
                shorts[coin] = {
                    "qty": new_qty,
                    "avgEntry": avg_entry,
                    "totalMarginSTN": prev.get("totalMarginSTN", 0.0) + size_stn,
                    "sl": sl_eur,
                    "tp": tp_eur,
                    "openedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
                }
                user_updates["shorts"] = shorts

            # Debita o tamanho da posição do saldo disponível do bot
            transaction.update(settings_ref, {
                "availableSTN": available - size_stn,
                "status": "in_position",
                "totalTrades": fs.Increment(1),
            })

            # Espelha a posição no portfolio do user — gráfico/portfólio passam
            # a mostrar a posição aberta pelo bot, sem tocar no stnBal/vol
            # (esses continuam separados, geridos só por availableSTN).
            transaction.update(user_ref, user_updates)

            # Cria a posição aberta
            pos_ref = db.collection("autoTradePositions").document(position_id)
            transaction.set(pos_ref, {
                "uid": uid,
                "coin": coin,
                "direction": direction,
                "avgEntry": exec_price,
                "qty": coin_amt,
                "size_stn": size_stn,
                "sl": sl_eur,
                "tp": tp_eur,
                "fee_rate": fee_rate,
                "status": "open",
                "opened_at": datetime.now(timezone.utc),
            })

            # Marca a proposta como executada
            transaction.update(prop_ref, {
                "status": "executed",
                "position_id": position_id,
                "executed_at": datetime.now(timezone.utc),
            })

        transaction = db.transaction()
        _execute(transaction)
        coin_amt = calc["coin_amt"]

        logger.info(
            f"Trade executado: uid={uid} coin={coin} direction={direction} "
            f"qty={coin_amt:.6f} entry={exec_price:.2f} position_id={position_id}"
        )

        return {
            "status": "executed",
            "coin": coin, "direction": direction, "exec_price": exec_price,
            "coin_amt": coin_amt, "size_stn": size_stn,
            "sl_eur": sl_eur, "tp_eur": tp_eur,
        }
    except ValueError as e:
        return {"status": "value_error", "message": str(e)}
    except Exception as e:
        logger.error(f"Erro ao executar trade {proposal_id}: {e}")
        return {"status": "error", "message": str(e)}


async def execute_trade(proposal_id: str, query) -> bool:
    """
    Chamado quando o utilizador clica Aprovar. O trabalho pesado (Firestore)
    corre em `_execute_trade_sync`, numa thread à parte, para o bot
    continuar a responder a tudo o resto enquanto espera pela rede.
    """
    result = await asyncio.to_thread(_execute_trade_sync, proposal_id)
    status = result["status"]

    if status == "not_found":
        await query.edit_message_text("⚠️ Proposta não encontrada ou já processada.")
        return False

    if status == "already_processed":
        await query.edit_message_text("⚠️ Esta proposta já foi processada.")
        return False

    if status == "expired":
        coin_emoji = formatters.COIN_EMOJI.get(result.get("coin", ""), "🪙")
        await query.edit_message_text(
            f"😅 Ihh... chegaste tarde demais! {coin_emoji}\n\n"
            f"Essa oportunidade já fugiu — o preço mudou entretanto e "
            f"já não era seguro abrir com os níveis antigos. 🏃💨\n\n"
            f"Não fiques triste, o bot está sempre de olho no mercado — "
            f"a próxima proposta pode chegar já a seguir! 👀📈",
        )
        return False

    if status == "invalid_amount":
        await query.edit_message_text("⚠️ Quantidade calculada inválida. Proposta cancelada.")
        return False

    if status == "value_error":
        await query.edit_message_text(f"⚠️ {result['message']}")
        return False

    if status == "error":
        await query.edit_message_text(f"⛔ Erro: {result['message']}")
        return False

    # status == "executed"
    coin_emoji = formatters.COIN_EMOJI.get(result["coin"], "🪙")
    dir_label  = "COMPRA" if result["direction"] == "BUY" else "VENDA"
    dir_emoji  = "🟢" if result["direction"] == "BUY" else "🔴"

    await query.edit_message_text(
        f"✅ *Trade executado!*\n\n"
        f"{dir_emoji} {dir_label} — {coin_emoji} {result['coin']}\n"
        f"Entrada: {result['exec_price']:,.2f} €\n"
        f"Quantidade: {result['coin_amt']:.6f} {result['coin']}\n"
        f"Tamanho: {result['size_stn']:.0f} STN\n"
        f"SL: {result['sl_eur']:,.2f} €  |  TP: {result['tp_eur']:,.2f} €\n\n"
        f"_O bot monitoriza e fecha automaticamente ao atingir SL ou TP._",
        parse_mode="Markdown",
    )
    return True


def _reject_trade_sync(proposal_id: str) -> bool:
    """Parte síncrona de reject_trade — corre em thread à parte, mesmo motivo que _execute_trade_sync."""
    db = get_db()
    try:
        db.collection("autoTradeProposals").document(proposal_id).update({
            "status": "rejected",
            "rejected_at": datetime.now(timezone.utc),
        })
        return True
    except Exception as e:
        logger.error(f"Erro ao rejeitar proposta {proposal_id}: {e}")
        return False


async def reject_trade(proposal_id: str, query) -> None:
    """Utilizador recusou a proposta — marca como rejeitada."""
    ok = await asyncio.to_thread(_reject_trade_sync, proposal_id)
    if ok:
        await query.edit_message_text("❌ Proposta recusada. O bot continua a monitorizar oportunidades.")
    else:
        await query.edit_message_text("⛔ Erro ao recusar a proposta. Tenta novamente.")


# ══════════════════════════════════════
# CIRCUIT BREAKER
# ══════════════════════════════════════

async def register_trade_result(bot: Bot, uid: str, position_id: str, pnl_stn: float) -> None:
    """
    Chamado pelo sltp_engine quando uma posição fecha (automática ou manual).
    Atualiza o saldo disponível, remove a posição do portfolio do user,
    regista o PnL no leaderboard e na meta mensal/anual, e verifica o
    circuit breaker.
    """
    db = get_db()
    settings_ref = db.collection("autoTradeSettings").document(uid)
    user_ref     = db.collection("users").document(uid)

    @fs.transactional
    def _register(transaction):
        snap = settings_ref.get(transaction=transaction)
        if not snap.exists:
            return None
        data = snap.to_dict()
        available      = data.get("availableSTN", 0.0)
        consec_losses  = data.get("consecutiveLosses", 0)
        total_pnl      = data.get("totalPnlSTN", 0.0)

        pos_ref  = db.collection("autoTradePositions").document(position_id)
        pos_snap = pos_ref.get(transaction=transaction)
        pos_data  = pos_snap.to_dict() if pos_snap.exists else {}
        size_stn  = pos_data.get("size_stn", 0.0)
        coin      = pos_data.get("coin", "")
        direction = pos_data.get("direction", "BUY")
        qty       = pos_data.get("qty", 0.0)

        # Lê o portfolio do user para remover/reduzir a posição espelhada —
        # mesma leitura+escrita atómica que o placeOrder/placeShort do app faz.
        user_snap = user_ref.get(transaction=transaction)
        user_data = user_snap.to_dict() if user_snap.exists else {}

        user_updates = {}
        if coin:
            if direction == "BUY":
                port = dict(user_data.get("port", {}) or {})
                prev = port.get(coin)
                if prev:
                    remaining = max(0.0, prev.get("qty", 0.0) - qty)
                    if remaining < 0.000001:
                        port.pop(coin, None)
                    else:
                        ratio = remaining / prev.get("qty", 1.0) if prev.get("qty") else 0.0
                        port[coin] = {
                            **prev,
                            "qty": remaining,
                            "totalCostEUR": prev.get("totalCostEUR", 0.0) * ratio,
                        }
                    user_updates["port"] = port
            else:
                shorts = dict(user_data.get("shorts", {}) or {})
                prev = shorts.get(coin)
                if prev:
                    remaining = max(0.0, prev.get("qty", 0.0) - qty)
                    if remaining < 0.000001:
                        shorts.pop(coin, None)
                    else:
                        ratio = remaining / prev.get("qty", 1.0) if prev.get("qty") else 0.0
                        shorts[coin] = {
                            **prev,
                            "qty": remaining,
                            "totalMarginSTN": prev.get("totalMarginSTN", 0.0) * ratio,
                        }
                    user_updates["shorts"] = shorts

        # Verifica se ainda há OUTRAS posições abertas deste user (ex: abriu
        # 2 em simultâneo por um sinal duplo no mesmo scan). Sem isto, fechar
        # uma delas reescrevia o status para "awaiting_signal" mesmo com
        # outra posição ainda aberta — fazendo /autotrade fechar dizer
        # "não há posição aberta" quando na verdade havia.
        other_open_query = (
            db.collection("autoTradePositions")
            .where("uid", "==", uid)
            .where("status", "==", "open")
        )
        other_open = [
            d for d in other_open_query.stream(transaction=transaction)
            if d.id != position_id
        ]

        new_available = available + size_stn + pnl_stn
        new_consec    = 0 if pnl_stn >= 0 else consec_losses + 1
        paused        = new_consec >= MAX_CONSEC_LOSSES

        if other_open:
            new_status = "in_position"   # ainda há posição(ões) aberta(s) — não liberta o user
        elif paused:
            new_status = "paused"
        else:
            new_status = "awaiting_signal"

        transaction.update(settings_ref, {
            "availableSTN": max(0, new_available),
            "consecutiveLosses": new_consec,
            "totalPnlSTN": total_pnl + pnl_stn,
            "status": new_status,
            "lastTradeAt": datetime.now(timezone.utc),
        })
        if user_updates:
            transaction.update(user_ref, user_updates)

        return paused, new_consec, user_data

    try:
        transaction = db.transaction()
        result = _register(transaction)
        if result:
            paused, consec, user_data = result
            if paused:
                await _notify_circuit_breaker(bot, uid, consec)
            # Leaderboard + meta mensal/anual — passo separado, não crítico,
            # tal como no app (fbUpdateLeaderboard/fbCheckMonthlyBonus também
            # correm depois da escrita crítica do saldo).
            await _update_leaderboard_and_goals(bot, uid, pnl_stn, user_data)
    except Exception as e:
        logger.error(f"Erro ao registar resultado do trade {position_id}: {e}")


def _derive_display_name(user_data: dict) -> str:
    """Mesma lógica do app: @username se existir, senão 'Nome S.' abreviado."""
    username = user_data.get("username")
    if username:
        return f"@{username}"
    name = user_data.get("name", "") or ""
    words = [w for w in name.split(" ") if w]
    if not words:
        return "Anónimo"
    parts = [w if i == 0 else f"{w[0]}." for i, w in enumerate(words[:2])]
    return " ".join(parts)


async def _update_leaderboard_and_goals(bot: Bot, uid: str, pnl_stn: float, user_data: dict) -> None:
    """
    Espelha fbUpdateLeaderboard + fbCheckMonthlyBonus do app — mesmos
    campos e mesma lógica — para os trades do bot contarem no Top Trades
    e nas metas mensal/anual do Dashboard.
    """
    db = get_db()
    is_win  = pnl_stn > 0
    is_loss = pnl_stn < 0
    now = datetime.now(timezone.utc)

    day_key   = now.strftime("%Y-%m-%d")
    # getDay() do JS: 0=domingo…6=sábado. Python weekday(): 0=segunda…6=domingo.
    js_day    = (now.weekday() + 1) % 7
    week_key  = (now.date() - timedelta(days=js_day)).isoformat()
    month_key = now.strftime("%Y-%m")
    gained    = pnl_stn if is_win else 0.0

    display_name = _derive_display_name(user_data)

    try:
        db.collection("leaderboard").document(uid).set({
            "uid": uid,
            "displayName": display_name,
            "email": user_data.get("email", ""),
            "photoBase64": user_data.get("photoBase64"),
            "pnl": fs.Increment(pnl_stn),
            "totalGained": fs.Increment(gained),
            "wins": fs.Increment(1 if is_win else 0),
            "losses": fs.Increment(1 if is_loss else 0),
            "total": fs.Increment(1),
            "vol": user_data.get("vol", 0.0),
            "updatedAt": now,
            "lastTradeAt": now,
            f"day_{day_key}":   fs.Increment(gained),
            f"week_{week_key}": fs.Increment(gained),
            f"month_{month_key}": fs.Increment(gained),
        }, merge=True)
    except Exception as e:
        logger.error(f"Erro ao atualizar leaderboard de {uid}: {e}")

    await _check_monthly_bonus(bot, uid, pnl_stn, user_data)


async def _check_monthly_bonus(bot: Bot, uid: str, pnl_net_stn: float, user_data: dict) -> None:
    """
    Espelha fbCheckMonthlyBonus do app: só acumula meses com lucro líquido
    positivo (perdas não descontam a meta, igual ao comportamento atual
    do app), e paga o bónus de performance ao cruzar o threshold.
    """
    if pnl_net_stn <= 0:
        return

    db = get_db()
    user_ref = db.collection("users").document(uid)
    now = datetime.now(timezone.utc)
    month_key = now.strftime("%Y-%m")

    monthly_pnl = user_data.get("monthlyPnl", {}) or {}
    current     = monthly_pnl.get(month_key, {}) or {"pnl": 0.0, "bonusesPaid": 0}
    new_month_pnl  = current.get("pnl", 0.0) + pnl_net_stn
    bonuses_paid   = current.get("bonusesPaid", 0)
    bonuses_earned = int(new_month_pnl // MONTHLY_BONUS_THRESHOLD)
    bonuses_to_pay = bonuses_earned - bonuses_paid

    try:
        if bonuses_to_pay > 0:
            bonus_amount = bonuses_to_pay * MONTHLY_BONUS_AMOUNT
            user_ref.update({
                "stnBal": fs.Increment(bonus_amount),
                f"monthlyPnl.{month_key}.pnl": new_month_pnl,
                f"monthlyPnl.{month_key}.bonusesPaid": bonuses_earned,
            })
            db.collection("transactions").add({
                "uid": uid,
                "type": "monthly_bonus",
                "amount": bonus_amount,
                "pnlAchieved": new_month_pnl,
                "bonusesCount": bonuses_to_pay,
                "monthKey": month_key,
                "ts": now,
            })
            chat_id = get_chat_id_for_uid(uid)
            if chat_id:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🎉 *Bónus mensal de performance!*\n\n"
                        f"+{bonus_amount:.0f} STN por {new_month_pnl:.0f} STN "
                        f"de lucro líquido este mês!"
                    ),
                    parse_mode="Markdown",
                )
        else:
            user_ref.update({
                f"monthlyPnl.{month_key}.pnl": new_month_pnl,
                f"monthlyPnl.{month_key}.bonusesPaid": bonuses_paid,
            })
    except Exception as e:
        logger.error(f"Erro ao verificar bónus mensal de {uid}: {e}")


async def _notify_circuit_breaker(bot: Bot, uid: str, consec_losses: int) -> None:
    """Notifica o utilizador que o autotrade foi pausado pelo circuit breaker."""
    from account_linking import get_chat_id_for_uid
    chat_id = get_chat_id_for_uid(uid)
    if not chat_id:
        return
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ *Autotrade pausado automaticamente*\n\n"
                f"Ocorreram {consec_losses} perdas consecutivas. "
                f"O bot pausou-se para proteger o teu capital.\n\n"
                f"Usa /autotrade resumir para reativar quando quiseres continuar."
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Não foi possível notificar circuit breaker ao user {uid}: {e}")
