import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.rabbit import ECOMMERCE_EXCHANGE, publish_signed_event, start_consumer

SERVICE = "pagamento"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"


def handle_event(ch, method, properties, data):
    pedido_id = data.get("pedido_id")
    print(f" [x] Processando pagamento do pedido {pedido_id}...")

    aprovado = random.choice([True, False])
    if aprovado:
        print(f" [x] Pagamento APROVADO para pedido {pedido_id}")
        publish_signed_event(
            ch,
            ECOMMERCE_EXCHANGE,
            "pagamento.aprovado",
            {"pedido_id": pedido_id},
            SERVICE,
            PRIVATE_KEY,
        )
    else:
        print(f" [x] Pagamento RECUSADO para pedido {pedido_id}")
        publish_signed_event(
            ch,
            ECOMMERCE_EXCHANGE,
            "pagamento.recusado",
            {"pedido_id": pedido_id},
            SERVICE,
            PRIVATE_KEY,
        )


def main():
    bindings = [(ECOMMERCE_EXCHANGE, "pedido.estoque_ok")]
    start_consumer("Microsservico Pagamento", "pagamento_queue", bindings, KEYS_DIR, handle_event)


if __name__ == "__main__":
    main()
