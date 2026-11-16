结合你本地那份苍穹外卖代码，AOP 这块其实就集中在一个功能上——**公共字段自动填充（AutoFillAspect）**。它把“枚举 + 自定义注解 + 切面 + 反射 + ThreadLocal”串成了一条完整链路，是理解 Spring AOP 全流程最好的标本。下面我按“为什么用 → 怎么写 → 运行时怎么跑 → 哪里容易踩坑”的顺序，把整条链路拆给你。

------

## 一、先看业务问题：AOP 到底解决了什么

苍穹外卖的员工表、分类表、菜品表、套餐表都有四个公共字段：`create_time`、`create_user`、`update_time`、`update_user`。插入数据时四个都要赋值，修改数据时只需要赋 `update_time` 和 `update_user`。

如果不用 AOP，每个 Service 方法里都要写：

```java
category.setCreateTime(LocalDateTime.now());
category.setCreateUser(currentUserId);
category.setUpdateTime(LocalDateTime.now());
category.setUpdateUser(currentUserId);
```

几十张表、上百个方法重复贴这段代码，一旦规则变化（比如时间精度、创建人改存用户名）就要全局改。这类“横跨多个模块、和业务无关”的逻辑叫**横切关注点**，AOP 的职责就是把它抽出来统一织入，业务代码保持干净。

项目里的技术选型是：自定义注解 `@AutoFill` 标识需要填充的方法，切面类 `AutoFillAspect` 统一拦截并用反射赋值，Mapper 方法上加注解，涉及枚举、注解、AOP、反射四个技术点。

------

## 二、全流程梳理（八个环节）

### 环节 0：环境与开关

Spring Boot 项目引入 `spring-boot-starter-aop`（或 `spring-boot-starter-web` 传递依赖）后，`@EnableAspectJAutoProxy` 会被自动装配，无需手写配置类；纯 Spring 项目才需要显式加 `@EnableAspectJAutoProxy` 开启 AOP 自动代理。苍穹外卖是 Spring Boot，所以你在启动类上找不到这个注解，属于正常现象。

### 环节 1：定义枚举，描述“操作类型”

`sky-common` 模块 `com.sky.enumeration` 下：

```java
public enum OperationType {
    UPDATE,   // 更新操作
    INSERT    // 插入操作
}
```

只定义 INSERT / UPDATE，不定义 DELETE 和 SELECT，因为查询和删除根本不需要填充公共字段。用枚举而不是字符串，是为了编译期约束和可读性。

### 环节 2：自定义注解 `@AutoFill`（打标记）

`sky-server` 模块 `com.sky.annotation` 下：

```java
@Target(ElementType.METHOD)          // 只能贴在方法上
@Retention(RetentionPolicy.RUNTIME)  // 保留到运行时
public @interface AutoFill {
    OperationType value();           // 数据库操作类型
}
```

这里有个**关键点**，也是面试常问的：`@Retention` 必须是 `RUNTIME`。因为切面是在运行时通过反射读取方法上的注解信息的，如果只保留到 SOURCE 或 CLASS，`getAnnotation()` 会拿到 null。注解本身没有任何执行逻辑，它只是一个“标签”，真正干活的是切面。

### 环节 3：切面类骨架（放在哪、加什么注解）

包位置：`sky-server` 的 `com.sky.aspect`（切面类一般单独建 `aspect` 包，和 `annotation`、`interceptor` 平级）。

```java
@Aspect                 // 声明这是一个切面类
@Component              // 必须交给 Spring 容器管理
@Slf4j
public class AutoFillAspect {
    ...
}
```

`@Aspect` 只是告诉框架“这是切面”，`@Component` 才是让它成为 Bean、被 Spring 扫描并创建代理的前提；少了 `@Component`（或类不在扫描路径下），切面永远不会生效。

### 环节 4：切入点表达式（拦谁）

官方课程版本的写法：

```java
@Pointcut("execution(* com.sky.mapper.*.*(..)) && @annotation(com.sky.annotation.AutoFill)")
public void autoFillPointCut() {}
```

拆开看每个符号：

| 片段                 | 含义                        |
| -------------------- | --------------------------- |
| `execution(...)`     | 按方法签名匹配              |
| 第一个 `*`           | 返回值任意类型              |
| `com.sky.mapper.*.*` | mapper 包下任意类的任意方法 |
| `(..)`               | 任意参数                    |
| `&&`                 | 并且（组合表达式）          |
| `@annotation(...)`   | 方法上必须带有该注解        |

`execution` 单独用粒度太粗，会把 mapper 里所有查询、删除方法都拦下来；再叠加 `@annotation` 就精准锁定“带 `@AutoFill` 的方法”，以后新增 Mapper 只要加注解就自动纳入管理。这也回答了你之前问的“怎么用 @annotation 选切入点”：**`execution` 划大范围，`@annotation` 做精确开关**，这是工程里最常见的组合套路。

`@Pointcut` 与 `@Before` 的分工也在这里：`@Pointcut` 只负责“定义匹配规则”（一个空方法，起命名作用），`@Before`/`@Around` 等通知注解才负责“在匹配到的时机干什么”，通知里通过方法名引用这个切入点。

### 环节 5：选择通知类型（什么时候干）

课程版本用的是**前置通知**：

```java
@Before("autoFillPointCut()")
public void autoFill(JoinPoint joinPoint) { ... }
```

为什么是前置？因为必须在 `insert`/`update` 真正执行**之前**把字段塞进实体，SQL 已经执行完再赋值就没意义了。

五种通知的对比（顺带复习）：

| 通知              | 时机             | 典型场景                 |
| ----------------- | ---------------- | ------------------------ |
| `@Before`         | 目标方法执行前   | 权限校验、参数校验、日志 |
| `@AfterReturning` | 正常返回后       | 记录返回值、成功日志     |
| `@AfterThrowing`  | 抛异常后         | 异常日志、告警           |
| `@After`          | 结束（无论成败） | 资源释放、上下文清理     |
| `@Around`         | 包裹全过程       | 耗时统计、事务、缓存     |

同一方法上多个通知的执行顺序是：`@Around` 前半段 → `@Before` → 目标方法 → `@Around` 后半段 → `@AfterReturning`/`@AfterThrowing` → `@After`。

补充一点：网上有些同学把这里改成了 `@Around("autoFillPointCut()")` + `ProceedingJoinPoint`，功能等价，但需要手动 `joinPoint.proceed()` 放行。填充字段这种“只改参数、不改流程”的需求，`@Before` 更简洁；只有需要控制是否执行、统计耗时、拿返回值时才用 `@Around`。

### 环节 6：通知内部的四步逻辑（核心代码）

```java
@Before("autoFillPointCut()")
public void autoFill(JoinPoint joinPoint) {
    log.info("开始进行公共字段自动填充...");

    // ① 拿到被拦截方法上的注解，读出操作类型
    //MethodSignature是AspectJ 的接口，来自aspectjweaver包；Spring AOP只是复用 AspectJ 的这套 API。作用是代表方法上的注释
    MethodSignature signature = (MethodSignature) joinPoint.getSignature();
    AutoFill autoFill = signature.getMethod().getAnnotation(AutoFill.class);
    OperationType operationType = autoFill.value();

    // ② 拿到方法参数，约定实体放在第一个位置
    Object[] args = joinPoint.getArgs();
    if (args == null || args.length == 0) return;
    Object entity = args[0];

    // ③ 准备要赋的值：时间 + 当前登录用户 id（ThreadLocal）
    LocalDateTime now = LocalDateTime.now();
    Long currentId = BaseContext.getCurrentId();

    // ④ 按操作类型反射赋值
    if (operationType == OperationType.INSERT) {
        Method setCreateTime = entity.getClass().getDeclaredMethod("setCreateTime", LocalDateTime.class);
        Method setCreateUser = entity.getClass().getDeclaredMethod("setCreateUser", Long.class);
        Method setUpdateTime = entity.getClass().getDeclaredMethod("setUpdateTime", LocalDateTime.class);
        Method setUpdateUser = entity.getClass().getDeclaredMethod("setUpdateUser", Long.class);
        setCreateTime.invoke(entity, now);
        setCreateUser.invoke(entity, currentId);
        setUpdateTime.invoke(entity, now);
        setUpdateUser.invoke(entity, currentId);
    } else if (operationType == OperationType.UPDATE) {
        Method setUpdateTime = entity.getClass().getDeclaredMethod("setUpdateTime", LocalDateTime.class);
        Method setUpdateUser = entity.getClass().getDeclaredMethod("setUpdateUser", Long.class);
        setUpdateTime.invoke(entity, now);
        setUpdateUser.invoke(entity, currentId);
    }
}
```

几个细节值得你留意：

- `joinPoint.getSignature()` 返回的是 `Signature`，因为被拦截的一定是方法，所以向下转型成 `MethodSignature`，再 `getMethod()` 拿到 `Method` 对象读注解。
- `getArgs()` 拿的是**全部参数数组**，项目做了约定：想用自动填充，实体必须放第一个参数；用 `Object` 接收而不是 `Employee`，因为 EmployeeMapper、CategoryMapper、DishMapper 传进来的实体类型各不相同。
- 反射赋值靠的是实体上 Lombok `@Data` 生成的 setter，方法名拼写必须严格对应；课程里把方法名抽成 `AutoFillConstant.SET_CREATE_TIME` 等常量，避免硬编码字符串写错。
- `BaseContext.getCurrentId()` 是项目里的 ThreadLocal 工具类，登录时由 JWT 拦截器写入，切面在同一个请求线程里直接取。

### 环节 7：在 Bean（该案例是mapper）的 方法上打注解（触发开关）

```java
@AutoFill(OperationType.INSERT)
void insert(Employee employee);

@AutoFill(OperationType.UPDATE)
void update(Employee employee);
```

注解加在 `insert` 上就填四个字段，加在 `update` 上就填两个。注意：`@AutoFill` 是加在 **Mapper 接口方法**上，不是 Service 上——因为要拦截的是最终执行 SQL 的那一层，且参数就是实体本身。

### 环节 8：完整调用链路（运行时序）

以“新增员工”为例：

```
HTTP 请求
  → JwtTokenUserInterceptor（解析 JWT，把员工 id 放进 BaseContext 的 ThreadLocal）
  → EmployeeController.save()
  → EmployeeServiceImpl.save()
  → employeeMapper.insert(employee)   ← 这里的 mapper 是代理对象
      → 【AOP 前置通知 autoFill() 执行】反射填 createTime/createUser/updateTime/updateUser
      → 目标方法：MyBatis 执行 @Insert SQL（此时实体字段已齐全）
  → 返回 Result
  → 拦截器 afterCompletion 清理 ThreadLocal
```

关键理解：`EmployeeMapper` 是接口，Spring AOP 对它做代理时走的是 **JDK 动态代理**（基于接口，`Proxy.newProxyInstance` + `InvocationHandler`）；如果目标类没有接口，才会退化成 CGLIB 生成子类。MyBatis 自身也会为 Mapper 接口生成 `MapperProxy`，两者是“套娃”关系：AOP 代理在外层负责增强，MapperProxy 在内层负责执行 SQL。

------

## 三、这套流程里最容易踩的坑

1. **`@Retention` 写成默认值**：默认是 CLASS，反射读不到注解，切面进了方法但 `autoFill` 为 null，直接 NPE（NullPointerException，空指针异常）。
2. **切面类没被 Spring 管理**：漏 `@Component`，或放在 `com.sky.aspect` 但不在启动类扫描包下，切面完全不生效。
3. **多切面顺序**：如果再加日志切面、限流切面，用 `@Order(1)`、`@Order(2)` 控制优先级，数值越小越先执行。

------

## 四、把它放进项目全景里对比一下

苍穹外卖里“增强”类技术有好几个，容易混，给你一条区分线：

- **AOP 切面（AutoFillAspect）**：作用于方法调用，能拿到方法签名、注解、参数，适合“改参数、填字段、记耗时”。
- **拦截器（JwtTokenUserInterceptor）**：作用于 HTTP 请求，能拿到 request/response，负责登录校验并把用户 id 放进 ThreadLocal，供切面消费。
- **`@Transactional`**：本质也是 AOP，声明式事务靠切面在方法前后开启/提交事务。
- **全局异常处理（`@RestControllerAdvice`）**：不是 AOP，是 Spring MVC 的异常处理机制。











> 包：`org.aspectj.lang.JoinPoint`，下面是标准方法，全部**无入参**，除了子类 `ProceedingJoinPoint` 的 `proceed`。

## JoinPoint（基础）

| 方法             | 入参 | 返回类型    | 含义                                              |
| ---------------- | ---- | ----------- | ------------------------------------------------- |
| `getArgs()`      | 无   | `Object[]`  | 当前调用的方法实参数组，AutoFill 拿 entity 就靠它 |
| `getSignature()` | 无   | `Signature` | 方法签名对象；一般强转成 `MethodSignature`        |
| `getTarget()`    | 无   | `Object`    | **原始目标对象（被代理本体）**                    |
| `getThis()`      | 无   | `Object`    | **AOP 代理对象**（容器注入拿到的那个）            |

### Signature（getSignature 返回）常用子方法

```
Signature sig = jp.getSignature();
// 全部无参
String getName()                     // 方法名
Class<?> getDeclaringType()          // 方法所在类
String getDeclaringTypeName()        // 全类名
int getModifiers()                   // 修饰符 public/private
```

强转为 `MethodSignature`（`org.aspectj.lang.reflect.MethodSignature`）之后额外：

```
Method getMethod()                   // java.lang.reflect.Method  （需要注解、泛型、反射调用、参数注解时用Method）
Class<?>[] getParameterTypes()       // 参数类型数组
String[] getParameterNames()         // 参数名（需要编译保留参数名）
```

## ProceedingJoinPoint（继承 JoinPoint，只给 @Around）

多出两个重载的 `proceed`，这是唯一带参数的方法：

| 方法                     | 入参       | 返回类型 | 说明                                         |
| ------------------------ | ---------- | -------- | -------------------------------------------- |
| `proceed()`              | 无         | `Object` | 使用原始参数执行目标方法；返回目标方法返回值 |
| `proceed(Object[] args)` | `Object[]` | `Object` | 使用你传入的新参数数组执行（用来改参）       |

> 注意：`proceed` 会抛出 `Throwable`，所以环绕通知必须 `throws Throwable`。

## 最简代码对照

```
@Before("autoFillPointCut()")
public void fill(JoinPoint jp) {
    Object[] args = jp.getArgs();

    Signature sig = jp.getSignature();
    MethodSignature ms = (MethodSignature) sig;
    Method method = ms.getMethod();

    Object target = jp.getTarget();
    Object proxy = jp.getThis();
}
@Around("autoFillPointCut()")
public Object around(ProceedingJoinPoint pjp) throws Throwable {
    Object[] args = pjp.getArgs();
    // 修改参数
    args[0] = ...;
    // 使用新参数放行
    Object result = pjp.proceed(args);
    return result;
}
```

## 容易踩的类型点

1. `getArgs()` 返回 `Object[]`，里面元素是实参，**数组里元素可以直接改（引用类型）**；但这个数组本身不是传给 `proceed` 的，如果你要换一套参数，必须构造新数组传给 `proceed(newArgs)`。
2. `getThis()` / `getTarget()` 返回 `Object`，需要自己强转成业务类型。
3. `Signature` 是父接口，不一定都是方法；但在 Spring AOP 里几乎都可以安全强转为 `MethodSignature`。
4. `ProceedingJoinPoint` **只能在 @Around 使用**；`@Before / @After` 只能用父接口 `JoinPoint`，没有 `proceed()`。