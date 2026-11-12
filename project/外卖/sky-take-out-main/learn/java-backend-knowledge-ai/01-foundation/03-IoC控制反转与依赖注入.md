# 03 IoC 与依赖注入

## 1. 核心概念

IoC（Inversion of Control，控制反转）表示对象的创建、组装和生命周期不再由业务代码自己管理，而交给 Spring 容器。依赖注入（DI，Dependency Injection）是 IoC 的一种具体实现：当前对象需要什么依赖，由容器在创建对象时提供什么依赖。

例如，Controller 需要调用 Service：

```java
// 不推荐在 Controller 中自己创建实现类
EmployeeService employeeService = new EmployeeServiceImpl();
```

这样写会让 Controller 直接负责实现类的创建，业务层和实现细节发生硬编码耦合。使用 Spring 后，Controller 只声明“我需要一个 EmployeeService”，由容器寻找合适的 Bean 并完成装配。

```text
Controller 声明依赖
        -> Spring 容器查找候选 Bean
        -> 注入 EmployeeServiceImpl（运行时对象）
        -> Controller 可以调用接口定义的方法
```

## 2. 项目中的代码

### 2.1 Controller 依赖接口

项目中的 [`EmployeeController.java`](../../../sky-server/src/main/java/com/sky/controller/admin/EmployeeController.java) 导入的是 Service 接口：

```java
import com.sky.service.EmployeeService;

@RestController
public class EmployeeController {
    @Autowired
    private EmployeeService employeeService;
}
```

它没有导入 `EmployeeServiceImpl`。对应的实现类位于 [`EmployeeServiceImpl.java`](../../../sky-server/src/main/java/com/sky/service/impl/EmployeeServiceImpl.java)：

```java
@Service
public class EmployeeServiceImpl implements EmployeeService {
    @Autowired
    private EmployeeMapper employeeMapper;
}
```

`EmployeeService` 定义业务能力和方法契约，`EmployeeServiceImpl` 负责具体实现：

```java
public interface EmployeeService {
    Employee login(EmployeeLoginDTO employeeLoginDTO);
    void save(EmployeeDTO employeeDTO);
    PageResult pageQuery(EmployeePageQueryDTO employeePageQueryDTO);
}
```

```java
@Service
public class EmployeeServiceImpl implements EmployeeService {
    // 具体的数据库访问和业务处理写在实现类中
}
```

Spring 扫描到 `@Service` 后，会创建 `EmployeeServiceImpl` 对象并注册到容器。由于它实现了 `EmployeeService`，当 Spring 看到 Controller 需要 `EmployeeService` 时，就可以把这个实现对象注入进去。这里的“面向接口”指的是依赖声明的静态类型是接口；运行时实际执行的仍然是 `EmployeeServiceImpl` 的方法。

### 2.2 配置类中的依赖

项目中的 [`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) 也声明了对两个拦截器的依赖：

```java
@Autowired
private JwtTokenAdminInterceptor jwtTokenAdminInterceptor;

@Autowired
private JwtTokenUserInterceptor jwtTokenUserInterceptor;
```

这个配置类不需要自己 `new JwtTokenAdminInterceptor()`。拦截器本身由 Spring 创建，配置类只负责把已经创建好的对象注册到 `InterceptorRegistry` 中。这个例子同时体现了两层关系：

```text
WebMvcConfiguration --依赖--> JwtTokenAdminInterceptor
WebMvcConfiguration --依赖--> JwtTokenUserInterceptor
```

## 3. 为什么依赖应该面向接口

“面向接口，而不是面向实现”是依赖倒置原则（DIP）在代码中的常见体现。它不是说所有类都必须额外创建一个接口，而是说在 Controller、Service 等模块边界上，调用方应依赖稳定的抽象契约，而不是依赖某个具体实现的细节。

### 3.1 降低耦合

如果 Controller 直接依赖实现类：

```java
// Controller 被具体实现类绑定
private EmployeeServiceImpl employeeService;
```

Controller 就知道并绑定了 `EmployeeServiceImpl` 这个类。以后即使业务逻辑拆成不同实现，Controller 也可能需要修改。依赖接口时，Controller 只知道 `EmployeeService` 规定了哪些操作，不关心方法内部是查 MySQL、调用远程服务，还是使用缓存。

```java
// Controller 只依赖业务契约
private EmployeeService employeeService;
```

依赖方向可以理解为：

```text
EmployeeController -> EmployeeService（抽象契约）
                                      ^
                                      |
                         EmployeeServiceImpl（具体实现）
```

### 3.2 便于替换实现和扩展功能

假设将来需要增加一个特殊实现：

```java
@Service
public class RemoteEmployeeService implements EmployeeService {
    // 通过远程接口获取员工数据
}
```

调用方仍然可以依赖 `EmployeeService`，不必因为实现方式变化而重写 Controller。实际项目中，如果容器里同时存在多个 `EmployeeService` 实现，Spring 无法仅凭接口类型判断应该注入哪一个，此时需要使用 `@Primary` 或 `@Qualifier` 明确选择：

```java
@Service("mysqlEmployeeService")
public class EmployeeServiceImpl implements EmployeeService {
}

@Service("remoteEmployeeService")
public class RemoteEmployeeService implements EmployeeService {
}
```

```java
public EmployeeController(
        @Qualifier("mysqlEmployeeService") EmployeeService employeeService) {
    this.employeeService = employeeService;
}
```

本项目目前主要使用一个 `EmployeeService` 实现，因此 Spring 可以自动完成唯一候选 Bean 的注入。

### 3.3 更容易进行单元测试

Controller 依赖接口后，测试时可以传入一个假的实现或 Mockito Mock，而不必启动完整的数据库、MyBatis 和 Web 环境：

```java
EmployeeService employeeService = mock(EmployeeService.class);
EmployeeController controller = new EmployeeController(employeeService, jwtProperties);
```

测试可以控制 `employeeService.login(...)` 的返回值或异常，单独验证 Controller 的参数处理和响应封装。

### 3.4 与 Spring AOP 代理更自然地配合

Spring 的事务、缓存、权限等功能通常通过代理对象实现。例如 Service 方法加上 `@Transactional` 后，容器中注入的可能不是原始的 `EmployeeServiceImpl`，而是一个负责“先开启事务、再调用目标对象、最后提交或回滚”的代理对象。

```text
Controller
    -> Spring 代理对象
        -> EmployeeServiceImpl
```

依赖接口可以表达“我只需要这组能力”，也更适合 JDK 动态代理这类基于接口的代理方式。具体使用哪种代理取决于 Spring 配置和代理目标，不能简单认为所有情况下都一定是 JDK 代理；但在业务边界上依赖接口通常更稳妥。

### 3.5 不是所有类都必须创建接口

面向接口是设计原则，不是机械规则。对于没有替换需求、职责很简单的内部工具类或不可变值对象，直接依赖具体类可能更清晰。项目中的 Service、Mapper、外部适配器等模块边界通常适合使用接口；不要为了形式而给每一个只有一行逻辑的类都创建一个接口。

## 4. 字段注入与构造器注入

### 4.1 项目中的字段注入

当前项目大量使用字段注入，典型代码是：

```java
@RestController
public class EmployeeController {
    @Autowired
    private EmployeeService employeeService;

    @Autowired
    private JwtProperties jwtProperties;
}
```

Spring 的大致处理过程是：

```text
1. 先调用无参构造方法创建 EmployeeController
2. 通过反射找到带有 @Autowired 的字段
3. 从容器中查找 EmployeeService 和 JwtProperties
4. 通过反射给字段赋值
5. 完成 Bean 初始化并放入容器
```

字段注入代码短，改造旧项目方便，但依赖隐藏在类的字段中，单看构造方法无法知道一个对象正常工作需要哪些协作者。

### 4.2 构造器注入示例

同一个 `EmployeeController` 可以改写为构造器注入：

```java
@RestController
@RequestMapping("/admin/employee")
public class EmployeeController {

    // final 表示依赖在构造完成后不能被重新指向其他对象。
    private final EmployeeService employeeService;
    private final JwtProperties jwtProperties;

    // Spring 创建 Controller 时，会从容器中查找这两个参数对应的 Bean。
    // 只有一个构造方法时，Spring 4.3+ 通常不需要再写 @Autowired。
    public EmployeeController(EmployeeService employeeService,
                               JwtProperties jwtProperties) {
        this.employeeService = employeeService;
        this.jwtProperties = jwtProperties;
    }
}
```

如果类中存在多个构造方法，需要在希望 Spring 使用的构造方法上标注 `@Autowired`：

```java
@Autowired
public EmployeeController(EmployeeService employeeService,
                          JwtProperties jwtProperties) {
    this.employeeService = employeeService;
    this.jwtProperties = jwtProperties;
}
```

构造器注入的核心不是把 `@Autowired` 从字段挪到构造方法，而是让“创建对象所必需的依赖”成为构造方法参数。对象一旦创建成功，就应当处于可用状态。

### 4.3 将项目的 WebMvcConfiguration 改为构造器注入

当前 [`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) 使用字段注入：

```java
@Configuration
public class WebMvcConfiguration implements WebMvcConfigurer {
    @Autowired
    private JwtTokenAdminInterceptor jwtTokenAdminInterceptor;

    @Autowired
    private JwtTokenUserInterceptor jwtTokenUserInterceptor;
}
```

等价的构造器注入写法如下：

```java
@Configuration
public class WebMvcConfiguration implements WebMvcConfigurer {

    private final JwtTokenAdminInterceptor jwtTokenAdminInterceptor;
    private final JwtTokenUserInterceptor jwtTokenUserInterceptor;

    public WebMvcConfiguration(JwtTokenAdminInterceptor jwtTokenAdminInterceptor,
                               JwtTokenUserInterceptor jwtTokenUserInterceptor) {
        this.jwtTokenAdminInterceptor = jwtTokenAdminInterceptor;
        this.jwtTokenUserInterceptor = jwtTokenUserInterceptor;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(jwtTokenAdminInterceptor)
                .addPathPatterns("/admin/**")
                .excludePathPatterns("/admin/employee/login");

        registry.addInterceptor(jwtTokenUserInterceptor)
                .addPathPatterns("/user/**")
                .excludePathPatterns("/user/user/login")
                .excludePathPatterns("/user/shop/status");
    }
}
```

这个改写只改变依赖的注入方式，不改变拦截器的匹配规则。Spring 创建 `WebMvcConfiguration` 时，会先准备两个拦截器，再调用构造方法传入它们；之后执行 `addInterceptors`，将已经注入的对象注册到 MVC 体系中。

也可以使用 Lombok 简化构造方法：

```java
@Configuration
@RequiredArgsConstructor
public class WebMvcConfiguration implements WebMvcConfigurer {
    private final JwtTokenAdminInterceptor jwtTokenAdminInterceptor;
    private final JwtTokenUserInterceptor jwtTokenUserInterceptor;
}
```

`@RequiredArgsConstructor` 会为 `final` 字段生成必需参数构造方法。使用这种写法时，要确认项目启用了 Lombok 注解处理，并且团队能够接受由 Lombok 生成构造方法的方式。

## 5. 为什么新代码通常推荐构造器注入

| 对比项 | 字段注入 | 构造器注入 |
| --- | --- | --- |
| 依赖是否明显 | 隐藏在字段和注解中 | 直接出现在构造方法参数中 |
| 是否可以使用 `final` | 通常不能保证 | 可以保证依赖引用初始化后不变 |
| 单元测试 | 往往需要反射或启动 Spring | 直接 `new` 并传入 Mock |
| 缺少依赖的反馈 | Bean 创建后注入阶段报错 | 创建对象时立即失败 |
| 对象完整性 | 可能存在短暂的未注入状态 | 构造成功后依赖齐全 |
| 循环依赖表现 | 某些场景可能被容器勉强处理 | 更早暴露设计问题 |

构造器注入特别适合必需依赖；如果依赖确实是可选的，可以使用 `Optional<T>`、`ObjectProvider<T>` 或其他明确的可选依赖表达方式，而不是让字段默默保持 `null`。

## 6. Spring 的装配过程（结合本项目）

以 `EmployeeController` 依赖 `EmployeeService` 为例，启动和装配可以抽象为以下过程：

```text
1. Spring Boot 启动应用
       |
2. @SpringBootApplication 触发组件扫描
       |
3. 扫描到 EmployeeController，注册 Controller Bean 定义
       |
4. 扫描到 EmployeeServiceImpl，发现 @Service，注册 Service Bean 定义
       |
5. 创建 EmployeeServiceImpl，并注入 EmployeeMapper
       |
6. 创建 EmployeeController
       |
7. 按字段注入或构造器参数注入 EmployeeServiceImpl
       |
8. 完成初始化，将 Bean 放入 ApplicationContext
       |
9. HTTP 请求到来时，DispatcherServlet 使用已经装配好的 Controller
```

在这个过程中：

- `@RestController` 使 `EmployeeController` 成为 Web 层 Bean；
- `@Service` 使 `EmployeeServiceImpl` 成为业务层 Bean；
- `EmployeeService` 是 Controller 与实现类之间的抽象契约；
- `EmployeeMapper` 通常由 MyBatis-Spring 生成代理对象，再注入 Service；
- Controller 不需要负责任何依赖对象的 `new`、连接数据库或代理创建。

## 7. 对象关系

```text
Spring 容器（ApplicationContext）
  ├── EmployeeController
  ├── EmployeeServiceImpl（可能被事务/AOP代理包装）
  ├── EmployeeMapper（MyBatis 代理对象）
  ├── JwtTokenAdminInterceptor
  ├── JwtTokenUserInterceptor
  └── JwtProperties

EmployeeController --依赖--> EmployeeService（接口）
                              └── 实际对象：EmployeeServiceImpl
EmployeeServiceImpl --依赖--> EmployeeMapper（接口/代理）
WebMvcConfiguration --依赖--> 两个 JWT 拦截器
```

Controller 不需要 `new EmployeeServiceImpl()`，也不需要知道 Mapper 代理如何生成。它只依赖稳定的接口和配置契约，具体对象的创建、选择、注入和生命周期由 Spring 容器负责。

## 8. 学习重点

- `@RestController`、`@Service`、`@Component` 都可以参与 Bean 注册；它们表达的组件职责不同。
- 依赖应该面向接口，例如 Controller 依赖 `EmployeeService`，而不是具体实现类 `EmployeeServiceImpl`。
- 面向接口可以降低耦合，便于替换实现、编写单元测试，并与 Spring AOP 代理配合。
- 本项目大量使用字段注入；新代码通常更推荐构造器注入，因为依赖更明确、引用可以设为 `final`，对象更容易测试。
- 构造器注入不是为了改变业务逻辑，而是为了让对象的依赖关系在创建阶段显式确定。
