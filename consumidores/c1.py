import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.events import parse_event, validate_event
from shared.rabbit import PROMOCOES_EXCHANGE, connect, declare_promocoes_exchange, declare_queue, bind_queue

CONSUMER = "C1"
KEYS_DIR = Path(__file__).resolve().parent.parent / "promocoes" / "keys"


def handle_promocao(body: bytes) -> None:
    envelope = parse_event(body)
    if envelope is None:
        print(f" [{CONSUMER}] Promocao invalida (JSON malformado).")
        return

    data = validate_event(envelope, KEYS_DIR)
    if data is None:
        print(f" [{CONSUMER}] Promocao com assinatura invalida. Descartando.")
        return

    print(
        f" [{CONSUMER}] Promocao recebida - Categoria {data['categoria']}: "
        f"{data['produto_nome']} por R$ {data['preco_promocional']:.2f} "
        f"({data['desconto_percentual']}% OFF)"
    )


def main():
    connection, channel = connect()
    declare_promocoes_exchange(channel)
    queue_name = "promocoes_c1_queue"
    declare_queue(channel, queue_name)
    bind_queue(channel, queue_name, PROMOCOES_EXCHANGE, "promocao.categoria.A")
    bind_queue(channel, queue_name, PROMOCOES_EXCHANGE, "promocao.categoria.B")

    def on_message(ch, method, properties, body):
        handle_promocao(body)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=queue_name, on_message_callback=on_message)
    print(f" [*] {CONSUMER} aguardando promocoes das categorias A e B...")
    channel.start_consuming()


if __name__ == "__main__":
    main()
