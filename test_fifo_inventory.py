"""fifo_inventory 的自动化测试。"""

from __future__ import annotations

import random

import pytest

from fifo_inventory import FifoInventory


def cum(k: int, total: int, qty: int) -> int:
    """独立重算的累计成本：k*total/qty 半分向上取整。"""
    return (2 * k * total + qty) // (2 * qty)


class PieceModel:
    """按件独立核算的参考模型：件 = (批次号, 件序号, 成本)。"""

    def __init__(self) -> None:
        self.pieces: list[tuple[int, int, int]] = []  # 按 (批次号, 件序号) 升序
        self.orders: dict[str, list[list]] = {}  # 出库单号 -> [[件, 已退], ...]
        self.returns: dict[str, int] = {}  # 退货单号 -> 金额
        self.batch_seq = 0
        self.outbound_seq = 0
        self.return_seq = 0

    def inbound(self, qty: int, cost: int) -> str:
        self.batch_seq += 1
        b = self.batch_seq
        for k in range(1, qty + 1):
            self.pieces.append((b, k, cum(k, cost, qty) - cum(k - 1, cost, qty)))
        return f"B{b}"

    def outbound(self, qty: int) -> str:
        taken = self.pieces[:qty]
        del self.pieces[:qty]
        self.outbound_seq += 1
        oid = f"O{self.outbound_seq}"
        self.orders[oid] = [[p, False] for p in taken]
        return oid

    def returnable(self, oid: str) -> int:
        return sum(1 for _, done in self.orders[oid] if not done)

    def return_goods(self, oid: str, qty: int) -> str:
        entries = self.orders[oid]
        chosen = [e for e in reversed(entries) if not e[1]][:qty]
        assert len(chosen) == qty
        amount = 0
        for entry in chosen:
            entry[1] = True
            amount += entry[0][2]
            piece = entry[0]
            pos = 0
            while pos < len(self.pieces) and self.pieces[pos][:2] < piece[:2]:
                pos += 1
            self.pieces.insert(pos, piece)
        self.return_seq += 1
        rid = f"R{self.return_seq}"
        self.returns[rid] = amount
        return rid

    def order_cost(self, oid: str) -> int:
        if oid in self.orders:
            return sum(p[2] for p, _ in self.orders[oid])
        return self.returns[oid]

    def stock(self) -> tuple[int, int]:
        return len(self.pieces), sum(p[2] for p in self.pieces)


def lib_pieces(inv: FifoInventory) -> list[tuple[int, int, int]]:
    """把库内部状态摊平成与参考模型可比的件列表。"""
    return [
        (int(b.batch_id[1:]), p.serial, p.cost)
        for b in inv._batches
        for p in b.pieces
    ]


class Ledger:
    """守恒校验：累计入库 == 库存 + 累计出库 - 累计退货。"""

    def __init__(self) -> None:
        self.in_qty = 0
        self.in_amt = 0
        self.out_qty = 0
        self.out_amt = 0
        self.ret_qty = 0
        self.ret_amt = 0

    def check(self, inv: FifoInventory) -> None:
        qty, amt = inv.stock()
        assert self.in_qty == qty + self.out_qty - self.ret_qty
        assert self.in_amt == amt + self.out_amt - self.ret_amt


def test_rounding_example_3_pieces_10_cents() -> None:
    inv = FifoInventory("SKU-A")
    led = Ledger()
    inv.inbound(3, 10)
    led.in_qty, led.in_amt = 3, 10
    led.check(inv)

    o1 = inv.outbound(2)
    assert inv.order_cost(o1) == 7  # 3 + 4
    led.out_qty, led.out_amt = 2, 7
    led.check(inv)

    r1 = inv.return_goods(o1, 1)
    assert inv.order_cost(r1) == 4  # 退最后出库的第 2 件
    led.ret_qty, led.ret_amt = 1, 4
    led.check(inv)

    o2 = inv.outbound(1)
    assert inv.order_cost(o2) == 4  # 退回的件回到原 FIFO 位置，先出
    led.out_qty, led.out_amt = 3, 11
    led.check(inv)
    assert inv.stock() == (1, 3)


def test_piece_cost_rounding_half_up_and_zero() -> None:
    inv = FifoInventory("SKU-B")
    inv.inbound(2, 1)  # 0.5 向上 -> 1, 0
    o = inv.outbound(1)
    assert inv.order_cost(o) == 1
    o = inv.outbound(1)
    assert inv.order_cost(o) == 0  # 单件成本可以为零
    inv.inbound(3, 2)  # 累计 1, 1, 2 -> 件成本 1, 0, 1
    o = inv.outbound(3)
    assert inv.order_cost(o) == 2
    assert inv.stock() == (0, 0)


def test_cross_batch_partial_returns_resell_and_interleaved_inbound() -> None:
    inv = FifoInventory("SKU-C")
    model = PieceModel()
    led = Ledger()

    def do_inbound(qty: int, cost: int) -> None:
        bid = inv.inbound(qty, cost)
        assert bid == model.inbound(qty, cost)
        led.in_qty += qty
        led.in_amt += cost
        led.check(inv)

    def do_outbound(qty: int) -> str:
        oid = inv.outbound(qty)
        assert oid == model.outbound(qty)
        assert inv.order_cost(oid) == model.order_cost(oid)
        led.out_qty += qty
        led.out_amt += inv.order_cost(oid)
        led.check(inv)
        assert lib_pieces(inv) == model.pieces
        return oid

    def do_return(oid: str, qty: int) -> str:
        rid = inv.return_goods(oid, qty)
        assert rid == model.return_goods(oid, qty)
        assert inv.order_cost(rid) == model.order_cost(rid)
        led.ret_qty += qty
        led.ret_amt += inv.order_cost(rid)
        led.check(inv)
        assert lib_pieces(inv) == model.pieces
        return rid

    do_inbound(2, 5)   # B1: 件成本 3, 2（累计 3, 5）
    do_inbound(3, 10)  # B2: 件成本 3, 4, 3
    o1 = do_outbound(4)  # 跨批次：B1 两件 + B2 前两件 = 3+2+3+4 = 12
    assert inv.order_cost(o1) == 12
    assert inv.stock() == (1, 3)

    r1 = do_return(o1, 1)  # 退最后出的 B2#2 = 4
    assert inv.order_cost(r1) == 4
    do_inbound(4, 7)  # B3 插入在退货之后，退货件仍排回原位
    r2 = do_return(o1, 2)  # 再退 B2#1=3, B1#2=2，共 5
    assert inv.order_cost(r2) == 5
    assert inv.order_cost(o1) == 12  # 原出库单历史金额不变

    o2 = do_outbound(3)  # B1#2=2, B2#1=3, B2#2=4 -> 9（退回件优先于 B3）
    assert inv.order_cost(o2) == 9
    r3 = do_return(o2, 2)  # 退 B2#2=4, B2#1=3 -> 7
    assert inv.order_cost(r3) == 7

    # 旧单 O1 只剩 B1#1 可退；新单 O2 只剩 B1#2 可退
    r4 = do_return(o1, 1)
    assert inv.order_cost(r4) == 3
    with pytest.raises(ValueError):
        inv.return_goods(o1, 1)  # O1 已全部退回，不会重新获得可退额度
    r5 = do_return(o2, 1)
    assert inv.order_cost(r5) == 2
    with pytest.raises(ValueError):
        inv.return_goods(o2, 1)

    assert inv.stock() == model.stock()
    assert lib_pieces(inv) == model.pieces


def test_randomized_against_piece_model() -> None:
    rng = random.Random(20261002)
    inv = FifoInventory("SKU-R")
    model = PieceModel()
    led = Ledger()
    history: list[tuple[str, str, int]] = []  # (kind, id, qty)

    for _ in range(400):
        stock_qty = model.stock()[0]
        returnable = [oid for oid in model.orders if model.returnable(oid) > 0]
        choices = ["inbound"]
        if stock_qty > 0:
            choices += ["outbound"] * 3
        if returnable:
            choices += ["return"] * 3
        op = rng.choice(choices)

        if op == "inbound":
            qty = rng.randint(1, 6)
            cost = rng.randint(0, 60)
            assert inv.inbound(qty, cost) == model.inbound(qty, cost)
            led.in_qty += qty
            led.in_amt += cost
        elif op == "outbound":
            qty = rng.randint(1, min(stock_qty, 5))
            oid = inv.outbound(qty)
            assert oid == model.outbound(qty)
            led.out_qty += qty
            led.out_amt += inv.order_cost(oid)
            history.append(("O", oid, qty))
        else:
            oid = rng.choice(returnable)
            qty = rng.randint(1, model.returnable(oid))
            rid = inv.return_goods(oid, qty)
            assert rid == model.return_goods(oid, qty)
            led.ret_qty += qty
            led.ret_amt += inv.order_cost(rid)
            history.append(("R", rid, qty))

        # 每一步：件级归属一致、库存一致、历史单据金额不变、守恒成立
        assert lib_pieces(inv) == model.pieces
        assert inv.stock() == model.stock()
        for kind, doc_id, _ in history:
            assert inv.order_cost(doc_id) == model.order_cost(doc_id)
        led.check(inv)


def test_validation_errors() -> None:
    inv = FifoInventory("SKU-E")
    with pytest.raises(ValueError):
        inv.inbound(0, 10)
    with pytest.raises(ValueError):
        inv.inbound(1, -1)
    with pytest.raises(ValueError):
        inv.outbound(1)  # 库存不足
    inv.inbound(1, 5)
    with pytest.raises(ValueError):
        inv.outbound(2)
    with pytest.raises(ValueError):
        inv.return_goods("O999", 1)
    with pytest.raises(ValueError):
        inv.order_cost("O999")
    o1 = inv.outbound(1)
    with pytest.raises(ValueError):
        inv.return_goods(o1, 2)  # 超过可退数量
    inv.return_goods(o1, 1)
    with pytest.raises(ValueError):
        inv.return_goods(o1, 1)  # 已退完
