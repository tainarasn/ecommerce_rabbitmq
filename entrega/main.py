import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.rabbit import ECOMMERCE_EXCHANGE, publish_signed_event, start_consumer

SERVICE = "entrega"
KEYS_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_KEY = KEYS_DIR / f"{SERVICE}_private.pem"


def handle_event(ch, method, properties, data):
    pedido_id = data.get("pedido_id")
    print(f" [x] Emitindo nota fiscal para pedido {pedido_id}...")
    print(f" [x] Preparando entrega do pedido {pedido_id}...")

    publish_signed_event(
        ch,
        ECOMMERCE_EXCHANGE,
        "pedido.enviado",
        {"pedido_id": pedido_id, "nota_fiscal": f"NF-{pedido_id:06d}"},
        SERVICE,
        PRIVATE_KEY,
    )
    print(f" [x] Pedido {pedido_id} enviado!")


def main():
    bindings = [(ECOMMERCE_EXCHANGE, "pagamento.aprovado")]
    start_consumer("Microsservico Entrega", "entrega_queue", bindings, KEYS_DIR, handle_event)


if __name__ == "__main__":
    main()
