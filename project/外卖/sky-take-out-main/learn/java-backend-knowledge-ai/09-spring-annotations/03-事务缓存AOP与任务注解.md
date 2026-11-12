# 事务、缓存、AOP 与任务注解

## 1. 事务注解

### 1.1 `@EnableTransactionManagement`

`@EnableTransactionManagement` 开启 Spring 的注解式事务管理能力，使 `@Transactional` 可以通过代理生效。

项目启动类：

```java
@SpringBootApplication
@EnableTransactionManagement
public class SkyApplication {
}
```

### 1.2 `@Transactional`

`@Transactional` 声明一个方法需要事务保护：方法正常结束时提交，抛出符合回滚规则的异常时回滚。

```java
@Transactional
public OrderSubmitVO submit(OrdersSubmitDTO dto) {
    // 写入 orders
    // 写入 order_detail
    // 任一步骤失败，事务整体回滚
}
```

项目的订单提交会同时操作订单和订单明细，因此使用事务。事务通常放在 Service 层，而不是 Controller 层。

注意：默认情况下，Spring 通过代理拦截外部调用；类内部直接调用同类的另一个 `@Transactional` 方法，可能绕过代理。

## 2. 缓存注解

### 2.1 `@EnableCaching`

`@EnableCaching` 开启 Spring Cache 注解处理能力：

```java
@SpringBootApplication
@EnableCaching
public class SkyApplication {
}
```

它负责开启缓存代理，但具体缓存后端和序列化方式由 `CacheManager` 决定。

### 2.2 `@Cacheable`

`@Cacheable` 表示先查缓存，缓存命中时直接返回；未命中才执行方法，并将返回结果写入缓存：

```java
@Cacheable(cacheNames = "setmealCache", key = "#categoryId")
public Result<List<Setmeal>> list(Long categoryId) {
    Setmeal setmeal = new Setmeal();
    setmeal.setCategoryId(categoryId);
    return Result.success(setmealService.list(setmeal));
}
```

`key = "#categoryId"` 使用 SpEL 表达式，表示用方法参数 `categoryId` 生成缓存 Key。

### 2.3 `@CacheEvict`

写操作可能使旧缓存失效，因此使用 `@CacheEvict`：

```java
@CacheEvict(cacheNames = "setmealCache", allEntries = true)
public Result update(@RequestBody SetmealDTO dto) {
    setmealService.update(dto);
    return Result.success();
}
```

常见属性：

| 属性 | 作用 |
| --- | --- |
| `cacheNames` | 指定缓存区域 |
| `key` | 指定要删除的 Key |
| `allEntries = true` | 删除该缓存区域的全部条目 |
| `beforeInvocation = true` | 方法执行前清理，而不是默认的执行成功后清理 |

当前项目的菜品缓存使用 `RedisTemplate` 手动读写，套餐缓存使用 Spring Cache 注解。两者是不同的调用方式。

## 3. AOP 注解

### 3.1 `@Aspect`

`@Aspect` 表示一个类是切面，用于集中编写日志、权限、审计等横切逻辑。

```java
@Aspect
@Component
public class LoggingAspect {
}
```

### 3.2 `@Pointcut`

`@Pointcut` 定义哪些方法会被切面匹配：

```java
@Pointcut("execution(* com.sky.service..*(..))")
public void serviceMethods() {
}
```

### 3.3 `@Before`、`@AfterReturning` 等通知

```java
@Before("serviceMethods()")
public void before() {
    // 目标 Service 方法执行前运行
}
```

项目中的 `AutoFIllAspect` 使用 AOP 在 Mapper 写操作前自动填充创建人、更新时间等审计字段。切面本身不是 Controller 的直接调用者，而是由代理在匹配方法执行时织入。

## 4. 定时与异步注解

### 4.1 `@EnableScheduling` 与 `@Scheduled`

`@EnableScheduling` 开启定时任务处理，`@Scheduled` 声明执行计划：

```java
@Configuration
@EnableScheduling
public class SchedulingConfiguration {
}

@Component
public class OrderTask {
    @Scheduled(cron = "0/5 * * * * ?")
    public void process() {
        // 每 5 秒执行一次
    }
}
```

项目中存在 `WebSocketTask`、`OrderTask` 等 `@Scheduled` 方法；阅读或运行项目时，还应确认应用是否通过 `@EnableScheduling` 或其他配置开启了调度。

### 4.2 `@EnableAsync` 与 `@Async`

```java
@Configuration
@EnableAsync
public class AsyncConfiguration {
}

@Async
public void sendNotification() {
    // 在线程池中异步执行
}
```

异步方法通常必须通过 Spring 代理对象被外部调用，同类内部直接调用可能不会异步。

## 5. 项目中的自定义注解与 AOP

项目定义了一个 `@AutoFill` 注解，用来标记需要自动填充创建时间、更新时间和操作人字段的 Mapper 方法：

```java
@AutoFill(value = OperationType.INSERT)
void insert(Employee employee);

@AutoFill(value = OperationType.UPDATE)
void update(Employee employee);
```

注解定义本身不是 Spring 注解，但它和 Spring AOP 一起工作：

```java
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface AutoFill {
    OperationType value();
}
```

- `@Target(ElementType.METHOD)`：限制 `@AutoFill` 只能标记方法。
- `@Retention(RetentionPolicy.RUNTIME)`：运行时仍保留注解，切面才能通过反射读取它。

对应切面：

```java
@Component
@Aspect
public class AutoFIllAspect {

    @Pointcut("execution(* com.sky.mapper.*.*(..)) && "
            + "@annotation(com.sky.annotation.AutoFill)")
    public void autoFillPointCut() {
    }

    @Before("autoFillPointCut()")
    public void autoFill(JoinPoint joinPoint) {
        // 读取 @AutoFill 的 INSERT/UPDATE 类型并填充公共字段
    }
}
```

执行链路：

```text
Service 调用带 @AutoFill 的 Mapper 方法
  -> Spring AOP 代理匹配 Pointcut
  -> @Before 通知读取注解参数
  -> 反射填充实体的审计字段
  -> 执行真正的 Mapper 方法
```

这里体现了协作关系：`@Component` 让切面进入 Spring 容器，`@Aspect` 声明切面，`@Pointcut` 选择目标方法，`@Before` 决定执行时机，而自定义 `@AutoFill` 提供业务语义。
