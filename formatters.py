"""
formatters.py

Todos os templates de mensagem do bot, num único módulo.
Centralizar aqui garante que mudar o estilo de uma mensagem
não implica procurar em vários ficheiros.
"""

from datetime import datetime, timezone


# ── Emojis e constantes visuais ────────────────────────────────────────────
MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
COIN_EMOJI = {
    "BTC": "₿", "ETH": "⟠", "BNB": "⬡", "SOL": "◎",
    "XRP": "✕", "ADA": "₳", "DOGE": "Ð", "AVAX": "🔺",
    "MATIC": "⬟",
    "DOT": "●", "LINK": "🔗", "LTC": "Ł", "ATOM": "⚛",
    "NEAR": "Ⓝ", "XLM": "★",
}
TIER_LABEL = {
    "free": "Free", "silver": "Silver 🥈",
    "gold": "Gold 🥇", "diamond": "Diamond 💎",
}


def _pnl_arrow(pnl: float) -> str:
    return "📈" if pnl >= 0 else "📉"


def progress_bar(pct: float, width: int = 10,
                  filled_char: str = "█", empty_char: str = "░") -> str:
    """
    Barra de progresso com preenchimento fracionado (estilo mono/terminal).
    Usada em sinais (confiança) e no /meta do bot, para consistência visual.
    """
    pct = max(0.0, min(pct, 100.0))
    exact = width * pct / 100
    full = int(exact)
    partial_chars = " ▏▎▍▌▋▊▉█"
    partial_idx = round((exact - full) * (len(partial_chars) - 1))
    bar_str = filled_char * full
    if full < width:
        bar_str += partial_chars[partial_idx]
        bar_str += empty_char * (width - full - 1)
    return bar_str


def _fmt_stn(value: float, decimals: int = 0) -> str:
    """Formata um valor STN com separador de milhar (ponto)."""
    if decimals == 0:
        return f"{int(round(value)):,}".replace(",", ".")
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


# ── Rankings ───────────────────────────────────────────────────────────────

def ranking_daily(traders: list[dict], date: datetime | None = None) -> str:
    """
    Formata o ranking diário.
    Cada item de `traders` deve ter: rank, name, pnlSTN, pnlPct, tier.
    """
    date = date or datetime.now(timezone.utc)
    label = date.strftime("%-d de %B de %Y")

    lines = [
        f"🏆 *Top Traders — {label}*",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]

    for t in traders[:5]:
        rank = t.get("rank", 0)
        medal = MEDAL.get(rank, f"{rank}.")
        name = t.get("name", "Anónimo")
        pnl = t.get("pnlSTN", 0.0)
        pct = t.get("pnlPct", 0.0)
        tier = TIER_LABEL.get(t.get("tier", "free"), "")

        lines.append(
            f"{medal} *{name}*  {tier}\n"
            f"   {_pnl_arrow(pnl)} {_fmt_stn(pnl)} STN  ({_fmt_pct(pct)})"
        )

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━",
        "📊 Cless Cripto · STP",
    ]
    return "\n".join(lines)


def ranking_weekly(traders: list[dict], week_label: str = "") -> str:
    lines = [
        f"🏆 *Top 10 Traders — Semana {week_label}*",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for t in traders[:10]:
        rank = t.get("rank", 0)
        medal = MEDAL.get(rank, f"{rank}.")
        name = t.get("name", "Anónimo")
        pnl = t.get("pnlSTN", 0.0)
        pct = t.get("pnlPct", 0.0)
        vol = t.get("vol", 0.0)
        lines.append(
            f"{medal} *{name}*\n"
            f"   {_pnl_arrow(pnl)} {_fmt_stn(pnl)} STN  ({_fmt_pct(pct)})"
            f"  •  Vol: {_fmt_stn(vol)} STN"
        )
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━",
        "📊 Cless Cripto · STP",
    ]
    return "\n".join(lines)


def ranking_monthly(traders: list[dict], month_label: str = "") -> str:
    lines = [
        f"🏆 *Top 10 Traders — {month_label}*",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Os melhores do mês na Cless Cripto STP:_",
        "",
    ]
    for t in traders[:10]:
        rank = t.get("rank", 0)
        medal = MEDAL.get(rank, f"{rank}.")
        name = t.get("name", "Anónimo")
        pnl = t.get("pnlSTN", 0.0)
        pct = t.get("pnlPct", 0.0)
        tier = TIER_LABEL.get(t.get("tier", "free"), "")
        lines.append(
            f"{medal} *{name}*  {tier}\n"
            f"   {_pnl_arrow(pnl)} {_fmt_stn(pnl)} STN  ({_fmt_pct(pct)})"
        )
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📊 Cless Cripto · STP\n_Trading com responsabilidade._",
    ]
    return "\n".join(lines)


# ── Sinais de trading ──────────────────────────────────────────────────────

def signal_public(signal: dict) -> str:
    """
    Mensagem pública de sinal (canal).
    signal: {coin, direction, entry, sl, tp, reason, confidence}
    """
    coin = signal.get("coin", "BTC")
    direction = signal.get("direction", "BUY")
    entry = signal.get("entry", 0.0)
    sl = signal.get("sl", 0.0)
    tp = signal.get("tp", 0.0)
    reason = signal.get("reason", "")
    confidence = signal.get("confidence", 0)
    emoji_coin = COIN_EMOJI.get(coin, "🪙")
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    dir_label = "COMPRA" if direction == "BUY" else "VENDA"

    sl_pct = abs((sl - entry) / entry * 100) if entry else 0
    tp_pct = abs((tp - entry) / entry * 100) if entry else 0
    rr = round(tp_pct / sl_pct, 1) if sl_pct else 0

    confidence_bar = progress_bar(confidence, width=10)

    lines = [
        f"{dir_emoji} *SINAL {dir_label} — {emoji_coin} {coin}*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Entrada:      ~{entry:,.2f} €",
        f"🛑 Stop-Loss:   {sl:,.2f} €  (-{sl_pct:.1f}%)",
        f"🎯 Take-Profit: {tp:,.2f} €  (+{tp_pct:.1f}%)",
        f"⚖️  Risco/Retorno: 1:{rr}",
        "",
        f"📊 Confiança: [{confidence_bar}] {confidence}%",
    ]
    if reason:
        lines += ["", f"💡 _{reason}_"]
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ _Não é conselho financeiro. Gere sempre o teu risco._",
        "📊 Cless Cripto · STP",
    ]
    return "\n".join(lines)


def signal_admin_preview(signal: dict, signal_id: str) -> str:
    """Prévia do sinal enviada ao admin para aprovação."""
    base = signal_public(signal)
    return (
        f"🔔 *Novo sinal detetado — aguarda aprovação*\n\n"
        f"{base}\n\n"
        f"`ID: {signal_id}`"
    )


def signal_autotrade_proposal(proposal: dict) -> str:
    """
    Proposta de autotrade enviada ao user para confirmação.
    proposal: {coin, direction, entry, sl, tp, size_stn, allocated_stn, rr}
    """
    coin = proposal.get("coin", "BTC")
    direction = proposal.get("direction", "BUY")
    entry = proposal.get("entry", 0.0)
    sl = proposal.get("sl", 0.0)
    tp = proposal.get("tp", 0.0)
    size = proposal.get("size_stn", 0.0)
    allocated = proposal.get("allocated_stn", 0.0)
    rr = proposal.get("rr", 0.0)
    size_pct = round(size / allocated * 100) if allocated else 0
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    dir_label = "COMPRA" if direction == "BUY" else "VENDA"

    return "\n".join([
        f"🤖 *Proposta de Trade — {COIN_EMOJI.get(coin,'🪙')} {coin}*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"{dir_emoji} Direção:    {dir_label}",
        f"💰 Entrada:   ~{entry:,.2f} €",
        f"🛑 Stop-Loss: {sl:,.2f} €",
        f"🎯 Take-Profit: {tp:,.2f} €",
        f"⚖️  Risco/Retorno: 1:{rr}",
        "",
        f"💼 Tamanho: {_fmt_stn(size)} STN ({size_pct}% do teu limite)",
        f"   Limite alocado: {_fmt_stn(allocated)} STN",
        "━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ _Após confirmar, o bot gere SL/TP automaticamente._",
    ])


# ── Bónus referral ─────────────────────────────────────────────────────────

def referral_bonus_pending(items: list[dict]) -> str:
    """Lista de bónus pendentes enviada ao admin para aprovação."""
    lines = [
        "💰 *Bónus Referral Pendentes*",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for item in items:
        name = item.get("name", "Utilizador")
        amount = item.get("amount", 0.0)
        uid = item.get("uid", "")
        ref_count = item.get("referralCount", 0)
        lines.append(
            f"👤 *{name}*  `{uid[:8]}…`\n"
            f"   Bónus: {_fmt_stn(amount)} STN  •  {ref_count} referidos"
        )
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Total: {len(items)} pendente(s)",
    ]
    return "\n".join(lines)


def referral_bonus_approved_dm(amount: float) -> str:
    return (
        f"🎉 *Bónus de Referral Aprovado!*\n\n"
        f"Recebeste *{_fmt_stn(amount)} STN* na tua carteira Cless Cripto "
        f"como recompensa pelos teus referidos.\n\n"
        f"Continua a partilhar o teu código! 🚀"
    )


def referral_status_dm(earnings: float, count: int, pending: float) -> str:
    return "\n".join([
        "💰 *O teu estado de Referral*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"👥 Referidos:         {count}",
        f"✅ Ganhos recebidos: {_fmt_stn(earnings)} STN",
        f"⏳ Pendente:          {_fmt_stn(pending)} STN",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_O bónus é aprovado manualmente pelo admin em 24-48h._",
    ])


# ── Saldos multi-moeda (STN/EUR/USD) ────────────────────────────────────────

def _fmt_fx(cur: str, value: float) -> str:
    import fx
    return fx.fmt_cur(cur, value)


def saldos_multi_moeda(stn: float, eur: float, usd: float) -> str:
    """Resumo dos 3 saldos — usado como bloco extra em /meusaldo."""
    return "\n".join([
        "💰 *Os teus saldos*",
        f"STN: *{_fmt_fx('STN', stn)}*",
        f"EUR: *{_fmt_fx('EUR', eur)}*",
        f"USD: *{_fmt_fx('USD', usd)}*",
    ])


# ── Câmbio (FX) ──────────────────────────────────────────────────────────────

def cambio_confirm(from_cur: str, to_cur: str, from_amount: float,
                    to_amount: float, rate: float, fee: float) -> str:
    return "\n".join([
        f"🔁 *Confirmar Câmbio*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"De:  *{_fmt_fx(from_cur, from_amount)}*",
        f"Para: *{_fmt_fx(to_cur, to_amount)}*",
        f"Taxa aplicada: 1 {from_cur} = {rate:.6f} {to_cur}",
        f"_Spread da plataforma incluído: {_fmt_fx(from_cur, fee)}_",
        "━━━━━━━━━━━━━━━━━━━━━",
    ])


def cambio_done(from_cur: str, to_cur: str, from_amount: float, to_amount: float) -> str:
    now = datetime.now(timezone.utc)
    return "\n".join([
        "✅ *Câmbio Concluído*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"{_fmt_fx(from_cur, from_amount)}  →  *{_fmt_fx(to_cur, to_amount)}*",
        f"Data: {now.strftime('%d/%m/%Y %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Os saldos já estão atualizados. Usa /meusaldo para conferir._",
        "📊 Cless Cripto · STP",
    ])


# ── P2P ──────────────────────────────────────────────────────────────────────

def p2p_confirm(recipient_label: str, cur: str, amount: float, fee: float, total: float) -> str:
    return "\n".join([
        "🤝 *Confirmar Transferência P2P*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Destinatário: *{recipient_label}*",
        f"Montante: *{_fmt_fx(cur, amount)}*",
        f"Taxa P2P (0.5%): {_fmt_fx(cur, fee)}",
        f"Sai do teu saldo: *{_fmt_fx(cur, total)}*",
        "━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ _Transferências P2P são instantâneas e não são reversíveis._",
    ])


def p2p_sent_dm(recipient_name: str, cur: str, net_amount: float, recipient_notified: bool = True) -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "✅ *Transferência Concluída*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Para: *{recipient_name}*",
        f"Montante: *{_fmt_fx(cur, net_amount)}*",
        f"Data: {now.strftime('%d/%m/%Y %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    if recipient_notified:
        lines.append(f"_{recipient_name} já foi notificado(a) por Telegram._")
    else:
        lines.append(
            f"_⚠️ {recipient_name} ainda não vinculou o Telegram — o saldo já "
            f"foi creditado, mas não foi possível avisá-lo(a) por aqui._"
        )
    lines.append("📊 Cless Cripto · STP")
    return "\n".join(lines)


def p2p_received_dm(sender_name: str, cur: str, amount: float) -> str:
    now = datetime.now(timezone.utc)
    return "\n".join([
        "🤝 *Recebeste uma Transferência!*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"De: *{sender_name}*",
        f"Montante: *+{_fmt_fx(cur, amount)}*",
        f"Data: {now.strftime('%d/%m/%Y %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Usa /meusaldo para ver o teu saldo atualizado._",
        "📊 Cless Cripto · STP",
    ])


# ── Remessas internacionais ─────────────────────────────────────────────────

def remessa_confirm(direction: str, cur: str, amount: float, fee: float,
                     total: float, country: str, recipient_name: str, recipient_details: str) -> str:
    direction_label = "Enviar (sair de STP)" if direction == "enviar" else "Receber (entrar em STP)"
    lines = [
        "🌍 *Confirmar Pedido de Remessa*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Direção: *{direction_label}*",
        f"Montante: *{_fmt_fx(cur, amount)}*",
        f"Taxa: {_fmt_fx(cur, fee)}",
    ]
    if direction == "enviar":
        lines.append(f"Sai do teu saldo: *{_fmt_fx(cur, total)}*")
    lines += [
        f"País: {country}",
        f"{'Destinatário' if direction=='enviar' else 'O teu nome'}: {recipient_name}",
        f"Dados: `{recipient_details}`",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def remessa_submitted_dm(direction: str) -> str:
    reserved_note = "O saldo foi reservado. " if direction == "enviar" else ""
    now = datetime.now(timezone.utc)
    return "\n".join([
        "✅ *Pedido de Remessa Registado*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"{reserved_note}O admin vai processar através do canal internacional.",
        f"Registado em: {now.strftime('%d/%m/%Y %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Vais ser notificado assim que for concluído._",
        "📊 Cless Cripto · STP",
    ])


def remessa_admin_preview(rem: dict, rem_id: str) -> str:
    direction = rem.get("direction", "enviar")
    direction_label = "↗ ENVIAR (sair de STP)" if direction == "enviar" else "↙ RECEBER (entrar em STP)"
    cur = rem.get("cur", "EUR")
    return "\n".join([
        "🔔 *Novo pedido de remessa — aguarda aprovação*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Direção: *{direction_label}*",
        f"User: {rem.get('email','—')}",
        f"Montante: *{_fmt_fx(cur, rem.get('amount',0.0))}*",
        f"Taxa: {_fmt_fx(cur, rem.get('fee',0.0))}",
        f"País: {rem.get('country','—')}",
        f"Nome: {rem.get('recipientName','—')}",
        f"Dados: `{rem.get('recipientDetails','—')}`",
        (f"Nota: _{rem['note']}_" if rem.get("note") else ""),
        "━━━━━━━━━━━━━━━━━━━━━",
        f"`ID: {rem_id}`",
    ])


def remessa_approved_dm(direction: str, cur: str, amount: float) -> str:
    now = datetime.now(timezone.utc)
    if direction == "receber":
        body = f"*{_fmt_fx(cur, amount)}* foram creditados no teu saldo."
    else:
        body = f"A tua remessa de *{_fmt_fx(cur, amount)}* foi entregue pelo canal internacional."
    return "\n".join([
        "✅ *Remessa Confirmada*",
        "━━━━━━━━━━━━━━━━━━━━━",
        body,
        f"Confirmado em: {now.strftime('%d/%m/%Y %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Usa /meusaldo para conferir._",
        "📊 Cless Cripto · STP",
    ])


def remessa_rejected_dm(direction: str, cur: str, amount: float) -> str:
    refund_note = "O montante reservado foi devolvido ao teu saldo." if direction == "enviar" else "Nenhum valor foi debitado."
    return "\n".join([
        "❌ *Pedido de Remessa Rejeitado*",
        "━━━━━━━━━━━━━━━━━━━━━",
        refund_note,
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Contacta o suporte se achares que é um engano._",
        "📊 Cless Cripto · STP",
    ])


# ── Depósito de Cripto ───────────────────────────────────────────────────────

def crypto_deposit_confirm(coin: str, network: str, address: str, amount_coin: float) -> str:
    return "\n".join([
        f"₿ *Depósito em {coin}*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"Rede: *{network}*",
        f"Envia *{amount_coin} {coin}* para:",
        f"`{address}`",
        "━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ _Envia APENAS nesta rede. Enviar pela rede errada causa perda "
        "permanente dos fundos — não é reversível._",
    ])


def crypto_deposit_submitted_dm() -> str:
    now = datetime.now(timezone.utc)
    return "\n".join([
        "✅ *Pedido de Depósito Registado*",
        "━━━━━━━━━━━━━━━━━━━━━",
        "O admin vai confirmar a transação no block explorer e creditar",
        "o saldo STN correspondente.",
        f"Registado em: {now.strftime('%d/%m/%Y %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Normalmente demora até 1-2 confirmações da rede._",
        "📊 Cless Cripto · STP",
    ])


def crypto_deposit_admin_preview(dep: dict, dep_id: str) -> str:
    return "\n".join([
        "🔔 *Novo depósito de cripto — aguarda confirmação*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"User: {dep.get('userEmail','—')}",
        f"Moeda: *{dep.get('coin','—')}*  ({dep.get('network','—')})",
        f"Montante: *{dep.get('amountCoin',0)} {dep.get('coin','')}*",
        f"TXID: `{dep.get('txid','—')}`",
        f"Endereço usado: `{dep.get('addressUsed','—')}`",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"`ID: {dep_id}`",
        "",
        "_Confirma o TXID no block explorer antes de aprovar. Usa "
        "/confirmarcripto <ID> <valorSTN> depois de validares._",
    ])


def crypto_deposit_approved_dm(coin: str, amount_stn: float) -> str:
    now = datetime.now(timezone.utc)
    return "\n".join([
        f"✅ *Depósito de {coin} Confirmado*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"*{_fmt_stn(amount_stn)} STN* foram creditados no teu saldo.",
        f"Confirmado em: {now.strftime('%d/%m/%Y %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Usa /meusaldo para conferir._",
        "📊 Cless Cripto · STP",
    ])


def crypto_deposit_rejected_dm(coin: str) -> str:
    return "\n".join([
        f"❌ *Depósito de {coin} Rejeitado*",
        "━━━━━━━━━━━━━━━━━━━━━",
        "A transação não pôde ser validada.",
        "━━━━━━━━━━━━━━━━━━━━━",
        "_Contacta o suporte se achares que é um engano._",
        "📊 Cless Cripto · STP",
    ])

