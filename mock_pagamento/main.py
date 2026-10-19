"""Mock de Pagamento (sistema externo): simula um provedor de pagamentos.
- POST /cobrancas: o MS Pagamento cria a cobrança e recebe a URL de checkout.
- GET /checkout/<id>: página aberta pelo usuário em nova aba, com os botões Aprovar/Recusar.
- Ao clicar, faz o POST (Webhook) para a URL informada pelo MS Pagamento.
Não usa RabbitMQ: é um sistema externo que conversa apenas por HTTP."""

import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from flask import Flask, jsonify, render_template_string, request

from shared.config import MOCK_PAGAMENTO_PORT, MOCK_PAGAMENTO_URL

app = Flask(__name__)

# cobranca_id -> dados da cobrança
cobrancas: dict[str, dict] = {}
lock = threading.Lock()


# POST /cobrancas -> cria a cobrança. Corpo: {"pedido_id", "valor", "descricao", "webhook_url"}
@app.post("/cobrancas")
def criar_cobranca():
    corpo = request.get_json(silent=True) or {}
    if not corpo.get("webhook_url") or "pedido_id" not in corpo or "valor" not in corpo:
        return jsonify({"erro": "pedido_id, valor e webhook_url sao obrigatorios"}), 400

    cobranca_id = uuid.uuid4().hex[:10]
    cobranca = {
        "id": cobranca_id,
        "pedido_id": corpo["pedido_id"],
        "valor": float(corpo["valor"]),
        "descricao": corpo.get("descricao", ""),
        "webhook_url": corpo["webhook_url"],
        "status": "PENDENTE",
        "criada_em": datetime.now().isoformat(timespec="seconds"),
        "checkout_url": f"{MOCK_PAGAMENTO_URL}/checkout/{cobranca_id}",
    }
    with lock:
        cobrancas[cobranca_id] = cobranca
    print(f" [*] Cobranca {cobranca_id} criada para pedido {cobranca['pedido_id']} (R$ {cobranca['valor']:.2f})")
    return jsonify(cobranca), 201, {"Location": f"{MOCK_PAGAMENTO_URL}/cobrancas/{cobranca_id}"}


# GET /cobrancas/<id> -> consulta a cobrança.
@app.get("/cobrancas/<cobranca_id>")
def obter_cobranca(cobranca_id: str):
    cobranca = cobrancas.get(cobranca_id)
    if cobranca is None:
        return jsonify({"erro": "Cobranca nao encontrada"}), 404
    return jsonify(cobranca), 200


# GET /checkout/<id> -> página de pagamento exibida ao usuário.
@app.get("/checkout/<cobranca_id>")
def checkout(cobranca_id: str):
    cobranca = cobrancas.get(cobranca_id)
    if cobranca is None:
        return render_template_string(PAGINA, cobranca=None, mensagem="Cobrança não encontrada."), 404
    return render_template_string(PAGINA, cobranca=cobranca, mensagem=None)


# POST /checkout/<id>/<decisao> -> usuário clicou em Aprovar/Recusar: dispara o Webhook.
@app.post("/checkout/<cobranca_id>/<decisao>")
def decidir(cobranca_id: str, decisao: str):
    cobranca = cobrancas.get(cobranca_id)
    if cobranca is None or decisao not in ("aprovar", "recusar"):
        return render_template_string(PAGINA, cobranca=None, mensagem="Requisição inválida."), 404
    if cobranca["status"] != "PENDENTE":
        return render_template_string(PAGINA, cobranca=cobranca, mensagem="Esta cobrança já foi processada.")

    status = "APROVADO" if decisao == "aprovar" else "RECUSADO"
    payload = {"cobranca_id": cobranca_id, "pedido_id": cobranca["pedido_id"], "status": status}
    print(f" [*] Enviando Webhook para {cobranca['webhook_url']}: {payload}")

    try:
        resposta = requests.post(cobranca["webhook_url"], json=payload, timeout=5)
    except requests.RequestException:
        return render_template_string(PAGINA, cobranca=cobranca, mensagem="A loja não respondeu ao Webhook. Tente novamente.")

    if resposta.ok:
        cobranca["status"] = status
        mensagem = "Pagamento aprovado! Você já pode fechar esta aba." if status == "APROVADO" else "Pagamento recusado. Você já pode fechar esta aba."
    elif resposta.status_code == 409:
        cobranca["status"] = "CANCELADO"
        mensagem = "O pedido foi cancelado pela loja. Nenhum valor foi cobrado."
    else:
        mensagem = f"A loja respondeu {resposta.status_code} ao Webhook."
    print(f" [*] Webhook respondeu {resposta.status_code}")
    return render_template_string(PAGINA, cobranca=cobranca, mensagem=mensagem, webhook_status=resposta.status_code)


PAGINA = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PayMock · Checkout</title>
<style>
  body { margin:0; font-family: system-ui, sans-serif; background:#eef1f6; color:#1d2433;
         min-height:100vh; display:grid; place-items:center; padding:16px; box-sizing:border-box; }
  .card { background:#fff; border-radius:14px; box-shadow:0 10px 30px rgba(0,0,0,.08); padding:28px; width:100%; max-width:420px; }
  .marca { font-weight:800; color:#5b3df5; letter-spacing:.5px; margin-bottom:18px; }
  .valor { font-size:2.2rem; font-weight:700; margin:6px 0 2px; }
  .muted { color:#6b7280; font-size:.9rem; }
  .linha { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #eef0f4; font-size:.92rem; }
  .botoes { display:flex; gap:10px; margin-top:22px; }
  button { flex:1; border:0; border-radius:10px; padding:14px; font-size:1rem; font-weight:600; cursor:pointer; color:#fff; }
  .aprovar { background:#16a34a; } .recusar { background:#dc2626; }
  button:hover { filter:brightness(1.08); }
  .msg { margin-top:20px; padding:12px; border-radius:10px; background:#f3f4f6; font-size:.95rem; }
  .st { font-weight:700; }
</style>
</head>
<body>
<div class="card">
  <div class="marca">PayMock · provedor de pagamento simulado</div>
  {% if cobranca %}
    <div class="muted">{{ cobranca.descricao }}</div>
    <div class="valor">R$ {{ "%.2f"|format(cobranca.valor) }}</div>
    <div class="linha"><span>Cobrança</span><span>{{ cobranca.id }}</span></div>
    <div class="linha"><span>Status</span><span class="st">{{ cobranca.status }}</span></div>
    <div class="linha"><span>Webhook</span><span class="muted">{{ cobranca.webhook_url }}</span></div>
    {% if cobranca.status == "PENDENTE" %}
    <div class="botoes">
      <form method="post" action="/checkout/{{ cobranca.id }}/aprovar" style="flex:1;display:flex"><button class="aprovar">Pagamento Aprovado</button></form>
      <form method="post" action="/checkout/{{ cobranca.id }}/recusar" style="flex:1;display:flex"><button class="recusar">Pagamento Recusado</button></form>
    </div>
    {% endif %}
  {% endif %}
  {% if mensagem %}<div class="msg">{{ mensagem }}{% if webhook_status %}<br><span class="muted">Webhook respondeu HTTP {{ webhook_status }}</span>{% endif %}</div>{% endif %}
</div>
</body>
</html>"""


def main():
    print(f" [*] Mock de Pagamento em {MOCK_PAGAMENTO_URL}")
    app.run(host="localhost", port=MOCK_PAGAMENTO_PORT, threaded=True)


if __name__ == "__main__":
    main()
