# 15 Spring 事务

## 1. 事务解决什么问题

一个业务动作可能包含多条 SQL。事务保证这些 SQL 作为一个整体提交：全部成功，或出现异常时整体回滚。

## 2. 项目中的代码

订单提交方法位于 [`OrderServiceImpl.java`](../../../sky-server/src/main/java/com/sky/service/impl/OrderServiceImpl.java)：

```java
@Override
@Transactional
public OrderSubmitVO submit(OrdersSubmitDTO dto) {
    // 查询地址和购物车
    // 插入 orders
    // 插入多个 order_detail
    // 清空购物车
}
```

菜品口味和套餐菜品关联的保存、更新操作也使用了 `@Transactional`。

## 3. 下单为什么必须有事务

```text
校验地址
  -> 读取购物车
  -> 插入订单主表
  -> 插入订单明细
  -> 清空购物车
```

如果插入明细失败而购物车已经清空，没有事务就会丢失用户购物数据。事务让数据库回到操作前的一致状态。

## 4. Spring 事务的关键条件

- 启动类启用了 `@EnableTransactionManagement`。
- 数据库引擎必须支持事务，MySQL 常用 InnoDB。
- 事务注解通常放在 Service 的公开方法上。
- 异常必须能够被事务拦截器感知；吞掉异常可能导致事务提交。
- 同一个类内部直接调用另一个事务方法时，可能绕过代理，这是常见边界问题。



