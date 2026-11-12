# 16 AOP 与自动填充审计字段

## 1. AOP 解决什么问题

AOP（面向切面编程）用于把多个方法都需要的横切逻辑集中起来，例如日志、权限、事务或审计字段填充，避免在每个业务方法中重复编写。

## 2. 项目中的自定义注解

Mapper 方法上标记：

```java
@AutoFill(value = OperationType.INSERT)
void insert(Employee employee);

@AutoFill(value = OperationType.UPDATE)
void update(Employee employee);
```

注解位置：[`AutoFill.java`](../../../sky-server/src/main/java/com/sky/annotation/AutoFill.java)。

## 3. 切面如何工作

代码位置：[`AutoFIllAspect.java`](../../../sky-server/src/main/java/com/sky/aspect/AutoFIllAspect.java)

```java
@Aspect
@Component
public class AutoFIllAspect {
    @Pointcut("execution(* com.sky.mapper.*.*(..)) &&"
            + " @annotation(com.sky.annotation.AutoFill)")
    public void autoFillPointCut() {}

    @Before("autoFillPointCut()")
    public void autoFill(JoinPoint joinPoint) { ... }
}
```

执行过程：

```text
调用带 @AutoFill 的 Mapper 方法
  -> AOP 代理先执行切面
  -> 读取 INSERT/UPDATE 类型
  -> 通过反射给 Entity 设置时间和用户 ID
  -> 执行真正的 Mapper 方法
```

## 4. 这里体现的知识

- `@Pointcut` 描述哪些方法会被拦截。
- `@Before` 表示目标方法执行前运行。
- `JoinPoint` 可以获得目标方法参数。
- 反射允许切面调用实体的 setter，但也会带来类型和方法名错误的运行时风险。
- `BaseContext.getCurrentId()` 提供当前操作人 ID。

## 5. 学习练习

新增 `DELETE` 操作类型时，判断是否真的需要自动填充；再尝试把反射调用改为更安全的统一审计接口，比较灵活性和类型安全的取舍。

