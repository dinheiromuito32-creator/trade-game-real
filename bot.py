"""
bot.py — Cless Cripto Bot (entrypoint completo)
"""

import asyncio
import logging
import os
import random
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters as tg_filters,
)

import config
from firestore_client import init_firestore, get_db
import account_linking
import formatters
import scheduler as sched
import admin_flows
import signals_engine
import fx

logger = logging.getLogger("cless_bot.main")


# ── Configuração de Marketing ──────────────────────────────────────────────

# Fotos de marketing (resultados, dinheiro, lifestyle)
START_PHOTOS = [
    os.getenv("START_PHOTO_1", "").strip(),
    os.getenv("START_PHOTO_2", "").strip(),
]

# Foto pessoal do fundador — legenda de autoridade
FOUNDER_PHOTO = os.getenv("FOUNDER_PHOTO", "").strip()

# Vídeos promocionais
START_VIDEOS = [
    os.getenv("START_VIDEO_1", "").strip(),
    os.getenv("START_VIDEO_2", "").strip(),
]

# Legendas para fotos de marketing
PHOTO_CAPTIONS = [
    "💸 *Isto não foi sorte.*\nFoi análise, disciplina e as ferramentas certas.\n\n📲 A Cless Cripto STP está a mudar a forma como STP faz dinheiro. 🇸🇹",
    "🔥 Enquanto a maioria dorme, os traders da Cless Cripto estão a lucrar.\n\n📊 Sinais reais. Resultados reais. 💎 Junta-te a nós.",
    "💰 O mercado cripto *nunca fecha.*\nA tua oportunidade está sempre aberta. ⏰\n\n⚡ Cless Cripto STP — trading profissional ao teu alcance. 🚀",
    "📈 Pequenos investimentos consistentes criam *grandes patrimónios.*\n\n🎯 Começa hoje. A Cless Cripto guia-te em cada passo. 💪",
    "🏆 *Os resultados falam por si.*\n\nO que estás à espera? ⏳\n🚀 Cless Cripto STP — onde o conhecimento encontra o lucro. 💰",
    "💎 Não precisas de muito para começar.\nPrecisas das *pessoas certas.* 🤝\n\n🔐 Segurança, transparência e lucro real — Cless Cripto STP. 🇸🇹",
    "⚡ *O cripto não espera por ninguém.*\n\nEnquanto hesitas, outros estão a lucrar. 📈\n💡 A hora certa é sempre agora. — Cless Cripto STP",
    "🌍 São Tomé e Príncipe já chegou ao mundo cripto. 🇸🇹💚\n\n📲 Sê parte da revolução financeira.\n🔥 Cless Cripto STP — feito por nós, para nós.",
]

# Legendas para vídeos
VIDEO_CAPTIONS = [
    "🎬 *Vê como funciona na prática.*\nSimples, rápido e rentável. ✅\n\n📲 Cless Cripto STP — a plataforma que STP estava à espera. 🇸🇹",
    "⚡ *30 segundos* que podem mudar a tua vida financeira.\n\n💡 Junta-te à revolução cripto em São Tomé e Príncipe. 🚀",
    "🚀 Do zero ao primeiro lucro.\nÉ mais simples do que pensas. 💪\n\n🏆 Cless Cripto STP — trading real para pessoas reais.",
    "🔥 Enquanto assistes, os nossos traders estão a lucrar. 📊\n\n💰 Não fiques de fora.\n👉 *Abre a tua conta hoje.* — Cless Cripto STP 🇸🇹",
    "📱 *Um telemóvel. Uma conta. Infinitas oportunidades.* 💫\n\n🎯 O cripto está ao alcance de todos em STP.\n⚡ Cless Cripto — começa agora.",
]

# Legenda da foto pessoal do fundador
FOUNDER_CAPTION = (
    "👤 *Quem está por trás da Cless Cripto?*\n\n"
    "Sou o *Clessio* — trader autodidata desde jovem, "
    "pioneiro do cripto em São Tomé e Príncipe. 🇸🇹\n\n"
    "📚 Aprendi na prática, errei, corrigi.\n"
    "💡 Hoje ajudo outros a navegar o mercado com segurança e confiança.\n\n"
    "🔥 A Cless Cripto não é só uma plataforma —\n"
    "é uma *comunidade* de pessoas que querem crescer juntas. 💎\n\n"
    "📲 *Estou aqui. Vamos crescer juntos.* 🚀"
)


# ── Funções de Marketing ───────────────────────────────────────────────────

async def send_start_media(bot, chat_id: int) -> None:
    """
    Envia sequência de media de marketing:
    1. Foto de marketing (resultados/lifestyle) com legenda persuasiva
    2. Foto pessoal do fundador com legenda de autoridade
    3. Vídeo promocional com legenda de acção
    Cada item só é enviado se a variável correspondente estiver configurada.
    """
    try:
        # 1. Foto de marketing
        photos = [p for p in START_PHOTOS if p]
        if photos:
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=random.choice(photos),
                    caption=random.choice(PHOTO_CAPTIONS),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Marketing foto erro: {e}")

        # 2. Foto do fundador
        if FOUNDER_PHOTO:
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=FOUNDER_PHOTO,
                    caption=FOUNDER_CAPTION,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Founder foto erro: {e}")

        # 3. Vídeo
        videos = [v for v in START_VIDEOS if v]
        if videos:
            try:
                await bot.send_video(
                    chat_id=chat_id,
                    video=random.choice(videos),
                    caption=random.choice(VIDEO_CAPTIONS),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Marketing vídeo erro: {e}")

    except Exception as e:
        logger.error(f"send_start_media: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────

def _require_linked(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        uid = account_linking.get_uid_for_chat_id(chat_id)
        if not uid:
            await update.message.reply_text(
                "⚠️ Ainda não vinculaste a tua conta.\n"
                "Usa /start para fazeres a ligação em 1 clique."
            )
            return
        return await func(update, context, uid=uid)
    return wrapper





def _require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs):
        if not config.is_admin(update.effective_chat.id):
            await update.message.reply_text("⛔ Sem permissão.")
            return
        return await func(update, context, **kwargs)
    return wrapper


# ── /start ─────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    first_name = update.effective_user.first_name or "trader"
    if account_linking.get_uid_for_chat_id(chat_id):
        await update.message.reply_text(
            f"Olá de novo, {first_name}! ✅\nUsa /ajuda para veres todos os comandos."
        )
        await send_start_media(context.bot, chat_id)
        return
    token, link_path = account_linking.generate_link_token(chat_id)
    link_url = f"{config.APP_BASE_URL}{link_path}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔗 Vincular a minha conta", url=link_url)
    ]])
    await update.message.reply_text(
        f"👋 Olá, *{first_name}*!\n\n"
        "🚀 Bem-vindo ao bot oficial da *Cless Cripto STP* — "
        "a primeira plataforma de trading cripto de São Tomé e Príncipe.\n\n"
        "🔗 Para começares, vincula a tua conta em 1 clique:\n"
        "_(Abre o app com sessão iniciada e confirma)_\n\n"
        f"⏱ O link expira em {account_linking.TOKEN_TTL_MINUTES} minutos.",
        reply_markup=keyboard, parse_mode="Markdown",
    )
    await send_start_media(context.bot, chat_id)
    asyncio.create_task(_poll_link_confirmation(token, chat_id, context))


async def _poll_link_confirmation(token, chat_id, context):
    max_attempts = (account_linking.TOKEN_TTL_MINUTES * 60) // 5
    for _ in range(max_attempts):
        await asyncio.sleep(5)
        try:
            status_data = account_linking.get_link_status(token)
            if status_data is None:
                return
            if status_data.get("status") == "confirmed":
                uid = account_linking.finalize_link(token)
                if uid:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "✅ *Conta vinculada com sucesso!*\n\n"
                            "💎 Estás oficialmente dentro da Cless Cripto STP.\n\n"
                            "🔥 O que podes fazer agora:\n"
                            "• /ranking — vê os melhores traders\n"
                            "• /meusaldo — o teu saldo e estatísticas\n"
                            "• /sinais on — recebe sinais de trading\n"
                            "• /autotrade — deixa o bot operar por ti\n\n"
                            "🚀 _O mercado nunca fecha. Começa agora._"
                        ),
                        parse_mode="Markdown",
                    )
                return
        except Exception as e:
            logger.warning(f"_poll_link_confirmation erro: {e}")
            continue


# ── /ajuda ─────────────────────────────────────────────────────────────────

async def ajuda_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not account_linking.get_uid_for_chat_id(chat_id):
        await update.message.reply_text("Ainda não vinculaste a tua conta. Usa /start.")
        return
    lines = [
        "🎯 *Cless Cripto Bot — Comandos*", "",
        "📊 *Trading & Mercado*",
        "/mercado — preços actuais de todas as moedas",
        "/ranking — top traders (hoje / semana / mês)",
        "/sinais on|off — receber sinais de trading por DM",
        "/autotrade — deixar o bot operar automaticamente",
        "/posicao — ver PnL da posição aberta agora",
        "",
        "👤 *A tua conta*",
        "/meusaldo — saldo, PnL e posições abertas",
        "/meta — progresso das metas mensal/anual",
        "/meubonus — bónus de referral pendentes",
        "",
        "💱 *Câmbio & Transferências*",
        "/cambio — converter entre STN/EUR/USD",
        "/p2p — enviar para outro user, por email",
        "/remessa — pedido de remessa internacional",
        "/depositocripto — registar um depósito de cripto",
        "_(toca num botão abaixo para ver a sintaxe exacta de cada um)_",
        "",
        "ℹ️ /ajuda — esta mensagem",
    ]
    if config.is_admin(chat_id):
        lines += [
            "",
            "🔧 *Admin*",
            "/admin — painel administrativo",
            "/saldo <email> — saldo de qualquer utilizador",
        ]
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔁 /cambio", callback_data="ajuda_ref:cambio"),
            InlineKeyboardButton("🤝 /p2p", callback_data="ajuda_ref:p2p"),
        ],
        [
            InlineKeyboardButton("🌍 /remessa", callback_data="ajuda_ref:remessa"),
            InlineKeyboardButton("₿ /depositocripto", callback_data="ajuda_ref:cripto"),
        ],
    ])
    await update.message.reply_text("\n".join(lines), reply_markup=keyboard, parse_mode="Markdown")


async def ajuda_ref_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Botões do /ajuda para Câmbio/P2P/Remessa/Depósito Cripto — só
    MOSTRAM a sintaxe do comando (reaproveitando o mesmo texto "Uso: ..."
    que cada comando já usa quando é chamado sem argumentos). Não
    executam nada, não abrem nenhum fluxo — são só uma chamada de
    atenção para o formato certo. Quem quer usar o comando escreve-o.
    """
    query = update.callback_query
    await query.answer()
    which = query.data.split(":", 1)[1]
    uso_por_comando = {
        "cambio": CAMBIO_USO,
        "p2p": P2P_USO,
        "remessa": REMESSA_USO,
        "cripto": _cripto_uso(),
    }
    texto = uso_por_comando.get(which)
    if not texto:
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=texto, parse_mode="Markdown",
    )


# ── /ranking ───────────────────────────────────────────────────────────────

@_require_linked
async def ranking_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    args = context.args
    period = args[0].lower() if args else "semana"
    await update.message.reply_text("⏳ A carregar ranking…")
    try:
        if period in ("hoje", "dia"):
            traders = await sched._fetch_daily_leaders(limit=5)
            msg = formatters.ranking_daily(traders)
        elif period in ("mes", "mês"):
            traders = await sched._fetch_monthly_leaders(limit=10)
            month_label = datetime.now(timezone.utc).strftime("%B %Y").capitalize()
            msg = formatters.ranking_monthly(traders, month_label=month_label)
        else:
            traders = await sched._fetch_weekly_leaders(limit=10)
            now = datetime.now(timezone.utc)
            week_start = now - timedelta(days=now.weekday() + 1 if now.weekday() != 6 else 0)
            week_label = f"{week_start.strftime('%-d/%m')} – {now.strftime('%-d/%m/%Y')}"
            msg = formatters.ranking_weekly(traders, week_label=week_label)
        if not traders:
            await update.message.reply_text(
                "📭 Ainda sem trades registados neste período.\n"
                "Os rankings actualizam quando os traders fecham posições."
            )
            return
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"ranking_command: {e}")
        await update.message.reply_text("⚠️ Erro ao obter o ranking.")


# ── /meubonus ──────────────────────────────────────────────────────────────

@_require_linked
async def meubonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    db = get_db()
    try:
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            await update.message.reply_text("⚠️ Conta não encontrada.")
            return
        data     = doc.to_dict()
        earnings = data.get("referralEarnings", 0.0)
        count    = data.get("referralCount", 0)
        paid     = data.get("totalReferralPaid", 0.0)
        await update.message.reply_text(
            formatters.referral_status_dm(paid, count, earnings), parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"meubonus_command: {e}")
        await update.message.reply_text("⚠️ Erro ao obter os teus dados.")


# ── /sinais ────────────────────────────────────────────────────────────────

@_require_linked
async def sinais_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    args = context.args
    if not args or args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Uso: /sinais on  ou  /sinais off")
        return
    enabled = args[0].lower() == "on"
    try:
        get_db().collection("telegramLinks").document(uid).update({"signalsEnabled": enabled})
        if enabled:
            await update.message.reply_text(
                "🔔 *Sinais activados!*\n\n"
                "📲 Vais receber alertas directamente aqui sempre que "
                "o nosso engine detectar uma oportunidade aprovada.\n\n"
                "💡 _Para desactivar: /sinais off_"
            , parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "🔕 Sinais desactivados.\n\n"
                "_Podes reactivar a qualquer momento com /sinais on_"
            , parse_mode="Markdown")
    except Exception as e:
        logger.error(f"sinais_command: {e}")
        await update.message.reply_text("⚠️ Erro ao guardar a preferência.")


# ── /meusaldo ──────────────────────────────────────────────────────────────

@_require_linked
async def meusaldo_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    """Mostra o saldo, portfólio e stats do próprio utilizador."""
    db = get_db()
    try:
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            await update.message.reply_text("⚠️ Conta não encontrada.")
            return
        d = doc.to_dict()
        stn_bal  = d.get("stnBal", 0.0) or 0.0
        eur_bal  = d.get("eurBal", 0.0) or 0.0
        usd_bal  = d.get("usdBal", 0.0) or 0.0
        vol      = d.get("vol", 0.0) or 0.0
        tier     = d.get("tier", "free")
        port     = d.get("port", {}) or {}
        kyc      = "✅" if d.get("kycDone") else "❌"
        name     = d.get("name", "—")

        # Leaderboard para PnL total
        lb = db.collection("leaderboard").document(uid).get()
        pnl_total = lb.to_dict().get("pnl", 0.0) if lb.exists else 0.0
        wins      = lb.to_dict().get("wins", 0)   if lb.exists else 0
        losses    = lb.to_dict().get("losses", 0) if lb.exists else 0

        # Posições abertas
        open_pos = [c for c, p in port.items() if p.get("qty", 0) > 0]
        pos_str  = ", ".join(open_pos) if open_pos else "Nenhuma"
        pnl_sign = "+" if pnl_total >= 0 else ""

        # Só mostra a linha de EUR/USD se o user tiver algum saldo nessas
        # moedas — a maioria ainda só opera em STN, e uma linha "€0.00 /
        # $0.00" sempre visível seria ruído para esses users.
        fx_line = ""
        if eur_bal > 0.0001 or usd_bal > 0.0001:
            fx_line = f"💶 *EUR:* {fx.fmt_cur('EUR', eur_bal)}  |  💵 *USD:* {fx.fmt_cur('USD', usd_bal)}\n"

        await update.message.reply_text(
            f"👤 *{name}*\n"
            f"KYC: {kyc} | Tier: {tier.upper()}\n\n"
            f"💰 *Saldo disponível:* {stn_bal:,.0f} STN\n"
            f"{fx_line}"
            f"📊 *Volume total:* {vol:,.0f} STN\n"
            f"📈 *PnL total:* {pnl_sign}{pnl_total:,.0f} STN\n"
            f"🏆 *Trades:* {wins + losses} ({wins}W / {losses}L)\n\n"
            f"📂 *Posições abertas:* {pos_str}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"meusaldo_command: {e}")
        await update.message.reply_text("⚠️ Erro ao obter os teus dados.")


# ── /saldo (admin) ─────────────────────────────────────────────────────────

@_require_linked
@_require_admin
async def saldo_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    """Admin: ver saldo de qualquer utilizador por email."""
    args = context.args
    if not args:
        await update.message.reply_text("Uso: /saldo <email>\nExemplo: /saldo user@email.com")
        return
    email = args[0].strip().lower()
    db = get_db()
    try:
        from google.cloud.firestore_v1 import FieldFilter
        docs = list(
            db.collection("users")
            .where(filter=FieldFilter("email", "==", email))
            .limit(1)
            .stream()
        )
        if not docs:
            await update.message.reply_text(f"⚠️ Utilizador `{email}` não encontrado.", parse_mode="Markdown")
            return
        d    = docs[0].to_dict()
        uid2 = docs[0].id
        stn_bal  = d.get("stnBal", 0.0) or 0.0
        vol      = d.get("vol", 0.0) or 0.0
        tier     = d.get("tier", "free")
        name     = d.get("name", "—")
        kyc      = "✅" if d.get("kycDone") else "❌"
        port     = d.get("port", {}) or {}
        open_pos = [c for c, p in port.items() if p.get("qty", 0) > 0]

        lb = db.collection("leaderboard").document(uid2).get()
        pnl_total = lb.to_dict().get("pnl", 0.0) if lb.exists else 0.0
        wins      = lb.to_dict().get("wins", 0)   if lb.exists else 0
        losses    = lb.to_dict().get("losses", 0) if lb.exists else 0
        pnl_sign  = "+" if pnl_total >= 0 else ""

        await update.message.reply_text(
            f"👤 *{name}* (`{email}`)\n"
            f"KYC: {kyc} | Tier: {tier.upper()}\n\n"
            f"💰 Saldo: *{stn_bal:,.0f} STN*\n"
            f"📊 Volume: {vol:,.0f} STN\n"
            f"📈 PnL total: {pnl_sign}{pnl_total:,.0f} STN\n"
            f"🏆 Trades: {wins + losses} ({wins}W / {losses}L)\n"
            f"📂 Posições abertas: {', '.join(open_pos) or 'Nenhuma'}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"saldo_admin_command: {e}")
        await update.message.reply_text("⚠️ Erro ao obter dados do utilizador.")


# ── /meta ──────────────────────────────────────────────────────────────────

@_require_linked
async def meta_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    """
    Mostra o progresso de Metas Mensal/Anual, replicando exatamente a lógica
    do card 'Lucros Acumulados' no Dashboard do index.html:
      - Meta mensal começa em 500 STN e duplica sempre que é batida.
      - Meta anual começa em 6.000 STN e duplica sempre que é batida.
      - Apenas lucros contam (perdas não reduzem o progresso).
    """
    db = get_db()
    try:
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            await update.message.reply_text("⚠️ Conta não encontrada.")
            return
        d = doc.to_dict()
        monthly_pnl = d.get("monthlyPnl", {}) or {}

        now = datetime.now(timezone.utc)
        month_key = f"{now.year}-{now.month:02d}"
        year_key  = str(now.year)
        meses_nome = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                      "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

        month_profit = max(0.0, (monthly_pnl.get(month_key, {}) or {}).get("pnl", 0.0) or 0.0)
        annual_profit = sum(
            max(0.0, (v or {}).get("pnl", 0.0) or 0.0)
            for k, v in monthly_pnl.items() if k.startswith(year_key)
        )

        def calc_meta(profit: float, base: float) -> float:
            m = base
            while profit >= m:
                m *= 2
            return m

        meta_mes = calc_meta(month_profit, 500)
        meta_mes_prev = meta_mes / 2
        meta_ano = calc_meta(annual_profit, 6000)
        meta_ano_prev = meta_ano / 2

        import math
        nivel_mes = int(math.log2(meta_mes / 500))
        nivel_ano = int(math.log2(meta_ano / 6000))

        pct_mes = 0.0 if meta_mes == meta_mes_prev else min(
            (month_profit - meta_mes_prev) / (meta_mes - meta_mes_prev) * 100, 100)
        pct_ano = 0.0 if meta_ano == meta_ano_prev else min(
            (annual_profit - meta_ano_prev) / (meta_ano - meta_ano_prev) * 100, 100)

        def fmt_meta(v: float) -> str:
            if v >= 1000:
                return f"{v/1000:.1f}".rstrip("0").rstrip(".") + "K" if v % 1000 else f"{int(v/1000)}K"
            return str(int(v))

        def bar(pct: float) -> str:
            return formatters.progress_bar(pct, width=14)

        lvl_mes_str = f" · 🏅 LVL {nivel_mes + 1}" if nivel_mes > 0 else ""
        lvl_ano_str = f" · 🏅 LVL {nivel_ano + 1}" if nivel_ano > 0 else ""

        if pct_mes >= 100:
            linha_mes = f"🏆 Próxima meta: {fmt_meta(meta_mes * 2)} STN"
        else:
            linha_mes = f"Faltam {max(0, meta_mes - month_profit):,.0f} STN para {fmt_meta(meta_mes)} STN"

        if pct_ano >= 100:
            linha_ano = f"🏆 Próxima meta: {fmt_meta(meta_ano * 2)} STN"
        else:
            linha_ano = f"Faltam {max(0, meta_ano - annual_profit):,.0f} STN para {fmt_meta(meta_ano)} STN"

        msg = (
            f"🎯 *Metas — {meses_nome[now.month - 1]}/{year_key}*\n"
            f"_Apenas lucros contam · perdas não reduzem o progresso_\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 *Este mês*{lvl_mes_str}\n"
            f"`{bar(pct_mes)}` *{pct_mes:.0f}%*\n"
            f"+{month_profit:,.0f} STN\n"
            f"{linha_mes}\n\n"
            f"🟡 *{year_key} (ano)*{lvl_ano_str}\n"
            f"`{bar(pct_ano)}` *{pct_ano:.0f}%*\n"
            f"+{annual_profit:,.0f} STN\n"
            f"{linha_ano}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"meta_command: {e}")
        await update.message.reply_text("⚠️ Erro ao obter o teu progresso de metas.")


# ── /idemoji (admin) ─────────────────────────────────────────────────────────

@_require_admin
async def idemoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Extrai os custom_emoji_id de emoji Premium colados na própria mensagem.
    Uso: adiciona o pack de emoji no Telegram (ex: t.me/addemoji/CryptoPJ),
    depois envia: /idemoji 🔥🚀💎  (colando os emoji premium do teclado)
    O bot responde com o ID real de cada um, pronto a usar no código.
    """
    msg = update.message
    entities = msg.parse_entities(types=["custom_emoji"])
    if not entities:
        await msg.reply_text(
            "⚠️ Não encontrei nenhum emoji Premium nesta mensagem.\n\n"
            "Como usar:\n"
            "1. Abre o link do pack (ex: t.me/addemoji/CryptoPJ) e clica em Adicionar.\n"
            "2. Abre o teclado de emoji no Telegram, escolhe os emoji desse pack.\n"
            "3. Envia aqui: /idemoji seguido dos emoji colados.\n\n"
            "_Nota: emoji normais (não Premium) não têm ID e são ignorados._",
            parse_mode="Markdown",
        )
        return

    lines = ["🆔 *Custom Emoji IDs encontrados:*", ""]
    for entity, text in entities.items():
        lines.append(f"`{entity.custom_emoji_id}`  →  {text}")
    lines.append("")
    lines.append("_Copia os IDs e diz-me a que moeda/uso cada um corresponde._")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /mercado ───────────────────────────────────────────────────────────────

@_require_linked
async def mercado_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    """Mostra preços actuais de todas as moedas da Cless Cripto."""
    await update.message.reply_text("⏳ A carregar preços…")
    try:
        import aiohttp
        COIN_EMOJI = formatters.COIN_EMOJI
        COINS = signals_engine.COINS

        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json()

            async with session.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "EURUSDT"},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as r2:
                eur_data = await r2.json()

        eur_usdt = float(eur_data.get("price", 1))
        price_map = {item["symbol"]: item for item in data}

        lines = ["📊 *Mercado Cless Cripto — Agora*", ""]
        for coin in COINS:
            symbol = f"{coin}USDT"
            item = price_map.get(symbol)
            if not item:
                continue
            price_eur = float(item["lastPrice"]) / eur_usdt
            chg = float(item["priceChangePercent"])
            arrow = "🟢" if chg >= 0 else "🔴"
            sign  = "+" if chg >= 0 else ""
            emoji = COIN_EMOJI.get(coin, "🪙")
            lines.append(f"{arrow} {emoji} *{coin}*: {price_eur:,.2f}€  ({sign}{chg:.2f}%)")

        lines += ["", f"_Actualizado: {datetime.now(timezone.utc).strftime('%H:%M UTC')}_"]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"mercado_command: {e}")
        await update.message.reply_text("⚠️ Erro ao obter preços. Tenta novamente.")


# ── /autotrade ─────────────────────────────────────────────────────────────

@_require_linked
def _format_close_result(result: dict) -> str:
    """Formatação partilhada entre /autotrade fechar e o callback de escolha de posição."""
    coin_emoji = formatters.COIN_EMOJI.get(result["coin"], "🪙")
    dir_label  = "COMPRA" if result["direction"] == "BUY" else "VENDA"
    sign = "+" if result["pnl_stn"] >= 0 else ""
    return (
        f"✅ *Posição fechada manualmente*\n\n"
        f"{coin_emoji} {result['coin']} — {dir_label}\n"
        f"Entrada: {result['avgEntry']:,.2f} €\n"
        f"Saída: {result['exit_price']:,.2f} €\n"
        f"Qtd: {result['qty']:.6f} {result['coin']}\n\n"
        f"PnL: *{sign}{result['pnl_stn']:.0f} STN*"
    )


@_require_linked
async def autotrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    from autotrade import get_autotrade_settings, deactivate_autotrade

    args = context.args
    sub  = args[0].lower() if args else ""
    settings = get_autotrade_settings(uid)

    # ── sem argumento: mostrar estado ──
    if not sub:
        if settings and settings.get("active"):
            available = settings.get("availableSTN", 0.0)
            allocated = settings.get("allocatedSTN", 0.0)
            total_pnl = settings.get("totalPnlSTN", 0.0)
            total_tr  = settings.get("totalTrades", 0)
            status_label = {
                "awaiting_signal": "🟢 À espera de sinal",
                "in_position":     "📊 Em posição",
                "paused":          "⏸ Pausado (circuit breaker)",
            }.get(settings.get("status", ""), "—")
            sign = "+" if total_pnl >= 0 else ""
            await update.message.reply_text(
                f"🤖 *Autotrade Ativo*\n\n"
                f"Estado: {status_label}\n"
                f"Alocado: {allocated:.0f} STN\n"
                f"Disponível: {available:.0f} STN\n"
                f"PnL total: *{sign}{total_pnl:.0f} STN*\n"
                f"Trades: {total_tr}\n\n"
                f"Usa /autotrade off para desativar.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "🤖 *Autotrade — Como funciona*\n\n"
                "O bot deteta oportunidades com alta confiança (rank 7-10) "
                "em todas as moedas da Cless Cripto e envia-te uma proposta por DM.\n"
                "Tu approvas com 1 clique — depois o SL/TP corre automaticamente.\n\n"
                "📌 *Regras de risco:*\n"
                "• Máx 15% do teu limite por trade\n"
                "• 1 posição de cada vez\n"
                "• SL obrigatório em todo o trade\n"
                "• Pausa automática após 3 perdas seguidas\n\n"
                "⚠️ _Risco de perda. Nunca aloca mais do que podes perder._\n\n"
                "Para ativar: /autotrade on 500",
                parse_mode="Markdown",
            )
        return

    # ── off ──
    if sub == "off":
        if not settings or not settings.get("active"):
            await update.message.reply_text("O autotrade já está desativado.")
            return
        returned = deactivate_autotrade(uid)
        await update.message.reply_text(f"✅ Autotrade desativado. {returned:.0f} STN devolvidos.")
        return

    # ── resumir após circuit breaker ──
    if sub == "resumir":
        if not settings or not settings.get("active"):
            await update.message.reply_text("O autotrade não está ativo.")
            return
        get_db().collection("autoTradeSettings").document(uid).update({
            "status": "awaiting_signal", "consecutiveLosses": 0,
        })
        await update.message.reply_text("✅ Autotrade reativado. O bot retoma a monitorização.")
        return

    # ── on <valor> ──
    if sub == "on":
        # AVISO: já existe autotrade ativo
        if settings and settings.get("active"):
            available = settings.get("availableSTN", 0.0)
            allocated = settings.get("allocatedSTN", 0.0)
            await update.message.reply_text(
                f"⚠️ *Já tens o autotrade ativo!*\n\n"
                f"Alocado: {allocated:.0f} STN\n"
                f"Disponível: {available:.0f} STN\n\n"
                f"Para alterar o valor, desativa primeiro com /autotrade off "
                f"(o saldo é devolvido) e depois ativa de novo com o novo valor.",
                parse_mode="Markdown",
            )
            return

        if len(args) < 2:
            await update.message.reply_text("Indica o valor. Exemplo: /autotrade on 500")
            return
        try:
            amount = float(args[1].replace(",", "."))
        except ValueError:
            await update.message.reply_text("Valor inválido. Exemplo: /autotrade on 500")
            return
        if amount < 70:
            await update.message.reply_text(
                "Mínimo: 70 STN.\n\n"
                "_(O bot aloca 15% por trade — abaixo de 70 STN o valor por "
                "operação ficaria demasiado pequeno para operar.)_",
                parse_mode="Markdown",
            )
            return

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"✅ Concordo e Ativo ({amount:.0f} STN)",
                callback_data=f"at_activate:{uid}:{amount}"
            ),
            InlineKeyboardButton("❌ Cancelar", callback_data="at_activate_cancel"),
        ]])
        await update.message.reply_text(
            f"⚠️ *Aviso de Risco*\n\n"
            f"Vais alocar *{amount:.0f} STN* ao autotrade.\n"
            f"O bot vai operar automaticamente nas moedas da Cless Cripto "
            f"quando detetar sinais de rank 7 a 10.\n"
            f"Podes perder parte ou todo o capital alocado. "
            f"A Cless Cripto não garante lucro.\n\n"
            f"Podes desativar a qualquer momento com /autotrade off.",
            reply_markup=keyboard, parse_mode="Markdown",
        )
        return

    # ── fechar posição(ões) manualmente, ao preço de mercado ──
    if sub == "fechar":
        # Não filtramos por settings.status aqui de propósito: esse campo
        # pode ficar dessincronizado. A fonte de verdade é sempre a
        # posição real em autoTradePositions.
        from sltp_engine import close_position_manual, get_all_positions_pnl

        # /autotrade fechar BTC — fecha só essa moeda, direto, sem perguntar
        if len(args) >= 2:
            coin_arg = args[1].upper()
            positions = await get_all_positions_pnl(uid)
            match = next((p for p in positions if p["coin"] == coin_arg), None)
            if not match:
                await update.message.reply_text(f"Não tens posição aberta em {coin_arg}.")
                return
            await update.message.reply_text(f"⏳ A fechar {coin_arg} ao preço de mercado…")
            result = await close_position_manual(context.bot, uid, position_id=match["position_id"])
            if not result:
                await update.message.reply_text("⚠️ Não foi possível fechar. Tenta novamente.")
                return
            await update.message.reply_text(_format_close_result(result), parse_mode="Markdown")
            return

        # /autotrade fechar sem moeda — decide consoante quantas há
        positions = await get_all_positions_pnl(uid)

        if not positions:
            await update.message.reply_text("Não há posição aberta neste momento.")
            return

        if len(positions) == 1:
            await update.message.reply_text("⏳ A fechar posição ao preço de mercado…")
            result = await close_position_manual(context.bot, uid, position_id=positions[0]["position_id"])
            if not result:
                await update.message.reply_text("⚠️ Não foi possível fechar. Tenta novamente.")
                return
            await update.message.reply_text(_format_close_result(result), parse_mode="Markdown")
            return

        # Várias posições abertas — pergunta qual, com botões
        buttons = []
        for p in positions:
            coin_emoji = formatters.COIN_EMOJI.get(p["coin"], "🪙")
            sign = "+" if p["pnl_stn"] >= 0 else ""
            label = f"{coin_emoji} {p['coin']} — {sign}{p['pnl_stn']:.0f} STN"
            buttons.append([InlineKeyboardButton(label, callback_data=f"close_pos:{uid}:{p['position_id']}")])
        buttons.append([InlineKeyboardButton("🔴 Fechar todas", callback_data=f"close_pos_all:{uid}")])

        await update.message.reply_text(
            f"Tens {len(positions)} posições abertas — qual queres fechar?\n"
            f"(ou usa /autotrade fechar <moeda> diretamente, ex: /autotrade fechar BTC)",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    await update.message.reply_text("Uso: /autotrade  |  /autotrade on 500  |  /autotrade off  |  /autotrade fechar [moeda]")


async def autotrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from autotrade import (
        activate_autotrade, execute_trade, reject_trade,
        CB_AUTOTRADE_CONFIRM, CB_AUTOTRADE_REJECT,
    )
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("at_activate:"):
        parts  = data.split(":")
        uid    = parts[1]
        amount = float(parts[2])
        try:
            activate_autotrade(uid, amount)
            await query.edit_message_text(
                f"✅ *Autotrade ativado com {amount:.0f} STN!*\n\n"
                "O bot começa a monitorizar o mercado agora. "
                "Quando detetar um sinal de rank 7-10, enviar-te-á uma proposta aqui.",
                parse_mode="Markdown",
            )
        except ValueError as e:
            await query.edit_message_text(f"⚠️ {e}")
        except Exception as e:
            await query.edit_message_text(f"⛔ Erro: {e}")
        return

    if data == "at_activate_cancel":
        await query.edit_message_text("Ativação cancelada.")
        return

    if data.startswith(CB_AUTOTRADE_CONFIRM):
        # Feedback imediato — a transação Firestore em si (execute_trade)
        # ainda vai levar algum tempo real de rede; isto só evita a sensação
        # de "não aconteceu nada" nos primeiros segundos após o clique.
        await query.edit_message_text("⏳ A executar…")
        await execute_trade(data[len(CB_AUTOTRADE_CONFIRM):], query)
        return

    if data.startswith(CB_AUTOTRADE_REJECT):
        await reject_trade(data[len(CB_AUTOTRADE_REJECT):], query)
        return


async def close_position_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler dos botões de "qual posição fechar" (quando há mais do que
    uma aberta) e de "fechar todas". O uid vem embutido no callback_data
    — mesmo padrão de segurança já usado em at_activate: só o dono vê
    este teclado, porque foi enviado em DM privada só a ele.
    """
    from sltp_engine import close_position_manual
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("close_pos_all:"):
        uid = data[len("close_pos_all:"):]
        await query.edit_message_text("⏳ A fechar todas as posições…")
        results = []
        for _ in range(5):  # limite de segurança — nunca deviam existir tantas
            result = await close_position_manual(context.bot, uid)
            if not result:
                break
            results.append(result)
        if not results:
            await query.message.reply_text("Não há posição aberta neste momento.")
            return
        for result in results:
            await query.message.reply_text(_format_close_result(result), parse_mode="Markdown")
        return

    if data.startswith("close_pos:"):
        _, uid, position_id = data.split(":", 2)
        await query.edit_message_text("⏳ A fechar posição…")
        result = await close_position_manual(context.bot, uid, position_id=position_id)
        if not result:
            await query.message.reply_text("⚠️ Não foi possível fechar essa posição (pode já ter sido fechada).")
            return
        await query.message.reply_text(_format_close_result(result), parse_mode="Markdown")
        return


@_require_linked
async def posicao_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    """Mostra TODAS as posições abertas do autotrade e o PnL não realizado, ao preço de mercado agora."""
    from sltp_engine import get_all_positions_pnl

    await update.message.reply_text("⏳ A calcular P&L…")
    try:
        results = await get_all_positions_pnl(uid)
    except Exception as e:
        logger.error(f"posicao_command: {e}")
        await update.message.reply_text("⚠️ Erro ao obter a posição.")
        return

    if not results:
        await update.message.reply_text(
            "📭 Não tens nenhuma posição aberta pelo autotrade neste momento.\n\n"
            "Usa /autotrade para ver o estado geral."
        )
        return

    if len(results) > 1:
        await update.message.reply_text(
            f"⚠️ Tens {len(results)} posições abertas em simultâneo — "
            f"não devia acontecer por design, mas aqui estão todas:"
        )

    for result in results:
        coin_emoji = formatters.COIN_EMOJI.get(result["coin"], "🪙")
        dir_label  = "COMPRA" if result["direction"] == "BUY" else "VENDA"
        sign = "+" if result["pnl_stn"] >= 0 else ""
        pnl_emoji = "📈" if result["pnl_stn"] >= 0 else "📉"
        trailing_note = "\n🔒 _Trailing stop ativo — SL a seguir o lucro_" if result.get("trailing_active") else ""

        await update.message.reply_text(
            f"📊 *Posição Aberta — {coin_emoji} {result['coin']}*\n\n"
            f"Direção: {dir_label}\n"
            f"Entrada: {result['avgEntry']:,.2f} €\n"
            f"Preço atual: {result['current_price']:,.2f} €\n"
            f"Qtd: {result['qty']:.6f} {result['coin']}\n"
            f"Tamanho: {result['size_stn']:.0f} STN\n"
            f"SL: {result['sl']:,.2f} €  |  TP: {result['tp']:,.2f} €{trailing_note}\n\n"
            f"{pnl_emoji} PnL não realizado: *{sign}{result['pnl_stn']:.0f} STN*\n\n"
            f"_Usa /autotrade fechar para fechar já ao preço de mercado._",
            parse_mode="Markdown",
        )


# ── /admin ─────────────────────────────────────────────────────────────────

@_require_linked
@_require_admin
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Ver bónus pendentes",  callback_data="admin_bonuses")],
        [InlineKeyboardButton("📡 Correr scan de sinais", callback_data="admin_scan")],
        [InlineKeyboardButton("📥 Sinais pendentes",      callback_data="admin_pending_signals")],
        [InlineKeyboardButton("🌍 Remessas pendentes",    callback_data="admin_pending_remittances")],
        [InlineKeyboardButton("₿ Depósitos cripto pendentes", callback_data="admin_pending_cryptodeposits")],
        [InlineKeyboardButton("📊 Publicar ranking agora", callback_data="admin_ranking")],
    ])
    await update.message.reply_text(
        "🔧 *Painel Admin — Cless Cripto Bot*",
        reply_markup=keyboard, parse_mode="Markdown",
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not config.is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Sem permissão.")
        return
    data = query.data

    if data == "admin_bonuses":
        await query.edit_message_text("⏳ A carregar…")
        await admin_flows.send_pending_bonuses_to_admins(context.bot)
        await query.edit_message_text("✅ Lista enviada.")

    elif data == "admin_scan":
        await query.edit_message_text("⏳ A correr scan (~30s)…")
        async def _notify(signal, signal_id):
            await admin_flows.send_signal_for_approval(context.bot, signal, signal_id)
        await signals_engine.run_scan(_notify)
        await query.edit_message_text("✅ Scan concluído.")

    elif data == "admin_pending_signals":
        await query.edit_message_text("⏳ A procurar sinais pendentes…")
        count = await admin_flows.resend_pending_signals(context.bot)
        if count == 0:
            await query.edit_message_text("✅ Não há sinais pendentes — tudo já foi publicado ou descartado.")
        else:
            await query.edit_message_text(f"📥 {count} sinal(is) pendente(s) reenviado(s) acima, com botões para decidires.")

    elif data == "admin_pending_remittances":
        await query.edit_message_text("⏳ A procurar remessas pendentes…")
        count = await admin_flows.send_pending_remittances_to_admins(context.bot)
        if count == 0:
            await query.edit_message_text("✅ Não há remessas pendentes.")
        else:
            await query.edit_message_text(f"🌍 {count} remessa(s) pendente(s) enviada(s) acima, com botões para decidires.")

    elif data == "admin_pending_cryptodeposits":
        await query.edit_message_text("⏳ A procurar depósitos de cripto pendentes…")
        count = await admin_flows.send_pending_crypto_deposits_to_admins(context.bot)
        if count == 0:
            await query.edit_message_text("✅ Não há depósitos de cripto pendentes.")
        else:
            await query.edit_message_text(
                f"₿ {count} depósito(s) pendente(s) enviado(s) acima. Valida o TXID no block "
                f"explorer e usa /confirmarcripto <ID> <valorSTN> para aprovar."
            )

    elif data == "admin_ranking":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Diário",  callback_data="pub_rank_daily")],
            [InlineKeyboardButton("Semanal", callback_data="pub_rank_weekly")],
            [InlineKeyboardButton("Mensal",  callback_data="pub_rank_monthly")],
        ])
        await query.edit_message_text("Qual ranking?", reply_markup=keyboard)

    elif data == "pub_rank_daily":
        await sched.publish_daily_ranking(context.bot)
        await query.edit_message_text("✅ Ranking diário publicado.")
    elif data == "pub_rank_weekly":
        await sched.publish_weekly_ranking(context.bot)
        await query.edit_message_text("✅ Ranking semanal publicado.")
    elif data == "pub_rank_monthly":
        await sched.publish_monthly_ranking(context.bot)
        await query.edit_message_text("✅ Ranking mensal publicado.")


# ── Engine de autotrade: scan periódico ────────────────────────────────────

async def _autotrade_scan_job(bot) -> None:
    """
    Job que corre a cada 5min. Para cada user com autotrade ativo
    e status 'awaiting_signal', analisa todas as moedas da Cless Cripto.
    Só envia proposta se a confiança for >= 70% (rank 7-10 em escala de 10).
    """
    from autotrade import get_active_autotrade_users, send_trade_proposal
    AUTOTRADE_MIN_CONFIDENCE = 60  # ajustado pelo utilizador (era 70)

    users = get_active_autotrade_users()
    if not users:
        return

    logger.info(f"Autotrade scan: {len(users)} utilizador(es) ativos.")

    # Taxa EUR/USDT — necessária para converter entry/sl/tp para EUR,
    # já que autotrade.py e sltp_engine.py trabalham inteiramente em EUR.
    eur_usdt = await signals_engine.get_eur_usdt_rate()

    # Analisa todas as moedas uma vez para todos os users
    for coin in signals_engine.COINS:
        try:
            candles = await signals_engine.fetch_klines(
                coin, signals_engine.TIMEFRAME, signals_engine.CANDLES_LIMIT
            )
            if len(candles) < 210:
                continue
            htf_candles = await signals_engine.fetch_klines(
                coin, signals_engine.HTF_TIMEFRAME, signals_engine.HTF_CANDLES_LIMIT
            )
            signal = signals_engine._analyze(coin, candles, eur_usdt, htf_candles)
            if not signal:
                continue
            if signal["confidence"] < AUTOTRADE_MIN_CONFIDENCE:
                logger.debug(
                    f"Autotrade scan: {coin} confiança {signal['confidence']}% "
                    f"abaixo do mínimo {AUTOTRADE_MIN_CONFIDENCE}%."
                )
                continue
            # Sinal de rank 7-10 detectado — envia proposta a todos os users elegíveis
            logger.info(
                f"Autotrade: sinal {coin} {signal['direction']} "
                f"confiança {signal['confidence']}% — a enviar propostas."
            )
            for user in users:
                uid = user["uid"]
                try:
                    await send_trade_proposal(bot, uid, signal)
                except Exception as e:
                    logger.error(f"Autotrade proposta para {uid}: {e}")
        except Exception as e:
            logger.error(f"Autotrade scan {coin}: {e}")
        await asyncio.sleep(0.5)


# ── Câmbio / P2P / Remessas / Depósito Cripto ──────────────────────────────
#
# Os quatro fluxos abaixo espelham exatamente a lógica e o Firestore do
# app principal (CambioTab, P2PTab, RemessaTab, CryptoDepositTab em
# index.html) — mesmas coleções, mesmos campos, mesmas taxas (ver fx.py).
# Câmbio e P2P são instantâneos (sem aprovação); Remessa e Depósito
# Cripto entram em Firestore como 'pending' e são aprovados pelo admin
# (ver admin_flows.py).
#
# Cada fluxo é um único CommandHandler que recebe todos os dados de uma
# vez em context.args — sem conversa passo-a-passo. O /ajuda mostra a
# sintaxe de cada um; se o comando for chamado sem argumentos (ou com
# argumentos a menos), a própria função responde com o "Uso: ..." em
# vez de tentar adivinhar ou pedir o resto por mensagens seguintes.


def _parse_float(raw: str) -> float | None:
    """Aceita tanto '.' como ',' como separador decimal (users PT usam
    vírgula com frequência). Devolve None em vez de lançar exceção."""
    try:
        return float(raw.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return None


PENDING_OPS_COLLECTION = "botPendingOps"
PENDING_OP_TTL_MINUTES = 15


def _create_pending_op(op_type: str, data: dict) -> str:
    """
    Guarda os dados de uma operação (P2P, Remessa, Depósito Cripto) entre
    o comando inicial e o clique no botão de confirmação. Usado em vez de
    context.user_data (que exigiria ConversationHandler) e em vez de
    embutir tudo no callback_data (que tem um limite de 64 bytes do
    Telegram — insuficiente para nomes/emails/detalhes de conta).
    O botão inline transporta só o ID deste documento.
    """
    db = get_db()
    ref = db.collection(PENDING_OPS_COLLECTION).document()
    ref.set({
        "type": op_type, "data": data, "created_at": fx.utcnow(),
    })
    return ref.id


def _get_pending_op(op_id: str, expected_type: str) -> dict | None:
    """Lê e valida uma operação pendente — devolve None (com o botão a
    explicar o motivo, feito pelo caller) se o ID não existir, for de um
    tipo diferente, ou tiver passado o TTL. Não apaga o documento aqui:
    quem chama decide apagar só depois de confirmar sucesso, para uma
    operação que falhe a meio poder ser reexecutada com /cancelar +
    o comando de novo, sem perder o registo do que se tentou."""
    db = get_db()
    doc = db.collection(PENDING_OPS_COLLECTION).document(op_id).get()
    if not doc.exists:
        return None
    d = doc.to_dict()
    if d.get("type") != expected_type:
        return None
    created_at = d.get("created_at")
    if created_at is not None:
        if hasattr(created_at, "tzinfo") and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (fx.utcnow() - created_at).total_seconds() > PENDING_OP_TTL_MINUTES * 60:
            return None
    return d.get("data", {})


def _delete_pending_op(op_id: str) -> None:
    get_db().collection(PENDING_OPS_COLLECTION).document(op_id).delete()


# ══════════════════════════════════════
# /cambio <de> <para> <montante> — conversão instantânea entre STN/EUR/USD
# ══════════════════════════════════════

CAMBIO_USO = (
    "Uso: `/cambio <de> <para> <montante>`\n"
    f"Moedas: {' / '.join(fx.FX_CURRENCIES)}\n"
    "Ex: `/cambio EUR STN 50`"
)


@_require_linked
async def cambio_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(CAMBIO_USO, parse_mode="Markdown")
        return

    de = args[0].strip().upper()
    para = args[1].strip().upper()
    amount = _parse_float(args[2])

    if de not in fx.FX_CURRENCIES or para not in fx.FX_CURRENCIES:
        await update.message.reply_text(
            f"Moeda inválida. Usa uma de: {', '.join(fx.FX_CURRENCIES)}\n\n{CAMBIO_USO}",
            parse_mode="Markdown",
        )
        return
    if de == para:
        await update.message.reply_text("As duas moedas têm de ser diferentes.")
        return
    if amount is None or amount <= 0:
        await update.message.reply_text(f"Montante inválido.\n\n{CAMBIO_USO}", parse_mode="Markdown")
        return

    db = get_db()
    doc = db.collection("users").document(uid).get()
    d = doc.to_dict() if doc.exists else {}
    from_field = fx.FX_BAL_FIELD[de]
    saldo = d.get(from_field, 0.0) or 0.0
    if saldo < amount - 0.0001:
        await update.message.reply_text(
            f"⚠️ Saldo insuficiente. Tens {fx.fmt_cur(de, saldo)} em {de}."
        )
        return

    conv = fx.calc_fx_conversion(de, para, amount)
    fee = fx.calc_fx_fee(amount)
    # Arredondado ANTES de ir para o callback_data (limite de 64 bytes do
    # Telegram) — 2 casas para montantes (dinheiro), 6 para a taxa (para
    # não perder precisão relevante no recálculo da confirmação).
    to_amount = round(conv["toAmount"], 2)
    rate = round(conv["rate"], 6)
    fee = round(fee, 2)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar", callback_data=f"cxc:{de}:{para}:{amount}:{to_amount}:{rate}:{fee}"),
        InlineKeyboardButton("❌ Cancelar", callback_data="cxc_cancel"),
    ]])
    await update.message.reply_text(
        formatters.cambio_confirm(de, para, amount, to_amount, rate, fee),
        reply_markup=keyboard, parse_mode="Markdown",
    )


async def cambio_confirma_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Espelha fbExchangeCurrency(): debita fromField, credita toField,
    regista a transação (type: fx_exchange) com o spread já convertido
    para STN (feeSTN) para entrar nos totais existentes da Caixa
    Exchange — mesma lógica de contabilidade já usada no app.

    Todos os dados da operação vêm embutidos no próprio callback_data
    (gerado por cambio_command) em vez de context.user_data — assim o
    botão de confirmação funciona mesmo que o processo do bot reinicie
    entre o comando e o clique, e não há estado de conversa para se
    perder ou colidir entre pedidos.
    """
    query = update.callback_query
    await query.answer()

    if query.data == "cxc_cancel":
        await query.edit_message_text("❌ Câmbio cancelado.")
        return

    chat_id = update.effective_chat.id
    uid = account_linking.get_uid_for_chat_id(chat_id)
    if not uid:
        await query.edit_message_text("⚠️ Sessão expirada, tenta /cambio de novo.")
        return

    try:
        _, de, para, amount_s, to_amount_s, rate_s, fee_s = query.data.split(":")
        amount, to_amount, rate, fee = float(amount_s), float(to_amount_s), float(rate_s), float(fee_s)
    except ValueError:
        await query.edit_message_text("⚠️ Dados inválidos, tenta /cambio de novo.")
        return

    try:
        db = get_db()
        from google.cloud.firestore_v1 import Increment
        from_field = fx.FX_BAL_FIELD[de]
        to_field = fx.FX_BAL_FIELD[para]

        # Revalida o saldo mesmo em cima da hora — pode ter mudado
        # entre o comando e o clique de confirmação (ex: outra
        # operação em paralelo).
        doc = db.collection("users").document(uid).get()
        d = doc.to_dict() if doc.exists else {}
        saldo = d.get(from_field, 0.0) or 0.0
        if saldo < amount - 0.0001:
            await query.edit_message_text(f"⚠️ Saldo insuficiente em {de} no momento da confirmação.")
            return

        fee_stn = fee if de == "STN" else fee * fx.fx_mid_rate(de, "STN")
        db.collection("users").document(uid).update({
            from_field: Increment(-amount),
            to_field: Increment(to_amount),
        })
        db.collection("transactions").document().set({
            "uid": uid, "type": "fx_exchange", "fromCur": de, "toCur": para,
            "fromAmount": amount, "toAmount": to_amount, "rate": rate,
            "feeFromCur": fee, "feeSTN": fee_stn, "ts": fx.utcnow(),
        })

        await query.edit_message_text(
            formatters.cambio_done(de, para, amount, to_amount), parse_mode="Markdown",
        )
        logger.info(f"Câmbio: uid={uid} {amount} {de} -> {to_amount} {para}")
    except Exception as e:
        logger.error(f"cambio_confirma_callback: {e}")
        await query.edit_message_text(f"⛔ Erro ao processar o câmbio: {e}")


# ══════════════════════════════════════
# /p2p <email> <montante> [moeda] — transferência instantânea, por email
# ══════════════════════════════════════

P2P_USO = (
    "Uso: `/p2p <email_do_destinatário> <montante> [moeda]`\n"
    f"Moedas: {' / '.join(fx.FX_CURRENCIES)} (default: STN)\n"
    "Ex: `/p2p joao@email.com 100 EUR`"
)


@_require_linked
async def p2p_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(P2P_USO, parse_mode="Markdown")
        return

    email = args[0].strip().lower()
    amount = _parse_float(args[1])
    cur = args[2].strip().upper() if len(args) > 2 else "STN"

    if cur not in fx.FX_CURRENCIES:
        await update.message.reply_text(
            f"Moeda inválida. Usa uma de: {', '.join(fx.FX_CURRENCIES)}\n\n{P2P_USO}",
            parse_mode="Markdown",
        )
        return
    if amount is None or amount < fx.P2P_MIN_AMOUNT:
        await update.message.reply_text(
            f"Montante mínimo: {fx.P2P_MIN_AMOUNT} {cur}\n\n{P2P_USO}", parse_mode="Markdown",
        )
        return

    db = get_db()
    sender_doc = db.collection("users").document(uid).get()
    sender_data = sender_doc.to_dict() if sender_doc.exists else {}
    sender_email = sender_data.get("email", "").strip().lower()
    if email == sender_email:
        await update.message.reply_text("Não podes transferir para ti próprio.")
        return

    idx_doc = db.collection("emailIndex").document(email).get()
    if not idx_doc.exists:
        await update.message.reply_text(
            "⚠️ Não encontrámos nenhum user Cless Cripto com esse email."
        )
        return
    idx_data = idx_doc.to_dict()
    recipient_uid = idx_data.get("uid")
    recipient_name = idx_data.get("name") or email

    fee = fx.calc_p2p_fee(amount)
    total = amount + fee
    field = fx.FX_BAL_FIELD[cur]
    saldo = sender_data.get(field, 0.0) or 0.0
    if saldo < total - 0.0001:
        await update.message.reply_text(
            f"⚠️ Saldo insuficiente em {cur} (inclui taxa). Tens {fx.fmt_cur(cur, saldo)}."
        )
        return

    op_id = _create_pending_op("p2p", {
        "uid": uid, "cur": cur, "amount": amount, "fee": fee, "total": total,
        "recipient_uid": recipient_uid, "recipient_email": email,
        "recipient_name": recipient_name,
    })
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar Envio", callback_data=f"p2pc:{op_id}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"p2px:{op_id}"),
    ]])
    await update.message.reply_text(
        formatters.p2p_confirm(recipient_name, cur, amount, fee, total),
        reply_markup=keyboard, parse_mode="Markdown",
    )


async def p2p_confirma_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Espelha fbSendP2P(): débito atómico no sender (amount+fee), crédito
    no recipient (amount líquido), duas transações registadas (sent/
    received) para cada lado ver o próprio histórico correctamente.

    Os dados da operação vêm de um documento em botPendingOps (ver
    _create_pending_op) em vez de context.user_data ou do próprio
    callback_data — um email de destinatário pode ultrapassar sozinho
    o limite de 64 bytes de callback_data do Telegram, por isso o botão
    só transporta o ID do documento pendente.
    """
    query = update.callback_query
    await query.answer()

    op_id = query.data.split(":", 1)[1]

    if query.data.startswith("p2px:"):
        _delete_pending_op(op_id)
        await query.edit_message_text("❌ Transferência cancelada.")
        return

    chat_id = update.effective_chat.id
    uid = account_linking.get_uid_for_chat_id(chat_id)
    op = _get_pending_op(op_id, "p2p")
    if not uid or not op or op.get("uid") != uid:
        await query.edit_message_text("⚠️ Sessão expirada ou pedido inválido, tenta /p2p de novo.")
        return

    cur = op["cur"]
    amount = op["amount"]
    fee = op["fee"]
    total = op["total"]
    recipient_uid = op["recipient_uid"]
    recipient_email = op["recipient_email"]
    recipient_name = op["recipient_name"]

    try:
        db = get_db()
        from google.cloud.firestore_v1 import Increment

        field = fx.FX_BAL_FIELD[cur]
        doc = db.collection("users").document(uid).get()
        d = doc.to_dict() if doc.exists else {}
        saldo = d.get(field, 0.0) or 0.0
        if saldo < total - 0.0001:
            await query.edit_message_text("⚠️ Saldo insuficiente no momento da confirmação.")
            _delete_pending_op(op_id)
            return

        sender_email = d.get("email", "")
        sender_name = d.get("name", "")

        batch = db.batch()
        batch.update(db.collection("users").document(uid), {field: Increment(-total)})
        batch.update(db.collection("users").document(recipient_uid), {field: Increment(amount)})

        taxa_stn = fee if cur == "STN" else fee * fx.fx_mid_rate(cur, "STN")
        tx_sent_ref = db.collection("transactions").document()
        batch.set(tx_sent_ref, {
            "uid": uid, "type": "p2p_transfer", "direction": "sent", "cur": cur,
            "amount": amount, "fee": fee, "taxaSTN": taxa_stn,
            "counterpartyUid": recipient_uid, "counterpartyEmail": recipient_email,
            "counterpartyName": recipient_name, "ts": fx.utcnow(),
        })
        tx_recv_ref = db.collection("transactions").document()
        batch.set(tx_recv_ref, {
            "uid": recipient_uid, "type": "p2p_transfer", "direction": "received", "cur": cur,
            "amount": amount, "fee": 0,
            "counterpartyUid": uid, "counterpartyEmail": sender_email,
            "counterpartyName": sender_name, "ts": fx.utcnow(),
        })
        batch.commit()
        _delete_pending_op(op_id)
        logger.info(f"P2P: uid={uid} -> {recipient_uid} {amount} {cur}")

        # Tenta notificar o destinatário ANTES de fechar a mensagem ao
        # remetente, para o recibo final poder dizer com certeza se a
        # notificação foi entregue — nunca falha silenciosamente. Se o
        # destinatário nunca vinculou o Telegram (get_chat_id_for_uid
        # devolve None), ou se bloqueou o bot entretanto, o remetente
        # fica a saber isso explicitamente em vez de assumir que a
        # pessoa foi avisada.
        recipient_notified = False
        recipient_chat_id = account_linking.get_chat_id_for_uid(recipient_uid)
        if recipient_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=recipient_chat_id,
                    text=formatters.p2p_received_dm(sender_name or sender_email, cur, amount),
                    parse_mode="Markdown",
                )
                recipient_notified = True
            except Exception as e:
                logger.warning(f"Não foi possível notificar recipient {recipient_uid}: {e}")

        await query.edit_message_text(
            formatters.p2p_sent_dm(recipient_name, cur, amount, recipient_notified),
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"p2p_confirma_callback: {e}")
        await query.edit_message_text(f"⛔ Erro ao processar a transferência: {e}")


# ══════════════════════════════════════
# /remessa — pedido de remessa internacional (aprovado por admin)
# ══════════════════════════════════════
#
# Sintaxe com | como separador de campos (não espaço): nome e dados de
# recebimento são texto livre e quase sempre têm espaços (ex: "Banco
# BAI, IBAN AO06..."), por isso context.args (que corta por espaço) não
# chega — o user escreve os campos separados por barra vertical.

REMESSA_USO = (
    "Uso: `/remessa <enviar|receber> | <moeda> | <montante> | <país> | "
    "<nome> | <dados_de_recebimento> | [nota]`\n\n"
    f"Moedas: {' / '.join(fx.FX_CURRENCIES)}\n"
    f"Países: {', '.join(fx.REMIT_COUNTRIES)}\n\n"
    "Ex:\n`/remessa enviar | EUR | 200 | Portugal | João Silva | "
    "IBAN PT50 0002 0123 1234 5678 901 54 | Renda de casa`\n\n"
    "A nota final é opcional — podes omitir a última barra."
)


@_require_linked
async def remessa_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    raw = update.message.text.split(" ", 1)
    if len(raw) < 2 or not raw[1].strip():
        await update.message.reply_text(REMESSA_USO, parse_mode="Markdown")
        return

    parts = [p.strip() for p in raw[1].split("|")]
    if len(parts) < 6:
        await update.message.reply_text(
            f"Faltam campos — indicaste {len(parts)}, são precisos pelo menos 6.\n\n{REMESSA_USO}",
            parse_mode="Markdown",
        )
        return

    direction = parts[0].strip().lower()
    cur = parts[1].strip().upper()
    amount = _parse_float(parts[2])
    country = parts[3].strip()
    recipient_name = parts[4].strip()
    recipient_details = parts[5].strip()
    note = parts[6].strip() if len(parts) > 6 else ""

    if direction not in ("enviar", "receber"):
        await update.message.reply_text(
            f"Direção inválida — usa 'enviar' ou 'receber'.\n\n{REMESSA_USO}", parse_mode="Markdown",
        )
        return
    if cur not in fx.FX_CURRENCIES:
        await update.message.reply_text(
            f"Moeda inválida. Usa uma de: {', '.join(fx.FX_CURRENCIES)}\n\n{REMESSA_USO}",
            parse_mode="Markdown",
        )
        return
    if amount is None or amount <= 0:
        await update.message.reply_text(f"Montante inválido.\n\n{REMESSA_USO}", parse_mode="Markdown")
        return
    if country not in fx.REMIT_COUNTRIES:
        await update.message.reply_text(
            f"País inválido. Usa um de: {', '.join(fx.REMIT_COUNTRIES)}\n\n{REMESSA_USO}",
            parse_mode="Markdown",
        )
        return
    if not recipient_name or not recipient_details:
        await update.message.reply_text(f"Nome e dados de recebimento não podem ficar vazios.\n\n{REMESSA_USO}", parse_mode="Markdown")
        return

    fee = fx.calc_remit_fee(amount)
    total = amount + fee

    db = get_db()
    doc = db.collection("users").document(uid).get()
    d = doc.to_dict() if doc.exists else {}
    user_email = d.get("email", "")

    if direction == "enviar":
        field = fx.FX_BAL_FIELD[cur]
        saldo = d.get(field, 0.0) or 0.0
        if saldo < total - 0.0001:
            await update.message.reply_text(
                f"⚠️ Saldo insuficiente (inclui taxa de {fx.get_remit_fee_pct(amount)}%). "
                f"Tens {fx.fmt_cur(cur, saldo)} em {cur}."
            )
            return

    op_id = _create_pending_op("remessa", {
        "uid": uid, "email": user_email, "direction": direction, "cur": cur,
        "amount": amount, "fee": fee, "total": total, "country": country,
        "recipient_name": recipient_name, "recipient_details": recipient_details,
        "note": note,
    })
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar Pedido", callback_data=f"remc:{op_id}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"remx:{op_id}"),
    ]])
    await update.message.reply_text(
        formatters.remessa_confirm(direction, cur, amount, fee, total, country, recipient_name, recipient_details),
        reply_markup=keyboard, parse_mode="Markdown",
    )


async def remessa_confirma_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Espelha fbSubmitRemittance(): se direction=='enviar', reserva
    (debita) amount+fee já no pedido — o saldo só volta se for
    rejeitado. Grava em 'remittances' com status 'pending' para o
    admin rever com /remessaspendentes.
    """
    query = update.callback_query
    await query.answer()

    op_id = query.data.split(":", 1)[1]

    if query.data.startswith("remx:"):
        _delete_pending_op(op_id)
        await query.edit_message_text("❌ Pedido de remessa cancelado.")
        return

    chat_id = update.effective_chat.id
    uid = account_linking.get_uid_for_chat_id(chat_id)
    op = _get_pending_op(op_id, "remessa")
    if not uid or not op or op.get("uid") != uid:
        await query.edit_message_text("⚠️ Sessão expirada ou pedido inválido, tenta /remessa de novo.")
        return

    direction = op["direction"]
    cur = op["cur"]
    amount = op["amount"]
    fee = op["fee"]
    total = op["total"]
    country = op["country"]
    recipient_name = op["recipient_name"]
    recipient_details = op["recipient_details"]
    note = op.get("note", "")

    try:
        db = get_db()
        from google.cloud.firestore_v1 import Increment

        doc = db.collection("users").document(uid).get()
        d = doc.to_dict() if doc.exists else {}
        user_email = d.get("email", "")

        if direction == "enviar":
            field = fx.FX_BAL_FIELD[cur]
            saldo = d.get(field, 0.0) or 0.0
            if saldo < total - 0.0001:
                await query.edit_message_text("⚠️ Saldo insuficiente no momento da confirmação.")
                _delete_pending_op(op_id)
                return

        batch = db.batch()
        if direction == "enviar":
            batch.update(db.collection("users").document(uid), {fx.FX_BAL_FIELD[cur]: Increment(-total)})
        rem_ref = db.collection("remittances").document()
        batch.set(rem_ref, {
            "uid": uid, "email": user_email, "direction": direction, "cur": cur,
            "amount": amount, "fee": fee, "total": total, "country": country,
            "recipientName": recipient_name, "recipientDetails": recipient_details,
            "note": note, "status": "pending", "ts": fx.utcnow(),
            "approvedBy": None, "approvedAt": None,
        })
        batch.commit()
        _delete_pending_op(op_id)
    except Exception as e:
        logger.error(f"remessa_confirma_callback (registo do pedido): {e}")
        await query.edit_message_text(f"⛔ Erro ao registar o pedido: {e}")
        return

    # A partir daqui o pedido já está gravado e o saldo (se aplicável)
    # já está reservado — qualquer falha abaixo (log, notificação ao
    # admin) é só "melhor esforço" e nunca deve fazer parecer ao user
    # que o pedido falhou, por isso corre fora do try/except principal.
    await query.edit_message_text(
        formatters.remessa_submitted_dm(direction), parse_mode="Markdown",
    )
    logger.info(f"Remessa registada: uid={uid} direction={direction} amount={amount} {cur} rem_id={rem_ref.id}")

    # Avisa os admins de imediato — não é preciso esperar pelo
    # próximo /remessaspendentes manual.
    for admin_id in config.ADMIN_TELEGRAM_IDS:
        try:
            preview_msg = formatters.remessa_admin_preview({
                "direction": direction, "email": user_email, "cur": cur,
                "amount": amount, "fee": fee, "country": country,
                "recipientName": recipient_name, "recipientDetails": recipient_details,
                "note": note,
            }, rem_ref.id)
            admin_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Aprovar", callback_data=f"{admin_flows.CB_REMIT_APPROVE}{rem_ref.id}"),
                InlineKeyboardButton("❌ Rejeitar", callback_data=f"{admin_flows.CB_REMIT_REJECT}{rem_ref.id}"),
            ]])
            await context.bot.send_message(
                chat_id=admin_id, text=preview_msg,
                parse_mode="Markdown", reply_markup=admin_keyboard,
            )
        except Exception as e:
            logger.warning(f"Não foi possível notificar admin {admin_id} sobre nova remessa: {e}")


# ══════════════════════════════════════
# /depositocripto <moeda> <montante> <txid> — registo de depósito on-chain
# (confirmado por admin)
# ══════════════════════════════════════

CRIPTO_USO_HEADER = "Uso: `/depositocripto <moeda> <montante> <txid>`"


def _cripto_uso() -> str:
    coins = ", ".join(fx.CRYPTO_ADDRESSES.keys())
    return (
        f"{CRIPTO_USO_HEADER}\n"
        f"Moedas: {coins}\n"
        "Ex: `/depositocripto BTC 0.002 3a1b2c...`\n\n"
        "Usa /depositocripto sem argumentos para veres os endereços de depósito."
    )


@_require_linked
async def cripto_command(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: str = "") -> None:
    args = context.args

    if not args:
        # Sem argumentos: mostra os endereços de depósito de todas as
        # moedas, para o user copiar o endereço certo antes de enviar
        # (mesmo papel que o primeiro ecrã do CryptoDepositTab no app).
        lines = ["₿ *Depósito de Cripto — endereços*\n"]
        for coin, info in fx.CRYPTO_ADDRESSES.items():
            lines.append(
                f"*{coin}* ({info['network']})\n`{info['address']}`\nMínimo: {info['min_coin']} {coin}\n"
            )
        lines.append(_cripto_uso())
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if len(args) < 3:
        await update.message.reply_text(_cripto_uso(), parse_mode="Markdown")
        return

    coin = args[0].strip().upper()
    amount_coin = _parse_float(args[1])
    txid = args[2].strip()

    info = fx.CRYPTO_ADDRESSES.get(coin)
    if not info:
        await update.message.reply_text(
            f"Moeda inválida. Usa uma de: {', '.join(fx.CRYPTO_ADDRESSES.keys())}\n\n{_cripto_uso()}",
            parse_mode="Markdown",
        )
        return
    if amount_coin is None or amount_coin <= 0:
        await update.message.reply_text(f"Montante inválido.\n\n{_cripto_uso()}", parse_mode="Markdown")
        return
    if amount_coin < info["min_coin"]:
        await update.message.reply_text(
            f"⚠️ O depósito mínimo de {coin} é {info['min_coin']}."
        )
        return
    if len(txid) < 8:
        await update.message.reply_text("Isso não parece um TXID válido — confirma e tenta de novo.")
        return

    op_id = _create_pending_op("cripto", {
        "uid": uid, "coin": coin, "amount_coin": amount_coin, "txid": txid,
    })
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar Registo", callback_data=f"crc:{op_id}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"crx:{op_id}"),
    ]])
    await update.message.reply_text(
        f"₿ *Confirmar Depósito*\n\n"
        f"Moeda: *{coin}*\n"
        f"Montante: *{amount_coin} {coin}*\n"
        f"TXID: `{txid}`\n\n"
        f"_O admin vai validar isto no block explorer antes de creditar o teu saldo._",
        reply_markup=keyboard, parse_mode="Markdown",
    )


async def cripto_confirma_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Espelha fbSubmitCryptoDeposit(): só regista o pedido em
    'cryptoDeposits' com status 'pending' — não mexe em saldo nenhum
    (o saldo só é creditado quando o admin usar /confirmarcripto,
    depois de validar o TXID manualmente no block explorer).
    """
    query = update.callback_query
    await query.answer()

    op_id = query.data.split(":", 1)[1]

    if query.data.startswith("crx:"):
        _delete_pending_op(op_id)
        await query.edit_message_text("❌ Registo de depósito cancelado.")
        return

    chat_id = update.effective_chat.id
    uid = account_linking.get_uid_for_chat_id(chat_id)
    op = _get_pending_op(op_id, "cripto")
    if not uid or not op or op.get("uid") != uid:
        await query.edit_message_text("⚠️ Sessão expirada ou pedido inválido, tenta /depositocripto de novo.")
        return

    coin = op["coin"]
    amount_coin = op["amount_coin"]
    txid = op["txid"]

    try:
        db = get_db()
        info = fx.CRYPTO_ADDRESSES.get(coin, {})
        doc = db.collection("users").document(uid).get()
        d = doc.to_dict() if doc.exists else {}
        user_email = d.get("email", "")
        user_name = d.get("name", "") or user_email or "—"

        dep_ref = db.collection("cryptoDeposits").document()
        dep_ref.set({
            "uid": uid, "userEmail": user_email, "userName": user_name,
            "coin": coin, "network": info.get("network", ""),
            "amountCoin": amount_coin, "txid": txid,
            "addressUsed": info.get("address", ""),
            "status": "pending", "ts": fx.utcnow(),
            "confirmedBy": None, "confirmedAt": None, "amountSTN": None,
        })
        _delete_pending_op(op_id)
    except Exception as e:
        logger.error(f"cripto_confirma_callback (registo do depósito): {e}")
        await query.edit_message_text(f"⛔ Erro ao registar o depósito: {e}")
        return

    # O depósito já está gravado como 'pending' — o resto (log,
    # notificação ao admin) é melhor esforço e não deve fazer parecer
    # ao user que o registo falhou.
    await query.edit_message_text(
        formatters.crypto_deposit_submitted_dm(), parse_mode="Markdown",
    )
    logger.info(f"Depósito cripto registado: uid={uid} coin={coin} amountCoin={amount_coin} dep_id={dep_ref.id}")

    for admin_id in config.ADMIN_TELEGRAM_IDS:
        try:
            preview_msg = formatters.crypto_deposit_admin_preview({
                "userEmail": user_email, "coin": coin, "network": info.get("network", ""),
                "amountCoin": amount_coin, "txid": txid, "addressUsed": info.get("address", ""),
            }, dep_ref.id)
            explorer_url = f"{info.get('explorer','')}{txid}" if info.get("explorer") else None
            keyboard_rows = []
            if explorer_url:
                keyboard_rows.append([InlineKeyboardButton("🔍 Ver no Block Explorer", url=explorer_url)])
            keyboard_rows.append([
                InlineKeyboardButton("❌ Rejeitar", callback_data=f"{admin_flows.CB_CRYPTODEP_REJECT}{dep_ref.id}")
            ])
            await context.bot.send_message(
                chat_id=admin_id, text=preview_msg,
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_rows),
            )
        except Exception as e:
            logger.warning(f"Não foi possível notificar admin {admin_id} sobre novo depósito: {e}")


@_require_admin
async def confirmarcripto_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /confirmarcripto <ID> <valorSTN> — só admin. Usado depois de validar
    o TXID no block explorer (link enviado junto com o pedido pendente).
    Delega toda a lógica de crédito para admin_flows.confirm_crypto_deposit,
    e só trata de enviar as duas mensagens finais (ao admin e ao user).
    """
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: `/confirmarcripto <ID_do_depósito> <valorSTN>`\n"
            "Encontras o ID na mensagem de pedido pendente.",
            parse_mode="Markdown",
        )
        return
    dep_id = args[0]
    try:
        amount_stn_gross = float(args[1].replace(",", "."))
    except ValueError:
        await update.message.reply_text("O valorSTN tem de ser um número, ex: 850")
        return
    if amount_stn_gross <= 0:
        await update.message.reply_text("O valorSTN tem de ser maior que zero.")
        return

    result = await admin_flows.confirm_crypto_deposit(dep_id, update.effective_chat.id, amount_stn_gross)
    if not result.get("ok"):
        await update.message.reply_text(f"⚠️ {result.get('message')}")
        return

    await update.message.reply_text(
        formatters.crypto_deposit_approved_dm(result["coin"], result["amount_net"]) +
        f"\n\n_(user notificado{' por Telegram' if result.get('chat_id') else ' — mas não tem Telegram vinculado'})_",
        parse_mode="Markdown",
    )

    if result.get("chat_id"):
        try:
            await context.bot.send_message(
                chat_id=result["chat_id"],
                text=formatters.crypto_deposit_approved_dm(result["coin"], result["amount_net"]),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Não foi possível notificar user sobre depósito confirmado: {e}")


# ── Build e Main ───────────────────────────────────────────────────────────

async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /backtest [dias] [moedas separadas por vírgula] — só admin.
    Corre o backtest.py em background (pode demorar minutos, por causa
    do rate limit da Binance) e manda o relatório por DM quando terminar,
    sem bloquear o resto do bot entretanto.
    """
    chat_id = update.effective_chat.id
    if not config.is_admin(chat_id):
        await update.message.reply_text("⛔ Comando só para admin.")
        return

    args = context.args
    days = 60
    coins_arg = None
    if args:
        try:
            days = int(args[0])
        except ValueError:
            await update.message.reply_text("Uso: /backtest [dias] [moedas]\nEx: /backtest 60\nEx: /backtest 30 BTC,ETH,ADA")
            return
        if len(args) >= 2:
            coins_arg = [c.strip().upper() for c in args[1].split(",") if c.strip()]

    coins = coins_arg or signals_engine.COINS
    await update.message.reply_text(
        f"⏳ A correr backtest de *{days} dias* em *{len(coins)} moeda(s)*.\n\n"
        f"Isto busca dados históricos reais da Binance e pode demorar "
        f"alguns minutos — mando o relatório aqui assim que terminar.",
        parse_mode="Markdown",
    )

    async def _do_backtest():
        import backtest as bt
        try:
            stats = await bt.run_backtest(days, coins, progress_callback=lambda m: logger.info(f"[backtest] {m}"))
            report = bt.build_report(days, coins, stats)
            await context.bot.send_message(chat_id=chat_id, text=report, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Erro no backtest: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"⛔ Erro ao correr o backtest: {e}")

    asyncio.create_task(_do_backtest())


async def postar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /postar <mensagem> — só admin. Publica texto livre no canal principal
    na hora, sem precisar de sair do bot para ir ao Telegram do canal.
    Suporta Markdown (negrito com *, itálico com _, etc.)
    """
    chat_id = update.effective_chat.id
    if not config.is_admin(chat_id):
        await update.message.reply_text("⛔ Comando só para admin.")
        return

    text = update.message.text.split(" ", 1)
    if len(text) < 2 or not text[1].strip():
        await update.message.reply_text(
            "Uso: /postar <mensagem>\n\n"
            "Ex: /postar 🚀 *Nova funcionalidade lançada!* Vem conferir."
        )
        return

    message = text[1].strip()
    ok = await admin_flows.post_to_channel(context.bot, message)
    if ok:
        await update.message.reply_text("✅ Publicado no canal.")
    else:
        await update.message.reply_text(
            "⛔ Não foi possível publicar — verifica se TELEGRAM_CHANNEL_ID "
            "está configurado e se o bot é admin do canal."
        )


def build_application() -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",      start_command))
    app.add_handler(CommandHandler("ajuda",      ajuda_command))
    app.add_handler(CommandHandler("ranking",    ranking_command))
    app.add_handler(CommandHandler("mercado",    mercado_command))
    app.add_handler(CommandHandler("meusaldo",   meusaldo_command))
    app.add_handler(CommandHandler("meta",       meta_command))
    app.add_handler(CommandHandler("idemoji",    idemoji_command))
    app.add_handler(CommandHandler("meubonus",   meubonus_command))
    app.add_handler(CommandHandler("sinais",     sinais_command))
    app.add_handler(CommandHandler("autotrade",  autotrade_command))
    app.add_handler(CommandHandler("posicao",    posicao_command))
    app.add_handler(CommandHandler("backtest",   backtest_command))
    app.add_handler(CommandHandler("postar",     postar_command))
    app.add_handler(CommandHandler("admin",      admin_command))
    app.add_handler(CommandHandler("saldo",      saldo_admin_command))
    app.add_handler(CommandHandler("confirmarcripto", confirmarcripto_command))

    # Câmbio / P2P / Remessas / Depósito Cripto — cada um é agora um
    # único CommandHandler (recebe tudo em context.args ou na própria
    # mensagem, sem conversa passo-a-passo) mais um CallbackQueryHandler
    # para o botão de confirmação. Os dados entre o comando e o clique
    # de confirmação vivem em botPendingOps (ver _create_pending_op),
    # não em context.user_data — por isso não há necessidade de
    # ConversationHandler nem do fallback /cancelar que ele exigia.
    app.add_handler(CommandHandler("cambio",        cambio_command))
    app.add_handler(CommandHandler("p2p",           p2p_command))
    app.add_handler(CommandHandler("remessa",       remessa_command))
    app.add_handler(CommandHandler("depositocripto", cripto_command))
    app.add_handler(CallbackQueryHandler(ajuda_ref_callback, pattern=r"^ajuda_ref:"))
    app.add_handler(CallbackQueryHandler(cambio_confirma_callback, pattern=r"^cxc(:|_cancel$)"))
    app.add_handler(CallbackQueryHandler(p2p_confirma_callback,    pattern=r"^p2p(c|x):"))
    app.add_handler(CallbackQueryHandler(remessa_confirma_callback, pattern=r"^rem(c|x):"))
    app.add_handler(CallbackQueryHandler(cripto_confirma_callback,  pattern=r"^cr(c|x):"))

    # Handler de debug — captura file_id de fotos e vídeos enviados ao bot
    from telegram.ext import MessageHandler, filters as tg_filters
    async def _debug_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not config.is_admin(update.effective_chat.id):
            return
        msg = update.message
        if msg.photo:
            file_id = msg.photo[-1].file_id
            await msg.reply_text(f"📷 PHOTO file_id:\n`{file_id}`", parse_mode="Markdown")
        elif msg.video:
            file_id = msg.video.file_id
            await msg.reply_text(f"🎥 VIDEO file_id:\n`{file_id}`", parse_mode="Markdown")
    app.add_handler(MessageHandler(tg_filters.PHOTO | tg_filters.VIDEO, _debug_media))

    app.add_handler(CallbackQueryHandler(
        admin_flows.handle_bonus_callback,
        pattern=r"^(bonus_approve:|bonus_reject:)"
    ))
    app.add_handler(CallbackQueryHandler(
        admin_flows.handle_signal_callback,
        pattern=r"^(signal_approve:|signal_reject:)"
    ))
    app.add_handler(CallbackQueryHandler(
        admin_flows.handle_remittance_callback,
        pattern=r"^(remit_approve:|remit_reject:)"
    ))
    app.add_handler(CallbackQueryHandler(
        admin_flows.handle_crypto_deposit_callback,
        pattern=r"^cryptodep_reject:"
    ))
    app.add_handler(CallbackQueryHandler(
        admin_callback,
        pattern=r"^(admin_|pub_rank_)"
    ))
    app.add_handler(CallbackQueryHandler(
        autotrade_callback,
        pattern=r"^(at_activate:|at_activate_cancel|at_confirm:|at_reject:)"
    ))
    app.add_handler(CallbackQueryHandler(
        close_position_callback,
        pattern=r"^(close_pos:|close_pos_all:)"
    ))

    return app


def main() -> None:
    logger.info("A inicializar Firestore…")
    init_firestore()

    logger.info("A construir a aplicação do bot…")
    app = build_application()

    logger.info("A configurar o scheduler…")
    scheduler = sched.setup_scheduler(app.bot)

    # SL/TP engine: verifica posições abertas a cada 30s
    from sltp_engine import setup_sltp_job
    setup_sltp_job(scheduler, app.bot)

    # Autotrade scan: procura sinais rank 7-10 a cada 5 minutos
    scheduler.add_job(
        _autotrade_scan_job,
        trigger="interval",
        minutes=5,
        kwargs={"bot": app.bot},
        id="autotrade_scan",
        replace_existing=True,
    )
    logger.info("Autotrade scan configurado: a cada 5 minutos.")

    # Resumo diário personalizado: todos os dias às 08:00 UTC
    from alerts import send_daily_summary, start_all_listeners
    scheduler.add_job(
        send_daily_summary,
        trigger="cron",
        hour=8,
        minute=0,
        kwargs={"bot": app.bot},
        id="daily_summary",
        replace_existing=True,
    )
    logger.info("Resumo diário configurado: 08:00 UTC.")

    scheduler.start()

    # Listeners em tempo real: depósitos, levantamentos, KYC
    import asyncio
    loop = asyncio.get_event_loop()
    start_all_listeners(app.bot, loop)

    logger.info("Cless Cripto Bot arrancado. À escuta de mensagens…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
