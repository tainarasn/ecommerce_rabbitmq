"""Microsserviço de estoque: persiste os produtos em SQLite, expõe a consulta via REST e
reserva/devolve itens ao consumir pedido.criado / pedido.excluido."""

import sqlite3
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request

from shared.catalog import ESTOQUE_INICIAL, PRODUTOS
from shared.config import ESTOQUE_PORT
from shared.rabbit import ECOMMERCE_EXCHANGE, publish_signed_event, start_consumer

SERVICE = "estoque"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"
DB_PATH = Path(__file__).resolve().parent / "estoque.db"

app = Flask(__name__)

# Serializa as operações de escrita (reserva/devolução) no banco.
lock = threading.Lock()


# Abre uma conexão nova a cada operação: conexões SQLite não devem ser compartilhadas entre threads.
def conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# Cria as tabelas e popula o catálogo inicial apenas na primeira execução.
def inicializar_banco() -> None:
    with conectar() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY,
                nome TEXT NOT NULL,
                categoria TEXT NOT NULL,
                preco REAL NOT NULL,
                quantidade INTEGER NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS reservas (
                pedido_id INTEGER NOT NULL,
                produto_id INTEGER NOT NULL,
                quantidade INTEGER NOT NULL,
                PRIMARY KEY (pedido_id, produto_id)
            )"""
        )
        if conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO produtos (id, nome, categoria, preco, quantidade) VALUES (?, ?, ?, ?, ?)",
                [
                    (pid, info["nome"], info["categoria"], info["preco"], ESTOQUE_INICIAL[pid])
                    for pid, info in PRODUTOS.items()
                ],
            )
            print(" [*] Banco de estoque criado com o catalogo inicial.")


# Reserva (dá baixa) nos produtos do pedido se todos tiverem quantidade suficiente.
def reservar_produtos(pedido_id: int, itens: list[dict]) -> tuple[bool, list[dict], float]:
    with lock, conectar() as conn:
        # Idempotência: se o evento chegar duplicado, não reserva duas vezes.
        existentes = conn.execute("SELECT produto_id, quantidade FROM reservas WHERE pedido_id = ?", (pedido_id,)).fetchall()
        if existentes:
            return True, [dict(r) for r in existentes], calcular_total(conn, existentes)

        for item in itens:
            row = conn.execute("SELECT quantidade FROM produtos WHERE id = ?", (item["produto_id"],)).fetchone()
            if row is None or row["quantidade"] < item["quantidade"]:
                return False, [], 0.0

        for item in itens:
            conn.execute(
                "UPDATE produtos SET quantidade = quantidade - ? WHERE id = ?",
                (item["quantidade"], item["produto_id"]),
            )
            conn.execute(
                "INSERT INTO reservas (pedido_id, produto_id, quantidade) VALUES (?, ?, ?)",
                (pedido_id, item["produto_id"], item["quantidade"]),
            )
        reservados = [{"produto_id": i["produto_id"], "quantidade": i["quantidade"]} for i in itens]
        return True, reservados, calcular_total(conn, reservados)


# Soma o valor dos itens usando o preço persistido no estoque.
def calcular_total(conn: sqlite3.Connection, itens) -> float:
    total = 0.0
    for item in itens:
        preco = conn.execute("SELECT preco FROM produtos WHERE id = ?", (item["produto_id"],)).fetchone()["preco"]
        total += preco * item["quantidade"]
    return round(total, 2)


# Devolve ao estoque os itens reservados quando o pedido é excluído.
def devolver_reserva(pedido_id: int) -> int:
    with lock, conectar() as conn:
        reservas = conn.execute("SELECT produto_id, quantidade FROM reservas WHERE pedido_id = ?", (pedido_id,)).fetchall()
        for r in reservas:
            conn.execute("UPDATE produtos SET quantidade = quantidade + ? WHERE id = ?", (r["quantidade"], r["produto_id"]))
        conn.execute("DELETE FROM reservas WHERE pedido_id = ?", (pedido_id,))
        return len(reservas)


# ---------------------------------------------------------------- RabbitMQ


# Processa eventos de criação e exclusão de pedidos.
def handle_event(ch, method, properties, data):
    routing_key = method.routing_key
    pedido_id = data.get("pedido_id")

    if routing_key == "pedido.criado":
        itens = data.get("itens", [])
        print(f" [x] Verificando estoque do pedido {pedido_id}: {itens}")

        ok, reservados, total = reservar_produtos(pedido_id, itens)
        if ok:
            print(f" [x] Estoque reservado para pedido {pedido_id} (total R$ {total:.2f})")
            publish_signed_event(
                ch,
                ECOMMERCE_EXCHANGE,
                "pedido.estoque_ok",
                {"pedido_id": pedido_id, "itens": reservados, "valor_total": total},
                SERVICE,
                PRIVATE_KEY,
            )
        else:
            print(f" [x] Estoque indisponivel para pedido {pedido_id}")
            publish_signed_event(
                ch,
                ECOMMERCE_EXCHANGE,
                "estoque.indisponivel",
                {"pedido_id": pedido_id, "itens": itens},
                SERVICE,
                PRIVATE_KEY,
            )

    elif routing_key == "pedido.excluido":
        devolvidos = devolver_reserva(pedido_id)
        print(f" [x] Pedido {pedido_id} excluido: {devolvidos} item(ns) devolvido(s) ao estoque")


def iniciar_consumidor():
    bindings = [
        (ECOMMERCE_EXCHANGE, "pedido.criado"),
        (ECOMMERCE_EXCHANGE, "pedido.excluido"),
    ]
    start_consumer("Microsservico Estoque", "estoque_queue", bindings, KEYS_DIR, handle_event)


# ---------------------------------------------------------------- REST


# GET /produtos -> lista os produtos disponíveis (quantidade > 0).
# Filtros via query string: ?categoria=A  e  ?todos=true (inclui os esgotados).
@app.get("/produtos")
def listar_produtos():
    sql = "SELECT id, nome, categoria, preco, quantidade FROM produtos WHERE 1=1"
    params: list = []
    if request.args.get("todos", "false").lower() != "true":
        sql += " AND quantidade > 0"
    if request.args.get("categoria"):
        sql += " AND categoria = ?"
        params.append(request.args["categoria"].upper())

    with conectar() as conn:
        produtos = [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]
    return jsonify(produtos), 200


# GET /produtos/<id> -> um produto específico ou 404.
@app.get("/produtos/<int:produto_id>")
def obter_produto(produto_id: int):
    with conectar() as conn:
        row = conn.execute("SELECT id, nome, categoria, preco, quantidade FROM produtos WHERE id = ?", (produto_id,)).fetchone()
    if row is None:
        return jsonify({"erro": "Produto nao encontrado"}), 404
    return jsonify(dict(row)), 200


def main():
    inicializar_banco()
    threading.Thread(target=iniciar_consumidor, daemon=True).start()
    print(f" [*] API REST do Estoque em http://localhost:{ESTOQUE_PORT}/produtos")
    app.run(host="localhost", port=ESTOQUE_PORT, threaded=True)


if __name__ == "__main__":
    main()
