# 多仓订单履约台

Python 标准库和 SQLite 实现。每条命令可独立启动，首次使用自动创建数据库。数量为正整数，重复 SKU 明细合并。成功输出 JSON；业务拒绝返回非零并向标准错误输出 JSON。

在本目录运行：

```sh
python -m dispatchdesk --db shop.db stock Shanghai tea 10
python -m dispatchdesk --db shop.db order O1 Shanghai tea=3 --policy near
python -m dispatchdesk --db shop.db ship O1 Shanghai
python -m dispatchdesk --db shop.db return O1 Shanghai tea 1 --quality sellable
python -m dispatchdesk --db shop.db status O1
python -m dispatchdesk --db shop.db report
python -m dispatchdesk --db shop.db cancel O2
python -m unittest discover -s tests -v
```

`near` 优先订单区域同名仓库，其余按仓库 ID 排序；`bulk` 按每个 SKU 可用库存降序、仓库 ID 升序分配。全部明细满足时才预留。补货、取消和可再售退货按提交顺序重试等待订单。发货以订单和仓库为单位；退货分别累计可再售和损坏数量，不改变发货状态。取消后的订单号仍被保留。
