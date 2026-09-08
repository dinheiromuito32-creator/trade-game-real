"""
sltp_engine.py

Motor de SL/TP automático para posições abertas pelo autotrade.
Corre como job contínuo (a cada 30s) no Railway, verifica preços
em tempo real via Binance REST e fecha posições automaticamente
quando SL ou TP é atingido — sem precisar de confirmação do utilizador.

Este é o comportamento que faz o autotrade ser "autónomo depois de entrar":
o utilizador aprovou a entrada uma vez, e a partir daí o bot trata do resto.
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from google.cloud import firestore as fs
from telegram import Bot

from firestore_client import get_db

logger = logging.getLogger("cless_bot.sltp_engine")

BINANCE_BASE = "https://api.binance.com"
STN_EUR      = 24.5
SPREAD       = 0.003
CHECK_INTERVAL_SECONDS = 30

# ── Trailing stop ────────────────────────────────────────────────────────
# Depois de o preço percorrer X% da distância até ao TP, o SL passa a
# "perseguir" o preço, trancando uma fatia do lucro já feito — nunca
# recua. Protege lucro em trades que revertem antes de bater no TP.
TRAILING_ACTIVATION_RATIO = 0.70  # era 0.50 — só ativa perto do fim do percurso (evita cortar na "zona de ruído" a meio)
TRAILING_LOCK_RATIO       = 0.65  # era 0.50 — tranca mais, mas só depois de já ter percorrido bastante do caminho


async def _get_prices_eur(coins: list[str]) -> dict[str, float]:
    """
    Busca preços atuais em EUR para uma lista de moedas.
    Usa EURUSDT como taxa de câmbio proxy.
    """
    if not coins:
        return {}

    async with aiohttp.ClientSession() as session:
        # Preço EUR/USDT
        async with session.get(
            f"{BINANCE_BASE}/api/v3/ticker/price",
            params={"symbol": "EURUSDT"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            eur_data = await r.json()
        eur_usdt = float(eur_data.get("price", 1))

        # Preços de todos os ativos em USDT (num só pedido)
        async with session.get(
            f"{BINANCE_BASE}/api/v3/ticker/price",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            all_prices = await r.json()

    price_map = {item["symbol"]: float(item["price"]) for item in all_prices}

    result = {}
    for coin in coins:
        symbol = f"{coin}USDT"
        if symbol in price_map and eur_usdt:
            result[coin] = price_map[symbol] / eur_usdt
    return result


def _get_open_positions() -> list[dict]:
    """Lê todas as posições abertas pelo autotrade no Firestore."""
    db = get_db()
    docs = (
        db.collection("autoTradePositions")
        .where("status", "==", "open")
        .stream()
    )
    positions = []
    for doc in docs:
        data = doc.to_dict()
        data["position_id"] = doc.id
        positions.append(data)
    return positions


def _get_open_position_for_user(uid: str) -> dict | None:
    """Devolve a posição aberta de um user específico (autotrade só permite 1 de cada vez)."""
    db = get_db()
    docs = (
        db.collection("autoTradePositions")
        .where("uid", "==", uid)
        .where("status", "==", "open")
        .limit(1)
        .stream()
    )
    for doc in docs:
        data = doc.to_dict()
        data["position_id"] = doc.id
        return data
    return None


def _get_all_open_positions_for_user(uid: str) -> list[dict]:
    """
    Devolve TODAS as posições abertas de um user. Por design só devia
    existir 1 de cada vez, mas serve de rede de segurança caso alguma
    vez volte a haver mais do que uma (ex: bug de proposta dupla no
    mesmo scan) — /posicao e /autotrade fechar usam isto para nunca
    "esconderem" uma posição extra por engano.
    """
    db = get_db()
    docs = (
        db.collection("autoTradePositions")
        .where("uid", "==", uid)
        .where("status", "==", "open")
        .stream()
    )
    positions = []
    for doc in docs:
        data = doc.to_dict()
        data["position_id"] = doc.id
        positions.append(data)
    return positions


def _calc_exit_and_pnl(pos: dict, current_price: float) -> tuple[float, float]:
    """
    Calcula o preço de saída (com spread) e o PnL líquido (com a fee_rate
    gravada na posição) para um dado preço de mercado atual. Usado tanto
    pelo fecho automático (SL/TP) como pelo fecho manual e pela consulta
    de PnL não realizado — para os três usarem sempre a mesma fórmula.
    """
    direction = pos.get("direction", "BUY")
    avg_entry = pos.get("avgEntry", 0.0)
    qty       = pos.get("qty", 0.0)
    fee_rate  = pos.get("fee_rate", 0.005)  # 'free' como fallback seguro

    if direction == "BUY":
        exit_price = current_price * (1 - SPREAD)
        gross_eur  = (exit_price - avg_entry) * qty
    else:
        exit_price = current_price * (1 + SPREAD)
        gross_eur  = (avg_entry - exit_price) * qty

    gross_stn = gross_eur * STN_EUR
    taxa_stn  = abs(exit_price * qty * STN_EUR) * fee_rate
    pnl_stn   = gross_stn - taxa_stn
    return exit_price, pnl_stn


async def get_all_positions_pnl(uid: str) -> list[dict]:
    """
    Como get_position_pnl(), mas devolve uma lista com TODAS as posições
    abertas do user, não só uma — usado por /posicao para nunca esconder
    uma posição extra, mesmo que o design normal seja só 1 de cada vez.
    """
    positions = _get_all_open_positions_for_user(uid)
    if not positions:
        return []

    coins = list({p.get("coin", "BTC") for p in positions})
    try:
        prices = await _get_prices_eur(coins)
    except Exception as e:
        logger.error(f"get_all_positions_pnl: erro ao buscar preços {coins}: {e}")
        return []

    results = []
    for pos in positions:
        coin = pos.get("coin", "BTC")
        current_price = prices.get(coin)
        if not current_price:
            continue
        exit_price, pnl_stn = _calc_exit_and_pnl(pos, current_price)
        results.append({
            "position_id": pos["position_id"],
            "coin": coin,
            "direction": pos.get("direction", "BUY"),
            "avgEntry": pos.get("avgEntry", 0.0),
            "current_price": exit_price,
            "qty": pos.get("qty", 0.0),
            "sl": pos.get("sl", 0.0),
            "tp": pos.get("tp", 0.0),
            "size_stn": pos.get("size_stn", 0.0),
            "pnl_stn": pnl_stn,
            "trailing_active": pos.get("trailing_active", False),
            "opened_at": pos.get("opened_at"),
        })
    return results


async def get_position_pnl(uid: str) -> dict | None:
    """
    Devolve o estado atual da posição aberta do autotrade de um user,
    com o PnL não realizado calculado ao preço de mercado agora.
    Não fecha nada — só consulta.
    """
    pos = _get_open_position_for_user(uid)
    if not pos:
        return None
    coin = pos.get("coin", "BTC")
    try:
        prices = await _get_prices_eur([coin])
    except Exception as e:
        logger.error(f"get_position_pnl: erro ao buscar preço de {coin}: {e}")
        return None
    current_price = prices.get(coin)
    if not current_price:
        return None
    exit_price, pnl_stn = _calc_exit_and_pnl(pos, current_price)
    return {
        "position_id": pos["position_id"],
        "coin": coin,
        "direction": pos.get("direction", "BUY"),
        "avgEntry": pos.get("avgEntry", 0.0),
        "current_price": exit_price,
        "qty": pos.get("qty", 0.0),
        "sl": pos.get("sl", 0.0),
        "tp": pos.get("tp", 0.0),
        "size_stn": pos.get("size_stn", 0.0),
        "pnl_stn": pnl_stn,
        "trailing_active": pos.get("trailing_active", False),
        "opened_at": pos.get("opened_at"),
    }


def _get_position_by_id(uid: str, position_id: str) -> dict | None:
    """
    Busca uma posição específica pelo ID, confirmando que pertence ao
    user e que ainda está aberta — usado por /autotrade fechar <coin>
    e pelo picker de "qual posição fechar" quando há mais do que uma.
    """
    db = get_db()
    doc = db.collection("autoTradePositions").document(position_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    if data.get("uid") != uid or data.get("status") != "open":
        return None
    data["position_id"] = doc.id
    return data


async def close_position_manual(bot: Bot, uid: str, position_id: str | None = None) -> dict | None:
    """
    Fecha uma posição do autotrade já, ao preço de mercado atual — sem
    esperar pelo SL/TP. Usa exatamente a mesma lógica de fecho e de
    registo de resultado que o fecho automático.

    Se position_id for dado, fecha especificamente essa (e só essa,
    mesmo que o user tenha outras abertas). Caso contrário, fecha a
    primeira que encontrar — comportamento de conveniência para quando
    só há 1 posição aberta.
    """
    pos = _get_position_by_id(uid, position_id) if position_id else _get_open_position_for_user(uid)
    if not pos:
        return None
    coin        = pos.get("coin", "BTC")
    position_id = pos["position_id"]
    try:
        prices = await _get_prices_eur([coin])
    except Exception as e:
        logger.error(f"close_position_manual: erro ao buscar preço de {coin}: {e}")
        return None
    current_price = prices.get(coin)
    if not current_price:
        return None
    exit_price, pnl_stn = _calc_exit_and_pnl(pos, current_price)

    _close_position_in_firestore(position_id, uid, exit_price, pnl_stn, "manual")

    from autotrade import register_trade_result
    await register_trade_result(bot, uid, position_id, pnl_stn)

    return {
        "coin": coin,
        "direction": pos.get("direction", "BUY"),
        "avgEntry": pos.get("avgEntry", 0.0),
        "exit_price": exit_price,
        "qty": pos.get("qty", 0.0),
        "pnl_stn": pnl_stn,
    }


def _close_position_in_firestore(position_id: str, uid: str, exit_price: float,
                                  pnl_stn: float, reason: str) -> None:
    """
    Fecha a posição no Firestore via Admin SDK.
    Exatamente o mesmo padrão que o fbPlaceOrder do app usa para sells,
    mas escrito a partir do servidor do bot.
    """
    db = get_db()
    pos_ref = db.collection("autoTradePositions").document(position_id)

    db.collection("transactions").add({
        "uid": uid,
        "type": "autotrade_close",
        "position_id": position_id,
        "exit_price": exit_price,
        "pnl_stn": pnl_stn,
        "reason": reason,
        "origin": "autotrade_bot",
        "ts": datetime.now(timezone.utc),
    })

    pos_ref.update({
        "status": "closed",
        "exit_price": exit_price,
        "pnl_stn": pnl_stn,
        "close_reason": reason,
        "closed_at": datetime.now(timezone.utc),
    })


async def _notify_close(bot: Bot, uid: str, position: dict,
                         exit_price: float, pnl_stn: float, reason: str) -> None:
    """Notifica o utilizador que a posição foi fechada automaticamente."""
    from account_linking import get_chat_id_for_uid
    import formatters

    chat_id = get_chat_id_for_uid(uid)
    if not chat_id:
        return

    coin      = position.get("coin", "BTC")
    direction = position.get("direction", "BUY")
    entry     = position.get("avgEntry", 0.0)
    qty       = position.get("qty", 0.0)
    coin_emoji = formatters.COIN_EMOJI.get(coin, "🪙")

    pnl_emoji = "📈" if pnl_stn >= 0 else "📉"
    reason_label = {
        "tp": "🎯 Take-Profit atingido",
        "sl": "🛑 Stop-Loss atingido",
        "trailing_sl": "🔒 Trailing Stop — lucro protegido",
        "manual": "👤 Fechado manualmente",
    }.get(reason, reason)

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"{pnl_emoji} *Posição Fechada — {coin_emoji} {coin}*\n\n"
                f"*{reason_label}*\n\n"
                f"Entrada:  {entry:,.2f} €\n"
                f"Saída:    {exit_price:,.2f} €\n"
                f"Qtd:      {qty:.6f} {coin}\n\n"
                f"PnL: *{'+'if pnl_stn>=0 else ''}{pnl_stn:.0f} STN*\n\n"
                f"_O bot está à procura da próxima oportunidade._"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Não foi possível notificar fecho ao user {uid}: {e}")


def _apply_trailing_stop(position_id: str, pos: dict, current_price: float) -> float:
    """
    Verifica se a posição já percorreu o suficiente do caminho até o TP
    para ativar o trailing stop, e se sim, sobe (BUY) ou desce (SELL) o
    SL para trancar parte do lucro. Nunca move o SL contra o trade —
    só na direção que reduz o risco. Devolve o SL efetivo a usar agora
    (atualizado ou o original, se o trailing ainda não ativou).
    """
    direction = pos.get("direction", "BUY")
    avg_entry = pos.get("avgEntry", 0.0)
    sl        = pos.get("sl", 0.0)
    tp        = pos.get("tp", 0.0)

    if direction == "BUY":
        distance_to_tp = tp - avg_entry
        progress = current_price - avg_entry
        if distance_to_tp <= 0 or progress < distance_to_tp * TRAILING_ACTIVATION_RATIO:
            return sl
        candidate_sl = avg_entry + progress * TRAILING_LOCK_RATIO
        if candidate_sl <= sl:
            return sl  # só sobe, nunca desce
        new_sl = candidate_sl
    else:
        distance_to_tp = avg_entry - tp
        progress = avg_entry - current_price
        if distance_to_tp <= 0 or progress < distance_to_tp * TRAILING_ACTIVATION_RATIO:
            return sl
        candidate_sl = avg_entry - progress * TRAILING_LOCK_RATIO
        if candidate_sl >= sl:
            return sl  # só desce, nunca sobe
        new_sl = candidate_sl

    try:
        db = get_db()
        db.collection("autoTradePositions").document(position_id).update({
            "sl": new_sl,
            "trailing_active": True,
        })
        logger.info(
            f"sltp_engine: trailing stop ativado em {position_id} — "
            f"SL {sl:.4f} → {new_sl:.4f}"
        )
    except Exception as e:
        logger.error(f"sltp_engine: erro ao atualizar trailing stop de {position_id}: {e}")
        return sl  # falhou a gravar, mantém o SL antigo por segurança

    return new_sl


async def check_and_close_positions(bot: Bot) -> None:
    """
    Job principal: verifica todas as posições abertas e fecha as que
    atingiram SL ou TP. Chamado a cada CHECK_INTERVAL_SECONDS pelo scheduler.
    """
    positions = _get_open_positions()
    if not positions:
        return

    # Busca preços só para as moedas que têm posições abertas
    coins_needed = list({p["coin"] for p in positions})
    try:
        prices = await _get_prices_eur(coins_needed)
    except Exception as e:
        logger.error(f"sltp_engine: erro ao buscar preços: {e}")
        return

    for pos in positions:
        position_id = pos["position_id"]
        uid         = pos.get("uid", "")
        coin        = pos.get("coin", "BTC")
        direction   = pos.get("direction", "BUY")
        avg_entry   = pos.get("avgEntry", 0.0)
        qty         = pos.get("qty", 0.0)
        sl          = pos.get("sl", 0.0)
        tp          = pos.get("tp", 0.0)
        size_stn    = pos.get("size_stn", 0.0)

        current_price = prices.get(coin)
        if not current_price:
            logger.warning(f"sltp_engine: preço não encontrado para {coin}")
            continue

        # Trailing stop — sobe/desce o SL se o preço já percorreu o
        # suficiente a favor do trade. Devolve o SL efetivo a usar já
        # nesta verificação (pode ter mudado agora mesmo).
        sl = _apply_trailing_stop(position_id, pos, current_price)

        # Preço de saída real (com spread, igual ao app)
        if direction == "BUY":
            exit_price = current_price * (1 - SPREAD)  # bid
            sl_hit = exit_price <= sl
            tp_hit = exit_price >= tp
        else:
            exit_price = current_price * (1 + SPREAD)  # ask
            sl_hit = exit_price >= sl
            tp_hit = exit_price <= tp

        if not (sl_hit or tp_hit):
            continue

        reason = "trailing_sl" if (sl_hit and pos.get("trailing_active")) else ("tp" if tp_hit else "sl")

        # Recalcula com o helper partilhado — mesma fórmula usada em
        # get_position_pnl() e close_position_manual(), evita duplicação.
        # Usa o pos original (sl antigo não interessa aqui, só entry/qty/fee).
        exit_price, pnl_stn = _calc_exit_and_pnl(pos, current_price)

        try:
            _close_position_in_firestore(position_id, uid, exit_price, pnl_stn, reason)
            logger.info(
                f"sltp_engine: {coin} {direction} fechado por {reason} "
                f"| entry={avg_entry:.2f} exit={exit_price:.2f} pnl={pnl_stn:.2f} STN"
            )

            # Atualizar saldo e circuit breaker
            from autotrade import register_trade_result
            await register_trade_result(bot, uid, position_id, pnl_stn)

            # Notificar o utilizador
            await _notify_close(bot, uid, pos, exit_price, pnl_stn, reason)

        except Exception as e:
            logger.error(f"sltp_engine: erro ao fechar posição {position_id}: {e}")


def setup_sltp_job(scheduler, bot: Bot) -> None:
    """
    Adiciona o job de verificação de SL/TP ao scheduler existente.
    Chamado no arranque do bot, logo a seguir ao setup_scheduler().
    """
    scheduler.add_job(
        check_and_close_positions,
        trigger="interval",
        seconds=CHECK_INTERVAL_SECONDS,
        kwargs={"bot": bot},
        id="sltp_check",
        replace_existing=True,
    )
    logger.info(f"SL/TP engine configurado: verificação a cada {CHECK_INTERVAL_SECONDS}s.")
