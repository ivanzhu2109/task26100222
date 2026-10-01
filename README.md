# FIFO 库存成本库

纯内存实现的单 SKU FIFO 库存成本台账，无任何外部依赖与服务。

## 用法

```python
from fifo_inventory import FifoInventory

inv = FifoInventory()          # 每个 SKU 一个实例
batch = inv.inbound(3, 10)     # 入库 3 件共 10 分，返回批次号
order = inv.outbound(2)        # FIFO 出库 2 件，返回出库单号
ret = inv.return_goods(order, 1)  # 按原出库单退货 1 件，返回退货单号

inv.stock_quantity()           # 当前库存数量
inv.stock_amount()             # 当前库存金额（分）
inv.order_cost(order)          # 出库/退货单的历史成本（分）
```

成本归属规则：同批 Q 件共 C 分时，前 k 件累计成本为
`round_half_up(k * C / Q)`，第 k 件成本为相邻累计值之差，归属终身不变。

## 测试

```bash
python3 -m unittest test_fifo_inventory -v
```
