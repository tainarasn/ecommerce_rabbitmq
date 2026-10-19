"""Microsserviço de promoções: consome interesse.promocao para manter os inscritos (SQLite),
gera promoções periodicamente e envia por e-mail (API externa Resend) aos interessados na categoria."""

import random
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from shared.catalog import PRODUTOS
from shared.config import CATEGORIAS, ESTOQUE_URL, PROMOCAO_INTERVALO, RESEND_API_KEY, RESEND_FROM
from shared.rabbit import ECOMMERCE_EXCHANGE, start_consumer

SERVICE = "promocoes"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
DB_PATH = Path(__file__).resolve().parent / "promocoes.db"

RESEND_URL = "https://api.resend.com/emails"
DESCONTOS = [10, 15, 20, 25, 30]


def conectar() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def inicializar_banco() -> None:
    with conectar() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS interesses (
                email TEXT NOT NULL,
                categoria TEXT NOT NULL,
                PRIMARY KEY (email, categoria)
            )"""
        )


# ---------------------------------------------------------------- E-mail (API externa)


# Envia o e-mail pela API REST da Resend. Sem RESEND_API_KEY, apenas mostra no log.
def enviar_email(destinatario: str, assunto: str, html: str) -> None:
    if not RESEND_API_KEY:
        print(f" [e-mail simulado] Para: {destinatario} | Assunto: {assunto}")
        return
    try:
        resposta = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": RESEND_FROM, "to": [destinatario], "subject": assunto, "html": html},
            timeout=10,
        )
    except requests.RequestException as erro:
        print(f" [e-mail] Falha ao chamar a Resend: {erro}")
        return
    if resposta.ok:
        print(f" [e-mail] Enviado para {destinatario}: {assunto} (id {resposta.json().get('id')})")
    else:
        print(f" [e-mail] Resend respondeu {resposta.status_code} para {destinatario}: {resposta.text}")


# ---------------------------------------------------------------- RabbitMQ


# interesse.promocao: {"acao": "inscrever"|"cancelar", "email": "...", "categorias": [...]}
def handle_event(ch, method, properties, data):
    acao = data.get("acao")
    email = data.get("email")
    categorias = data.get("categorias") or []

    with conectar() as conn:
        if acao == "inscrever":
            conn.executemany("INSERT OR IGNORE INTO interesses (email, categoria) VALUES (?, ?)", [(email, c) for c in categorias])
            print(f" [x] {email} inscrito nas categorias {categorias}")
        elif acao == "cancelar":
            if categorias:
                conn.executemany("DELETE FROM interesses WHERE email = ? AND categoria = ?", [(email, c) for c in categorias])
            else:
                conn.execute("DELETE FROM interesses WHERE email = ?", (email,))
            print(f" [x] {email} cancelou interesse em {categorias or 'todas as categorias'}")
            return

    if acao == "inscrever":
        enviar_email(
            email,
            "Inscrição confirmada nas promoções",
            f"<p>Você vai receber promoções das categorias <b>{', '.join(categorias)}</b>.</p>",
        )


def iniciar_consumidor():
    bindings = [(ECOMMERCE_EXCHANGE, "interesse.promocao")]
    start_consumer("Microsservico Promocoes", "promocoes_queue", bindings, KEYS_DIR, handle_event)


# ---------------------------------------------------------------- Geração de promoções


# Busca os produtos disponíveis no MS Estoque (REST); se estiver fora do ar, usa o catálogo base.
def produtos_disponiveis() -> list[dict]:
    try:
        return requests.get(f"{ESTOQUE_URL}/produtos", timeout=3).json()
    except requests.RequestException:
        return [{"id": pid, **info} for pid, info in PRODUTOS.items()]


def interessados(categoria: str) -> list[str]:
    with conectar() as conn:
        return [r[0] for r in conn.execute("SELECT email FROM interesses WHERE categoria = ?", (categoria,))]


# Sorteia uma categoria que tenha inscritos, cria a promoção e notifica cada interessado.
def gerar_e_notificar() -> None:
    with conectar() as conn:
        com_inscritos = [r[0] for r in conn.execute("SELECT DISTINCT categoria FROM interesses")]
    if not com_inscritos:
        print(" [x] Nenhum consumidor inscrito ainda. Promocao nao gerada.")
        return

    categoria = random.choice(com_inscritos)
    produtos = [p for p in produtos_disponiveis() if p["categoria"] == categoria]
    if not produtos:
        print(f" [x] Nenhum produto disponivel na categoria {categoria}.")
        return

    produto = random.choice(produtos)
    desconto = random.choice(DESCONTOS)
    preco_promocional = round(produto["preco"] * (1 - desconto / 100), 2)
    emails = interessados(categoria)
    print(f" [x] Promocao: {produto['nome']} (cat. {categoria}) {desconto}% OFF -> {len(emails)} inscrito(s)")

    assunto = f"{desconto}% OFF em {produto['nome']}!"
    html = (
        f"<h2>Promoção na categoria {categoria}</h2>"
        f"<p><b>{produto['nome']}</b> de <s>R$ {produto['preco']:.2f}</s> por "
        f"<b>R$ {preco_promocional:.2f}</b> ({desconto}% OFF).</p>"
    )
    for email in emails:
        enviar_email(email, assunto, html)


def main():
    inicializar_banco()
    threading.Thread(target=iniciar_consumidor, daemon=True).start()
    modo = "Resend" if RESEND_API_KEY else "simulado (defina RESEND_API_KEY no .env)"
    print(f" [*] Gerando promocoes a cada {PROMOCAO_INTERVALO}s. Envio de e-mail: {modo}")
    try:
        while True:
            time.sleep(PROMOCAO_INTERVALO)
            gerar_e_notificar()
    except KeyboardInterrupt:
        print("\n [*] Microsservico Promocoes encerrado.")


if __name__ == "__main__":
    main()
