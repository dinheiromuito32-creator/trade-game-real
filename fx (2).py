"""
fx.py

Lógica partilhada de multi-moeda (STN/EUR/USD): câmbio, P2P, remessas
internacionais e depósito de cripto.

Todas as constantes e fórmulas aqui ESPELHAM EXATAMENTE as do index.html
(a mesma fonte de verdade usada pelo app principal). Se uma taxa mudar
na app, tem de mudar aqui também — não há forma automática de sincronizar
isto, porque o bot e o app são dois códigos separados a falar com o
mesmo Firestore. Ver comentário equivalente em cada constante do
index.html (secções "CÂMBIO (FX)", "P2P", "REMESSAS INTERNACIONAIS",
"DEPÓSITO DE CRIPTO — endereços on-chain").
"""

from datetime import datetime, timezone


# ── Taxas STN/EUR/USD ───────────────────────────────────────────────────────
# Mesmos valores fixos que RATES no index.html (linha ~730). Não são obtidos
# em tempo real — são a taxa de câmbio "oficial" da plataforma, ajustada
# manualmente quando necessário.
STN_EUR = 24.5   # 1 EUR = 24.5 STN
STN_USD = 22.6   # 1 USD = 22.6 STN
EUR_USD = 1.08

FX_CURRENCIES = ["STN", "EUR", "USD"]
FX_BAL_FIELD = {"STN": "stnBal", "EUR": "eurBal", "USD": "usdBal"}
CUR_SYM = {"STN": "", "EUR": "€", "USD": "$"}


def fmt_cur(cur: str, amount: float, decimals: int | None = None) -> str:
    """Formata um valor com o símbolo/sufixo certo por moeda — mesmo
    padrão visual do fmtCur() no index.html."""
    d = decimals if decimals is not None else (0 if cur == "STN" else 2)
    if cur == "STN":
        return f"{amount:,.{d}f} STN".replace(",", ".")
    sym = CUR_SYM.get(cur, "")
    return f"{sym}{amount:,.{d}f}"


def fx_mid_rate(from_cur: str, to_cur: str) -> float:
    """Taxa 'meio' (sem spread): quantas unidades de to_cur equivalem a
    1 unidade de from_cur. Espelha fxMidRate() no index.html."""
    if from_cur == to_cur:
        return 1.0
    table = {
        "STN": {"EUR": 1 / STN_EUR, "USD": 1 / STN_USD},
        "EUR": {"STN": STN_EUR, "USD": STN_EUR / STN_USD},
        "USD": {"STN": STN_USD, "EUR": STN_USD / STN_EUR},
    }
    return table.get(from_cur, {}).get(to_cur, 1.0)


# ── CÂMBIO (FX) ──────────────────────────────────────────────────────────────
FX_SPREAD = 0.008  # 0.8% — mesma margem do index.html


def calc_fx_conversion(from_cur: str, to_cur: str, from_amount: float) -> dict:
    """Conversão aplicada (com spread) — espelha calcFxConversion()."""
    mid = fx_mid_rate(from_cur, to_cur)
    rate = mid * (1 - FX_SPREAD)
    to_amount = from_amount * rate if from_amount > 0 else 0.0
    return {"toAmount": to_amount, "rate": rate, "mid": mid}


def calc_fx_fee(from_amount: float) -> float:
    """Valor do spread 'perdido' pelo cliente — receita da plataforma,
    na moeda de origem. Espelha calcFxFee()."""
    return from_amount * FX_SPREAD if from_amount > 0 else 0.0


# ── P2P ──────────────────────────────────────────────────────────────────────
P2P_FEE_PCT = 0.005   # 0.5%
P2P_FEE_MIN = 1        # taxa mínima, na moeda de origem
P2P_MIN_AMOUNT = 10    # montante mínimo por transferência


def calc_p2p_fee(amount: float) -> float:
    return max(amount * P2P_FEE_PCT, P2P_FEE_MIN)


# ── REMESSAS INTERNACIONAIS ──────────────────────────────────────────────────
REMIT_FEE_TIERS = [
    {"max": 1000, "fee": 0.035},
    {"max": 5000, "fee": 0.028},
    {"max": float("inf"), "fee": 0.020},
]
REMIT_FLAT_FEE = 5  # taxa fixa adicional por operação


def _remit_tier(amount: float) -> dict:
    for tier in REMIT_FEE_TIERS:
        if amount <= tier["max"]:
            return tier
    return REMIT_FEE_TIERS[-1]


def calc_remit_fee(amount: float) -> float:
    tier = _remit_tier(amount)
    return amount * tier["fee"] + REMIT_FLAT_FEE


def get_remit_fee_pct(amount: float) -> str:
    tier = _remit_tier(amount)
    return f"{tier['fee'] * 100:.1f}"


REMIT_COUNTRIES = [
    "Portugal", "França", "Angola", "Cabo Verde", "Guiné-Equatorial",
    "Estados Unidos", "Brasil", "Espanha", "Outro",
]


# ── DEPÓSITO DE CRIPTO — mesmos endereços do index.html ─────────────────────
# ATENÇÃO: se um endereço mudar na app, tem de mudar aqui também — são
# strings copiadas, não uma referência à mesma fonte.
CRYPTO_ADDRESSES = {
    "BTC":  {"label": "Bitcoin",        "sym": "₿", "network": "Bitcoin (BTC nativo)",
             "address": "bc1qd7tdjts4xdc8ym5kecld3kn4s2uqmg6hd6pkd3", "min_coin": 0.0002,
             "explorer": "https://www.blockchain.com/explorer/search?search="},
    "USDT": {"label": "Tether (USDT)",  "sym": "₮", "network": "Ethereum · ERC20",
             "address": "0xB6f9A001Dcf2929962ff716dCDaB938C6FD7f4C6", "min_coin": 5,
             "explorer": "https://etherscan.io/tx/"},
    "ETH":  {"label": "Ethereum",       "sym": "Ξ", "network": "Ethereum (ERC20 nativo)",
             "address": "0xB6f9A001Dcf2929962ff716dCDaB938C6FD7f4C6", "min_coin": 0.004,
             "explorer": "https://etherscan.io/tx/"},
    "BNB":  {"label": "BNB",            "sym": "Ⓑ", "network": "BNB Smart Chain (BEP20)",
             "address": "0xB6f9A001Dcf2929962ff716dCDaB938C6FD7f4C6", "min_coin": 0.01,
             "explorer": "https://bscscan.com/tx/"},
    "SOL":  {"label": "Solana",         "sym": "◎", "network": "Solana (SOL nativo)",
             "address": "4UC6zebqY1kW8N1oEyn3W2SHqQXe8Zxfc4NJDha8Kk99", "min_coin": 0.05,
             "explorer": "https://solscan.io/tx/"},
}


def utcnow():
    return datetime.now(timezone.utc)
