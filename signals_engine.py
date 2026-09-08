"""
signals_engine.py

Engine de análise técnica para deteção de sinais de trading.
Reutiliza a mesma lógica dos teus bots Python anteriores
(RSI, EMA, MACD, Bollinger Bands, ADX) adaptada para Python puro,
sem dependência de Binance API — os preços são lidos diretamente
do Binance WebSocket público (sem autenticação necessária) ou via
REST público.

Todos os sinais são guardados em botSignals (status: "pending") e
depois enviados ao admin para aprovação — nunca publicados
automaticamente. O admin clica Publicar no Telegram e só então vão
para o canal.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

import aiohttp

from firestore_client import get_db

logger = logging.getLogger("cless_bot.signals_engine")

# Moedas monitoradas — as mesmas 14 do app Cless Cripto (const COINS no index.html)
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX",
         "DOT", "LINK", "LTC", "ATOM", "NEAR", "XLM"]
TIMEFRAME = "1h"     # timeframe principal para análise
CANDLES_LIMIT = 250  # precisa de 200+ para calcular EMA200 (filtro de tendência macro)

# Timeframe superior — usado para confirmar que a tendência maior concorda
# com o sinal do timeframe principal, antes de operar.
HTF_TIMEFRAME = "4h"
HTF_CANDLES_LIMIT = 100

# URL base da Binance REST pública (sem API key, sem autenticação)
BINANCE_BASE = "https://api.binance.com"


# ── Fetch de dados ─────────────────────────────────────────────────────────

async def fetch_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list[dict]:
    """
    Busca as últimas `limit` velas de um par na Binance REST pública.
    Devolve lista de dicts com open, high, low, close, volume.
    """
    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            raw = await resp.json()

    return [
        {
            "open":   float(k[1]),
            "high":   float(k[2]),
            "low":    float(k[3]),
            "close":  float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]


async def get_eur_usdt_rate() -> float:
    """
    Devolve a taxa EUR/USDT atual da Binance (quantos USDT vale 1 EUR).
    Usada para converter os preços dos candles (sempre em USDT) para EUR
    antes de gravar/enviar qualquer sinal — para não misturar unidades
    com o resto do sistema (autotrade, sltp_engine), que trabalha em EUR.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BINANCE_BASE}/api/v3/ticker/price",
                params={"symbol": "EURUSDT"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
        rate = float(data.get("price", 0))
        return rate if rate > 0 else 1.0
    except Exception as e:
        logger.error(f"get_eur_usdt_rate: erro ao obter taxa EUR/USDT: {e}")
        return 1.0


async def fetch_current_price_eur(symbol: str) -> float:
    """
    Devolve o preço atual em EUR, usando USDT como proxy e o rate
    EUR/USDT da Binance. Aproximação suficiente para os sinais.
    """
    async with aiohttp.ClientSession() as session:
        # Preço do ativo em USDT
        url_asset = f"{BINANCE_BASE}/api/v3/ticker/price"
        async with session.get(url_asset, params={"symbol": f"{symbol}USDT"}) as r:
            asset_data = await r.json()
        price_usdt = float(asset_data.get("price", 0))

        # EURUSDT (quantos USDT vale 1 EUR → inverso do que precisamos)
        async with session.get(url_asset, params={"symbol": "EURUSDT"}) as r:
            eur_data = await r.json()
        eur_usdt = float(eur_data.get("price", 1))

    # preço em EUR = preço em USDT / EURUSDT
    return price_usdt / eur_usdt if eur_usdt else price_usdt


# ── Indicadores técnicos ────────────────────────────────────────────────────

def _ema(closes: list[float], period: int) -> list[float]:
    """EMA simples — sem dependências externas."""
    if len(closes) < period:
        return []
    k = 2 / (period + 1)
    emas = [sum(closes[:period]) / period]
    for price in closes[period:]:
        emas.append(price * k + emas[-1] * (1 - k))
    return emas


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI do Wilder — devolve o valor mais recente."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(closes: list[float], fast=12, slow=26, signal=9) -> tuple[float, float, float] | None:
    """
    Devolve (macd_line, signal_line, histogram) para o último ponto.
    """
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    if not ema_fast or not ema_slow:
        return None
    # Alinhar pelo comprimento mais curto (ema_slow é sempre mais curto)
    offset = len(ema_fast) - len(ema_slow)
    macd_line_series = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    signal_ema = _ema(macd_line_series, signal)
    if not signal_ema:
        return None
    macd_val = macd_line_series[-1]
    signal_val = signal_ema[-1]
    histogram = macd_val - signal_val
    return macd_val, signal_val, histogram


def _bollinger(closes: list[float], period: int = 20, std_dev: float = 2.0) -> tuple[float, float, float] | None:
    """Devolve (upper, middle, lower) da BB para o último ponto."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = variance ** 0.5
    return middle + std_dev * std, middle, middle - std_dev * std


def _is_breakout_continuation(closes: list[float], highs: list[float], lows: list[float],
                               volumes: list[float], direction: str, lookback: int = 3) -> bool:
    """
    Deteta se as últimas `lookback` velas mostram um ROMPIMENTO A CONTINUAR
    (não uma exaustão/reversão) na direção oposta ao sinal proposto.

    Motivo: RSI alto + toque na banda superior de Bollinger tanto acontece
    numa reversão real como no início de um breakout forte de alta — os
    dois casos são indistinguíveis só com esses indicadores. Este filtro
    olha para o comportamento cru do preço nas últimas velas:

      - Corpo médio dos candles anormalmente grande (vs média das últimas 20)
      - Fechos consistentes na direção do movimento (não pavios longos
        indicando rejeição)
      - Volume nas últimas velas bem acima da média (não só 1.2x)

    Se isto for verdade NA DIREÇÃO CONTRÁRIA ao sinal (ex: sinal é SELL
    mas as últimas velas mostram breakout de ALTA a continuar), o sinal
    é considerado "sinal de exaustão falso" e deve ser descartado.

    Devolve True se detetar breakout de continuação contra o sinal.
    """
    if len(closes) < 25 or len(volumes) < 25:
        return False

    recent_closes = closes[-lookback:]
    recent_opens_proxy = closes[-lookback-1:-1]  # aproxima "open" pelo close anterior
    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]
    recent_vols = volumes[-lookback:]

    vol_avg = sum(volumes[-20:]) / 20
    body_avg = sum(abs(closes[i] - closes[i-1]) for i in range(-20, 0)) / 20

    recent_bodies = [abs(recent_closes[i] - recent_opens_proxy[i]) for i in range(lookback)]
    recent_body_avg = sum(recent_bodies) / lookback

    net_move = recent_closes[-1] - recent_opens_proxy[0]

    recent_vol_avg = sum(recent_vols) / lookback
    strong_volume = vol_avg > 0 and recent_vol_avg > vol_avg * 1.8

    strong_body = body_avg > 0 and recent_body_avg > body_avg * 1.5

    if net_move > 0:
        wick_rejection = any((recent_highs[i] - recent_closes[i]) > recent_bodies[i] * 1.2
                              for i in range(lookback))
        moving_up = True
    else:
        wick_rejection = any((recent_closes[i] - recent_lows[i]) > recent_bodies[i] * 1.2
                              for i in range(lookback))
        moving_up = False

    is_strong_breakout = strong_volume and strong_body and not wick_rejection

    if not is_strong_breakout:
        return False

    if direction == "SELL" and moving_up:
        return True
    if direction == "BUY" and not moving_up:
        return True
    return False


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """
    ADX (Average Directional Index) — mede a FORÇA da tendência, não a
    direção. Usado como filtro de regime de mercado: ADX < 20 costuma
    indicar mercado lateral (sem tendência), onde RSI/MACD/Bollinger dão
    muito mais sinais falsos. Acima de 20-25 já há tendência a valer a pena.
    """
    n = len(highs)
    if n < period * 2 + 1:
        return None

    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up_move   = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    def _wilder_smooth(values: list[float], period: int) -> list[float]:
        if len(values) < period:
            return []
        smoothed = [sum(values[:period])]
        for v in values[period:]:
            smoothed.append(smoothed[-1] - (smoothed[-1] / period) + v)
        return smoothed

    s_tr    = _wilder_smooth(trs, period)
    s_plus  = _wilder_smooth(plus_dm, period)
    s_minus = _wilder_smooth(minus_dm, period)
    if not s_tr or not s_plus or not s_minus:
        return None

    plus_di  = [100 * (p / t) if t else 0.0 for p, t in zip(s_plus, s_tr)]
    minus_di = [100 * (m / t) if t else 0.0 for m, t in zip(s_minus, s_tr)]
    dx = [100 * abs(p - m) / (p + m) if (p + m) else 0.0 for p, m in zip(plus_di, minus_di)]
    if len(dx) < period:
        return None

    adx = sum(dx[:period]) / period
    for d in dx[period:]:
        adx = (adx * (period - 1) + d) / period
    return adx


# ── Lógica de decisão ───────────────────────────────────────────────────────

def _analyze(coin: str, candles: list[dict], eur_usdt: float = 1.0,
             htf_candles: list[dict] | None = None) -> dict | None:
    """
    Analisa as velas com múltiplos indicadores.

    IMPORTANTE: os candles vêm sempre em USDT (Binance). Os indicadores
    (RSI, MACD, Bollinger, EMA) são calculados em USDT — isso não afeta
    o resultado, pois são todos rácios/diferenças relativas. Mas o preço
    final de entry/sl/tp devolvido É convertido para EUR usando `eur_usdt`,
    porque o resto do sistema (autotrade.py, sltp_engine.py) trabalha
    inteiramente em EUR. Sem esta conversão, o SL/TP fica ~8-10% desviado
    do preço real de mercado em EUR, fazendo a posição fechar quase de
    imediato assim que o sltp_engine faz a primeira verificação.

    Filtros de qualidade adicionais (elevam a seletividade dos sinais):
      - ADX(14) < 20 → mercado lateral, sinal descartado
      - EMA200 → só opera a favor da tendência macro (BUY acima, SELL abaixo)
      - Confirmação multi-timeframe (4h) → EMA20/50 do 4h tem de concordar
        com a direção do sinal do 1h, senão é descartado
      - Confirmação forte obrigatória → a direção escolhida tem de ter
        pelo menos 1 sinal forte real (MACD crossover ou toque Bollinger),
        não basta RSI + EMA + volume só por si (ver histórico de backtest:
        60% de threshold sem esta exigência deu 49,5% win rate e -7,4%)

    Critérios BUY (cada um com peso; ★ = conta como "confirmação forte"):
      - RSI < 40 (+2), RSI < 30 (+1 extra)
      - MACD histogram cruzou negativo→positivo (+2) ★
      - Preço ≤ Bollinger inferior (+2) ★
      - EMA20 > EMA50 (tendência favorável) (+1)
      - Volume actual > média de volume (+1)

    Critérios SELL (simétricos):
      - RSI > 60 (+2), RSI > 70 (+1 extra)
      - MACD histogram cruzou positivo→negativo (+2) ★
      - Preço ≥ Bollinger superior (+2) ★
      - EMA20 < EMA50 (+1)
      - Volume actual > média (+1)

    Confiança: soma / máximo * 100. Threshold: 65%.
    Além do threshold, exige-se ≥1 critério ★ na direção escolhida.
    """
    closes  = [c["close"]  for c in candles]
    highs   = [c["high"]   for c in candles]
    lows    = [c["low"]    for c in candles]
    volumes = [c.get("volume", 0) for c in candles]
    current_price = closes[-1]

    rsi         = _rsi(closes)
    macd_result = _macd(closes)
    bb          = _bollinger(closes)
    ema20       = _ema(closes, 20)
    ema50       = _ema(closes, 50)
    ema200      = _ema(closes, 200)
    adx         = _adx(highs, lows, closes)

    if rsi is None or macd_result is None or bb is None:
        return None

    macd_line, signal_line, histogram = macd_result
    bb_upper, bb_mid, bb_lower = bb

    macd_prev = _macd(closes[:-1])
    prev_histogram = macd_prev[2] if macd_prev else 0

    # Volume médio (últimas 20 velas)
    vol_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    vol_current = volumes[-1] if volumes else 0
    vol_elevated = vol_current > vol_avg * 1.2 if vol_avg > 0 else False

    buy_signals  = 0
    sell_signals = 0
    buy_strong   = False  # tem pelo menos 1 confirmação forte (MACD crossover ou toque Bollinger)?
    sell_strong  = False
    reasons      = []

    # RSI
    if rsi < 40:
        buy_signals += 2
        reasons.append(f"RSI sobrevendido ({rsi:.1f})")
        if rsi < 30:
            buy_signals += 1
            reasons.append("RSI crítico < 30")
    elif rsi > 60:
        sell_signals += 2
        reasons.append(f"RSI sobrecomprado ({rsi:.1f})")
        if rsi > 70:
            sell_signals += 1
            reasons.append("RSI crítico > 70")

    # MACD crossover (crossover real = confirmação forte; "momentum" sozinho não conta)
    if prev_histogram < 0 < histogram:
        buy_signals += 2
        buy_strong = True
        reasons.append("MACD cruzou ↑")
    elif prev_histogram > 0 > histogram:
        sell_signals += 2
        sell_strong = True
        reasons.append("MACD cruzou ↓")
    elif histogram > 0 and histogram > prev_histogram:
        buy_signals += 1
        reasons.append("MACD momentum ↑")
    elif histogram < 0 and histogram < prev_histogram:
        sell_signals += 1
        reasons.append("MACD momentum ↓")

    # Bollinger Bands (toque na banda = confirmação forte)
    if current_price <= bb_lower * 1.02:
        buy_signals += 2
        buy_strong = True
        reasons.append("Preço na BB inferior")
    elif current_price >= bb_upper * 0.98:
        sell_signals += 2
        sell_strong = True
        reasons.append("Preço na BB superior")

    # EMA trend
    if ema20 and ema50 and len(ema20) > 1 and len(ema50) > 1:
        if ema20[-1] > ema50[-1]:
            buy_signals += 1
            reasons.append("EMA20 > EMA50 ↑")
        else:
            sell_signals += 1
            reasons.append("EMA20 < EMA50 ↓")

    # Volume
    if vol_elevated:
        if buy_signals > sell_signals:
            buy_signals += 1
            reasons.append("Volume elevado")
        elif sell_signals > buy_signals:
            sell_signals += 1
            reasons.append("Volume elevado")

    max_signals = 9  # máximo possível agora
    buy_confidence  = round(buy_signals  / max_signals * 100)
    sell_confidence = round(sell_signals / max_signals * 100)

    MIN_CONFIDENCE = 65  # restaurado — 60% deixava passar sinais só com RSI+tendência+volume, sem MACD/BB reais

    direction = None
    confidence = 0

    if buy_confidence >= MIN_CONFIDENCE and buy_confidence > sell_confidence:
        direction  = "BUY"
        confidence = buy_confidence
    elif sell_confidence >= MIN_CONFIDENCE and sell_confidence > buy_confidence:
        direction  = "SELL"
        confidence = sell_confidence

    if direction is None:
        logger.info(
            f"{coin}: sem sinal — confiança BUY {buy_confidence:.0f}% / "
            f"SELL {sell_confidence:.0f}% (mínimo {MIN_CONFIDENCE}%)"
        )
        return None

    # ── Filtro 0: confirmação forte obrigatória ────────────────────────
    # A confiança total pode passar no threshold só com RSI + EMA + volume
    # (nenhum deles confirma reversão de facto). Exige-se que a direção
    # escolhida tenha pelo menos 1 sinal "forte" real: MACD com cruzamento
    # (não só momentum) ou preço a tocar a banda de Bollinger.
    strong_ok = buy_strong if direction == "BUY" else sell_strong
    if not strong_ok:
        logger.info(
            f"{coin}: sinal descartado — confiança {confidence}% mas sem "
            f"confirmação forte (MACD crossover ou toque Bollinger) para {direction}"
        )
        return None

    # ── Filtro 1: regime de mercado (ADX) ──────────────────────────────
    # ADX < 20 = mercado lateral, sem tendência definida. Indicadores como
    # RSI/MACD/Bollinger dão muito mais sinais falsos nestas condições —
    # é mais seguro não operar do que arriscar.
    if adx is not None and adx < 20:
        logger.info(f"{coin}: sinal descartado — ADX {adx:.1f} (mercado lateral)")
        return None

    # ── Filtro 2: tendência macro (EMA200) ─────────────────────────────
    # Só opera a favor da tendência de longo prazo: BUY só se o preço
    # estiver acima da EMA200, SELL só se estiver abaixo. Evita apanhar
    # "facas a cair" ou vender no meio de uma subida forte.
    if ema200:
        if direction == "BUY" and current_price < ema200[-1]:
            logger.info(f"{coin}: BUY descartado — preço abaixo da EMA200 (contra tendência)")
            return None
        if direction == "SELL" and current_price > ema200[-1]:
            logger.info(f"{coin}: SELL descartado — preço acima da EMA200 (contra tendência)")
            return None

    # ── Filtro 3: confirmação multi-timeframe (4h) ─────────────────────
    # A tendência do timeframe superior (EMA20 vs EMA50 no 4h) tem de
    # concordar com a direção do sinal do 1h. Evita operar contra o
    # "pano de fundo" maior do mercado.
    if htf_candles and len(htf_candles) >= 55:
        htf_closes = [c["close"] for c in htf_candles]
        htf_ema20  = _ema(htf_closes, 20)
        htf_ema50  = _ema(htf_closes, 50)
        if htf_ema20 and htf_ema50:
            htf_uptrend = htf_ema20[-1] > htf_ema50[-1]
            if direction == "BUY" and not htf_uptrend:
                logger.info(f"{coin}: BUY descartado — 4h em tendência de baixa")
                return None
            if direction == "SELL" and htf_uptrend:
                logger.info(f"{coin}: SELL descartado — 4h em tendência de alta")
                return None

    # ── Filtro 4: guarda contra falsos sinais de reversão em breakout ──
    # RSI alto + toque na banda superior de Bollinger (ou o espelho para
    # BUY) acontecem tanto numa reversão real como no início de um
    # rompimento forte que vai continuar. Os filtros 0-3 não distinguem
    # os dois casos. Aqui olhamos o comportamento cru das últimas velas
    # (corpo grande, volume bem acima da média, sem pavios de rejeição)
    # para descartar o sinal quando na verdade é um breakout a continuar
    # contra a direção proposta — este era exatamente o padrão do DOT
    # (SELL a 0.74€ que continuou a subir e bateu no SL).
    if _is_breakout_continuation(closes, highs, lows, volumes, direction):
        logger.info(
            f"{coin}: {direction} descartado — breakout de continuação "
            f"detetado contra o sinal (provável falso sinal de reversão)"
        )
        return None

    # SL/TP dinâmicos baseados em ATR
    atr_period = 14
    trs = [
        max(highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]))
        for i in range(1, len(candles))
    ]
    atr = sum(trs[-atr_period:]) / atr_period if len(trs) >= atr_period else current_price * 0.015

    if direction == "BUY":
        sl = current_price - 1.5 * atr
        tp = current_price + 3.0 * atr
    else:
        sl = current_price + 1.5 * atr
        tp = current_price - 3.0 * atr

    sl_pct = abs((sl - current_price) / current_price * 100)
    tp_pct = abs((tp - current_price) / current_price * 100)
    rr = round(tp_pct / sl_pct, 1) if sl_pct else 0

    # Converte de USDT (candles Binance) para EUR — mesma unidade usada
    # em autotrade.py e sltp_engine.py. eur_usdt = quantos USDT vale 1 EUR.
    entry_eur = current_price / eur_usdt if eur_usdt else current_price
    sl_eur    = sl / eur_usdt if eur_usdt else sl
    tp_eur    = tp / eur_usdt if eur_usdt else tp

    return {
        "coin":       coin,
        "direction":  direction,
        "entry":      round(entry_eur, 4),
        "sl":         round(sl_eur, 4),
        "tp":         round(tp_eur, 4),
        "rr":         rr,
        "confidence": confidence,
        "reason":     " · ".join(reasons),
        "timeframe":  TIMEFRAME,
        "created_at": datetime.now(timezone.utc),
        "status":     "pending",
    }


async def save_signal(signal: dict) -> str:
    """Guarda o sinal no Firestore e devolve o signal_id."""
    db = get_db()
    signal_id = str(uuid.uuid4())[:8]
    db.collection("botSignals").document(signal_id).set(signal)
    logger.info(f"Sinal guardado: {signal_id} {signal['coin']} {signal['direction']} ({signal['confidence']}%)")
    return signal_id


async def run_scan(notify_callback) -> None:
    """
    Faz um scan a todas as moedas. Para cada sinal encontrado,
    guarda no Firestore e chama notify_callback(signal, signal_id)
    para enviar ao admin para aprovação.

    notify_callback é injetado pelo bot.py para evitar dependência circular.
    """
    logger.info("A iniciar scan de sinais...")
    eur_usdt = await get_eur_usdt_rate()
    for coin in COINS:
        try:
            candles = await fetch_klines(coin, TIMEFRAME, CANDLES_LIMIT)
            if len(candles) < 210:
                logger.warning(f"{coin}: dados insuficientes para EMA200 ({len(candles)} velas).")
                continue
            htf_candles = await fetch_klines(coin, HTF_TIMEFRAME, HTF_CANDLES_LIMIT)
            signal = _analyze(coin, candles, eur_usdt, htf_candles)
            if signal:
                signal_id = await save_signal(signal)
                await notify_callback(signal, signal_id)
                logger.info(f"Sinal detetado e enviado para aprovação: {coin} {signal['direction']}")
            else:
                logger.debug(f"{coin}: sem sinal claro neste momento.")
        except Exception as e:
            logger.error(f"Erro ao analisar {coin}: {e}")
        await asyncio.sleep(0.5)  # respeitar rate limits da Binance
    logger.info("Scan de sinais concluído.")
