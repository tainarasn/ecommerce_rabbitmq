"""Microsserviço de pagamento: ao consumir pedido.estoque_ok, solicita uma cobrança ao Mock de
Pagamento (REST) e recebe o resultado pelo Webhook, publicando pagamento.aprovado/recusado."""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from flask import Flask, jsonify, request

from shared.config import MOCK_PAGAMENTO_URL, PAGAMENTO_PORT, PAGAMENTO_URL
from shared.rabbit import ECOMMERCE_EXCHANGE, publish_once, publish_signed_event, start_consumer

SERVICE = "pagamento"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"

# URL que o provedor externo chamará quando o usuário decidir o pagamento.
WEBHOOK_URL = f"{PAGAMENTO_URL}/webhook/pagamento"

app = Flask(__name__)

# pedido_id -> {"cobranca_id", "valor", "status"}  (status: PENDENTE, APROVADO, RECUSADO, CANCELADO)
cobrancas: dict[int, dict] = {}
lock = threading.Lock()


# ---------------------------------------------------------------- RabbitMQ


# Pede ao Mock de Pagamento a criação da cobrança, informando a URL de Webhook para o retorno.
def solicitar_cobranca(ch, pedido_id: int, valor: float) -> None:
    print(f" [x] Solicitando cobranca do pedido {pedido_id} (R$ {valor:.2f}) ao provedor...")
    try:
        resposta = requests.post(
            f"{MOCK_PAGAMENTO_URL}/cobrancas",
            json={
                "pedido_id": pedido_id,
                "valor": valor,
                "descricao": f"Pedido #{pedido_id}",
                "webhook_url": WEBHOOK_URL,
            },
            timeout=5,
        )
        resposta.raise_for_status()
        cobranca = resposta.json()
    except requests.RequestException as erro:
        print(f" [x] Provedor de pagamento indisponivel: {erro}")
        publish_signed_event(
            ch,
            ECOMMERCE_EXCHANGE,
            "pagamento.recusado",
            {"pedido_id": pedido_id, "motivo": "Provedor de pagamento indisponível"},
            SERVICE,
            PRIVATE_KEY,
        )
        return

    with lock:
        cobrancas[pedido_id] = {"cobranca_id": cobranca["id"], "valor": valor, "status": "PENDENTE"}

    print(f" [x] Cobranca {cobranca['id']} criada. Checkout: {cobranca['checkout_url']}")
    publish_signed_event(
        ch,
        ECOMMERCE_EXCHANGE,
        "pagamento.link_gerado",
        {"pedido_id": pedido_id, "cobranca_id": cobranca["id"], "checkout_url": cobranca["checkout_url"]},
        SERVICE,
        PRIVATE_KEY,
    )


def handle_event(ch, method, properties, data):
    pedido_id = data.get("pedido_id")

    if method.routing_key == "pedido.estoque_ok":
        solicitar_cobranca(ch, pedido_id, float(data.get("valor_total", 0)))

    elif method.routing_key == "pedido.excluido":
        # Se o pedido foi cancelado antes do pagamento, o Webhook posterior será recusado.
        with lock:
            cobranca = cobrancas.get(pedido_id)
            if cobranca and cobranca["status"] == "PENDENTE":
                cobranca["status"] = "CANCELADO"
                print(f" [x] Cobranca do pedido {pedido_id} cancelada")


def iniciar_consumidor():
    bindings = [
        (ECOMMERCE_EXCHANGE, "pedido.estoque_ok"),
        (ECOMMERCE_EXCHANGE, "pedido.excluido"),
    ]
    start_consumer("Microsservico Pagamento", "pagamento_queue", bindings, KEYS_DIR, handle_event)


# ---------------------------------------------------------------- Webhook (REST)


# POST /webhook/pagamento -> chamado pelo provedor externo com o resultado da cobrança.
# Corpo: {"cobranca_id": "...", "pedido_id": 1, "status": "APROVADO" | "RECUSADO"}
@app.post("/webhook/pagamento")
def webhook_pagamento():
    corpo = request.get_json(silent=True) or {}
    pedido_id = corpo.get("pedido_id")
    status = corpo.get("status")

    if status not in ("APROVADO", "RECUSADO") or not isinstance(pedido_id, int):
        return jsonify({"erro": "payload invalido"}), 400

    with lock:
        cobranca = cobrancas.get(pedido_id)
        if cobranca is None or cobranca["cobranca_id"] != corpo.get("cobranca_id"):
            return jsonify({"erro": "cobranca nao encontrada"}), 404
        # Webhooks podem ser reenviados: se já processamos o mesmo status, só confirmamos.
        if cobranca["status"] == status:
            return jsonify({"recebido": True, "status": status}), 200
        if cobranca["status"] != "PENDENTE":
            return jsonify({"erro": f"cobranca ja esta {cobranca['status']}"}), 409
        cobranca["status"] = status

    print(f" [x] Webhook recebido: pedido {pedido_id} -> {status}")
    routing_key = "pagamento.aprovado" if status == "APROVADO" else "pagamento.recusado"
    publish_once(routing_key, {"pedido_id": pedido_id, "cobranca_id": cobranca["cobranca_id"]}, SERVICE, PRIVATE_KEY)
    return jsonify({"recebido": True, "status": status}), 200


def main():
    threading.Thread(target=iniciar_consumidor, daemon=True).start()
    print(f" [*] Webhook do Pagamento em {WEBHOOK_URL}")
    app.run(host="localhost", port=PAGAMENTO_PORT, threaded=True)


if __name__ == "__main__":
    main()
