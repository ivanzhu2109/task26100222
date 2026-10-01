"""fifo_inventory 的自动化测试。

包含一个独立的按件参考模型，用于交叉验证库的每笔金额、
原批次归属、剩余可退数量与守恒不变式。
"""

from __future__ import annotations

import random
import unittest
from fractions import Fraction

from fifo_inventory import (
    FifoInventory,
    InsufficientStockError,
    ReturnError,
)


def reference_piece_costs(quantity: int, total_cost: int) -> list[int]:
    """参考实现：用 Fraction 精确计算四舍五入（半分向上）。"""

    def cumulative(k: int) -> int:
        value = Fraction(k * total_cost, quantity)
        return int(value + Fraction(1, 2))  # floor(x + 1/2)，半分向上

    return [cumulative(k) - cumulative(k - 1) for k in range(1, quantity + 1)]


class ReferenceModel:
    """独立的按件独立核算模型，与库的实现路径完全不同。"""

    def __init__(self) -> None:
        self.stock: list[tuple[int, int, int]] = []  # (批次号, 件序号, 成本)
        self.orders: dict[str, list[tuple[int, int, int]]] = {}
        self.returned: dict[str, int] = {}
        self.batch_seq = 0
        self.order_seq = 0
        self.return_seq = 0
        self.order_costs: dict[str, int] = {}

    def inbound(self, quantity: int, total_cost: int) -> None:
        self.batch_seq += 1
        for idx, cost in enumerate(reference_piece_costs(quantity, total_cost), start=1):
            self.stock.append((self.batch_seq, idx, cost))

    def outbound(self, quantity: int) -> str:
        assert quantity <= len(self.stock)
        taken = self.stock[:quantity]
        del self.stock[:quantity]
        self.order_seq += 1
        order_id = f"O{self.order_seq}"
        self.orders[order_id] = list(taken)
        self.returned[order_id] = 0
        self.order_costs[order_id] = sum(p[2] for p in taken)
        return order_id

    def return_goods(self, outbound_id: str, quantity: int) -> str:
        pieces = self.orders[outbound_id]
        end = len(pieces) - self.returned[outbound_id]
        assert quantity <= end
        back = pieces[end - quantity : end]
        self.returned[outbound_id] += quantity
        self.stock.extend(back)
        self.stock.sort(key=lambda p: (p[0], p[1]))
        self.return_seq += 1
        return_id = f"R{self.return_seq}"
        self.order_costs[return_id] = sum(p[2] for p in back)
        return return_id

    def returnable(self, outbound_id: str) -> int:
        return len(self.orders[outbound_id]) - self.returned[outbound_id]


def library_stock_pieces(inv: FifoInventory) -> list[tuple[int, int, int]]:
    """读出库内每件的 (批次号, 件序号, 成本) 并按 FIFO 顺序排列。"""
    pieces = []
    for batch in inv._batches:
        batch_no = int(batch.batch_id[1:])
        for index in sorted(batch.in_stock):
            pieces.append((batch_no, index, batch.piece_costs[index]))
    return pieces


def library_returnable(inv: FifoInventory, outbound_id: str) -> int:
    order = inv._outbounds[outbound_id]
    return len(order.pieces) - order.returned


class RoundingTest(unittest.TestCase):
    def test_half_up_rounding(self):
        self.assertEqual(reference_piece_costs(3, 10), [3, 4, 3])
        self.assertEqual(reference_piece_costs(2, 1), [1, 0])  # 0.5 向上归第 1 件
        self.assertEqual(reference_piece_costs(4, 2), [1, 0, 1, 0])
        self.assertEqual(reference_piece_costs(1, 0), [0])
        self.assertEqual(reference_piece_costs(5, 0), [0, 0, 0, 0, 0])

    def test_piece_costs_sum_to_total(self):
        for quantity, total in [(3, 10), (7, 100), (10, 3), (6, 1), (100, 9999)]:
            costs = reference_piece_costs(quantity, total)
            self.assertEqual(sum(costs), total)


class RequiredScenarioTest(unittest.TestCase):
    """题目指定场景：3 件 10 分，出 2 退 1 再出 1。"""

    def test_scenario(self):
        inv = FifoInventory()
        inv.inbound(3, 10)

        out1 = inv.outbound(2)
        self.assertEqual(inv.order_cost(out1), 7)
        self.assertEqual(inv.stock_quantity(), 1)
        self.assertEqual(inv.stock_amount(), 3)

        ret1 = inv.return_goods(out1, 1)
        self.assertEqual(inv.order_cost(ret1), 4)
        self.assertEqual(inv.stock_quantity(), 2)
        self.assertEqual(inv.stock_amount(), 7)

        out2 = inv.outbound(1)
        self.assertEqual(inv.order_cost(out2), 4)
        self.assertEqual(inv.stock_quantity(), 1)
        self.assertEqual(inv.stock_amount(), 3)

        # 历史单据成本不受后续操作影响
        self.assertEqual(inv.order_cost(out1), 7)
        self.assertEqual(inv.order_cost(ret1), 4)


class CrossBatchTest(unittest.TestCase):
    """跨批次出库、部分退货、退回后再售再退、穿插新入库。"""

    def test_cross_batch_and_resell(self):
        inv = FifoInventory()
        ref = ReferenceModel()

        inv.inbound(2, 10)   # B1: [5, 5]
        ref.inbound(2, 10)
        inv.inbound(3, 10)   # B2: [3, 4, 3]
        ref.inbound(3, 10)

        out1 = inv.outbound(4)  # 跨 B1、B2：5+5+3+4 = 17
        ref_out1 = ref.outbound(4)
        self.assertEqual(out1, ref_out1)
        self.assertEqual(inv.order_cost(out1), 17)
        self.assertEqual(ref.order_costs[out1], 17)

        # 第一次部分退货：退 1 件，应退 B2 第 2 件（4 分）
        ret1 = inv.return_goods(out1, 1)
        ref_ret1 = ref.return_goods(out1, 1)
        self.assertEqual(ret1, ref_ret1)
        self.assertEqual(inv.order_cost(ret1), 4)

        # 退回的货恢复原 FIFO 位置：再次出库 1 件仍取到 B2 第 2 件
        out2 = inv.outbound(1)
        ref_out2 = ref.outbound(1)
        self.assertEqual(inv.order_cost(out2), 4)
        self.assertEqual(ref.order_costs[out2], 4)

        # 穿插新批次入库
        inv.inbound(2, 8)    # B3: [4, 4]
        ref.inbound(2, 8)

        # 已退回再出库的货只能通过新单 out2 再退
        ret2 = inv.return_goods(out2, 1)
        ref_ret2 = ref.return_goods(out2, 1)
        self.assertEqual(inv.order_cost(ret2), 4)
        self.assertEqual(ref.order_costs[ret2], 4)

        # 旧单 out1 的剩余可退数量不因再售而恢复
        self.assertEqual(library_returnable(inv, out1), ref.returnable(out1))
        self.assertEqual(library_returnable(inv, out1), 3)
        with self.assertRaises(ReturnError):
            inv.return_goods(out1, 4)

        # 多次部分退货：把 out1 剩余 3 件分两次退完
        ret3 = inv.return_goods(out1, 2)   # B2#3(3) + B2#1(3) = 6
        ref_ret3 = ref.return_goods(out1, 2)
        self.assertEqual(inv.order_cost(ret3), ref.order_costs[ref_ret3])
        ret4 = inv.return_goods(out1, 1)   # B1#2(5)
        ref_ret4 = ref.return_goods(out1, 1)
        self.assertEqual(inv.order_cost(ret4), ref.order_costs[ref_ret4])
        self.assertEqual(library_returnable(inv, out1), 0)

        # 库存件级归属与参考模型完全一致
        self.assertEqual(library_stock_pieces(inv), ref.stock)
        self.assertEqual(inv.stock_quantity(), len(ref.stock))
        self.assertEqual(inv.stock_amount(), sum(p[2] for p in ref.stock))

        # 历史单据成本不变
        self.assertEqual(inv.order_cost(out1), 17)
        self.assertEqual(inv.order_cost(ret1), 4)


class ConservationTest(unittest.TestCase):
    """随机操作下与参考模型逐步对账，并验证数量与金额守恒。"""

    def test_randomized_against_reference(self):
        for seed in range(20):
            with self.subTest(seed=seed):
                self._run_random(seed)

    def _run_random(self, seed: int) -> None:
        rng = random.Random(seed)
        inv = FifoInventory()
        ref = ReferenceModel()
        open_orders: list[str] = []
        total_in_qty = total_in_amt = 0
        total_out_qty = total_out_amt = 0
        total_ret_qty = total_ret_amt = 0

        def check_conservation() -> None:
            self.assertEqual(
                total_in_amt, inv.stock_amount() + total_out_amt - total_ret_amt
            )
            self.assertEqual(
                total_in_qty, inv.stock_quantity() + total_out_qty - total_ret_qty
            )
            self.assertEqual(library_stock_pieces(inv), ref.stock)
            self.assertEqual(inv.stock_amount(), sum(p[2] for p in ref.stock))

        for _ in range(300):
            action = rng.choice(["in", "in", "out", "out", "ret"])
            if action == "in":
                qty = rng.randint(1, 6)
                cost = rng.randint(0, 100)
                inv.inbound(qty, cost)
                ref.inbound(qty, cost)
                total_in_qty += qty
                total_in_amt += cost
            elif action == "out":
                stock = inv.stock_quantity()
                if stock == 0:
                    continue
                qty = rng.randint(1, min(stock, 8))
                order_id = inv.outbound(qty)
                ref_id = ref.outbound(qty)
                self.assertEqual(order_id, ref_id)
                self.assertEqual(inv.order_cost(order_id), ref.order_costs[ref_id])
                open_orders.append(order_id)
                total_out_qty += qty
                total_out_amt += inv.order_cost(order_id)
            else:
                candidates = [
                    oid for oid in open_orders if ref.returnable(oid) > 0
                ]
                if not candidates:
                    continue
                order_id = rng.choice(candidates)
                qty = rng.randint(1, ref.returnable(order_id))
                ret_id = inv.return_goods(order_id, qty)
                ref_ret = ref.return_goods(order_id, qty)
                self.assertEqual(ret_id, ref_ret)
                self.assertEqual(inv.order_cost(ret_id), ref.order_costs[ref_ret])
                self.assertEqual(
                    library_returnable(inv, order_id), ref.returnable(order_id)
                )
                total_ret_qty += qty
                total_ret_amt += inv.order_cost(ret_id)
            check_conservation()


class ErrorHandlingTest(unittest.TestCase):
    def test_invalid_inbound(self):
        inv = FifoInventory()
        with self.assertRaises(ValueError):
            inv.inbound(0, 10)
        with self.assertRaises(ValueError):
            inv.inbound(-1, 10)
        with self.assertRaises(ValueError):
            inv.inbound(1, -1)

    def test_insufficient_stock(self):
        inv = FifoInventory()
        inv.inbound(2, 10)
        with self.assertRaises(InsufficientStockError):
            inv.outbound(3)
        self.assertEqual(inv.stock_quantity(), 2)

    def test_invalid_outbound(self):
        inv = FifoInventory()
        with self.assertRaises(ValueError):
            inv.outbound(0)

    def test_return_errors(self):
        inv = FifoInventory()
        with self.assertRaises(ReturnError):
            inv.return_goods("O999", 1)
        inv.inbound(1, 5)
        order = inv.outbound(1)
        with self.assertRaises(ReturnError):
            inv.return_goods(order, 2)
        with self.assertRaises(ValueError):
            inv.return_goods(order, 0)

    def test_unknown_order_cost(self):
        inv = FifoInventory()
        with self.assertRaises(KeyError):
            inv.order_cost("O999")


if __name__ == "__main__":
    unittest.main()
