"""Teste ponta a ponta pela API REST do Gateway: abre o SSE, cria pedidos e simula
o clique no Mock de Pagamento (aprovado e recusado), conferindo as notificações recebidas."""

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

from shared.config import GATEWAY_URL

CLIENTE = f"teste-{int(time.time())}"
recebidos: list[dict] = []


# Lê o stream SSE e guarda cada evento "pedido" recebido.
def ouvir_sse():
    with requests.get(f"{GATEWAY_URL}/eventos", params={"cliente_id": CLIENTE}, stream=True, timeout=60) as resp:
        evento = None
        for linha in resp.iter_lines(decode_unicode=True):
            if linha.startswith("event:"):
                evento = linha[6:].strip()
            elif linha.startswith("data:") and evento == "pedido":
                pedido = json.loads(linha[5:])
                recebidos.append(pedido)
                print(f"   [SSE] pedido #{pedido['id']}: {pedido['status']}")


def esperar_status(pedido_id: int, status: str, limite: float = 15) -> dict:
    fim = time.time() + limite
    while time.time() < fim:
        pedido = requests.get(f"{GATEWAY_URL}/pedidos/{pedido_id}").json()
        if pedido["status"] == status:
            return pedido
        time.sleep(0.3)
    raise AssertionError(f"pedido {pedido_id} nao chegou em '{status}' (atual: {pedido['status']})")


def criar_pedido(produto_id: int, quantidade: int) -> dict:
    resp = requests.post(f"{GATEWAY_URL}/pedidos", json={"cliente_id": CLIENTE, "itens": [{"produto_id": produto_id, "quantidade": quantidade}]})
    assert resp.status_code == 201, resp.text
    print(f" POST /pedidos -> 201, Location: {resp.headers['Location']}")
    return resp.json()


def pagar(pedido: dict, decisao: str) -> None:
    checkout = pedido["_links"]["pagamento"]["href"]
    cobranca_id = checkout.rstrip("/").split("/")[-1]
    base = checkout.rsplit("/checkout/", 1)[0]
    resp = requests.post(f"{base}/checkout/{cobranca_id}/{decisao}")
    print(f" Mock: clique em '{decisao}' -> HTTP {resp.status_code}")


def main():
    threading.Thread(target=ouvir_sse, daemon=True).start()
    time.sleep(1)

    produtos = requests.get(f"{GATEWAY_URL}/produtos").json()
    print(f" GET /produtos -> {len(produtos)} produtos")
    estoque_antes = {p["id"]: p["quantidade"] for p in produtos}

    print("\n== Pedido 1: pagamento aprovado ==")
    p1 = criar_pedido(2, 1)
    p1 = esperar_status(p1["id"], "Aguardando pagamento")
    pagar(p1, "aprovar")
    esperar_status(p1["id"], "Enviado")

    print("\n== Pedido 2: pagamento recusado (estoque deve ser devolvido) ==")
    p2 = criar_pedido(3, 2)
    p2 = esperar_status(p2["id"], "Aguardando pagamento")
    pagar(p2, "recusar")
    esperar_status(p2["id"], "Pagamento recusado")

    print("\n== Pedido 3: estoque indisponivel ==")
    p3 = criar_pedido(1, 9999)
    esperar_status(p3["id"], "Estoque indisponível")

    print("\n== Pedido 4: cancelado pelo cliente antes de pagar ==")
    p4 = criar_pedido(4, 1)
    p4 = esperar_status(p4["id"], "Aguardando pagamento")
    resp = requests.delete(f"{GATEWAY_URL}/pedidos/{p4['id']}")
    print(f" DELETE /pedidos/{p4['id']} -> {resp.status_code}")
    pagar(p4, "aprovar")  # o webhook deve ser rejeitado (409)
    time.sleep(1)
    assert requests.get(f"{GATEWAY_URL}/pedidos/{p4['id']}").json()["status"] == "Cancelado"

    print("\n== Interesse em promocoes ==")
    r = requests.post(f"{GATEWAY_URL}/interesses", json={"email": "teste@example.com", "categorias": ["A", "C"]})
    print(f" POST /interesses -> {r.status_code}")
    r = requests.delete(f"{GATEWAY_URL}/interesses/teste@example.com/categorias/C")
    print(f" DELETE /interesses/.../categorias/C -> {r.status_code}")
    r = requests.delete(f"{GATEWAY_URL}/interesses/teste@example.com")
    print(f" DELETE /interesses/... -> {r.status_code}")

    time.sleep(1)
    depois = {p["id"]: p["quantidade"] for p in requests.get(f"{GATEWAY_URL}/produtos", params={"todos": "true"}).json()}
    assert depois[2] == estoque_antes[2] - 1, "pedido aprovado deveria baixar 1 unidade"
    assert depois[3] == estoque_antes[3], "pedido recusado deveria devolver o estoque"
    assert depois[4] == estoque_antes[4], "pedido cancelado deveria devolver o estoque"

    print(f"\n Notificacoes SSE recebidas: {len(recebidos)}")
    assert len(recebidos) >= 11
    print(" OK: fluxo completo funcionando.")


if __name__ == "__main__":
    main()
