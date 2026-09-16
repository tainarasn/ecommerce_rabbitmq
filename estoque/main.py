import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.catalog import ESTOQUE_INICIAL
from shared.rabbit import ECOMMERCE_EXCHANGE, publish_signed_event, start_consumer

SERVICE = "estoque"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"

estoque = dict(ESTOQUE_INICIAL)
reservas: dict[int, list[dict]] = {}


def reservar_produtos(pedido_id: int, produtos: list[dict]) -> tuple[bool, list[dict] | None]:
    for item in produtos:
        produto_id = item["id"]
        quantidade = item["quantidade"]
        if estoque.get(produto_id, 0) < quantidade:
            return False, None

    for item in produtos:
        produto_id = item["id"]
        quantidade = item["quantidade"]
        estoque[produto_id] -= quantidade

    reservas[pedido_id] = [dict(item) for item in produtos]
    return True, reservas[pedido_id]


def devolver_reserva(pedido_id: int) -> None:
    produtos = reservas.pop(pedido_id, [])
    for item in produtos:
        produto_id = item["id"]
        quantidade = item["quantidade"]
        estoque[produto_id] = estoque.get(produto_id, 0) + quantidade


def handle_event(ch, method, properties, data):
    routing_key = method.routing_key
    pedido_id = data.get("pedido_id")

    if routing_key == "pedido.criado":
        produtos = data.get("produtos", [])
        print(f" [x] Verificando estoque do pedido {pedido_id}...")

        ok, reservados = reservar_produtos(pedido_id, produtos)
        if ok:
            print(f" [x] Estoque reservado para pedido {pedido_id}: {reservados}")
            publish_signed_event(
                ch,
                ECOMMERCE_EXCHANGE,
                "pedido.estoque_ok",
                {"pedido_id": pedido_id, "produtos": reservados},
                SERVICE,
                PRIVATE_KEY,
            )
        else:
            print(f" [x] Estoque indisponivel para pedido {pedido_id}")
            publish_signed_event(
                ch,
                ECOMMERCE_EXCHANGE,
                "estoque.indisponivel",
                {"pedido_id": pedido_id, "produtos": produtos},
                SERVICE,
                PRIVATE_KEY,
            )

    elif routing_key == "pedido.excluido":
        print(f" [x] Devolvendo estoque do pedido {pedido_id}...")
        devolver_reserva(pedido_id)
        print(f" [x] Estoque devolvido para pedido {pedido_id}")


def main():
    bindings = [
        (ECOMMERCE_EXCHANGE, "pedido.criado"),
        (ECOMMERCE_EXCHANGE, "pedido.excluido"),
    ]
    start_consumer("Microsservico Estoque", "estoque_queue", bindings, KEYS_DIR, handle_event)


if __name__ == "__main__":
    main()
