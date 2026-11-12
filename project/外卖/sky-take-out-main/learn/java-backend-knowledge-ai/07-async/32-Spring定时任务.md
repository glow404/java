# 32 Spring 定时任务

## 1. 定时任务的用途

有些工作不需要用户实时触发，例如取消超时未支付订单、自动完成配送超时订单、定期清理数据。项目把这类工作放在 `task` 包中。

## 2. 项目中的代码

代码位置：[`OrderTask.java`](../../../sky-server/src/main/java/com/sky/task/OrderTask.java)

```java
@Component
public class OrderTask {
    @Scheduled(cron = "1/5 * * * * ?")
    public void processTimeoutOrders() { ... }

    @Scheduled(cron = "0/5 * * * * ?")
    public void processDeliveryOrders() { ... }
}
```

任务会查询特定状态和时间之前的订单，然后更新订单状态、取消原因或完成时间。

## 3. 任务执行链路

```text
调度器到达 cron 时间
  -> 执行任务方法
  -> 查询满足条件的订单
  -> 逐条更新状态
  -> 记录日志
```

## 4. 重要配置点

标准 Spring 使用 `@EnableScheduling` 开启 `@Scheduled`。当前项目源码中能看到 `@Scheduled`，但没有找到 `@EnableScheduling`，因此运行时应验证任务是否真正被调度。

`cron = "1/5 * * * * ?"` 是示例用的每 5 秒执行一次；生产环境应使用真实业务周期，避免频繁扫描数据库。

## 5. 生产环境注意点

- 任务必须幂等，重复执行不能重复扣款或重复发货。
- 查询和更新最好使用条件更新，避免并发任务覆盖新状态。
- 多实例部署时每个实例都可能执行同一任务，需要分布式锁或任务调度平台。
- 任务失败应记录、告警并支持重试。
- 大批量处理应分页或分批，避免一次加载过多数据。

## 6. 学习练习

为超时订单任务增加状态条件更新和处理数量日志，再模拟任务执行到一半抛异常，观察下次执行是否可以安全恢复。

