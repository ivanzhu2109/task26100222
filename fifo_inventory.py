"""FIFO 库存成本库（纯内存，按 SKU 由上层分别创建实例）。"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field


def _cum_cost(k: int, total_cost: int, qty: int) -> int:
    """同批 qty 件共 total_cost 分时，前 k 件的累计成本（半分向上取整）。"""
    return (2 * k * total_cost + qty) // (2 * qty)


def _check_qty(qty: int) -> None:
    if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        raise ValueError("数量必须为正整数件")


def _check_cost(total_cost: int) -> None:
    if isinstance(total_cost, bool) or not isinstance(total_cost, int) or total_cost < 0:
        raise ValueError("金额必须为非负整数分")


@dataclass
class _Piece:
    """一件货的归属信息：原批次、原入库件序号、归属成本（分）。"""

    batch_id: str
    serial: int
    cost: int


@dataclass
class _Batch:
    """一个入库批次，持有当前在库的件（按件序号升序）。"""

    batch_id: str
    qty: int
    total_cost: int
    pieces: list[_Piece] = field(default_factory=list)


@dataclass
class _OutboundOrder:
    """一张出库单：按出库顺序记录所取的件及退回标记。"""

    order_id: str
    pieces: list[_Piece]
    returned: list[bool]
    cost: int


@dataclass
class _ReturnOrder:
    """一张退货单：记录退回的件与金额（历史不可变）。"""

    return_id: str
    outbound_id: str
    pieces: list[_Piece]
    cost: int


class FifoInventory:
    """单 SKU 的 FIFO 库存成本账本。""" 

    def __init__(self, sku: str) -> None:
        self.sku = sku
        self._batches: list[_Batch] = []
        self._batch_by_id: dict[str, _Batch] = {}
        self._outbounds: dict[str, _OutboundOrder] = {}
        self._returns: dict[str, _ReturnOrder] = {}
        self._batch_seq = 0
        self._outbound_seq = 0
        self._return_seq = 0

    def inbound(self, qty: int, total_cost: int) -> str:
        """入库，返回批次标识。"""
        _check_qty(qty)
        _check_cost(total_cost)
        self._batch_seq += 1
        batch_id = f"B{self._batch_seq}"
        pieces = [
            _Piece(batch_id, k, _cum_cost(k, total_cost, qty) - _cum_cost(k - 1, total_cost, qty))
            for k in range(1, qty + 1)
        ]
        batch = _Batch(batch_id, qty, total_cost, pieces)
        self._batches.append(batch)
        self._batch_by_id[batch_id] = batch
        return batch_id

    def outbound(self, qty: int) -> str:
        """出库，返回出库单标识。"""
        _check_qty(qty)
        if sum(len(b.pieces) for b in self._batches) < qty:
            raise ValueError("库存不足")
        taken: list[_Piece] = []
        remaining = qty
        for batch in self._batches:
            while remaining > 0 and batch.pieces:
                taken.append(batch.pieces.pop(0))
                remaining -= 1
            if remaining == 0:
                break
        self._outbound_seq += 1
        order_id = f"O{self._outbound_seq}"
        self._outbounds[order_id] = _OutboundOrder(
            order_id, taken, [False] * len(taken), sum(p.cost for p in taken)
        )
        return order_id

    def return_goods(self, outbound_id: str, qty: int) -> str:
        """按原出库单退货，返回退货单标识。"""
        _check_qty(qty)
        order = self._outbounds.get(outbound_id)
        if order is None:
            raise ValueError(f"出库单不存在: {outbound_id}")
        indexes = [
            i for i in range(len(order.pieces) - 1, -1, -1) if not order.returned[i]
        ]
        if len(indexes) < qty:
            raise ValueError("可退数量不足")
        chosen = indexes[:qty]
        pieces: list[_Piece] = []
        for i in chosen:
            order.returned[i] = True
            piece = order.pieces[i]
            pieces.append(piece)
            bisect.insort(
                self._batch_by_id[piece.batch_id].pieces,
                piece,
                key=lambda p: p.serial,
            )
        self._return_seq += 1
        return_id = f"R{self._return_seq}"
        self._returns[return_id] = _ReturnOrder(
            return_id, outbound_id, pieces, sum(p.cost for p in pieces)
        )
        return return_id

    def stock(self) -> tuple[int, int]:
        """查询当前库存（数量, 金额分）。"""
        qty = sum(len(b.pieces) for b in self._batches)
        amount = sum(p.cost for b in self._batches for p in b.pieces)
        return qty, amount

    def order_cost(self, order_id: str) -> int:
        """查询指定出库单或退货单的成本（分）。"""
        if order_id in self._outbounds:
            return self._outbounds[order_id].cost
        if order_id in self._returns:
            return self._returns[order_id].cost
        raise ValueError(f"单据不存在: {order_id}")
