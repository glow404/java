# 06 DTO、Entity 与 VO

## 1. 为什么要拆成三种对象

同一份业务数据在不同边界上的需求不同：前端提交的数据、数据库保存的数据、页面展示的数据不应该强行使用同一个 Java 类。

项目中的三种对象：

```text
DTO       Data Transfer Object      接收请求
Entity    Persistence Object        对应数据库
VO        View Object               返回页面
```

## 2. 订单案例

[`OrdersSubmitDTO.java`](../../../sky-pojo/src/main/java/com/sky/dto/OrdersSubmitDTO.java) 包含地址、支付方式、备注、送达时间等下单输入；[`Orders.java`](../../../sky-pojo/src/main/java/com/sky/entity/Orders.java) 还包含订单状态、用户 ID、订单号、支付状态等数据库和业务字段；[`OrderVO.java`](../../../sky-pojo/src/main/java/com/sky/vo/OrderVO.java) 则在订单基础上增加订单菜品展示信息。

```text
请求 DTO
  -> Service 补充系统字段和业务字段
  -> Entity 写入数据库
  -> VO 聚合成页面需要的结果
```

## 3. 这样设计的好处

- 防止客户端直接修改 `createUser`、`status` 等内部字段。
- 返回时可以隐藏密码或内部字段。
- 一个 VO 可以组合多个 Entity，例如订单主表和订单明细。
- 数据库表结构变化不必直接暴露给前端。

## 4. 项目中的转换方式

Service 中常使用 Spring 的 `BeanUtils.copyProperties` 做同名字段复制，再手动补充不能直接复制的字段，例如订单号、当前用户 ID、默认状态和时间。

复制工具不能代替业务转换。凡是涉及权限、默认值、状态或金额计算的字段，都应明确写出规则。



