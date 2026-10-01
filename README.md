# task26100222

FIFO 库存成本库（纯内存，Python，无外部依赖）。上层按 SKU 分别创建 `FifoInventory` 实例。

## 用法

```python
from fifo_inventory import FifoInventory

inv = FifoInventory("SKU-1")
bid = inv.inbound(3, 10)        # 入库 3 件共 10 分，返回批次号 B1
o1 = inv.outbound(2)            # FIFO 出库 2 件，返回出库单号 O1
inv.order_cost(o1)              # 7（出库单成本，历史不可变）
r1 = inv.return_goods(o1, 1)    # 按原出库单退货 1 件，返回退货单号 R1
inv.order_cost(r1)              # 4（退货单成本）
inv.stock()                     # (2, 7)：当前库存数量与金额（分）
```

- 数量为正整数件，金额为非负整数分；批次先后按成功入库顺序。
- 同批 Q 件共 C 分：前 k 件累计成本为 `round_half_up(k*C/Q)`，单件成本为相邻累计值之差，归属终身不变。
- 出库取最早批次中件序号最小的件，可跨批次；退货从原出库单最后出库且未退的件开始逆向退，退回件回到原批次原序号位置。

## 测试

```bash
python3 -m pytest test_fifo_inventory.py -q
```
