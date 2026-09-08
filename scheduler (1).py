"""
scheduler.py — Rankings automáticos com campos correctos do Firestore.

Campos reais no leaderboard (escritos pelo app):
  - pnl          → PnL total acumulado (STN)
  - displayName  → nome do trader
  - vol          → volume total
  - wins/losses/total → contadores
  - day_YYYY-MM-DD   → ganhos do dia
  - week_YYYY-MM-DD  → ganhos da semana (domingo de início)
  - month_YYYY-MM    → ganhos do mês
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from firestore_client import get_db
from google.cloud import firestore as fs
import formatters
from session_posts import SESSIONS, send_session_post

logger = logging.getLogger("cless_bot.scheduler")

CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

# ID do grupo/canal onde os posts de sessão (Manhã/Tarde/Noite/Madrugada)
# são publicados. Por omissão usa o mesmo CHANNEL_ID dos rankings, mas
# pode ser definido em separado se forem sítios diferentes.
SESSION_GROUP_ID = os.environ.get("TELEGRAM_SESSION_GROUP_ID", "") or CHANNEL_ID


def _day_key()   -> str: return datetime.now(timezone.utc).strftime("%Y-%m-%d")
def _week_key()  -> str:
    d = datetime.now(timezone.utc)
    d -= timedelta(days=d.weekday() + 1) if d.weekday() != 6 else timedelta(days=0)
    return d.strftime("%Y-%m-%d")
def _month_key() -> str: return datetime.now(timezone.utc).strftime("%Y-%m")


async def _fetch_leaderboard(limit: int = 10) -> list[dict]:
    """Leaderboard geral — ordenado por pnl total."""
    db = get_db()
    docs = (
        db.collection("leaderboard")
        .order_by("pnl", direction="DESCENDING")
        .limit(limit)
        .stream()
    )
    traders = []
    for rank, doc in enumerate(docs, start=1):
        d = doc.to_dict()
        vol = d.get("vol", 0.0) or 0.0
        pnl = d.get("pnl", 0.0) or 0.0
        traders.append({
            "rank":    rank,
            "name":    d.get("displayName", "Anónimo"),
            "pnlSTN":  pnl,
            "pnlPct":  round(pnl / vol * 100, 1) if vol else 0.0,
            "vol":     vol,
            "tier":    d.get("tier", "free"),
            "wins":    d.get("wins", 0),
            "losses":  d.get("losses", 0),
        })
    return traders


async def _fetch_daily_leaders(limit: int = 5) -> list[dict]:
    """
    Top traders do dia — usa o campo day_YYYY-MM-DD.
    Nota: o nome do campo tem hífens, e o order_by() do Firestore não aceita
    isso como string simples (tenta fazer parsing de path com pontos).
    Por isso lê-se a coleção toda e ordena-se aqui em Python — para o volume
    de utilizadores da Cless Cripto isto não tem impacto de performance.
    """
    db = get_db()
    key = f"day_{_day_key()}"
    docs = db.collection("leaderboard").stream()
    traders = []
    for doc in docs:
        d = doc.to_dict()
        pnl = d.get(key, 0.0) or 0.0
        if pnl == 0:
            continue
        vol = d.get("vol", 0.0) or 0.0
        traders.append({
            "name":   d.get("displayName", "Anónimo"),
            "pnlSTN": pnl,
            "pnlPct": round(pnl / vol * 100, 1) if vol else 0.0,
            "vol":    vol,
            "tier":   d.get("tier", "free"),
        })
    traders.sort(key=lambda t: t["pnlSTN"], reverse=True)
    traders = traders[:limit]
    for rank, t in enumerate(traders, start=1):
        t["rank"] = rank
    return traders


async def _fetch_weekly_leaders(limit: int = 10) -> list[dict]:
    """Top traders da semana — usa o campo week_YYYY-MM-DD (ordenado em Python, ver nota em _fetch_daily_leaders)."""
    db = get_db()
    key = f"week_{_week_key()}"
    docs = db.collection("leaderboard").stream()
    traders = []
    for doc in docs:
        d = doc.to_dict()
        pnl = d.get(key, 0.0) or 0.0
        if pnl == 0:
            continue
        vol = d.get("vol", 0.0) or 0.0
        traders.append({
            "name":   d.get("displayName", "Anónimo"),
            "pnlSTN": pnl,
            "pnlPct": round(pnl / vol * 100, 1) if vol else 0.0,
            "vol":    vol,
            "tier":   d.get("tier", "free"),
        })
    traders.sort(key=lambda t: t["pnlSTN"], reverse=True)
    traders = traders[:limit]
    for rank, t in enumerate(traders, start=1):
        t["rank"] = rank
    return traders


async def _fetch_monthly_leaders(limit: int = 10) -> list[dict]:
    """Top traders do mês — usa o campo month_YYYY-MM (ordenado em Python, ver nota em _fetch_daily_leaders)."""
    db = get_db()
    key = f"month_{_month_key()}"
    docs = db.collection("leaderboard").stream()
    traders = []
    for doc in docs:
        d = doc.to_dict()
        pnl = d.get(key, 0.0) or 0.0
        if pnl == 0:
            continue
        vol = d.get("vol", 0.0) or 0.0
        traders.append({
            "name":   d.get("displayName", "Anónimo"),
            "pnlSTN": pnl,
            "pnlPct": round(pnl / vol * 100, 1) if vol else 0.0,
            "vol":    vol,
            "tier":   d.get("tier", "free"),
        })
    traders.sort(key=lambda t: t["pnlSTN"], reverse=True)
    traders = traders[:limit]
    for rank, t in enumerate(traders, start=1):
        t["rank"] = rank
    return traders


async def publish_daily_ranking(bot: Bot) -> bool:
    """Devolve True apenas se a mensagem foi mesmo enviada ao canal."""
    if not CHANNEL_ID:
        logger.warning("TELEGRAM_CHANNEL_ID não definido — ranking diário NÃO enviado.")
        return False
    try:
        traders = await _fetch_daily_leaders(limit=5)
        if not traders:
            logger.info("Ranking diário: sem dados hoje — nada enviado.")
            return False
        msg = formatters.ranking_daily(traders)
        await bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="Markdown")
        logger.info("Ranking diário publicado no canal.")
        return True
    except Exception as e:
        logger.error(f"Ranking diário: falha ao enviar ao canal — {e}")
        return False


async def publish_weekly_ranking(bot: Bot) -> bool:
    """Devolve True apenas se a mensagem foi mesmo enviada ao canal."""
    if not CHANNEL_ID:
        logger.warning("TELEGRAM_CHANNEL_ID não definido — ranking semanal NÃO enviado.")
        return False
    try:
        traders = await _fetch_weekly_leaders(limit=10)
        if not traders:
            logger.info("Ranking semanal: sem dados — nada enviado.")
            return False
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=now.weekday() + 1 if now.weekday() != 6 else 0)
        week_label = f"{week_start.strftime('%-d/%m')} – {now.strftime('%-d/%m/%Y')}"
        msg = formatters.ranking_weekly(traders, week_label=week_label)
        await bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="Markdown")
        logger.info("Ranking semanal publicado no canal.")
        return True
    except Exception as e:
        logger.error(f"Ranking semanal: falha ao enviar ao canal — {e}")
        return False


async def publish_monthly_ranking(bot: Bot) -> bool:
    """Devolve True apenas se a mensagem foi mesmo enviada ao canal."""
    if not CHANNEL_ID:
        logger.warning("TELEGRAM_CHANNEL_ID não definido — ranking mensal NÃO enviado.")
        return False
    try:
        traders = await _fetch_monthly_leaders(limit=10)
        if not traders:
            logger.info("Ranking mensal: sem dados — nada enviado.")
            return False
        month_label = datetime.now(timezone.utc).strftime("%B %Y").capitalize()
        msg = formatters.ranking_monthly(traders, month_label=month_label)
        await bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode="Markdown")
        logger.info("Ranking mensal publicado no canal.")
        return True
    except Exception as e:
        logger.error(f"Ranking mensal: falha ao enviar ao canal — {e}")
        return False


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(publish_daily_ranking,  "cron", hour=23, minute=55, kwargs={"bot": bot}, id="ranking_daily",   replace_existing=True)
    scheduler.add_job(publish_weekly_ranking, "cron", day_of_week="sun", hour=20, minute=0,  kwargs={"bot": bot}, id="ranking_weekly",  replace_existing=True)
    scheduler.add_job(publish_monthly_ranking,"cron", day=1, hour=9, minute=0,               kwargs={"bot": bot}, id="ranking_monthly", replace_existing=True)
    logger.info("Scheduler configurado: ranking diário (23:55), semanal (dom 20:00), mensal (dia 1, 09:00).")

    # Posts de sessão (Madrugada/Manhã/Tarde/Noite) — só regista se
    # houver um grupo/canal definido, para não falhar silenciosamente.
    if SESSION_GROUP_ID:
        for key, session in SESSIONS.items():
            scheduler.add_job(
                send_session_post,
                "cron",
                hour=session["hour"],
                minute=session["minute"],
                kwargs={"bot": bot, "chat_id": SESSION_GROUP_ID, "session_key": key},
                id=f"session_post_{key}",
                replace_existing=True,
                misfire_grace_time=60,  # ainda executa se atrasar até 60s (ex: resumo diário a ocupar o loop)
                coalesce=True,          # se perder várias execuções, só corre uma vez ao recuperar
                max_instances=1,        # nunca duas instâncias do mesmo post em paralelo
            )
        logger.info(
            f"Scheduler: 4 posts de sessão registados para {SESSION_GROUP_ID} "
            f"(madrugada 04:15, manhã 08:01, tarde 13:30, noite 20:01, UTC)."
        )
    else:
        logger.warning(
            "TELEGRAM_SESSION_GROUP_ID / TELEGRAM_CHANNEL_ID não definido — "
            "posts de sessão NÃO foram agendados."
        )

    return scheduler
