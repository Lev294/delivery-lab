import os

import grpc

from delivery.rpc import inventory_pb2 as pb
from delivery.rpc import inventory_pb2_grpc as rpc


class InventoryClient:
    def __init__(self, target=None, timeout=None):
        self.channel = grpc.insecure_channel(
            target or os.getenv("INVENTORY_TARGET", "inventory:50051"),
            options=(
                ("grpc.initial_reconnect_backoff_ms", 200),
                ("grpc.min_reconnect_backoff_ms", 200),
                ("grpc.max_reconnect_backoff_ms", 1000),
                ("grpc.dns_min_time_between_resolutions_ms", 1000),
            ),
        )
        self.stub = rpc.InventoryStub(self.channel)
        self.timeout = timeout or float(os.getenv("GRPC_TIMEOUT_SECONDS", "1"))

    def reserve(self, order_id, sku, quantity):
        return self.stub.Reserve(
            pb.ReserveRequest(order_id=str(order_id), sku=sku, quantity=quantity),
            timeout=self.timeout,
        ).outcome

    def stock(self, sku):
        reply = self.stub.Stock(pb.StockRequest(sku=sku), timeout=self.timeout)
        return {"sku": reply.sku, "available": reply.available}

    def close(self):
        self.channel.close()
