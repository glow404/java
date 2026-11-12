# 苍穹外卖：项目结构与 Java 后端学习指南

> 目标：以当前仓库为案例，理解一个典型 Java Web 后端从“请求进入”到“数据落库、响应返回”的完整链路。
>
> 本文基于当前仓库中的实际文件整理。它不只说明目录名称，还说明每层为什么存在、代码应如何阅读，以及可以从中学习哪些后端知识点。

> 配套的分主题学习文档： [Java 后端知识点学习路线](java-backend-knowledge/00-学习路线总览.md)。

## 1. 项目是什么

这是一个餐饮外卖系统，包含两类业务使用者：

- 管理端员工：管理员工、分类、菜品、套餐、订单、营业数据和店铺状态。
- 用户端消费者：微信登录、浏览菜品、维护地址、操作购物车、下单、支付、查看历史订单和催单。

后端采用 Spring Boot 单体应用。它不是微服务架构：所有业务模块由同一个 `sky-server` 进程提供，只是通过包和 Maven 模块进行职责划分。

核心技术栈：

| 领域 | 技术 |
| --- | --- |
| 语言与构建 | Java 17、Maven 多模块 |
| Web | Spring Boot、Spring MVC |
| 持久化 | MyBatis、MySQL、PageHelper |
| 缓存 | Redis、Spring Cache |
| 鉴权 | JWT |
| 文件与第三方服务 | 阿里云 OSS、微信登录/支付、百度地图 |
| 实时与后台任务 | WebSocket、Spring `@Scheduled` |
| 接口与报表 | springdoc OpenAPI、Apache POI |

## 2. 顶层目录与模块依赖

```text
sky-take-out-main/
├── pom.xml                         Maven 父工程与统一版本管理
├── sky-common/                     通用基础能力
├── sky-pojo/                       DTO、实体、VO 等数据模型
├── sky-server/                     可运行的 Spring Boot 后端服务
├── assert/                         README 使用的界面截图与架构图
├── learn/                          本项目的学习资料目录
├── README.md                       项目说明
└── SECURITY.md                     安全说明模板
```

Maven 模块依赖关系如下：

```text
                    ┌──────────────┐
                    │ sky-common   │
                    │ 工具/异常等  │
                    └──────┬───────┘
                           │
┌──────────────┐           │           ┌──────────────┐
│ sky-pojo     │───────────┼──────────▶│ sky-server   │
│ DTO/Entity/VO│           │           │ 可运行服务   │
└──────────────┘                       └──────────────┘
```

根目录的 [pom.xml](../pom.xml) 是聚合工程：定义 Java 版本、Spring Boot 版本和依赖版本，并声明三个 Maven 模块。`sky-server` 依赖另外两个模块，是最终打包和启动的模块。

## 3. 每个 Maven 模块的职责

### 3.1 `sky-common`：横切的公共能力

位置：[`sky-common`](../sky-common)

```text
sky-common/src/main/java/com/sky/
├── constant/       常量
├── context/        当前请求上下文
├── enumeration/    枚举
├── exception/      业务异常体系
├── json/           JSON 序列化规则
├── properties/     配置绑定对象
├── result/         统一响应对象
└── utils/          JWT、HTTP、OSS、支付工具
```

这个模块不实现“菜品”或“订单”业务，而是放多个业务都会使用的代码。

| 目录 | 代表文件 | 作用 | 可学习点 |
| --- | --- | --- | --- |
| `result` | [`Result.java`](../sky-common/src/main/java/com/sky/result/Result.java) | 统一包装接口返回值，成功使用 `code=1`，失败使用 `code=0` | 统一 API 响应设计、泛型 |
| `context` | [`BaseContext.java`](../sky-common/src/main/java/com/sky/context/BaseContext.java) | 用 `ThreadLocal<Long>` 保存当前用户或员工 ID | 线程隔离、请求上下文 |
| `exception` | `BaseException` 及各业务异常 | 让业务层用异常表达失败原因 | 自定义异常、全局异常处理 |
| `properties` | `JwtProperties`、`AliOssProperties`、`WeChatProperties` | 将 YAML 配置绑定为 Java 对象 | `@ConfigurationProperties` |
| `utils` | `JwtUtil`、`AliOssUtil`、`HttpClientUtil`、`WeChatPayUtil` | 封装第三方协议或 SDK 调用 | 工具封装、第三方接口边界 |
| `json` | `JacksonObjectMapper` | 统一时间等数据的 JSON 格式 | HTTP 消息转换 |

### 3.2 `sky-pojo`：业务数据契约

位置：[`sky-pojo`](../sky-pojo)

```text
sky-pojo/src/main/java/com/sky/
├── dto/            接收请求的数据对象
├── entity/         与数据库表对应的实体对象
└── vo/             返回给前端的视图对象
```

这三个概念要刻意区分，它们是理解分层后端代码的关键。

| 类型 | 使用位置 | 例子 | 为什么不直接混用 |
| --- | --- | --- | --- |
| DTO | Controller 入参 | `OrdersSubmitDTO`、`EmployeeDTO` | 前端只传本次操作需要的字段，避免绑定数据库全部字段 |
| Entity | Service/Mapper/数据库 | `Orders`、`Dish`、`Employee` | 表达数据表结构和持久化字段 |
| VO | Controller 返回值 | `OrderVO`、`DishVO`、`BusinessDataVO` | 返回面向页面的数据，可组合多个实体字段，也可隐藏密码等敏感字段 |

以订单为例：

```text
小程序提交 JSON
      │
      ▼
OrdersSubmitDTO                 只包含地址、金额、备注、送达时间等请求字段
      │
      ▼
Orders                          补全用户、订单号、状态、下单时间后写入 orders 表
      │
      ▼
OrderSubmitVO / OrderVO         返回订单号、金额、订单详情等页面所需数据
```

建议先阅读 [`OrdersSubmitDTO.java`](../sky-pojo/src/main/java/com/sky/dto/OrdersSubmitDTO.java)、[`Orders.java`](../sky-pojo/src/main/java/com/sky/entity/Orders.java) 和 [`OrderVO.java`](../sky-pojo/src/main/java/com/sky/vo/OrderVO.java)，观察同一份业务信息在不同边界上的不同形态。

### 3.3 `sky-server`：真正启动的业务服务

位置：[`sky-server`](../sky-server)

```text
sky-server/src/main/
├── java/com/sky/
│   ├── SkyApplication.java      启动入口
│   ├── controller/              HTTP 接口层
│   ├── service/                 业务层接口与实现
│   ├── mapper/                  数据访问接口
│   ├── config/                  Spring 配置
│   ├── interceptor/             请求拦截器
│   ├── aspect/                  AOP 切面
│   ├── handler/                 全局异常处理
│   ├── task/                    定时任务
│   └── websocket/               WebSocket 服务
└── resources/
    ├── application.yml          基础配置
    ├── application-dev.yml      开发环境配置
    ├── mapper/                  MyBatis XML SQL
    ├── template/                Excel 报表模板
    └── mp-weixin/               微信小程序编译产物
```

[`SkyApplication.java`](../sky-server/src/main/java/com/sky/SkyApplication.java) 是唯一的启动入口。它通过 `@SpringBootApplication` 启动 Spring 容器，并开启事务管理和缓存支持。

### 3.4 `resources/mp-weixin`：用户端小程序产物

位置：[`sky-server/src/main/resources/mp-weixin`](../sky-server/src/main/resources/mp-weixin)

```text
sky-server/src/main/resources/mp-weixin/
├── pages/             首页、订单、地址、支付、个人中心等页面
├── common/            runtime、vendor、请求封装等公共编译文件
├── static/            图片资源
├── uni_modules/       UniApp 组件编译文件
├── app.js
├── app.json
└── project.config.json
```

它是由 UniApp 构建出的微信小程序代码，不是常见的 Vue 源码目录：目前仓库中没有对应的 `.vue` 源文件和 `package.json`。`common/vendor.js` 中可看到接口请求与基础地址，例如 `http://localhost:8080`。

当前仓库只保留这一份小程序构建产物。学习后端时应把它看作接口调用方；若要维护小程序，应该找到原始 UniApp 工程，而不是直接长期维护生成的代码。

### 3.5 `assert`：说明文档素材

位置：[`assert`](../assert)

其中包含项目架构图、工作台、订单管理、菜品管理等截图，主要被 README 引用，不参与后端运行。

## 4. 后端分层：一次请求是如何流动的

典型请求路径：

```text
客户端（管理端/微信小程序）
        │ HTTP 请求
        ▼
Spring MVC DispatcherServlet
        ▼
JWT Interceptor                校验身份，写入 BaseContext
        ▼
Controller                     参数接收、调用业务、包装 Result
        ▼
Service / ServiceImpl          业务规则、事务、跨表操作
        ▼
Mapper + Mapper XML            组织 SQL
        ▼
MySQL / Redis / OSS / 微信等外部服务
        │
        ▼
Result<T> JSON 响应
```

这条链路是学习本项目最重要的主线。遇到任意接口时，都可以按下面的顺序追踪：

1. 先找到 Controller 的 URL 映射。
2. 查看它接收的 DTO 和返回的 VO。
3. 进入 Service 接口和 `impl` 实现类，看业务判断、事务和协作对象。
4. 找到对应 Mapper 方法。
5. 查看注解 SQL 或 `resources/mapper/*.xml` 中的 SQL。
6. 回到 Entity，理解表字段和对象字段如何映射。

## 5. 各层的具体职责

### 5.1 Controller：HTTP 边界

位置：[`sky-server/src/main/java/com/sky/controller`](../sky-server/src/main/java/com/sky/controller)

Controller 被按角色拆分：

| 包 | URL 前缀 | 代表功能 |
| --- | --- | --- |
| `controller.admin` | `/admin/**` | 员工、菜品、套餐、订单、报表、工作台、上传、店铺状态 |
| `controller.user` | `/user/**` | 微信登录、地址、菜品、套餐、购物车、下单、支付、历史订单 |
| `controller.nofity` | `/notify/**` | 微信支付回调；目录名 `nofity` 是拼写问题 |

Controller 应保持薄：它主要做参数绑定、调用 Service 和返回 `Result`，不应承载复杂 SQL 或大段业务规则。

例如 [`user/OrderController.java`](../sky-server/src/main/java/com/sky/controller/user/OrderController.java) 暴露用户下单、支付、取消、再来一单和催单接口；真正的订单创建逻辑在 `OrderServiceImpl`。

常见 Spring MVC 注解及其含义：

| 注解 | 作用 |
| --- | --- |
| `@RestController` | 返回 JSON，而不是跳转视图 |
| `@RequestMapping` | 定义类级 URL 前缀 |
| `@GetMapping`、`@PostMapping`、`@PutMapping`、`@DeleteMapping` | 定义 HTTP 方法和具体路由 |
| `@RequestBody` | 将 JSON 请求体绑定到 DTO |
| `@PathVariable` | 读取 URL 路径参数 |
| `@RequestParam` | 读取查询参数 |
| `@Operation`、`@Tag` | 生成 OpenAPI 接口文档 |

### 5.2 Service：业务规则和事务边界

位置：[`sky-server/src/main/java/com/sky/service`](../sky-server/src/main/java/com/sky/service)

每类核心业务通常有一对接口和实现：

```text
OrderService.java
└── impl/OrderServiceImpl.java
```

接口描述“系统能做什么”，实现类描述“这件事怎么做”。这种设计便于测试、替换实现和降低 Controller 与具体实现的耦合。

主要业务服务：

| 服务 | 负责内容 |
| --- | --- |
| `EmployeeService` | 管理员登录、员工 CRUD、启停、修改密码 |
| `CategoryService` | 菜品/套餐分类管理 |
| `DishService` | 菜品及口味管理 |
| `SetmealService` | 套餐及套餐菜品关联管理 |
| `ShoppingCartService` | 购物车加减、查询、清空 |
| `AddressBookService` | 用户地址和默认地址 |
| `UserService` | 微信授权登录和用户创建 |
| `OrderService` | 下单、支付、订单状态流转、催单、再来一单 |
| `WorkspaceService` | 管理端工作台汇总数据 |
| `ReportService` | 营业额、用户、订单、销量统计和 Excel 导出 |

`@Transactional` 出现在订单、菜品和套餐等跨多表操作上。它意味着操作中的任一步失败，相关数据库修改应整体回滚。

例如下单需要同时创建订单、创建多条订单明细、清空购物车；这三个动作必须在一个事务中完成，否则会出现“订单有了但明细缺失”或“购物车被清空但订单失败”的数据不一致问题。

### 5.3 Mapper：数据库访问边界

位置：

- Java 接口：[`sky-server/src/main/java/com/sky/mapper`](../sky-server/src/main/java/com/sky/mapper)
- XML SQL：[`sky-server/src/main/resources/mapper`](../sky-server/src/main/resources/mapper)

Mapper 负责定义“对哪些表做什么操作”，不负责业务规则。

项目混用了两种 MyBatis SQL 写法：

- 简单 SQL 写在 Mapper 方法注解中，例如 `@Select`、`@Insert`、`@Update`。
- 动态查询、批量操作和复杂关联查询写在 XML 中，例如 `OrderMapper.xml`、`DishMapper.xml`。

以员工分页查询为例：`EmployeeController` 接收 `EmployeePageQueryDTO`，`EmployeeServiceImpl` 调用 PageHelper，再由 `EmployeeMapper.pageQuery` 执行 XML 中带条件的 SQL，最终包装成 `PageResult`。

这能帮助你理解：分页插件并没有改变业务层的写法，而是在 Mapper SQL 执行前后附加分页逻辑和总数查询。

### 5.4 配置、拦截器、切面：不属于单一业务的规则

这部分通常被称为横切关注点：它们会影响多个接口或多个业务模块。

| 类别 | 代表文件 | 作用 |
| --- | --- | --- |
| MVC 配置 | [`WebMvcConfiguration.java`](../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) | 注册拦截器和 JSON 转换器 |
| JWT 拦截器 | `JwtTokenAdminInterceptor`、`JwtTokenUserInterceptor` | 在进入 Controller 前鉴权 |
| AOP 切面 | [`AutoFIllAspect.java`](../sky-server/src/main/java/com/sky/aspect/AutoFIllAspect.java) | 自动填充创建/更新时间和创建/更新人 |
| 全局异常处理 | [`GlobalExceptionHandler.java`](../sky-server/src/main/java/com/sky/handler/GlobalExceptionHandler.java) | 将业务异常转换为统一 `Result` |
| Redis 配置 | `RedisConfiguration` | 创建 `RedisTemplate` 并配置 Key 序列化 |
| OpenAPI 配置 | `OpenApiConfiguration` | 按 `/admin/**` 和 `/user/**` 分组接口文档 |
| OSS 配置 | `OssConfiguration` | 将 OSS 配置组装为上传工具 |

## 6. 身份认证：JWT 如何贯穿请求

系统为管理端和用户端使用不同的 JWT 配置与请求头。

```text
管理端登录
/admin/employee/login
        │
        ▼
EmployeeService 查询员工、校验密码和状态
        │
        ▼
生成带 EMP_ID 的 JWT
        │
        ▼
客户端后续请求携带 token 请求头

用户端登录
/user/user/login
        │
        ▼
UserService 调用微信 jscode2session，获得 openid
        │
        ▼
查询或创建 user
        │
        ▼
生成带 USER_ID 的 JWT
        │
        ▼
客户端后续请求携带 authentication 请求头
```

在 [`WebMvcConfiguration.java`](../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) 中：

- `/admin/**` 会经过管理员 JWT 拦截器，登录接口除外。
- `/user/**` 会经过用户 JWT 拦截器，用户登录和获取营业状态接口除外。

拦截器解析 JWT 后，将 ID 写入 `BaseContext`。Service 不必层层传递 `userId` 或 `empId`，可以直接调用：

```java
Long currentId = BaseContext.getCurrentId();
```

这是一种常见而方便的设计，但也要理解其边界：`ThreadLocal` 必须与当前请求线程正确对应，并在请求结束后清理，才能避免在线程池环境中残留旧数据。

## 7. 订单主链路：最值得精读的案例

建议以 [`OrderServiceImpl.java`](../sky-server/src/main/java/com/sky/service/impl/OrderServiceImpl.java) 为主线阅读，因为它集中体现了真实业务代码的常见复杂度。

### 7.1 用户下单

入口：`POST /user/order/submit`

```text
OrderController.submit
        │
        ▼
OrderServiceImpl.submit  @Transactional
        │
        ├── 查询收货地址，校验地址存在
        ├── 调用百度地图接口校验配送范围
        ├── 从 BaseContext 获取当前用户 ID
        ├── 查询购物车，校验购物车非空
        ├── 创建 orders 记录
        ├── 根据购物车创建多条 order_detail 记录
        ├── 清空当前用户购物车
        └── 返回 OrderSubmitVO
```

这里能学习到：

- 多表写入为什么需要事务。
- 如何从 DTO 转换为 Entity。
- 如何将一个集合映射为多条订单明细。
- 如何通过业务异常中断流程。
- 如何调用第三方 HTTP 服务并解析 JSON。

### 7.2 支付与来单提醒

入口：`PUT /user/order/payment`

订单支付涉及 `WeChatPayUtil` 和 `/notify/paySuccess` 支付回调接口。支付成功后，订单状态会流转，同时通过 [`WebSocketServer.java`](../sky-server/src/main/java/com/sky/websocket/WebSocketServer.java) 向已连接的管理端客户端推送来单或催单消息。

当前代码在支付接口中还直接调用了 `orderService.paySuccess(...)`，这方便本地模拟支付；真实生产环境通常应以微信的异步回调作为最终支付成功依据，并处理重复回调和幂等性。

### 7.3 管理端处理订单

管理端订单接口位于 [`controller/admin/OrderController.java`](../sky-server/src/main/java/com/sky/controller/admin/OrderController.java)，包含：

- 条件分页查询
- 各状态订单数量统计
- 查看详情
- 接单
- 拒单
- 取消
- 派送
- 完成

订单状态定义在 [`Orders.java`](../sky-pojo/src/main/java/com/sky/entity/Orders.java) 中：待支付、待接单、已接单、派送中、已完成、已取消。阅读这些状态切换时，应特别关注“哪些状态允许变成哪些状态”，这就是业务状态机思想。

## 8. 主要业务数据关系

虽然当前仓库没有提供建表 SQL，仍可以从 Entity、Mapper 和 XML 推导核心关系：

```text
Category
  ├── Dish
  │     └── DishFlavor
  └── Setmeal
        └── SetmealDish ──── Dish

User
  ├── AddressBook
  ├── ShoppingCart
  └── Orders
        └── OrderDetail

Employee
  └── 创建/修改分类、菜品、套餐等后台数据
```

学习时可将 Entity 名称对应到数据库表：

| Entity | 可能对应的表 | 说明 |
| --- | --- | --- |
| `Category` | `category` | 菜品或套餐分类 |
| `Dish` | `dish` | 菜品基本信息 |
| `DishFlavor` | `dish_flavor` | 菜品口味选项 |
| `Setmeal` | `setmeal` | 套餐基本信息 |
| `SetmealDish` | `setmeal_dish` | 套餐和菜品的关联表 |
| `User` | `user` | 微信用户 |
| `AddressBook` | `address_book` | 收货地址 |
| `ShoppingCart` | `shopping_cart` | 当前购物车条目 |
| `Orders` | `orders` | 订单主表 |
| `OrderDetail` | `order_detail` | 订单明细表 |
| `Employee` | `employee` | 管理端员工 |

## 9. 缓存、文件、报表与实时能力

### 9.1 Redis 缓存

项目存在两种缓存风格：

- 菜品：直接使用 `RedisTemplate`，按 `dish_<categoryId>` 作为 Key 手动读写和删除。
- 套餐：使用 Spring Cache 注解，例如用户端 `@Cacheable`，管理端修改套餐时用 `@CacheEvict` 清理缓存。

对应文件：

- [`controller/user/DishController.java`](../sky-server/src/main/java/com/sky/controller/user/DishController.java)
- [`controller/admin/DishController.java`](../sky-server/src/main/java/com/sky/controller/admin/DishController.java)
- [`controller/user/SetmealController.java`](../sky-server/src/main/java/com/sky/controller/user/SetmealController.java)
- [`controller/admin/SetmealController.java`](../sky-server/src/main/java/com/sky/controller/admin/SetmealController.java)

这两个实现可作为对比案例：前者让你理解 Redis 的显式操作，后者让你理解框架注解如何减少模板代码。

### 9.2 阿里云 OSS 文件上传

管理端上传接口在 [`CommonController.java`](../sky-server/src/main/java/com/sky/controller/admin/CommonController.java)。它接收 `MultipartFile`，生成随机文件名，调用 `AliOssUtil` 上传并返回访问地址。

学习点：`multipart/form-data`、文件扩展名处理、对象存储与数据库中保存 URL 的常见模式。

### 9.3 报表导出

[`ReportController.java`](../sky-server/src/main/java/com/sky/controller/admin/ReportController.java) 提供营业额、用户数、订单统计、销量 Top 10 和 Excel 导出接口。Excel 模板位于 `resources/template/运营数据报表模板.xlsx`，报表生成使用 Apache POI。

### 9.4 WebSocket

`WebSocketServer` 使用 `@ServerEndpoint("/ws/{sid}")` 保存连接会话，并支持向全部客户端群发。订单支付成功和用户催单时，业务层会推送 JSON 消息给管理端。

这与普通 HTTP 的区别是：HTTP 通常由客户端发起，而 WebSocket 允许服务端主动推送。

### 9.5 定时任务

`task/OrderTask.java` 中定义了：

- 超时未支付订单自动取消。
- 配送中订单超时自动完成。

`WebSocketTask.java` 还会周期性向客户端发送测试消息。

注意：当前启动类和配置类中未找到 `@EnableScheduling`。在标准 Spring Boot 配置中，只有加入该注解后 `@Scheduled` 方法才会被调度；这是一个很好的排查练习点。

## 10. 配置文件如何工作

配置文件位于：

- [`application.yml`](../sky-server/src/main/resources/application.yml)
- [`application-dev.yml`](../sky-server/src/main/resources/application-dev.yml)

基础配置启用了 `dev` Profile，因此运行时会合并两份 YAML。配置涵盖：

```text
server.port
spring.datasource
spring.redis
mybatis
sky.jwt
sky.alioss
sky.wechat
sky.shop
sky.baidu
```

配置到 Java 类的路径是：

```text
application.yml / application-dev.yml
        │
        ▼
@ConfigurationProperties
        │
        ├── JwtProperties
        ├── AliOssProperties
        └── WeChatProperties
        │
        ▼
Controller / Service / Util 使用配置
```

开发环境至少需要可访问的 MySQL 和 Redis。数据库用户名和密码通过 `SKY_DB_USERNAME`、`SKY_DB_PASSWORD` 环境变量读取。

配置文件涉及 OSS、微信支付和地图服务等敏感信息。真实项目中应避免把密钥、支付证书路径和外网回调地址直接提交到仓库；更合适的做法是使用环境变量、密钥管理服务或不提交的本地 Profile 文件。

## 11. 推荐阅读顺序

不要一开始就逐行读完整个 `OrderServiceImpl`。按下面顺序会更容易建立全局认识：

1. 阅读根目录 [pom.xml](../pom.xml)，理解 Maven 父工程和三个模块。
2. 阅读 [`SkyApplication.java`](../sky-server/src/main/java/com/sky/SkyApplication.java)，了解服务如何启动。
3. 阅读两份 YAML，认识数据库、Redis、JWT 和第三方服务配置。
4. 阅读 [`WebMvcConfiguration.java`](../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java)、两个 JWT 拦截器和 `BaseContext`，理解请求身份如何传入业务层。
5. 选员工登录：`EmployeeController -> EmployeeServiceImpl -> EmployeeMapper`，练习一条较短的完整链路。
6. 选菜品分页：练习 DTO、PageHelper、Mapper XML、VO 的协作。
7. 选用户下单：阅读 `OrderController -> OrderServiceImpl -> OrderMapper/OrderDetailMapper`，理解事务和多表操作。
8. 阅读 Redis 菜品缓存和套餐缓存，对比手写缓存与注解缓存。
9. 最后阅读报表、OSS、微信支付、WebSocket 和定时任务等扩展能力。

## 12. 建议动手练习

以下练习按由浅入深排列，适合用这个仓库训练后端思维：

| 练习 | 目标 | 涉及知识 |
| --- | --- | --- |
| 为分类增加一个查询接口 | 按分层写完整 CRUD | Controller、DTO、Service、Mapper、XML |
| 给员工接口补充参数校验 | 拒绝非法请求 | Bean Validation、全局异常处理 |
| 为菜品查询增加缓存过期策略 | 控制缓存一致性与时效 | Redis、TTL、缓存失效 |
| 为订单状态增加状态转移校验 | 防止非法状态更新 | 状态机、领域规则 |
| 为支付回调做幂等控制 | 避免重复更新订单 | 幂等、数据库条件更新、分布式思维 |
| 给订单提交写单元/集成测试 | 验证事务与异常分支 | JUnit、MockMvc、测试数据库 |
| 将密码 MD5 改为 BCrypt | 改善安全性 | 密码哈希、Spring Security 的 `PasswordEncoder` |

## 13. 当前仓库中值得带着问题观察的地方

这些内容不是要求立刻修改，而是很适合在学习时追问“生产项目应该怎样做得更稳妥”。

- 当前仓库未看到 `src/test` 下的测试代码，且 `.gitignore` 忽略了测试目录和 `*Test.java`；测试实践值得补上。
- 员工密码使用 MD5。它适合学习基础流程，但生产系统应使用 BCrypt、Argon2 等专用密码哈希算法，并配合盐值。
- 小程序目录是编译产物，而且其中可见部分历史接口路径；修改接口时应以服务端 Controller 为准，再确认小程序原始源码是否同步。
- 数据库建表 SQL 未随当前仓库提供。学习数据模型时可以从 Entity、Mapper 和 XML 反推，也建议自行补一份可重复初始化的 schema/migration。
- 定时任务代码需要确认是否启用了 `@EnableScheduling`。
- `BaseContext` 使用 `ThreadLocal`，应关注请求结束后的清理时机。
- `controller/nofity` 目录名拼写应为 `notify`，属于可改进的命名一致性问题。

## 14. 最终心智模型

学习这个项目时，把每个功能都归入下面这句话：

> Controller 定义 HTTP 边界，Service 执行业务规则，Mapper 读写数据，DTO/Entity/VO 管理数据在不同边界的形态，配置、拦截器、切面和异常处理为所有业务提供统一规则。

当你能从任意一个 URL 一直追到 SQL，再解释结果如何被包装回前端时，就已经掌握了这个项目最核心的 Java 后端开发逻辑。
