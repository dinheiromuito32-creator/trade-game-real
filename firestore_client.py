"""
firestore_client.py

Wrapper fino sobre o Firebase Admin SDK. Inicializa a ligação ao MESMO
Firestore que o app Cless Cripto usa — uma única fonte de verdade,
sem duplicar dados.

IMPORTANTE — SEGURANÇA:
A credencial nunca é lida de um valor fixo neste ficheiro. É sempre lida
de uma variável de ambiente (FIREBASE_SERVICE_ACCOUNT_JSON em produção,
ou FIREBASE_SERVICE_ACCOUNT_PATH em desenvolvimento local). Nunca commitar
nem colar a chave diretamente em código.
"""

import json
import os
import logging

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

_db = None


def init_firestore():
    """
    Inicializa o Firebase Admin SDK uma única vez (idempotente — chamadas
    repetidas reutilizam a instância já criada). Deve ser chamado no
    arranque do bot, antes de qualquer outro módulo tentar usar get_db().
    """
    global _db

    if firebase_admin._apps:
        # Já inicializado (ex: recarregamento em dev) — reutiliza.
        _db = firestore.client()
        return _db

    json_blob = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    json_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH")

    if json_blob:
        # Produção (Railway): credencial inteira numa variável de ambiente.
        try:
            cred_dict = json.loads(json_blob)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                "FIREBASE_SERVICE_ACCOUNT_JSON não é um JSON válido. "
                "Confirma que colaste o ficheiro inteiro, numa linha só."
            ) from e
        cred = credentials.Certificate(cred_dict)
    elif json_path:
        # Desenvolvimento local: caminho para ficheiro .json (nunca
        # commitado — está no .gitignore).
        if not os.path.isfile(json_path):
            raise RuntimeError(
                f"FIREBASE_SERVICE_ACCOUNT_PATH aponta para '{json_path}', "
                "mas esse ficheiro não existe."
            )
        cred = credentials.Certificate(json_path)
    else:
        raise RuntimeError(
            "Nenhuma credencial Firebase encontrada. Define "
            "FIREBASE_SERVICE_ACCOUNT_JSON (produção) ou "
            "FIREBASE_SERVICE_ACCOUNT_PATH (desenvolvimento local) "
            "no ficheiro .env."
        )

    firebase_admin.initialize_app(cred)
    _db = firestore.client()
    logger.info("Firebase Admin SDK inicializado com sucesso.")
    return _db


def get_db():
    """
    Devolve a instância do cliente Firestore. Lança erro claro se
    init_firestore() ainda não foi chamado — evita um NoneType
    silencioso mais tarde, no meio de uma operação de trading.
    """
    if _db is None:
        raise RuntimeError(
            "Firestore ainda não foi inicializado. "
            "Chama init_firestore() no arranque do bot, antes de "
            "qualquer handler tentar aceder à base de dados."
        )
    return _db
