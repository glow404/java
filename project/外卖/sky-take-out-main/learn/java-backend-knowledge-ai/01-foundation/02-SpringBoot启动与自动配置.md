# 02 Spring Boot 启动与自动配置

## 1. 这个知识点解决什么问题

传统 Spring 项目需要配置大量 XML 或手动 Bean。Spring Boot 通过约定、自动配置和内嵌服务器，让应用可以用一个启动类运行起来。

## 2. 项目中的代码

入口是 [`SkyApplication.java`](../../../sky-server/src/main/java/com/sky/SkyApplication.java)：

```java
@SpringBootApplication
@EnableTransactionManagement
@EnableCaching
public class SkyApplication {
    public static void main(String[] args) {
        SpringApplication.run(SkyApplication.class, args);
    }
}
```

### 2.1 为什么通常写在主程序类上

这几个注解通常集中写在 Spring Boot 主程序类上，但它们的要求并不完全相同：

| 注解 | 通常位置 | 是否必须放在主程序类 |
| --- | --- | --- |
| `@SpringBootApplication` | 主配置类，通常就是主程序类 | 不强制，但必须被 `SpringApplication.run` 使用或被其他配置导入 |
| `@EnableTransactionManagement` | 主程序类或独立的 `@Configuration` 类 | 不必须 |
| `@EnableCaching` | 主程序类或独立的 `@Configuration` 类 | 不必须 |
| `@Slf4j` | 需要日志的类 | 与 Spring 装配无关 |

当前项目把全局开关集中在 [`SkyApplication.java`](../../../sky-server/src/main/java/com/sky/SkyApplication.java) 上，是为了让应用启动配置集中、清晰可见。也可以拆成独立配置类：

```java
@Configuration
@EnableTransactionManagement
@EnableCaching
public class InfrastructureConfiguration {
}
```

只要这个配置类位于组件扫描范围内，例如 `com.sky.config`，它就可以生效。

`SkyApplication` 位于 `com.sky` 根包下，这一点很重要。`@SpringBootApplication` 默认从它所在的包开始扫描，因此可以发现：

```text
com.sky.controller
com.sky.service
com.sky.mapper
com.sky.config
com.sky.interceptor
com.sky.task
```

如果主配置类错误地放在 `com.sky.controller` 下，默认扫描范围可能无法覆盖同级的 `service` 和 `config`。这时需要显式指定：

```java
@SpringBootApplication(scanBasePackages = "com.sky")
```

### 2.2 `@SpringBootApplication` 的三个能力

`@SpringBootApplication` 组合了三个能力：

```text
@SpringBootConfiguration       标记 Spring Boot 主配置类
@EnableAutoConfiguration       根据依赖和配置进行自动配置
@ComponentScan                  扫描当前包及其子包中的组件
```

#### `@SpringBootConfiguration`

它是 Spring Boot 对 `@Configuration` 的特殊标记，表示这个类是应用的主要配置来源。配置类中可以使用 `@Bean` 方法声明对象。

#### `@EnableAutoConfiguration`

它根据项目依赖、配置文件和容器中已有的 Bean，自动导入合适的配置。例如当前项目引入：

```text
spring-boot-starter-web          -> Spring MVC、Jackson、内嵌 Tomcat
mybatis-spring-boot-starter      -> SqlSessionFactory、Mapper 支持
spring-boot-starter-data-redis   -> Redis 连接基础设施
spring-boot-starter-cache        -> CacheManager 相关基础设施
spring-boot-starter-websocket    -> WebSocket 基础设施
```

自动配置不是无条件创建所有对象，而是根据 classpath、配置项和条件注解判断是否创建。

#### `@ComponentScan`

它发现并注册带有 `@Component`、`@Service`、`@RestController`、`@Configuration` 等注解的类。例如：

```java
@Service
public class OrderServiceImpl implements OrderService {
}
```

```java
@RestController
public class EmployeeController {
}
```

MyBatis 的 `@Mapper` 接口则由 MyBatis Spring Boot Starter 提供的 Mapper 扫描机制处理，和普通的 `@Component` 扫描不是完全同一个过程。

### 2.3 两个全局功能开关

#### `@EnableTransactionManagement`

它开启 Spring 对 `@Transactional` 的处理能力。它本身不代表每个方法都会开启事务，而是注册事务拦截器和代理创建机制。

当前项目的订单提交方法：

```java
@Transactional
public OrderSubmitVO submit(OrdersSubmitDTO orderSubmitDTO) {
    // 创建订单
    // 创建订单明细
    // 清空购物车
}
```

代码位置：[`OrderServiceImpl.java`](../../../sky-server/src/main/java/com/sky/service/impl/OrderServiceImpl.java)。运行时，Controller 注入的通常是被 Spring 增强过的 Service 代理对象：

```text
调用 OrderService.submit()
  -> 代理对象拦截
  -> 开启数据库事务
  -> 执行真实 submit 方法
  -> 成功提交，异常回滚
```

#### `@EnableCaching`

它开启 `@Cacheable`、`@CacheEvict` 等缓存注解的处理能力。

当前项目中，用户查询套餐的方法使用：

```java
@Cacheable(cacheNames = "setmealCache", key = "#categoryId")
public Result<List<Setmeal>> list(Long categoryId) {
    ...
}
```

代码位置：[`SetmealController.java`](../../../sky-server/src/main/java/com/sky/controller/user/SetmealController.java)。调用时，缓存代理会先查询缓存；未命中时才执行原方法并保存结果。管理端修改套餐时使用 `@CacheEvict` 清理旧缓存。

#### `@Slf4j`

`@Slf4j` 是 Lombok 注解，不参与 Spring 装配。它在编译期生成一个日志对象，使下面代码可以直接使用：

```java
log.info("server started");
```

代码中的 `//开启缓存` 是普通 Java 注释，不是注解的一部分。

## 3. `SpringApplication.run` 做了什么

主方法中的：

```java
SpringApplication.run(SkyApplication.class, args);
```

是整个 Spring Boot 应用的启动入口。它有两个重要参数：

| 参数 | 含义 |
| --- | --- |
| `SkyApplication.class` | 告诉 Spring 哪个类是主配置来源 |
| `args` | 传入 JVM 的命令行参数，可覆盖部分配置 |

`run` 的返回值是 `ConfigurableApplicationContext`，也就是 Spring 应用上下文。当前项目没有保存它，因为项目只需要启动应用：

```java
ConfigurableApplicationContext context =
        SpringApplication.run(SkyApplication.class, args);
```

保存上下文后，可以手动获取 Bean，但普通业务代码不应该依赖这种方式，通常使用依赖注入。

`SpringApplication.run` 不会直接执行某个 Controller，也不会直接执行订单 SQL。它主要负责：

```text
准备运行环境
创建 Spring 容器
注册配置和 Bean 定义
执行自动配置
创建并组装 Bean
启动内嵌 Web 服务器
返回 ApplicationContext
```

## 4. Spring Boot 装配流程

下面是从 JVM 启动到应用可以接收请求的简化过程：

```text
main()
  -> SpringApplication.run(SkyApplication.class, args)
  -> 读取 application.yml、Profile 和环境变量
  -> 创建 ApplicationContext
  -> 注册 SkyApplication 作为主配置来源
  -> 解析 @SpringBootApplication
  -> 组件扫描和自动配置
  -> 注册 BeanDefinition
  -> 执行 BeanFactoryPostProcessor
  -> 创建 Bean 并完成依赖注入
  -> 执行 BeanPostProcessor，创建事务/缓存/AOP 代理
  -> 注册 Controller 路由和 Web 基础设施
  -> 启动内嵌 Web 服务器
  -> 应用开始接收请求
```

### 4.1 准备运行环境

Spring Boot 会读取：

```text
application.yml
application-dev.yml
环境变量
JVM 参数
命令行参数
```

当前项目在 `application.yml` 中激活了 `dev` Profile，因此会继续加载 `application-dev.yml`。数据库、Redis、JWT、OSS、微信支付和百度地图配置都会进入 Spring 的 Environment。

### 4.2 创建 ApplicationContext

Spring 创建应用上下文。Web 应用通常使用可以管理 Web 服务器的上下文，它负责管理：

- Bean 和 Bean 生命周期
- 配置和环境属性
- 依赖注入
- AOP 代理
- Web MVC 路由
- 应用事件

可以把它理解为 Spring 应用的总容器。

### 4.3 注册 BeanDefinition

Spring 首先登记“如何创建对象”，这个登记信息叫 `BeanDefinition`。它描述：

- Bean 类型和名称
- 构造方式
- 依赖关系
- 初始化方式
- 是否懒加载
- 是否需要代理

例如项目中会登记：

```text
EmployeeController
OrderServiceImpl
WebMvcConfiguration
RedisTemplate
DataSource
SqlSessionFactory
```

此时主要是登记定义，不等于已经执行了所有业务代码。

### 4.4 解析配置、扫描组件和执行自动配置

Spring 的配置类处理器会解析 `@SpringBootApplication`，完成两类工作：

```text
扫描 com.sky 下的 @Service、@RestController、@Configuration 等类
根据 Starter 依赖和 application.yml 导入自动配置类
```

项目中的 [`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) 带有 `@Configuration`，会被扫描并参与 MVC 配置：

```java
registry.addInterceptor(jwtTokenAdminInterceptor)
        .addPathPatterns("/admin/**");
```

项目中的 [`RedisConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/RedisConfiguration.java) 通过 `@Bean` 声明 `RedisTemplate`：

```java
@Bean
public RedisTemplate redisTemplate(
        RedisConnectionFactory redisConnectionFactory) {
    ...
}
```

Spring 会先找到 `RedisConnectionFactory`，再把它注入这个 `@Bean` 方法。

### 4.5 执行 BeanFactoryPostProcessor

这类后置处理器会在 Bean 真正创建前修改或补充 BeanDefinition。解析配置类、处理 `@Bean`、导入自动配置等工作都发生在这个阶段附近。

重点是：Spring 会先把“对象创建计划”准备完整，再批量创建对象。

### 4.6 创建 Bean 和依赖注入

以员工 Controller 为例：

```text
EmployeeController
  -> 依赖 EmployeeService
  -> 实际注入 EmployeeServiceImpl
  -> EmployeeServiceImpl 依赖 EmployeeMapper
  -> 注入 MyBatis 创建的 Mapper 代理
```

Spring 不会按照文件名顺序创建对象，而是根据依赖关系组装对象图。创建过程中会经历构造、依赖注入、初始化回调和后置处理等步骤。

### 4.7 BeanPostProcessor 和代理

BeanPostProcessor 可以在 Bean 创建前后对对象进行处理，常见功能包括：

```text
处理 @Autowired
处理 @ConfigurationProperties
识别事务方法
识别缓存方法
处理 AOP 切面
生成代理对象
```

因此带有 `@Transactional` 或 `@Cacheable` 的对象，注入到其他 Bean 中的可能不是原始对象，而是代理对象。代理对象在调用真实方法前后插入事务、缓存等逻辑。

### 4.8 启动 Web 服务器

所有必要 Bean 创建完成后，Spring Boot 启动内嵌 Web 服务器。当前项目端口配置为：

```yaml
server:
  port: 8080
```

同时会注册 Controller 路由、JWT 拦截器、JSON 转换器、全局异常处理器和 WebSocket 端点。到这里，应用才真正能够接收 HTTP 请求。

## 5. 启动阶段和请求阶段的区别

Spring 装配主要发生在启动阶段；事务、缓存和 JWT 校验主要发生在请求阶段。

### 5.1 这条链路在启动时已经准备了什么

下面的下单请求链路能够运行，不是因为请求到达后 Spring 才临时创建所有对象，而是因为 `SpringApplication.run(...)` 在启动阶段已经准备了这些基础设施：

| 启动阶段准备项 | 当前项目中的来源 | 请求阶段的作用 |
| --- | --- | --- |
| 内嵌 Web 服务器和 `DispatcherServlet` | `spring-boot-starter-web` 自动配置 | 接收 `8080` 端口的 HTTP 请求，并调度到 Spring MVC |
| URL 到 Controller 方法的映射 | [`OrderController.java`](../../../sky-server/src/main/java/com/sky/controller/user/OrderController.java) 上的 `@RequestMapping`、`@PostMapping` | 将 `POST /user/order/submit` 定位到 `submit(...)` 方法 |
| 用户端 JWT 拦截器 | [`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) | 在 Controller 前校验 `/user/**` 请求 |
| JSON 转换器 | `WebMvcConfiguration.extendMessageConverters(...)` | 将请求 JSON 转成 DTO，将返回对象转成 JSON |
| `OrderService` Bean 和事务代理 | [`OrderServiceImpl.java`](../../../sky-server/src/main/java/com/sky/service/impl/OrderServiceImpl.java) 上的 `@Service`、`@Transactional` | 调用下单方法时开启、提交或回滚事务 |
| MyBatis Mapper 代理与 XML 映射 | `@Mapper` 接口、`resources/mapper/*.xml`、`application.yml` 的 `mapper-locations` | 把 Mapper 方法调用转换成 SQL 并执行 |
| 数据源和事务管理器 | MySQL 配置、MyBatis/Spring Boot 自动配置 | 为 Mapper 提供连接，并将多条 SQL 纳入同一事务 |

因此，启动阶段是“准备舞台”；请求阶段是“在已经准备好的对象和规则上执行业务”。

### 5.2 以真实下单接口为例

本项目用户端下单入口是：

```text
POST /user/order/submit
```

它来自 [`OrderController.java`](../../../sky-server/src/main/java/com/sky/controller/user/OrderController.java)：

```java
@RestController("userOrderController")
@RequestMapping("/user/order")
public class OrderController {

    @PostMapping("/submit")
    public Result<OrderSubmitVO> submit(
            @RequestBody OrdersSubmitDTO orderSubmitDTO) {
        OrderSubmitVO orderSubmitVO =
                orderService.submit(orderSubmitDTO);
        return Result.success(orderSubmitVO);
    }
}
```

一个简化后的请求可能长这样：

```http
POST /user/order/submit HTTP/1.1
Host: localhost:8080
Content-Type: application/json
authentication: <用户登录后获得的 JWT>

{
  "addressBookId": 12,
  "payMethod": 1,
  "remark": "少辣",
  "estimatedDeliveryTime": "2026-09-06 18:30:00",
  "deliveryStatus": 1,
  "tablewareNumber": 2,
  "tablewareStatus": 0,
  "packAmount": 1,
  "amount": 38.00
}
```

其中 `authentication` 不是随意命名的。它来自 [`application.yml`](../../../sky-server/src/main/resources/application.yml) 中的 `sky.jwt.user-token-name`，再绑定到 [`JwtProperties.java`](../../../sky-common/src/main/java/com/sky/properties/JwtProperties.java)。

请求中的 JSON 会被绑定为 [`OrdersSubmitDTO.java`](../../../sky-pojo/src/main/java/com/sky/dto/OrdersSubmitDTO.java)。DTO 只承载这次“提交订单”允许由客户端提交的字段，不应该承担由服务端决定的订单状态、当前用户 ID、订单号和创建时间。

### 5.3 总体时序图

原先的简略箭头可以展开为下面这条完整链路：

```text
微信小程序 / HTTP 客户端
  |
  | POST /user/order/submit
  | Header: authentication: <JWT>
  | Body: OrdersSubmitDTO 对应的 JSON
  v
内嵌 Tomcat
  v
DispatcherServlet
  |
  | 通过 HandlerMapping 找到
  | OrderController.submit(...)
  v
HandlerExecutionChain
  |
  | JwtTokenUserInterceptor.preHandle(...)
  | 解析 JWT，写入 BaseContext
  v
RequestMappingHandlerAdapter
  |
  | 使用 HttpMessageConverter/Jackson
  | JSON -> OrdersSubmitDTO
  v
OrderController.submit(...)
  |
  | 调用注入的 OrderService 代理
  v
事务代理
  |
  | 开启事务
  v
OrderServiceImpl.submit(...)
  |
  | AddressBookMapper.getById(...)
  | 百度地图配送范围校验
  | ShoppingCartMapper.list(...)
  | OrderMapper.insert(...)
  | OrderDetailMapper.insertBatch(...)
  | ShoppingCartMapper.deleteByUserId(...)
  v
MyBatis Mapper 代理 -> SQL -> MySQL
  |
  | 成功：提交事务，返回 OrderSubmitVO
  | 异常：回滚事务，异常继续向上抛出
  v
OrderController 包装为 Result.success(...)
  v
HttpMessageConverter/Jackson
  v
HTTP JSON 响应
```

下面按实际执行顺序逐步拆开。

### 5.4 第 1 步：HTTP 请求进入内嵌服务器

`SkyApplication` 中的：

```java
SpringApplication.run(SkyApplication.class, args);
```

在启动阶段创建 Web 类型的 `ApplicationContext` 并启动内嵌 Web 服务器。当前项目的端口配置是：

```yaml
server:
  port: 8080
```

因此请求先到达 `http://localhost:8080/user/order/submit`。Tomcat 负责网络连接、HTTP 协议解析和 Servlet 调度；它不理解订单、JWT 或 Mapper，这些属于后续 Spring MVC 层的职责。

### 5.5 第 2 步：DispatcherServlet 接管请求

`DispatcherServlet` 是 Spring MVC 的前端控制器。它可以理解为所有 Spring MVC 请求的总入口，主要职责是：

```text
找到哪个 Controller 方法应处理请求
组织拦截器链
调用参数解析和方法执行
处理返回值或异常
```

对于当前请求，Spring 启动时已经根据以下两个注解注册了路由信息：

```java
@RequestMapping("/user/order")
@PostMapping("/submit")
```

两者拼接后是 `POST /user/order/submit`。`DispatcherServlet` 会通过 `HandlerMapping` 找到对应的 `HandlerMethod`，也就是：

```text
OrderController.submit(OrdersSubmitDTO)
```

这里还没有进入 Controller 方法，也还没有执行下单 SQL。此时 Spring 只是完成“路由匹配”。

### 5.6 第 3 步：构造拦截器链并执行 JWT 校验

Spring 找到 Controller 方法后，会为它构造 `HandlerExecutionChain`，其中包含目标方法和匹配的拦截器。

项目在 [`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) 中注册了：

```java
registry.addInterceptor(jwtTokenUserInterceptor)
        .addPathPatterns("/user/**")
        .excludePathPatterns("/user/user/login")
        .excludePathPatterns("/user/shop/status");
```

`/user/order/submit` 满足 `/user/**`，且不在排除列表，因此会执行 [`JwtTokenUserInterceptor.java`](../../../sky-server/src/main/java/com/sky/interceptor/JwtTokenUserInterceptor.java) 的 `preHandle(...)`。

该方法的核心逻辑是：

```java
String token = request.getHeader(jwtProperties.getUserTokenName());
Claims claims = JwtUtil.parseJWT(
        jwtProperties.getUserSecretKey(), token);
Long userId = Long.valueOf(
        claims.get(JwtClaimsConstant.USER_ID).toString());
BaseContext.setCurrentId(userId);
return true;
```

这一段完成三件事：

1. 从 `authentication` 请求头中拿到 Token。
2. 用用户端密钥验签并检查 JWT 是否可解析、是否过期。
3. 从 Claims 中取出 `userId`，存到 [`BaseContext.java`](../../../sky-common/src/main/java/com/sky/context/BaseContext.java) 的 `ThreadLocal`。

之后 Service 不需要让每个方法都显式传递用户 ID，可以通过：

```java
Long userId = BaseContext.getCurrentId();
```

拿到当前请求对应的用户。

如果 Token 不存在、格式错误、签名不匹配或过期，当前拦截器会：

```java
response.setStatus(401);
return false;
```

此时链路在 Controller 前终止，`OrderController`、事务和数据库操作都不会执行。注意，当前实现只设置 HTTP `401`，没有在这里写入统一的 `Result` JSON；这与 Service 抛业务异常后的响应路径不同。

### 5.7 第 4 步：请求 JSON 反序列化为 DTO

拦截器放行后，`RequestMappingHandlerAdapter` 准备调用 Controller 方法。它看到参数：

```java
@RequestBody OrdersSubmitDTO orderSubmitDTO
```

因此会选择能处理 `application/json` 的 `HttpMessageConverter`，读取请求体并使用 Jackson 将 JSON 转换为 `OrdersSubmitDTO` 对象。

项目的 [`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) 将自定义 `JacksonObjectMapper` 放到转换器列表最前面：

```java
MappingJackson2HttpMessageConverter converter =
        new MappingJackson2HttpMessageConverter();
converter.setObjectMapper(new JacksonObjectMapper());
converters.add(0, converter);
```

自定义对象映射器位于 [`JacksonObjectMapper.java`](../../../sky-common/src/main/java/com/sky/json/JacksonObjectMapper.java)，负责 `LocalDateTime`、`LocalDate`、`LocalTime` 等类型的序列化和反序列化规则。`OrdersSubmitDTO` 的 `estimatedDeliveryTime` 还通过 `@JsonFormat` 明确指定了 `yyyy-MM-dd HH:mm:ss` 格式。

如果 JSON 格式不合法、字段类型不匹配或日期格式无法解析，请求会在进入 Controller 前失败。当前全局异常处理器没有专门处理这类参数绑定异常，因此通常由 Spring MVC 返回 `400 Bad Request` 类型的错误响应。

### 5.8 第 5 步：Controller 处理 HTTP 边界

参数转换成功后，Spring 才真正调用：

```java
public Result<OrderSubmitVO> submit(
        OrdersSubmitDTO orderSubmitDTO) {
    log.info("订单提交参数：{}", orderSubmitDTO);
    OrderSubmitVO orderSubmitVO =
            orderService.submit(orderSubmitDTO);
    return Result.success(orderSubmitVO);
}
```

这里的 Controller 只做三件事：

```text
接收已转换好的 DTO
委托 Service 执行业务
将 Service 返回的 VO 包装成统一 Result
```

它不直接编写 SQL，不负责从 Token 读取用户 ID，也不负责手动开启 JDBC 事务。这样 HTTP 细节和订单业务规则不会混在一起。

### 5.9 第 6 步：进入事务代理，而不是直接进入实现类

`OrderController` 注入字段的类型是接口：

```java
@Autowired
private OrderService orderService;
```

因为 [`OrderServiceImpl.java`](../../../sky-server/src/main/java/com/sky/service/impl/OrderServiceImpl.java) 的 `submit(...)` 标有：

```java
@Transactional
public OrderSubmitVO submit(OrdersSubmitDTO orderSubmitDTO) {
    ...
}
```

在 Spring 容器创建 Bean 时，事务基础设施会为它创建代理。因而 Controller 调用的概念链路是：

```text
orderService.submit(dto)
  -> Spring 事务代理
  -> TransactionInterceptor
  -> 真实的 OrderServiceImpl.submit(dto)
```

代理在调用真实方法前，通过事务管理器开启事务；成功返回后提交；方法抛出符合回滚规则的异常时回滚。由于本项目的 `BaseException` 继承 `RuntimeException`，`AddressBookBusinessException`、`ShoppingCartBusinessException`、`OrderBusinessException` 等业务异常默认会触发事务回滚。

事务代理的意义是：`OrderServiceImpl` 只写业务步骤，事务开始、提交和回滚由 Spring 在方法边界统一处理。

### 5.10 第 7 步：Service 编排完整下单业务

真实业务在 `OrderServiceImpl.submit(...)` 中，按顺序执行如下。

#### 7.1 查询并校验收货地址

```java
AddressBook addressBook =
        addressBookMapper.getById(orderSubmitDTO.getAddressBookId());
if (addressBook == null) {
    throw new AddressBookBusinessException(...);
}
```

`AddressBookMapper.getById` 使用注解 SQL：

```java
@Select("select * from address_book where id = #{id}")
AddressBook getById(Long id);
```

MyBatis 将方法参数绑定到 `#{id}`，执行查询，将结果映射为 `AddressBook`。

#### 7.2 调用百度地图校验配送范围

```java
checkOutOfRange(
        addressBook.getCityName()
        + addressBook.getDistrictName()
        + addressBook.getDetail());
```

这个私有方法会调用百度地理编码和路线规划 HTTP 接口，获得店铺到用户地址的距离；超过 `30000` 米就抛出 `OrderBusinessException`。

这一步不是数据库操作，而是第三方远程调用。它仍处于 `@Transactional` 方法范围内。由于前面已经查询过地址，数据库事务可能已经绑定连接，远程调用过慢时会延长事务时间。在生产系统中，常会重新评估事务边界，避免在持有数据库事务期间执行耗时的网络调用。

#### 7.3 从 ThreadLocal 得到当前用户，并查询购物车

```java
Long userId = BaseContext.getCurrentId();
ShoppingCart shoppingCart = new ShoppingCart();
shoppingCart.setUserId(userId);
List<ShoppingCart> shoppingCartList =
        shoppingCartMapper.list(shoppingCart);
```

这里的 `userId` 正是 JWT 拦截器在第 3 步保存的值。`ShoppingCartMapper.list(...)` 对应 [`ShoppingCartMapper.xml`](../../../sky-server/src/main/resources/mapper/ShoppingCartMapper.xml) 中的动态 SQL：

```xml
select * from shopping_cart
<where>
    <if test="userId != null">
        and user_id = #{userId}
    </if>
</where>
```

若购物车为空，Service 抛出 `ShoppingCartBusinessException`，事务代理会回滚，后续订单插入不会发生。

#### 7.4 组装订单主表实体并写入 `orders`

```java
Orders order = new Orders();
BeanUtils.copyProperties(orderSubmitDTO, order);
order.setOrderTime(LocalDateTime.now());
order.setPayStatus(Orders.UN_PAID);
order.setStatus(Orders.PENDING_PAYMENT);
order.setNumber(String.valueOf(System.currentTimeMillis()));
order.setUserId(userId);
order.setPhone(addressBook.getPhone());
order.setConsignee(addressBook.getConsignee());
order.setAddress(...);
orderMapper.insert(order);
```

这里体现 DTO 和 Entity 的边界：客户端传来的地址簿 ID、备注和金额会被复制；订单状态、支付状态、订单号、用户 ID、收货人、电话和完整地址由服务端补充。

`OrderMapper.insert(...)` 对应 [`OrderMapper.xml`](../../../sky-server/src/main/resources/mapper/OrderMapper.xml)：

```xml
<insert id="insert"
        useGeneratedKeys="true"
        keyProperty="id">
    insert into orders (...)
    values (...)
</insert>
```

`useGeneratedKeys="true"` 和 `keyProperty="id"` 表示数据库生成主键后，MyBatis 会把主键回填到 `order.getId()`。后面的订单明细正是依赖这个 ID。

#### 7.5 由购物车生成多条订单明细并批量写入

```java
for (ShoppingCart cart : shoppingCartList) {
    OrderDetail orderDetail = new OrderDetail();
    BeanUtils.copyProperties(cart, orderDetail);
    orderDetail.setOrderId(order.getId());
    orderDetailList.add(orderDetail);
}
orderDetailMapper.insertBatch(orderDetailList);
```

一个订单可以包含多项菜品或套餐，因此 `orders` 是订单主表，`order_detail` 是一对多的明细表。批量插入 SQL 位于 [`OrderDetailMapper.xml`](../../../sky-server/src/main/resources/mapper/OrderDetailMapper.xml)：

```xml
<foreach collection="orderDetailList"
         item="orderDetail" separator=",">
    (...)
</foreach>
```

MyBatis 会将集合展开成一条包含多组 `VALUES` 的 INSERT SQL。

#### 7.6 清空当前用户购物车

```java
shoppingCartMapper.deleteByUserId(userId);
```

它对应 Mapper 中的：

```java
@Delete("delete from shopping_cart where user_id = #{userId}")
void deleteByUserId(Long userId);
```

这一步必须和插入订单、插入订单明细处于同一个事务。否则一旦中途失败，可能出现订单没有创建但购物车已被清空的问题。

#### 7.7 组装返回 VO

```java
OrderSubmitVO orderSubmitVO = OrderSubmitVO.builder()
        .id(order.getId())
        .orderTime(order.getOrderTime())
        .orderNumber(order.getNumber())
        .orderAmount(order.getAmount())
        .build();
return orderSubmitVO;
```

返回 [`OrderSubmitVO.java`](../../../sky-pojo/src/main/java/com/sky/vo/OrderSubmitVO.java)，而不是直接返回 `Orders` 实体。这样可以控制给前端暴露的字段，只返回订单 ID、订单号、金额和下单时间。

### 5.11 第 8 步：提交事务并返回 Controller

如果 `submit(...)` 正常返回，控制权先回到事务代理：

```text
OrderServiceImpl.submit() 返回 OrderSubmitVO
  -> 事务代理提交事务
  -> 返回给 OrderController
```

只有事务提交成功后，Controller 才会得到业务返回值。随后 Controller 调用：

```java
return Result.success(orderSubmitVO);
```

[`Result.java`](../../../sky-common/src/main/java/com/sky/result/Result.java) 会创建统一的响应对象：

```java
Result<T> result = new Result<>();
result.setCode(1);
result.setData(orderSubmitVO);
```

### 5.12 第 9 步：Java 返回对象序列化为 JSON 响应

因为 Controller 使用 `@RestController`，方法返回的 `Result<OrderSubmitVO>` 不会被当成页面名称，而会被 `HttpMessageConverter` 序列化成 JSON。

响应大致是：

```json
{
  "code": 1,
  "data": {
    "id": 10001,
    "orderNumber": "...",
    "orderAmount": 38.00,
    "orderTime": "..."
  }
}
```

实际 JSON 是否包含值为 `null` 的 `msg` 字段，取决于当前 Jackson 配置；`Result.success(...)` 本身只设置了 `code` 和 `data`。

随后响应按相反方向离开：

```text
OrderController 返回 Result<OrderSubmitVO>
  -> HandlerAdapter 处理返回值
  -> MappingJackson2HttpMessageConverter
  -> HttpServletResponse
  -> Tomcat 写回 HTTP 响应
  -> 微信小程序收到 JSON
```

### 5.13 两条失败链路：认证失败与业务失败

成功链路之外，至少要区分下面两类失败，因为它们发生的位置不同。

#### JWT 校验失败：在 Controller 前终止

```text
HTTP 请求
  -> JwtTokenUserInterceptor.preHandle(...)
  -> Token 不存在、过期或验签失败
  -> 设置 HTTP 401
  -> return false
  -> 不进入 Controller，不开启事务，不执行 SQL
```

#### 业务校验或数据库操作失败：事务回滚后进入异常处理器

```text
OrderServiceImpl.submit(...)
  -> 抛出 BaseException 的子类，或出现数据库异常
  -> 事务代理回滚数据库操作
  -> 异常回到 DispatcherServlet
  -> GlobalExceptionHandler 匹配 @ExceptionHandler
  -> Result.error(ex.getMessage())
  -> JSON 错误响应
```

项目的 [`GlobalExceptionHandler.java`](../../../sky-server/src/main/java/com/sky/handler/GlobalExceptionHandler.java) 使用 `@RestControllerAdvice` 统一处理 `BaseException`：

```java
@ExceptionHandler
public Result exceptionHandler(BaseException ex) {
    return Result.error(ex.getMessage());
}
```

因此“购物车为空”这类业务失败通常会以 `code=0` 的统一 JSON 返回；而 JWT 失败当前直接返回 HTTP `401`，二者不是同一套响应路径。

### 5.14 沿着该链路阅读代码的顺序

在 IDE 中按这个顺序跳转，最容易建立完整理解：

1. [`SkyApplication.java`](../../../sky-server/src/main/java/com/sky/SkyApplication.java)：确认应用如何启动、事务功能如何开启。
2. [`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java)：确认 `/user/**` 如何绑定 JWT 拦截器和 JSON 转换器。
3. [`JwtTokenUserInterceptor.java`](../../../sky-server/src/main/java/com/sky/interceptor/JwtTokenUserInterceptor.java)：确认 Token 如何变成 `BaseContext` 中的用户 ID。
4. [`OrderController.java`](../../../sky-server/src/main/java/com/sky/controller/user/OrderController.java)：确认 URL、请求 DTO 和响应 VO。
5. [`OrderServiceImpl.java`](../../../sky-server/src/main/java/com/sky/service/impl/OrderServiceImpl.java)：阅读 `@Transactional submit(...)` 的业务步骤。
6. [`AddressBookMapper.java`](../../../sky-server/src/main/java/com/sky/mapper/AddressBookMapper.java)、[`ShoppingCartMapper.xml`](../../../sky-server/src/main/resources/mapper/ShoppingCartMapper.xml)、[`OrderMapper.xml`](../../../sky-server/src/main/resources/mapper/OrderMapper.xml)、[`OrderDetailMapper.xml`](../../../sky-server/src/main/resources/mapper/OrderDetailMapper.xml)：追踪每次 Mapper 调用实际执行的 SQL。
7. [`GlobalExceptionHandler.java`](../../../sky-server/src/main/java/com/sky/handler/GlobalExceptionHandler.java)：理解异常最终为什么会变成 JSON。

### 5.15 从当前代码中可继续思考的边界

这不是理解基础链路的前提，但沿着链路阅读时应注意以下工程问题：

- `BaseContext` 提供了 `removeCurrentId()`，但当前 JWT 拦截器没有看到 `afterCompletion` 清理逻辑。在线程池复用场景应在请求结束后清理 ThreadLocal。
- 收货地址查询只按 `id` 查询。生产代码通常还要校验该地址是否属于 `BaseContext` 中的当前用户。
- 订单金额由 DTO 复制到 `Orders`。生产系统通常应按服务端菜品价格和数量重新计算金额，不能完全信任客户端传入的 `amount`。
- 订单号使用当前毫秒时间生成，高并发下可能重复；应使用更可靠的全局 ID 或数据库唯一约束与重试策略。
- 配送范围的远程地图调用位于事务范围内，网络慢时可能拉长事务时间；应评估事务边界和超时策略。

## 6. 从项目中学习什么

- Spring Boot 并不是替代 Spring，而是简化 Spring 的装配过程。
- 引入 Starter 依赖后，相关自动配置才可能生效。
- `application.yml` 是运行时配置，Java 配置类负责将配置转成 Bean 或规则。
- 应用启动成功不等于所有外部服务可用；MySQL、Redis、微信证书等仍需要正确配置。
- 应用级开关通常放在主配置类或独立的 `@Configuration` 类上，具体业务行为放在方法上，例如 `@EnableCaching` 和 `@Cacheable`。
- `@Transactional`、`@Cacheable` 和自定义 `@Aspect` 通常依赖 Spring 代理，因此类内部直接调用自己的方法时，可能绕过代理而导致增强不生效。
- 当前项目存在 `@Scheduled` 定时任务，但源码中没有明显看到 `@EnableScheduling`，应实际验证定时任务是否被 Spring 调度。

## 7. 学习练习

在 `SkyApplication` 上分别观察去掉 `@EnableCaching` 或 `@EnableTransactionManagement` 后，缓存注解和事务注解会有什么变化。再查看 `sky-server/pom.xml`，把依赖和自动配置建立联系。

进一步练习：

1. 把 `@EnableCaching` 和 `@EnableTransactionManagement` 移到 `com.sky.config` 下的独立 `@Configuration` 类，确认应用仍能启动。
2. 把 `SkyApplication` 移到子包中，观察哪些组件无法被扫描，再使用 `scanBasePackages` 修复。
3. 在 `SpringApplication.run` 返回值上设置断点，观察 ApplicationContext 中的 Bean。
4. 为 `OrderServiceImpl.submit` 人为抛出异常，验证事务代理是否回滚订单相关操作。
