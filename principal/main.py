"""MS Principal / API Gateway: expõe a API REST consumida pelo frontend, mantém as conexões SSE
para notificar o status dos pedidos em tempo real e traduz as ações do usuário em eventos no RabbitMQ."""

import json
import queue
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from flask import Flask, Response, jsonify, request

from shared.config import CATEGORIAS, ESTOQUE_URL, GATEWAY_PORT, GATEWAY_URL
from shared.rabbit import ECOMMERCE_EXCHANGE, publish_once, publish_signed_event, start_consumer

SERVICE = "principal"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Status em que o cliente ainda pode cancelar o pedido (antes do pagamento ser aprovado).
STATUS_CANCELAVEIS = {"Pedido criado", "Estoque confirmado", "Aguardando pagamento"}

app = Flask(__name__)

# Estado em memória dos pedidos.
pedidos: dict[int, dict] = {}
lock = threading.Lock()
proximo_id = 1

# Conexões SSE abertas: cliente_id -> filas (uma por aba/conexão EventSource do cliente).
assinantes: dict[str, list[queue.Queue]] = {}
assinantes_lock = threading.Lock()


# ---------------------------------------------------------------- CORS
# O frontend roda em outra origem (porta 8080), então o navegador exige estes cabeçalhos.
@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Expose-Headers"] = "Location"
    return response


# ---------------------------------------------------------------- Representação (HATEOAS)


# Monta a representação do pedido com os links das ações disponíveis no estado atual.
# O frontend só mostra "Cancelar"/"Pagar" se o link correspondente vier na resposta.
def representar(pedido: dict) -> dict:
    href = f"{GATEWAY_URL}/pedidos/{pedido['id']}"
    links = {"self": {"href": href, "method": "GET"}}
    if pedido["status"] in STATUS_CANCELAVEIS:
        links["cancelar"] = {"href": href, "method": "DELETE"}
    if pedido["status"] == "Aguardando pagamento" and pedido.get("pagamento_url"):
        links["pagamento"] = {"href": pedido["pagamento_url"], "method": "GET"}
    return {**pedido, "_links": links}


# ---------------------------------------------------------------- SSE


# Envia uma notificação para todas as conexões SSE abertas do cliente.
def notificar(cliente_id: str, evento: str, dados: dict) -> None:
    with assinantes_lock:
        filas = list(assinantes.get(cliente_id, []))
    for fila in filas:
        fila.put((evento, dados))


# Formata uma mensagem no protocolo SSE: "event: <nome>\ndata: <json>\n\n".
def formatar_sse(evento: str, dados: dict) -> str:
    return f"event: {evento}\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n"


# GET /eventos?cliente_id=X -> conexão SSE (text/event-stream) mantida aberta pelo servidor.
@app.get("/eventos")
def eventos():
    cliente_id = request.args.get("cliente_id", "").strip()
    if not cliente_id:
        return jsonify({"erro": "Informe cliente_id na query string"}), 400

    fila: queue.Queue = queue.Queue()
    with assinantes_lock:
        assinantes.setdefault(cliente_id, []).append(fila)
    print(f" [SSE] Cliente {cliente_id} conectado")

    def stream():
        try:
            yield formatar_sse("conectado", {"cliente_id": cliente_id})
            while True:
                try:
                    evento, dados = fila.get(timeout=15)
                    yield formatar_sse(evento, dados)
                except queue.Empty:
                    # Comentário SSE: mantém a conexão viva e detecta clientes que fecharam a aba.
                    yield ": keep-alive\n\n"
        finally:
            with assinantes_lock:
                assinantes.get(cliente_id, []).remove(fila)
            print(f" [SSE] Cliente {cliente_id} desconectado")

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- Estado dos pedidos


# Atualiza o status, registra no histórico e notifica o dono do pedido via SSE.
def atualizar_status(pedido_id: int, status: str, detalhe: str, **extras) -> None:
    with lock:
        pedido = pedidos.get(pedido_id)
        if pedido is None:
            return
        pedido.update(extras)
        pedido["status"] = status
        pedido["detalhe"] = detalhe
        pedido["historico"].append({"status": status, "detalhe": detalhe, "em": datetime.now().isoformat(timespec="seconds")})
        representacao = representar(pedido)
        cliente_id = pedido["cliente_id"]
    notificar(cliente_id, "pedido", representacao)


# ---------------------------------------------------------------- RabbitMQ (consumidor)


# Trata os eventos de Estoque, Pagamento e Entrega e converte em notificações SSE.
def handle_event(ch, method, properties, data):
    routing_key = method.routing_key
    pedido_id = data.get("pedido_id")

    with lock:
        pedido = pedidos.get(pedido_id)
        status_atual = pedido["status"] if pedido else None
    if pedido is None:
        print(f" [>] Evento {routing_key} de pedido desconhecido ({pedido_id}). Ignorando.")
        return
    if status_atual == "Cancelado":
        print(f" [>] Evento {routing_key} do pedido {pedido_id} ignorado: pedido ja cancelado.")
        return

    print(f" [>] Pedido {pedido_id}: {routing_key}")

    if routing_key == "pedido.estoque_ok":
        atualizar_status(pedido_id, "Estoque confirmado", "Produtos reservados no estoque", total=data.get("valor_total"))

    elif routing_key == "estoque.indisponivel":
        atualizar_status(pedido_id, "Estoque indisponível", "Algum produto não tem quantidade suficiente")
        publicar_exclusao(ch, pedido_id, "Estoque indisponivel")

    elif routing_key == "pagamento.link_gerado":
        atualizar_status(
            pedido_id,
            "Aguardando pagamento",
            "Cobrança criada no provedor de pagamento",
            pagamento_url=data.get("checkout_url"),
        )

    elif routing_key == "pagamento.aprovado":
        atualizar_status(pedido_id, "Pagamento aprovado", "Emitindo nota fiscal e preparando envio")

    elif routing_key == "pagamento.recusado":
        atualizar_status(pedido_id, "Pagamento recusado", data.get("motivo", "Pagamento recusado pelo provedor"))
        publicar_exclusao(ch, pedido_id, "Pagamento recusado")

    elif routing_key == "pedido.enviado":
        nota = data.get("nota_fiscal", "")
        atualizar_status(pedido_id, "Enviado", f"Nota fiscal {nota}", nota_fiscal=nota)


# Publica pedido.excluido para o Estoque devolver a reserva (e o Pagamento cancelar a cobrança).
def publicar_exclusao(channel, pedido_id: int, motivo: str) -> None:
    publish_signed_event(
        channel,
        ECOMMERCE_EXCHANGE,
        "pedido.excluido",
        {"pedido_id": pedido_id, "motivo": motivo},
        SERVICE,
        PRIVATE_KEY,
    )


def iniciar_consumidor():
    bindings = [
        (ECOMMERCE_EXCHANGE, "pedido.estoque_ok"),
        (ECOMMERCE_EXCHANGE, "estoque.indisponivel"),
        (ECOMMERCE_EXCHANGE, "pagamento.link_gerado"),
        (ECOMMERCE_EXCHANGE, "pagamento.aprovado"),
        (ECOMMERCE_EXCHANGE, "pagamento.recusado"),
        (ECOMMERCE_EXCHANGE, "pedido.enviado"),
    ]
    start_consumer("API Gateway (consumer)", "principal_queue", bindings, KEYS_DIR, handle_event)


# ---------------------------------------------------------------- REST


# GET / -> ponto de entrada da API: o cliente só conhece este endereço e descobre o resto pelos links.
@app.get("/")
def raiz():
    return jsonify(
        {
            "_links": {
                "produtos": {"href": f"{GATEWAY_URL}/produtos", "method": "GET"},
                "criar_pedido": {"href": f"{GATEWAY_URL}/pedidos", "method": "POST"},
                "pedidos": {"href": f"{GATEWAY_URL}/pedidos{{?cliente_id}}", "method": "GET"},
                "registrar_interesse": {"href": f"{GATEWAY_URL}/interesses", "method": "POST"},
                "cancelar_interesse": {"href": f"{GATEWAY_URL}/interesses/{{email}}", "method": "DELETE"},
                "eventos": {"href": f"{GATEWAY_URL}/eventos{{?cliente_id}}", "method": "GET"},
            }
        }
    )


# GET /produtos -> consulta o MS Estoque via REST e repassa a lista (aceita ?categoria=A).
@app.get("/produtos")
def listar_produtos():
    try:
        resposta = requests.get(f"{ESTOQUE_URL}/produtos", params=request.args, timeout=3)
    except requests.RequestException:
        return jsonify({"erro": "MS Estoque indisponivel"}), 503
    return jsonify(resposta.json()), resposta.status_code


# POST /pedidos -> cria o pedido e publica pedido.criado.
# Corpo: {"cliente_id": "abc", "itens": [{"produto_id": 1, "quantidade": 2}]}
@app.post("/pedidos")
def criar_pedido():
    global proximo_id

    corpo = request.get_json(silent=True) or {}
    cliente_id = str(corpo.get("cliente_id", "")).strip()
    itens = corpo.get("itens")

    if not cliente_id:
        return jsonify({"erro": "cliente_id e obrigatorio"}), 400
    if not isinstance(itens, list) or not itens:
        return jsonify({"erro": "itens deve ser uma lista nao vazia"}), 400
    for item in itens:
        if not isinstance(item, dict) or not isinstance(item.get("produto_id"), int) or not isinstance(item.get("quantidade"), int):
            return jsonify({"erro": "cada item precisa de produto_id e quantidade inteiros"}), 400
        if item["quantidade"] <= 0:
            return jsonify({"erro": "quantidade deve ser maior que zero"}), 400

    # Consulta o catálogo no MS Estoque para validar os produtos e completar nome/preço.
    try:
        catalogo = {p["id"]: p for p in requests.get(f"{ESTOQUE_URL}/produtos", params={"todos": "true"}, timeout=3).json()}
    except requests.RequestException:
        return jsonify({"erro": "MS Estoque indisponivel"}), 503

    itens_pedido = []
    for item in itens:
        produto = catalogo.get(item["produto_id"])
        if produto is None:
            return jsonify({"erro": f"produto {item['produto_id']} nao existe"}), 404
        itens_pedido.append(
            {"produto_id": produto["id"], "nome": produto["nome"], "preco": produto["preco"], "quantidade": item["quantidade"]}
        )

    agora = datetime.now().isoformat(timespec="seconds")
    with lock:
        pedido_id = proximo_id
        proximo_id += 1
        pedidos[pedido_id] = {
            "id": pedido_id,
            "cliente_id": cliente_id,
            "itens": itens_pedido,
            "total": round(sum(i["preco"] * i["quantidade"] for i in itens_pedido), 2),
            "status": "Pedido criado",
            "detalhe": "Aguardando confirmação do estoque",
            "pagamento_url": None,
            "nota_fiscal": None,
            "criado_em": agora,
            "historico": [{"status": "Pedido criado", "detalhe": "Aguardando confirmação do estoque", "em": agora}],
        }
        representacao = representar(pedidos[pedido_id])

    publish_once(
        "pedido.criado",
        {
            "pedido_id": pedido_id,
            "cliente_id": cliente_id,
            "itens": [{"produto_id": i["produto_id"], "quantidade": i["quantidade"]} for i in itens_pedido],
        },
        SERVICE,
        PRIVATE_KEY,
    )
    print(f" [<] Pedido {pedido_id} criado pelo cliente {cliente_id}: publicado pedido.criado")

    # 201 Created + Location com a URL do novo recurso.
    return jsonify(representacao), 201, {"Location": representacao["_links"]["self"]["href"]}


# GET /pedidos?cliente_id=X -> pedidos do cliente.
@app.get("/pedidos")
def listar_pedidos():
    cliente_id = request.args.get("cliente_id")
    with lock:
        lista = [representar(p) for p in pedidos.values() if not cliente_id or p["cliente_id"] == cliente_id]
    return jsonify(lista), 200


# GET /pedidos/<id> -> um pedido ou 404.
@app.get("/pedidos/<int:pedido_id>")
def obter_pedido(pedido_id: int):
    with lock:
        pedido = pedidos.get(pedido_id)
        if pedido is None:
            return jsonify({"erro": "Pedido nao encontrado"}), 404
        return jsonify(representar(pedido)), 200


# DELETE /pedidos/<id> -> cancela o pedido e publica pedido.excluido.
@app.delete("/pedidos/<int:pedido_id>")
def cancelar_pedido(pedido_id: int):
    with lock:
        pedido = pedidos.get(pedido_id)
        if pedido is None:
            return jsonify({"erro": "Pedido nao encontrado"}), 404
        status = pedido["status"]

    # DELETE é idempotente: cancelar de novo um pedido já cancelado não muda nada.
    if status == "Cancelado":
        return "", 204
    if status not in STATUS_CANCELAVEIS:
        return jsonify({"erro": f"Pedido no status '{status}' nao pode ser cancelado"}), 409

    publish_once("pedido.excluido", {"pedido_id": pedido_id, "motivo": "Cancelado pelo cliente"}, SERVICE, PRIVATE_KEY)
    atualizar_status(pedido_id, "Cancelado", "Cancelado pelo cliente")
    print(f" [<] Pedido {pedido_id} cancelado pelo cliente: publicado pedido.excluido")
    return "", 204


# Valida o e-mail e as categorias recebidas nas rotas de interesse.
def validar_interesse(email: str, categorias) -> str | None:
    if not EMAIL_RE.match(email):
        return "e-mail invalido"
    if not isinstance(categorias, list) or any(c not in CATEGORIAS for c in categorias):
        return f"categorias deve ser uma lista com valores em {CATEGORIAS}"
    return None


# POST /interesses -> registra interesse em promoções. Corpo: {"email": "...", "categorias": ["A", "B"]}
# Responde 202 Accepted: o pedido foi aceito, mas quem grava o interesse é o MS Promoções (assíncrono).
@app.post("/interesses")
def registrar_interesse():
    corpo = request.get_json(silent=True) or {}
    email = str(corpo.get("email", "")).strip().lower()
    categorias = corpo.get("categorias")

    erro = validar_interesse(email, categorias)
    if erro or not categorias:
        return jsonify({"erro": erro or "escolha ao menos uma categoria"}), 400

    publish_once("interesse.promocao", {"acao": "inscrever", "email": email, "categorias": categorias}, SERVICE, PRIVATE_KEY)
    print(f" [<] Interesse registrado: {email} -> {categorias}")
    return jsonify({"email": email, "categorias": categorias, "status": "inscricao enviada"}), 202


# DELETE /interesses/<email> -> cancela o interesse em todas as categorias.
@app.delete("/interesses/<email>")
def cancelar_interesse(email: str):
    return _cancelar(email.strip().lower(), [])


# DELETE /interesses/<email>/categorias/<categoria> -> cancela o interesse em uma categoria.
@app.delete("/interesses/<email>/categorias/<categoria>")
def cancelar_interesse_categoria(email: str, categoria: str):
    return _cancelar(email.strip().lower(), [categoria.upper()])


def _cancelar(email: str, categorias: list[str]):
    erro = validar_interesse(email, categorias)
    if erro:
        return jsonify({"erro": erro}), 400
    publish_once("interesse.promocao", {"acao": "cancelar", "email": email, "categorias": categorias}, SERVICE, PRIVATE_KEY)
    print(f" [<] Interesse cancelado: {email} -> {categorias or 'todas'}")
    return jsonify({"email": email, "categorias": categorias or CATEGORIAS, "status": "cancelamento enviado"}), 202


def main():
    threading.Thread(target=iniciar_consumidor, daemon=True).start()
    print(f" [*] API Gateway em {GATEWAY_URL}")
    app.run(host="localhost", port=GATEWAY_PORT, threaded=True)


if __name__ == "__main__":
    main()
