"""
backtest.py

Backtesting real da estratégia do signals_engine.py contra dados
históricos da Binance (API pública, sem API key).

IMPORTANTE: reutiliza a MESMA função _analyze() que corre em produção
(importa diretamente de signals_engine.py) — os resultados refletem
exatamente os filtros atuais (RSI/MACD/Bollinger/EMA, ADX, EMA200,
confirmação 4h, threshold de confiança). Se ajustares o
signals_engine.py no futuro, corre o backtest outra vez para veres o
efeito real da mudança.

Também simula o trailing stop (mesma lógica do sltp_engine.py).

USO:
    pip install aiohttp --break-system-packages
    python3 backtest.py --days 60
    python3 backtest.py --days 30 --coins BTC,ETH,ADA

Corre isto num sítio com acesso à internet (Railway shell, ou local).
NÃO corre dentro do bot em produção — é uma ferramenta de análise à parte.

LIMITAÇÕES CONHECIDAS (para interpretares os resultados com cautela):
  - Não simula o atraso entre a proposta ser enviada e tu aprovares —
    assume execução imediata ao preço de fecho da vela do sinal.
  - Não aplica o circuit breaker (3 perdas seguidas = pausa) como um
    "stop" real; só reporta a maior sequência de perdas que teria
    ocorrido, para veres a que ponto isso seria ativado.
  - Usa uma taxa de câmbio EUR/USDT fixa aproximada, não histórica.
  - Usa uma fee_rate média fixa (não depende do teu tier real).
"""

import argparse
import asyncio
from datetime import datetime, timezone, timedelta

import aiohttp

import signals_engine as se  # reaproveita a lógica real de produção

# ── Constantes do backtest (espelham autotrade.py / sltp_engine.py) ────────
STARTING_BALANCE  = 1000.0   # STN fictícios, só para a curva de equity
MAX_POSITION_PCT  = 0.15
SPREAD            = 0.003
FEE_RATE          = 0.002    # aproximação média entre tiers (free 0.5% .. diamond 0.1%)
TRAILING_ACTIVATION_RATIO = 0.5
TRAILING_LOCK_RATIO       = 0.5
EUR_USDT_APPROX   = 1.08     # aproximação fixa — não histórica


# ═══════════════════════════════════════════════════════════════════════
# DADOS HISTÓRICOS
# ═══════════════════════════════════════════════════════════════════════
async def fetch_klines_range(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """Busca todas as velas entre start_ms e end_ms, paginando de 1000 em 1000."""
    symbol = f"{coin}USDT"
    candles = []
    cursor = start_ms
    async with aiohttp.ClientSession() as session:
        while cursor < end_ms:
            params = {
                "symbol": symbol, "interval": interval,
                "startTime": cursor, "endTime": end_ms, "limit": 1000,
            }
            async with session.get(f"{se.BINANCE_BASE}/api/v3/klines", params=params) as r:
                data = await r.json()
            if not data or not isinstance(data, list):
                break
            for k in data:
                candles.append({
                    "open_time": k[0],
                    "open":  float(k[1]),
                    "high":  float(k[2]),
                    "low":   float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            if len(data) < 1000:
                break
            cursor = data[-1][0] + 1
            await asyncio.sleep(0.25)  # não martelar o rate limit da Binance
    return candles


# ═══════════════════════════════════════════════════════════════════════
# SIMULAÇÃO
# ═══════════════════════════════════════════════════════════════════════
def _slice_up_to(candles: list[dict], idx: int, lookback: int = 260) -> list[dict]:
    start = max(0, idx - lookback + 1)
    return candles[start: idx + 1]


def _htf_window_for(htf_candles: list[dict], target_open_time: int, lookback: int = 100) -> list[dict]:
    """Últimas `lookback` velas 4h fechadas antes/igual ao open_time da vela 1h atual."""
    idx = -1
    for i, c in enumerate(htf_candles):
        if c["open_time"] <= target_open_time:
            idx = i
        else:
            break
    if idx < 0:
        return []
    start = max(0, idx - lookback + 1)
    return htf_candles[start: idx + 1]


async def backtest_coin(coin: str, candles_1h: list[dict], candles_4h: list[dict]) -> list[dict]:
    """
    Percorre as velas 1h em ordem, aplicando a lógica real de sinais e
    simulando abertura/trailing/fecho exatamente como o bot faria — mas
    só para ESTA moeda isoladamente (a alocação sequencial entre moedas
    é resolvida depois, em run_backtest, juntando todos os trades).
    """
    trades = []
    open_pos = None

    for i in range(210, len(candles_1h)):
        bar = candles_1h[i]
        price = bar["close"]

        if open_pos:
            direction = open_pos["direction"]
            entry = open_pos["entry"]
            tp    = open_pos["tp"]

            if direction == "BUY":
                dist = tp - entry
                progress = price - entry
                if dist > 0 and progress >= dist * TRAILING_ACTIVATION_RATIO:
                    candidate = entry + progress * TRAILING_LOCK_RATIO
                    if candidate > open_pos["sl"]:
                        open_pos["sl"] = candidate
                        open_pos["trailing"] = True
                hit_sl = bar["low"]  * (1 - SPREAD) <= open_pos["sl"]
                hit_tp = bar["high"] * (1 - SPREAD) >= tp
            else:
                dist = entry - tp
                progress = entry - price
                if dist > 0 and progress >= dist * TRAILING_ACTIVATION_RATIO:
                    candidate = entry - progress * TRAILING_LOCK_RATIO
                    if candidate < open_pos["sl"]:
                        open_pos["sl"] = candidate
                        open_pos["trailing"] = True
                hit_sl = bar["high"] * (1 + SPREAD) >= open_pos["sl"]
                hit_tp = bar["low"]  * (1 + SPREAD) <= tp

            if hit_sl or hit_tp:
                exit_price = open_pos["sl"] if hit_sl else tp
                if direction == "BUY":
                    pnl_pct = (exit_price - entry) / entry
                else:
                    pnl_pct = (entry - exit_price) / entry
                pnl_pct -= FEE_RATE

                reason = "trailing" if (hit_sl and open_pos.get("trailing")) else ("tp" if hit_tp else "sl")
                trades.append({
                    "coin": coin, "direction": direction,
                    "entry": entry, "exit": exit_price, "pnl_pct": pnl_pct,
                    "reason": reason,
                    "opened_time": open_pos["entry_time"], "closed_time": bar["open_time"],
                })
                open_pos = None
            continue

        # Sem posição aberta — procura sinal com a lógica REAL de produção
        window = _slice_up_to(candles_1h, i)
        if len(window) < 210:
            continue
        htf_window = _htf_window_for(candles_4h, bar["open_time"])

        signal = se._analyze(coin, window, eur_usdt=1.0, htf_candles=htf_window)
        if not signal:
            continue

        open_pos = {
            "direction": signal["direction"],
            "entry": price,
            "sl": signal["sl"],
            "tp": signal["tp"],
            "entry_time": bar["open_time"],
            "trailing": False,
        }

    return trades


# ═══════════════════════════════════════════════════════════════════════
# EXECUÇÃO E RELATÓRIO
# ═══════════════════════════════════════════════════════════════════════
def simulate_equity(all_trades: list[dict]) -> dict:
    """
    Ordena os trades por tempo de abertura e simula alocação sequencial
    (1 posição de cada vez no total, como o design real do autotrade),
    devolvendo a curva de equity e as estatísticas agregadas.
    """
    all_trades = sorted(all_trades, key=lambda t: t["opened_time"])

    balance = STARTING_BALANCE
    peak = balance
    max_drawdown = 0.0
    consec_losses = 0
    max_consec_losses = 0
    wins = losses = 0

    for t in all_trades:
        size = balance * MAX_POSITION_PCT
        pnl = size * t["pnl_pct"]
        balance += pnl
        peak = max(peak, balance)
        drawdown = (peak - balance) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)

        if pnl >= 0:
            wins += 1
            consec_losses = 0
        else:
            losses += 1
            consec_losses += 1
            max_consec_losses = max(max_consec_losses, consec_losses)

        t["pnl_stn"] = pnl
        t["balance_after"] = balance

    total = wins + losses
    return {
        "trades": all_trades,
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / total * 100) if total else 0.0,
        "final_balance": balance,
        "max_drawdown_pct": max_drawdown * 100,
        "max_consec_losses": max_consec_losses,
        "avg_pnl_pct": (sum(t["pnl_pct"] for t in all_trades) / total * 100) if total else 0.0,
    }


def build_report(days: int, coins: list[str], stats: dict) -> str:
    lines = []
    lines.append(f"📊 *Resultados do Backtest*")
    lines.append(f"{days} dias · {len(coins)} moeda(s)")
    lines.append("")
    lines.append(f"Total de trades: *{stats['total']}*")
    lines.append(f"Win rate: *{stats['win_rate']:.1f}%*  ({stats['wins']}W / {stats['losses']}L)")
    lines.append(f"Saldo: {STARTING_BALANCE:.0f} → *{stats['final_balance']:.0f} STN* "
                  f"({(stats['final_balance']/STARTING_BALANCE - 1) * 100:+.1f}%)")
    lines.append(f"Maior sequência de perdas: {stats['max_consec_losses']} "
                  f"(circuit breaker real ativa aos 3)")
    lines.append(f"Máximo drawdown: {stats['max_drawdown_pct']:.1f}%")
    lines.append(f"PnL% médio por trade: {stats['avg_pnl_pct']:+.2f}%")

    lines.append("\n*Por moeda:*")
    by_coin: dict[str, list[dict]] = {}
    for t in stats["trades"]:
        by_coin.setdefault(t["coin"], []).append(t)
    for coin in coins:
        trades = by_coin.get(coin, [])
        if not trades:
            lines.append(f"  {coin}: 0 trades")
            continue
        w = sum(1 for t in trades if t["pnl_stn"] >= 0)
        wr = w / len(trades) * 100
        lines.append(f"  {coin}: {len(trades)} trades, {wr:.0f}% win rate")

    lines.append("\n*Por motivo de fecho:*")
    by_reason: dict[str, int] = {}
    for t in stats["trades"]:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
    for reason, count in by_reason.items():
        lines.append(f"  {reason}: {count}")

    return "\n".join(lines)


def print_report(days: int, coins: list[str], stats: dict) -> None:
    print("\n" + build_report(days, coins, stats).replace("*", "") + "\n")


async def run_backtest(days: int, coins: list[str] | None = None, progress_callback=None) -> dict:
    """
    progress_callback(msg: str) é opcional — se dado, é chamado em vez de
    print() a cada etapa (usado pelo comando /backtest do bot para
    reportar progresso via Telegram; no CLI simplesmente imprime).
    """
    def report(msg: str):
        if progress_callback:
            progress_callback(msg)
        else:
            print(msg)

    coins = coins or se.COINS
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms   = int(end.timestamp() * 1000)
    # buffer extra atrás do início para os indicadores (EMA200, ADX) já
    # terem contexto suficiente logo no primeiro trade possível
    buffer_ms = 15 * 24 * 3600 * 1000

    all_trades = []
    for coin in coins:
        report(f"A buscar dados de {coin}...")
        candles_1h = await fetch_klines_range(coin, se.TIMEFRAME, start_ms - buffer_ms, end_ms)
        candles_4h = await fetch_klines_range(coin, se.HTF_TIMEFRAME, start_ms - buffer_ms, end_ms)
        report(f"  {coin}: {len(candles_1h)} velas 1h, {len(candles_4h)} velas 4h")
        if len(candles_1h) < 210:
            report(f"  {coin}: dados insuficientes, a saltar.")
            continue
        trades = await backtest_coin(coin, candles_1h, candles_4h)
        all_trades.extend(trades)
        report(f"  {coin}: {len(trades)} trade(s) simulado(s)")

    stats = simulate_equity(all_trades)
    if not progress_callback:
        print_report(days, coins, stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest do signals_engine.py contra dados reais da Binance")
    parser.add_argument("--days", type=int, default=60, help="Quantos dias para trás testar (default: 60)")
    parser.add_argument("--coins", type=str, default="", help="Lista separada por vírgulas, ex: BTC,ETH,ADA (default: todas)")
    args = parser.parse_args()

    coins_arg = [c.strip().upper() for c in args.coins.split(",") if c.strip()] or None
    asyncio.run(run_backtest(args.days, coins_arg))
