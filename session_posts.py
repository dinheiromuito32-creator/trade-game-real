"""
session_posts.py

Posts automáticos das 4 sessões diárias (Madrugada, Manhã, Tarde, Noite)
para o grupo/canal do Telegram — imagem + legenda persuasiva, com CTAs
disfarçados para criar conta ou aceder à conta existente.

USO:
    from session_posts import schedule_session_posts
    schedule_session_posts(scheduler, bot, GROUP_CHAT_ID)

Preenche os links reais em LINK_CADASTRO e LINK_LOGIN antes de usar.
As imagens são servidas por URL (CDN), não é preciso nenhuma pasta local.
"""

import logging
from telegram import Bot
from telegram.constants import ParseMode

logger = logging.getLogger("cless_bot.session_posts")

# ═══════════════════════════════════════════════════════════════════════
# LINKS — substitui pelos links reais da tua plataforma antes de usar
# ═══════════════════════════════════════════════════════════════════════
LINK_CADASTRO = "https://clesscrypto.eclesiolindo.workers.dev"  # criar conta
LINK_LOGIN    = "https://clesscrypto.eclesiolindo.workers.dev"  # aceder à conta (mesmo link — plataforma única)

# Rodapé de benefícios, igual em todas as sessões
_BADGES = "🤖 Robô de Sinais  ·  📚 Ebook Exclusivo  ·  🤝 Comunidade Trade"

# CTA duplo — disfarçado em texto, nunca mostra o link em bruto.
# Um caminho para quem não tem conta, outro para quem já tem.
_CTA = (
    f"🆕 *Ainda não tens conta?*\n"
    f"👉 [Clique aqui para criar a tua conta]({LINK_CADASTRO})\n\n"
    f"🔑 *Já és membro?*\n"
    f"👉 [Clique aqui para aceder à tua conta]({LINK_LOGIN})"
)


# ═══════════════════════════════════════════════════════════════════════
# SESSÕES — imagem, horário e legenda de cada uma
# ═══════════════════════════════════════════════════════════════════════
SESSIONS = {

    "madrugada": {
        "hour": 4,
        "minute": 15,
        "image": "https://cdn.phototourl.com/free/2026-07-03-aa609035-ae41-4706-9eb6-5622933de52a.png",
        "caption": (
            "🌌 *SESSÃO DA MADRUGADA — OPERAÇÕES AO VIVO* 🌌\n\n"
            "4:15. O mundo dorme. O mercado, não.\n\n"
            "Enquanto a maioria descansa, os movimentos mais fortes de "
            "Bitcoin e das principais altcoins estão a começar a formar-se "
            "nas sessões asiáticas — e é exatamente aqui que os traders "
            "mais disciplinados se posicionam primeiro.\n\n"
            "Esta é a sessão de quem leva isto a sério.\n\n"
            f"{_BADGES}\n\n"
            f"{_CTA}"
        ),
    },

    "manha": {
        "hour": 8,
        "minute": 1,  # 1 min depois do send_daily_summary (08:00) — evita misfire por sobreposição
        "image": "https://cdn.phototourl.com/free/2026-07-03-3b733e3b-d63e-4673-a8ea-5459bccbc4e8.png",
        "caption": (
            "🌅 *SESSÃO DA MANHÃ — OPERAÇÕES AO VIVO* 🌅\n\n"
            "O mercado acorda. Nós já estamos posicionados.\n\n"
            "A abertura do dia costuma trazer os movimentos mais limpos e "
            "com maior volume — e a nossa análise já está pronta para "
            "aproveitar isso, ao vivo, contigo a acompanhar cada entrada.\n\n"
            "Sinais em tempo real. Estratégia validada. Zero achismo.\n\n"
            f"{_BADGES}\n\n"
            f"{_CTA}"
        ),
    },

    "tarde": {
        "hour": 13,
        "minute": 30,
        "image": "https://cdn.phototourl.com/free/2026-07-03-3cee1a89-5464-42e8-baac-64217a83cd15.png",
        "caption": (
            "🌇 *SESSÃO DA TARDE — OPERAÇÕES AO VIVO* 🌇\n\n"
            "13:30. O dia já mostrou o rumo — agora é hora de capitalizar.\n\n"
            "É na sessão da tarde que confirmamos as tendências que se "
            "formaram de manhã e ajustamos a estratégia com base no que o "
            "mercado já revelou. Precisão, não pressa.\n\n"
            "Análise. Estratégia. Resultados.\n\n"
            f"{_BADGES}\n\n"
            f"{_CTA}"
        ),
    },

    "noite": {
        "hour": 20,
        "minute": 1,  # 1 min depois do publish_weekly_ranking (dom 20:00) — evita colisão exata
        "image": "https://cdn.phototourl.com/free/2026-07-03-67e24c68-a3d3-4fb5-96fe-b98cd70163fb.png",
        "caption": (
            "🌃 *SESSÃO DA NOITE — OPERAÇÕES AO VIVO* 🌃\n\n"
            "O dia termina para muitos. Para nós, é outra oportunidade.\n\n"
            "A abertura do mercado americano traz volatilidade e volume "
            "elevado — e é aqui que costumam surgir alguns dos melhores "
            "setups do dia inteiro. Não fiques de fora desta sessão.\n\n"
            "Fecha o dia como quem entende do jogo.\n\n"
            f"{_BADGES}\n\n"
            f"{_CTA}"
        ),
    },
}


# ═══════════════════════════════════════════════════════════════════════
# ENVIO
# ═══════════════════════════════════════════════════════════════════════
async def send_session_post(bot: Bot, chat_id: str | int, session_key: str) -> None:
    """
    Envia a imagem + legenda de uma sessão específica para o grupo/canal.
    session_key: "madrugada" | "manha" | "tarde" | "noite"
    """
    session = SESSIONS.get(session_key)
    if not session:
        logger.error(f"session_posts: sessão desconhecida '{session_key}'")
        return

    try:
        await bot.send_photo(
            chat_id=chat_id,
            photo=session["image"],  # URL — o Telegram descarrega diretamente
            caption=session["caption"],
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"session_posts: sessão '{session_key}' enviada para {chat_id}")
    except Exception as e:
        logger.error(f"session_posts: erro ao enviar sessão '{session_key}': {e}")


# ═══════════════════════════════════════════════════════════════════════
# AGENDAMENTO — liga isto ao teu scheduler (APScheduler) já existente
# ═══════════════════════════════════════════════════════════════════════
def schedule_session_posts(scheduler, bot: Bot, chat_id: str | int) -> None:
    """
    Regista os 4 posts diários no scheduler, cada um ao horário definido
    em SESSIONS. Chama isto uma vez no arranque do bot.

    Exemplo:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        schedule_session_posts(scheduler, bot, GROUP_CHAT_ID)
        scheduler.start()
    """
    for key, session in SESSIONS.items():
        scheduler.add_job(
            send_session_post,
            "cron",
            hour=session["hour"],
            minute=session["minute"],
            args=[bot, chat_id, key],
            id=f"session_post_{key}",
            replace_existing=True,
        )
        logger.info(
            f"session_posts: '{key}' agendado para "
            f"{session['hour']:02d}:{session['minute']:02d}"
        )
