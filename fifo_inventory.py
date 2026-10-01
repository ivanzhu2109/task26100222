"""FIFO 库存成本库（内存实现）。

上层按 SKU 分别创建实例，每个实例独立维护一个 SKU 的
入库批次、库存、出库单与退货单。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class InsufficientStockError(Exception):
    """库存不足，无法完成出库。"""


class ReturnError(Exception):
    """退货单不存在或可退数量不足。"""


@dataclass(frozen=True)
class Piece:
    """单件货的成本归属：原批次 + 原入库件序号 + 归属成本（分）。"""

    batch_id: str
    piece_index: int
    cost: int


@dataclass
class InboundBatch:
    """一个入库批次。"""

    batch_id: str
    quantity: int
    total_cost: int
    piece_costs: list[int] = field(default_factory=list)
    in_stock: set[int] = field(default_factory=set)


@dataclass
class OutboundOrder:
    """一张出库单，记录按出库顺序取走的件。"""

    order_id: str
    pieces: list[Piece]
    total_cost: int
    returned: int = 0


@dataclass
class ReturnOrder:
    """一张退货单。"""

    return_id: str
    outbound_id: str
    pieces: list[Piece]
    total_cost: int


class FifoInventory:
    """单个 SKU 的 FIFO 库存成本台账。"""

    def __init__(self) -> None:
        self._batches: list[InboundBatch] = []
        self._batch_by_id: dict[str, InboundBatch] = {}
        self._outbounds: dict[str, OutboundOrder] = {}
        self._returns: dict[str, ReturnOrder] = {}
        self._batch_seq = 0
        self._outbound_seq = 0
        self._return_seq = 0

    def inbound(self, quantity: int, total_cost: int) -> str:
        """入库，返回批次标识。"""
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("入库数量必须为正整数件")
        if not isinstance(total_cost, int) or total_cost < 0:
            raise ValueError("入库金额必须为非负整数分")
        self._batch_seq += 1
        batch_id = f"B{self._batch_seq}"
        piece_costs = _allocate_costs(quantity, total_cost)
        batch = InboundBatch(
            batch_id=batch_id,
            quantity=quantity,
            total_cost=total_cost,
            piece_costs=piece_costs,
            in_stock=set(range(1, quantity + 1)),
        )
        self._batches.append(batch)
        self._batch_by_id[batch_id] = batch
        return batch_id

    def outbound(self, quantity: int) -> str:
        """出库，返回出库单标识。"""
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("出库数量必须为正整数件")
        if quantity > self.stock_quantity():
            raise InsufficientStockError(
                f"库存不足：需出库 {quantity} 件，当前库存 {self.stock_quantity()} 件"
            )
        pieces: list[Piece] = []
        remaining = quantity
        for batch in self._batches:
            if remaining == 0:
                break
            while remaining > 0 and batch.in_stock:
                index = min(batch.in_stock)
                batch.in_stock.discard(index)
                pieces.append(Piece(batch.batch_id, index, batch.piece_costs[index]))
                remaining -= 1
        self._outbound_seq += 1
        order_id = f"O{self._outbound_seq}"
        self._outbounds[order_id] = OutboundOrder(
            order_id=order_id,
            pieces=pieces,
            total_cost=sum(p.cost for p in pieces),
        )
        return order_id

    def return_goods(self, outbound_id: str, quantity: int) -> str:
        """按原出库单退货，返回退货单标识。"""
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("退货数量必须为正整数件")
        order = self._outbounds.get(outbound_id)
        if order is None:
            raise ReturnError(f"出库单不存在：{outbound_id}")
        available = len(order.pieces) - order.returned
        if quantity > available:
            raise ReturnError(
                f"可退数量不足：出库单 {outbound_id} 可退 {available} 件，申请退 {quantity} 件"
            )
        end = len(order.pieces) - order.returned
        pieces = order.pieces[end - quantity : end]
        for piece in pieces:
            self._batch_by_id[piece.batch_id].in_stock.add(piece.piece_index)
        order.returned += quantity
        self._return_seq += 1
        return_id = f"R{self._return_seq}"
        self._returns[return_id] = ReturnOrder(
            return_id=return_id,
            outbound_id=outbound_id,
            pieces=list(pieces),
            total_cost=sum(p.cost for p in pieces),
        )
        return return_id

    def stock_quantity(self) -> int:
        """当前库存数量。"""
        return sum(len(batch.in_stock) for batch in self._batches)

    def stock_amount(self) -> int:
        """当前库存金额（分）。"""
        return sum(
            batch.piece_costs[index]
            for batch in self._batches
            for index in batch.in_stock
        )

    def order_cost(self, order_id: str) -> int:
        """查询出库单或退货单的历史成本（分）。"""
        order = self._outbounds.get(order_id)
        if order is not None:
            return order.total_cost
        return_order = self._returns.get(order_id)
        if return_order is not None:
            return return_order.total_cost
        raise KeyError(f"单据不存在：{order_id}")


def _allocate_costs(quantity: int, total_cost: int) -> list[int]:
    """按四舍五入（半分向上）把总成本归属到每件。

    前 k 件累计成本 = round_half_up(k * total_cost / quantity)，
    第 k 件成本为相邻累计值之差。返回下标从 1 开始的列表。
    """

    def cumulative(k: int) -> int:
        return (2 * k * total_cost + quantity) // (2 * quantity)

    costs = [0] * (quantity + 1)
    for k in range(1, quantity + 1):
        costs[k] = cumulative(k) - cumulative(k - 1)
    return costs
