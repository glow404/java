# 苍穹外卖 Day01-Day12 技术总结：结合项目源码的新手教程

> 这是一份按课程 Day01 到 Day12 重新编排的主线教程。每个知识点都尽量回答三件事：它解决什么问题、当前仓库在哪里实现、阅读代码时应该怎样思考。
>
> 文档中的“当前代码”指本仓库实际存在的实现；“生产建议”用于帮助你理解示例代码和真实系统之间的差距。不要把生产建议误认为仓库已经实现的功能。

## 0. 先建立一张地图

### 0.1 这个项目做什么

苍穹外卖是一个餐饮外卖系统，分成两类使用者：

- 管理端员工：登录后台，维护分类、菜品、套餐、员工，处理订单，查看营业数据。
- 用户端消费者：通过微信小程序登录，浏览菜品，维护地址，加入购物车，下单、支付、查看历史订单和催单。

后端不是微服务，而是一个 Spring Boot 单体应用。所有业务最终由一个 `sky-server` 进程提供 HTTP 服务；`sky-common` 和 `sky-pojo` 是被它依赖的类库模块。

### 0.2 先记住一次请求的主线

```text
浏览器 / 微信小程序
        |
        | HTTP 请求
        v
Nginx（部署时可选，负责静态资源和反向代理）
        |
        v
Spring MVC DispatcherServlet
        |
        v
JWT 拦截器（登录接口除外）
        |
        v
Controller：接收参数、调用业务、返回 Result
        |
        v
Service：校验规则、事务、跨表编排
        |
        +--> Mapper + MyBatis XML --> MySQL
        +--> Redis / OSS / 微信 / 百度地图
        |
        v
Jackson 把 Result 转成 JSON
        |
        v
客户端
```

以后遇到任何功能，都按这个顺序追代码：

1. 先找 URL 对应的 Controller 方法。
2. 看它接收的 DTO 和返回的 VO。
3. 进入 Service 接口及实现，找校验、事务和业务步骤。
4. 找 Mapper 方法，再看注解 SQL 或 `resources/mapper/*.xml`。
5. 回到 Entity，理解字段和数据库表的关系。
6. 最后观察异常如何处理、对象如何序列化成 JSON。

### 0.3 建议的阅读工具

- IntelliJ IDEA：打开工程、运行、断点调试和跳转定义。
- Maven：下载依赖、编译和打包。
- MySQL 客户端：查看表和数据。
- Redis 客户端：查看缓存 Key 和 TTL。
- Postman 或 Swagger UI：模拟 HTTP 请求。
- 微信开发者工具：导入小程序编译产物并查看页面请求。
- Git：保存每个学习阶段的可恢复版本。

本仓库已有更细的专题，可作为本篇的参考章节：[Java 后端知识点学习路线](java-backend-knowledge-ai/00-学习路线总览.md) 和 [项目结构与 Java 后端学习指南](项目结构与Java后端学习指南.md)。

---

# 一、Day01 技术总结

## 1. 软件开发整体介绍

### 1.1 软件开发流程

软件开发不是“写完代码就结束”，而是一条持续循环的流程：

```text
需求分析 -> 原型/交互 -> 技术设计 -> 编码 -> 联调 -> 测试
     ^                                           |
     |                                           v
     +----------- 发布、监控、反馈、迭代 <--------+
```

各阶段的输出大致如下：

| 阶段 | 要回答的问题 | 在苍穹外卖中的例子 |
| --- | --- | --- |
| 需求分析 | 用户要完成什么事情？ | 用户能否把购物车提交成订单？ |
| 交互/原型 | 页面如何操作？ | 订单页需要地址、备注、金额和提交按钮 |
| 技术设计 | 数据如何保存，接口如何定义？ | `orders` 主表、`order_detail` 明细表、`POST /user/order/submit` |
| 编码 | 用什么类和方法实现？ | `OrderController` 调用 `OrderServiceImpl.submit` |
| 联调 | 前后端字段和 URL 是否一致？ | 小程序传 `addressBookId`，后端 DTO 能否接收 |
| 测试 | 正常和异常情况是否都正确？ | 空购物车、地址不存在、超出配送范围 |
| 发布 | 如何让用户访问？ | 打包 Jar，Nginx 代理到 8080 端口 |
| 运维 | 出问题怎么发现和恢复？ | 日志、数据库备份、Redis 监控、回滚 |

初学时最容易只关注“功能成功”，但真正的后端工作还要考虑失败：网络超时、参数缺失、重复请求、数据库约束、权限和数据一致性。

### 1.2 角色分工

不同公司叫法会不同，但常见职责可以这样理解：

- 产品经理：描述业务目标、流程和规则。
- UI/交互设计师：设计页面视觉和交互。
- 前端工程师：把页面做出来，调用后端接口。
- 后端工程师：设计接口、业务、数据库和第三方服务。
- 测试工程师：验证功能、兼容性、性能和异常场景。
- 运维/平台工程师：负责部署、监控、网络、备份和发布。
- 项目负责人：协调范围、进度和风险。

在小团队中一个人可能承担多个角色。代码中的分层不是按“人”分，而是按职责分：Controller 是 HTTP 边界，Service 是业务，Mapper 是数据访问。

### 1.3 软件环境

“环境”是代码运行所依赖的一组条件。至少区分：

- 开发环境：自己的电脑，便于断点和修改。
- 测试环境：供测试和联调，数据与生产隔离。
- 生产环境：真正的用户流量，要求安全、稳定、可监控。

本项目后端的主要版本在根目录 [`pom.xml`](../pom.xml) 中：Java 17、Spring Boot 3.5.13、MyBatis Spring 3.0.5、PageHelper 6.1.1、Springdoc 2.8.17、Apache POI 5.4.1。`sky-server/pom.xml` 再引入 Web、MySQL、Redis、Cache、WebSocket 和 POI 等运行依赖。

环境还包括外部服务：

```text
JDK 17 + Maven
       |
       +--> MySQL（业务数据）
       +--> Redis（缓存/店铺状态）
       +--> 微信接口（小程序登录/支付）
       +--> 百度地图（配送范围）
       +--> 阿里云 OSS（图片文件）
```

---

## 2. 苍穹外卖项目介绍

README 中的功能架构图和截图在 [`README.md`](../README.md) 与 [`assert`](../assert) 目录。核心业务关系可以从实体类推出来：

```text
Category
  +-- Dish ------ DishFlavor
  +-- Setmeal -- SetmealDish ---- Dish

User
  +-- AddressBook
  +-- ShoppingCart
  +-- Orders ------ OrderDetail

Employee
  +-- 创建/修改分类、菜品、套餐和订单
```

关键模块：

| 模块 | 目录 | 学习重点 |
| --- | --- | --- |
| 公共模块 | `sky-common` | 结果、异常、JWT、配置属性、工具类 |
| 数据模型 | `sky-pojo` | DTO、Entity、VO、Lombok |
| 服务模块 | `sky-server` | Controller、Service、Mapper、配置、拦截器 |
| 小程序产物 | `sky-server/src/main/resources/mp-weixin` | 页面、请求封装和静态资源 |
| 接口文档 | `项目接口文档` | 管理端和用户端 HTML 接口说明 |

一个最适合入门的短链路是“员工登录”：

```text
POST /admin/employee/login
  -> EmployeeController.login
  -> EmployeeServiceImpl.login
  -> EmployeeMapper.getByUsername
  -> employee 表
  -> JwtUtil.createJWT
  -> EmployeeLoginVO
  -> Result JSON
```

一个最能体现综合能力的长链路是“用户下单”：它同时包含 JWT、ThreadLocal、DTO/Entity、事务、第三方地图、跨表写入、订单状态和异常处理。

---

## 3. 前端环境搭建

### 3.1 当前仓库的前端现状

仓库没有管理端 Vue 源码，也没有小程序原始 UniApp 的 `.vue` 文件和 `package.json`。当前保留的是已经编译出来的微信小程序目录：

[`sky-server/src/main/resources/mp-weixin`](../sky-server/src/main/resources/mp-weixin)

其中常见文件含义：

| 文件/目录 | 作用 |
| --- | --- |
| `app.json` | 小程序全局页面、窗口和分包配置 |
| `app.js` | 小程序启动脚本 |
| `app.wxss` | 全局样式 |
| `pages/` | 首页、订单、地址、支付等页面编译文件 |
| `components/` | 可复用组件 |
| `static/` | 图片等静态资源 |
| `common/vendor.js` | 打包后的依赖、请求封装和业务模块 |
| `project.config.json` | 微信开发者工具项目配置 |

因此可以用它学习“前端如何调用后端”，但不适合直接修改作为长期源代码。真正维护页面时，应找到原始 UniApp 工程。

### 3.2 用微信开发者工具导入

1. 安装微信开发者工具。
2. 选择“导入项目”。
3. 指向 `sky-server/src/main/resources/mp-weixin`。
4. 填写自己的小程序 AppID；没有 AppID 时可以使用测试号或游客模式（功能会受限）。
5. 确认 `common/vendor.js` 中的 `baseUrl` 指向正在运行的后端。
6. 如果请求失败，优先看开发者工具控制台、Network 和后端日志。

当前编译产物里的地址在 `common/vendor.js` 附近：

```javascript
var baseUrl = 'http://localhost:8080';
```

手机预览时，手机上的 `localhost` 指的是手机自己，不是电脑，所以通常需要电脑局域网 IP、HTTPS 合法域名或内网穿透域名，并在微信后台配置 request 合法域名。

### 3.3 前后端接口必须同时匹配

联调至少要匹配四件事：

```text
URL 路径 + HTTP 方法 + 参数位置/字段 + 请求头
```

例如用户登录：

```text
POST /user/user/login
请求体：{"code":"微信临时登录 code"}
响应：Result<UserLoginVO>
```

登录后，编译产物的请求封装会把 Token 放到 `authentication` 请求头；后端用户拦截器也正是从这个名称读取 Token。管理端使用的请求头名称是 `token`。名称不一致时，接口会返回 401。

---

## 4. 后端环境搭建

### 4.1 项目框架

#### 4.1.1 `sky-common`

这是横切能力模块，不放“菜品业务流程”，而放多个模块都可能使用的代码：

- `result`：[`Result.java`](../sky-common/src/main/java/com/sky/result/Result.java)、[`PageResult.java`](../sky-common/src/main/java/com/sky/result/PageResult.java)。
- `exception`：`BaseException` 和各类业务异常。
- `context`：[`BaseContext.java`](../sky-common/src/main/java/com/sky/context/BaseContext.java)。
- `properties`：JWT、OSS、微信配置绑定对象。
- `utils`：JWT、HTTP、OSS、微信支付工具。
- `json`：[`JacksonObjectMapper.java`](../sky-common/src/main/java/com/sky/json/JacksonObjectMapper.java)。

#### 4.1.2 `sky-pojo`

这是数据模型模块：

- `dto`：接收一次请求所需的字段。
- `entity`：接近数据库表结构的对象。
- `vo`：专门为页面返回的数据对象。

例如订单相关文件：

- [`OrdersSubmitDTO.java`](../sky-pojo/src/main/java/com/sky/dto/OrdersSubmitDTO.java)：用户提交订单的输入。
- [`Orders.java`](../sky-pojo/src/main/java/com/sky/entity/Orders.java)：订单主表实体和状态常量。
- [`OrderDetail.java`](../sky-pojo/src/main/java/com/sky/entity/OrderDetail.java)：订单明细实体。
- [`OrderSubmitVO.java`](../sky-pojo/src/main/java/com/sky/vo/OrderSubmitVO.java)：下单成功后返回页面的信息。

#### 4.1.3 `sky-server`

这是最终启动的模块。结构如下：

```text
com.sky
  +-- SkyApplication.java        启动入口
  +-- controller/                HTTP 接口
  +-- service/                   业务接口
  +-- service/impl/              业务实现
  +-- mapper/                    MyBatis 数据访问接口
  +-- config/                    Spring、Redis、WebSocket 等配置
  +-- interceptor/               JWT 请求拦截器
  +-- aspect/                    AOP 自动填充
  +-- handler/                   全局异常
  +-- task/                      定时任务
  +-- websocket/                 WebSocket 服务
resources/
  +-- application.yml            基础配置
  +-- application-dev.yml        开发配置
  +-- mapper/*.xml               SQL
  +-- template/*.xlsx            Excel 模板
  +-- mp-weixin/                 小程序编译产物
```

启动入口 [`SkyApplication.java`](../sky-server/src/main/java/com/sky/SkyApplication.java) 的三个关键注解：

```java
@SpringBootApplication
@EnableTransactionManagement
@EnableCaching
```

`@SpringBootApplication` 组合了配置类、自动配置和组件扫描。它位于 `com.sky` 根包下，所以能扫描 `com.sky.controller`、`service`、`mapper`、`config` 等子包。`@EnableTransactionManagement` 开启事务注解支持，`@EnableCaching` 开启 Spring Cache 注解支持。

更细的 Maven 和 Spring 说明见 [Maven 多模块项目结构](java-backend-knowledge-ai/01-foundation/01-Maven多模块项目结构.md) 与 [Spring Boot 启动与自动配置](java-backend-knowledge-ai/01-foundation/02-SpringBoot启动与自动配置.md)。

### 4.2 使用 Git 进行版本控制

Git 解决的是“代码变化可追踪、可恢复、可协作”，不是网盘同步。建议把每个阶段拆成小提交：

```bash
git status                 # 查看工作区状态
git add learn/Day01-Day12技术总结-项目实战新手教程.md
git commit -m "docs: add day01-day12 learning guide"
git log --oneline          # 查看提交历史
git diff                   # 查看未提交差异
```

#### 4.2.1 本地仓库

本地仓库包含三个概念：

```text
工作区 --git add--> 暂存区 --git commit--> 本地仓库
```

- 工作区：你正在编辑的文件。
- 暂存区：准备放入下一次提交的改动。
- 本地仓库：已经提交的历史，可以比较和回滚。

第一次学习时不要直接使用破坏性命令覆盖整个工程。先用 `git status` 和 `git diff` 确认目标文件，避免把别人的改动一并撤销。

#### 4.2.2 远程仓库

远程仓库是团队共享的 Git 服务：

```bash
git remote -v
git push origin main
git pull --rebase origin main
```

多人协作时，先拉取再提交；遇到冲突要逐个文件理解后解决，而不是盲目覆盖。分支适合隔离功能开发，例如 `feature/report-export`。

### 4.3 数据库创建

当前仓库没有随项目提供建表 SQL，因此不能假装已经有一键初始化脚本。可以从实体、Mapper 和 XML 反推主要表：

| 实体 | 表 | 关键关系 |
| --- | --- | --- |
| `Employee` | `employee` | 后台员工，用户名通常唯一 |
| `Category` | `category` | 菜品/套餐分类 |
| `Dish` | `dish` | 菜品 |
| `DishFlavor` | `dish_flavor` | 菜品口味，一对多 |
| `Setmeal` | `setmeal` | 套餐 |
| `SetmealDish` | `setmeal_dish` | 套餐和菜品关联 |
| `User` | `user` | 微信用户，`openid` 应唯一 |
| `AddressBook` | `address_book` | 用户收货地址 |
| `ShoppingCart` | `shopping_cart` | 用户购物车 |
| `Orders` | `orders` | 订单主表 |
| `OrderDetail` | `order_detail` | 订单明细，一张订单多行 |

数据库配置在 [`application.yml`](../sky-server/src/main/resources/application.yml) 和 [`application-dev.yml`](../sky-server/src/main/resources/application-dev.yml)：

```yaml
spring:
  datasource:
    url: jdbc:mysql://${sky.datasource.host}:${sky.datasource.port}/${sky.datasource.database}?serverTimezone=Asia/Shanghai
    username: ${sky.datasource.username}
    password: ${sky.datasource.password}
```

开发配置使用环境变量 `SKY_DB_USERNAME` 和 `SKY_DB_PASSWORD`。启动前需要：

1. 安装并启动 MySQL。
2. 创建 `sky_take_out` 数据库。
3. 准备与实体/Mapper 匹配的表结构和初始数据。
4. 设置数据库用户名、密码环境变量。
5. 从根目录运行 `mvn -pl sky-server -am spring-boot:run`，或进入 `sky-server` 目录后运行 `mvn spring-boot:run`；也可以从 IDEA 启动 `SkyApplication`。根项目只是 Maven 聚合工程，Spring Boot Maven 插件实际配置在 `sky-server` 模块；`-am` 表示同时构建它依赖的模块。

数据库设计要理解三个概念：

- 主表：描述主体，如 `orders` 的订单号、金额、状态。
- 明细表：描述主体包含的多项内容，如 `order_detail` 的菜品和数量。
- 关联表：描述多对多关系，如 `setmeal_dish`。

### 4.4 前后端联调

联调不是“两个项目都能启动”，而是验证完整契约：

```text
前端 URL/方法/字段/请求头
          ==
后端 @RequestMapping/@GetMapping/@PostMapping/DTO/拦截器
```

以员工登录为例：

```http
POST http://localhost:8080/admin/employee/login
Content-Type: application/json

{"username":"admin","password":"123456"}
```

后端经过 `EmployeeController.login`，成功后返回 `Result<EmployeeLoginVO>`。后续管理端请求要带 `token` 请求头；用户端请求要带 `authentication` 请求头。出现 401 时先检查 Token 名称、是否过期、请求是否走了正确的 `/admin/**` 或 `/user/**` 路径。

---

## 5. 断点调试

断点的作用是“暂停程序，让你观察真实运行状态”，比单纯猜测更可靠。

### 5.1 用员工登录练习

建议按以下位置下断点：

1. [`EmployeeController.login`](../sky-server/src/main/java/com/sky/controller/admin/EmployeeController.java) 的方法入口。
2. [`EmployeeServiceImpl.login`](../sky-server/src/main/java/com/sky/service/impl/EmployeeServiceImpl.java) 查询员工后的位置。
3. `DigestUtils.md5DigestAsHex` 前后，观察密码从明文变成摘要。
4. `JwtUtil.createJWT` 调用处，观察 Claims 和 Token。
5. [`JwtTokenAdminInterceptor.preHandle`](../sky-server/src/main/java/com/sky/interceptor/JwtTokenAdminInterceptor.java) 的 Token 解析处。

### 5.2 调试时看什么

- Variables：当前方法的局部变量。
- Watches：固定观察 `employee.getStatus()`、`BaseContext.getCurrentId()` 等表达式。
- Call Stack：谁调用了当前方法，返回后会回到哪里。
- Step Over：执行下一行，不进入方法内部。
- Step Into：进入当前调用的方法。
- Resume：继续运行到下一个断点。

### 5.3 断点阅读下单链路

用户下单的实际顺序是：

```text
拦截器写入 userId
  -> OrderController.submit
  -> OrderServiceImpl.submit
  -> 地址查询
  -> 百度地图请求
  -> 购物车查询
  -> orders 插入并回填 id
  -> order_detail 批量插入
  -> 清空购物车
  -> Result 返回
```

如果断点没有停下，检查请求 URL、登录排除规则、应用是否使用当前代码，以及断点是否设置在真正执行的分支上。

---

## 6. 配置类和 Builder 创建对象

### 6.1 配置类和 Bean

Spring 容器负责创建和管理对象。配置类可以显式声明 Bean：

```java
@Configuration
public class RedisConfiguration {
    @Bean
    public RedisTemplate redisTemplate(RedisConnectionFactory factory) {
        RedisTemplate template = new RedisTemplate();
        template.setConnectionFactory(factory);
        return template;
    }
}
```

项目中的 [`RedisConfiguration.java`](../sky-server/src/main/java/com/sky/config/RedisConfiguration.java)、[`WebSocketConfiguration.java`](../sky-server/src/main/java/com/sky/config/WebSocketConfiguration.java) 和 [`OssConfiguration.java`](../sky-server/src/main/java/com/sky/config/OssConfiguration.java) 都是这个模式。

常见注解区别：

| 注解 | 含义 |
| --- | --- |
| `@Component` | 让组件扫描发现这个类 |
| `@Service` | 语义上表示业务组件 |
| `@Configuration` | 语义上表示配置类 |
| `@Bean` | 把方法返回值注册到 Spring 容器 |
| `@Autowired` | 从容器注入依赖 |
| `@ConfigurationProperties` | 把 YAML 一组配置绑定到 Java 对象 |

例如 [`WeChatProperties.java`](../sky-common/src/main/java/com/sky/properties/WeChatProperties.java) 的前缀是 `sky.wechat`，所以 YAML 中的 `sky.wechat.appid` 会绑定到 `appid` 字段。

### 6.2 Lombok 的 `@Builder`

订单登录返回对象使用了 Lombok 生成的 Builder：

```java
EmployeeLoginVO vo = EmployeeLoginVO.builder()
        .id(employee.getId())
        .userName(employee.getUsername())
        .name(employee.getName())
        .token(token)
        .build();
```

对应 [`EmployeeLoginVO.java`](../sky-pojo/src/main/java/com/sky/vo/EmployeeLoginVO.java) 上的 `@Builder`。Builder 适合字段较多、构造顺序容易混淆的对象；`.build()` 最后才创建对象。

实体类还经常组合 `@Data`、`@Builder`、`@NoArgsConstructor` 和 `@AllArgsConstructor`。这些是编译期代码生成：`@Data` 生成 getter/setter、`toString` 等；两个构造器注解生成不同参数的构造方法。IDE 中看到的调用方法不一定手写在源文件里，可能由 Lombok 生成。

---

## 7. Nginx 反向代理

### 7.1 什么是反向代理

客户端只访问 Nginx，Nginx 再把请求转发给后端服务：

```text
浏览器 -> http://example.com/api
                 |
                 v
          Nginx:80/443
                 |
                 v
          sky-server:8080
```

“反向”表示客户端不需要知道真正的后端地址。它还能负责静态文件、HTTPS、负载均衡、压缩和统一跨域策略。

仓库没有 Nginx 配置文件，下面是帮助理解的示例：

```nginx
server {
    listen 80;
    server_name localhost;

    location / {
        root /opt/sky-web;
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8080/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

如果前端请求 `/api/user/...`，而后端 Controller 映射是 `/user/...`，要特别核对 `proxy_pass` 末尾斜杠和是否需要去掉 `/api` 前缀。配置错误通常表现为 404，而不是 Java 代码错误。

课程中常见的管理端部署写法是把 `/api/` 转发到后端的 `/admin/` 接口，例如让前端请求 `/api/employee/page`，Nginx 转发后变成 `/admin/employee/page`。这只是部署约定，不是当前仓库内的真实配置；本仓库没有 Nginx 配置文件，最终应以实际 `location` 和 `proxy_pass` 规则为准。

---

## 8. MD5 加密

### 8.1 当前代码如何做

员工登录在 [`EmployeeServiceImpl.java`](../sky-server/src/main/java/com/sky/service/impl/EmployeeServiceImpl.java) 中：

```java
password = DigestUtils.md5DigestAsHex(password.getBytes());
if (!password.equals(employee.getPassword())) {
    throw new PasswordErrorException(...);
}
```

新增员工时，默认密码 `123456` 也先经过 MD5 再入库。登录时不是把数据库密码解密，而是把用户输入再次计算摘要，然后比较两个摘要。

### 8.2 摘要和加密不是一回事

- 加密：有密钥，理论上可以解密回原文。
- 哈希/摘要：单向计算，设计目标是难以从摘要还原原文。

MD5 输出 128 位摘要，通常显示为 32 个十六进制字符。它已经不适合生产密码存储，因为速度太快、彩虹表和 GPU 破解成本低。

### 8.3 生产建议

生产应使用 BCrypt、Argon2 等带盐、可调成本的密码哈希：

```java
String encoded = passwordEncoder.encode(rawPassword);
boolean ok = passwordEncoder.matches(rawPassword, encoded);
```

不要记录明文密码；也不要记录完整 JWT、Cookie、支付回调原文或 Authorization 请求头。当前 `EmployeeController.login` 会把登录 DTO 整体写入日志，其中可能包含密码；两个 JWT 拦截器的调试日志也会输出完整 Token。它们学习时便于观察流程，但生产代码应删除，或只记录脱敏后的少量字符。不要把密钥、密码和证书提交到 Git。当前 `application-dev.yml` 中存在历史示例凭证，学习时应视为需要替换和轮换的敏感信息。

---

## 9. 导入接口文档

`项目接口文档` 目录中有两份现成 HTML：

- [苍穹外卖-管理端接口.html](../项目接口文档/苍穹外卖-管理端接口.html)
- [苍穹外卖-用户端接口.html](../项目接口文档/苍穹外卖-用户端接口.html)

可以直接用浏览器打开，或在 IDEA 中查看。学习一个接口时，把文档里的 URL、方法、参数和返回示例与 Controller 对照。例如“用户下单”最终应对应 [`OrderController.java`](../sky-server/src/main/java/com/sky/controller/user/OrderController.java) 的 `POST /user/order/submit`。

文档不是代码的替代品：如果 HTML 和 Controller 不一致，应以当前服务端代码和实际请求测试结果为准，同时记录需要修正文档的地方。

---

## 10. Swagger / OpenAPI

### 10.1 Swagger 使用步骤

项目使用 springdoc OpenAPI，不是旧版 springfox。启动后常见地址是：

```text
http://localhost:8080/swagger-ui/index.html
http://localhost:8080/v3/api-docs
```

步骤：

1. 确认 `sky-server/pom.xml` 引入 `springdoc-openapi-starter-webmvc-ui`。
2. 启动 `SkyApplication`。
3. 打开 Swagger UI。
4. 选择 `admin` 或 `user` 分组。
5. 查看接口参数、响应模型并执行 Try it out。
6. 需要登录的接口先在请求头中填入对应 JWT。

分组配置在 [`OpenApiConfiguration.java`](../sky-server/src/main/java/com/sky/config/OpenApiConfiguration.java)：

```java
GroupedOpenApi.builder()
        .group("admin")
        .pathsToMatch("/admin/**")
        .build();
```

### 10.2 Swagger 常用注解

项目实际使用：

| 注解 | 示例 | 作用 |
| --- | --- | --- |
| `@Tag` | `@Tag(name = "员工相关接口")` | 给 Controller 分组和命名 |
| `@Operation` | `@Operation(summary = "新增员工")` | 描述一个接口 |
| `@Schema` | DTO/VO 字段上的描述 | 描述请求或响应字段 |

常见补充注解：

```java
@Parameter(description = "订单状态") Integer status
// OpenAPI 的 RequestBody 注解与 Spring MVC 的 @RequestBody 是两个不同注解。
// 实际项目中可在方法参数旁同时使用它们，或直接让 DTO 上的 @Schema 描述字段。
@org.springframework.web.bind.annotation.RequestBody OrdersSubmitDTO dto
```

Swagger 只描述接口契约，不会自动替你实现参数校验、权限校验和业务状态限制。比如 `Orders.status=5` 代表已完成，这种业务含义仍应写在说明或项目文档中。

---

# 二、Day02 技术总结

## 1. DTO、VO 和 POJO

### 1.1 POJO 是什么

POJO（Plain Old Java Object）可以理解为普通 Java 对象，不要求继承某个框架基类。项目中的 DTO、Entity、VO 都可以是 POJO；POJO 是一个宽泛称呼，不是第四种和 DTO/VO 并列的固定层。

### 1.2 DTO：请求输入模型

DTO（Data Transfer Object）只表达某次接口需要的输入。例如 `OrdersSubmitDTO` 里有地址簿 ID、付款方式、备注、预计送达时间和金额等字段。客户端不应该直接提交 `Orders` 实体的全部字段，因为订单号、用户 ID、状态和支付状态必须由服务端决定。

```text
客户端 JSON
  -> @RequestBody
  -> OrdersSubmitDTO
```

### 1.3 Entity：持久化模型

Entity 贴近数据库表。`Orders` 包含订单号、状态、用户、地址、金额、支付状态和时间；`OrderDetail` 表示订单中的一条菜品/套餐明细。Service 会把 DTO 转成 Entity，再交给 Mapper 写入数据库。

### 1.4 VO：响应展示模型

VO（View Object）面向页面。`EmployeeLoginVO` 只返回员工 ID、用户名、姓名和 JWT，不返回密码；`OrderSubmitVO` 只返回下单成功页面需要的订单 ID、订单号、金额和时间。

```text
请求：OrdersSubmitDTO
       |
       v
业务补全：Orders Entity + OrderDetail Entity
       |
       v
响应：OrderSubmitVO / OrderVO
```

不要机械地认为“一个接口只能有一个对象”。一个复杂页面可能组合多个实体字段形成一个 VO；一个实体也可能被多个 DTO/VO 以不同字段形态使用。

更详细的字段边界说明见 [DTO-Entity-VO 分层数据模型](java-backend-knowledge-ai/02-web/06-DTO-Entity-VO分层数据模型.md)。

---

## 2. Swagger 全局参数配置

项目的 `OpenApiConfiguration` 已配置 API 标题和 `admin`/`user` 分组，但没有统一声明“所有接口自动携带 JWT”的安全方案。理解全局参数要区分两件事：

- 文档描述：让 Swagger UI 知道某接口需要什么参数。
- 请求执行：让 Swagger UI 在 Try it out 时真的把参数放进请求。

### 2.1 为单个接口声明参数

查询参数可用 `@Parameter`：

```java
@GetMapping("/page")
public Result<PageResult> page(
        @Parameter(description = "页码") int page,
        @Parameter(description = "每页条数") int pageSize) {
    ...
}
```

### 2.2 声明 Bearer Token 安全方案

标准 JWT 常用 Bearer 形式：

```java
@Bean
public OpenAPI skyOpenApi() {
    return new OpenAPI()
            .components(new Components().addSecuritySchemes(
                    "bearerAuth",
                    new SecurityScheme()
                            .type(SecurityScheme.Type.HTTP)
                            .scheme("bearer")
                            .bearerFormat("JWT")))
            .addSecurityItem(new SecurityRequirement().addList("bearerAuth"));
}
```

但本项目当前拦截器读取的是自定义头 `token` 和 `authentication`，并不是统一的 `Authorization: Bearer ...`。如果要在 Swagger 中正确测试，应该直接声明对应 Header，或统一后端改为读取 `Authorization`，或为管理端和用户端分别设计两个安全方案。不要只改 Swagger 配置而不改拦截器。

---

## 3. 全局异常处理器

### 3.1 为什么需要统一处理

如果每个 Controller 都写 `try/catch`，会出现重复代码和不一致的错误格式。项目用 [`GlobalExceptionHandler.java`](../sky-server/src/main/java/com/sky/handler/GlobalExceptionHandler.java) 统一把异常转换为 `Result.error(...)`。

```java
@RestControllerAdvice
public class GlobalExceptionHandler {
    @ExceptionHandler
    public Result exceptionHandler(BaseException ex) {
        return Result.error(ex.getMessage());
    }
}
```

- `@RestControllerAdvice`：对所有 Controller 生效，并把返回值写成 JSON。
- `@ExceptionHandler`：声明某个方法负责处理哪类异常。
- `BaseException`：项目业务异常的父类。

当前还专门处理 `SQLIntegrityConstraintViolationException`，遇到 `Duplicate entry` 时返回“已存在”的提示。

### 3.2 异常在请求链路中的位置

```text
Service 抛出 AccountNotFoundException
   -> 事务代理决定是否回滚
   -> DispatcherServlet 接住异常
   -> GlobalExceptionHandler 匹配 BaseException
   -> Result.error(message)
   -> Jackson 序列化为 JSON
```

JWT 拦截失败是一个不同路径：拦截器直接设置 HTTP 401 并 `return false`，请求不会进入 Controller，也不会经过这个业务异常处理器。

### 3.3 初学者要注意

- 不要把异常“吃掉”后返回成功。
- 日志里记录堆栈，响应里只返回适合用户看的信息。
- 还应处理参数绑定异常、未知异常，并设置合适的 HTTP 状态码。
- 当前处理 SQL 异常使用字符串 `contains` 和 `split`，适合学习，不够稳健。

---

## 4. ThreadLocal

### 4.1 它解决什么问题

请求进入时已经从 JWT 中解析出员工 ID 或用户 ID，后面的 Service 需要使用它。如果每层方法都增加 `userId` 参数，会让接口变得冗长。项目用 [`BaseContext.java`](../sky-common/src/main/java/com/sky/context/BaseContext.java) 保存当前请求身份：

```java
public static ThreadLocal<Long> threadLocal = new ThreadLocal<>();

public static void setCurrentId(Long id) {
    threadLocal.set(id);
}

public static Long getCurrentId() {
    return threadLocal.get();
}

public static void removeCurrentId() {
    threadLocal.remove();
}
```

`ThreadLocal` 的含义不是“全局只有一个值”，而是“每条线程各自有一份值”：

```text
线程 A -> userId=10
线程 B -> userId=20
线程 C -> empId=3
```

### 4.2 当前项目如何写入

[`JwtTokenUserInterceptor`](../sky-server/src/main/java/com/sky/interceptor/JwtTokenUserInterceptor.java) 解析 JWT 的 `userId` 后调用 `BaseContext.setCurrentId(userId)`；管理端拦截器写入 `empId`。订单提交因此可以直接调用：

```java
Long userId = BaseContext.getCurrentId();
```

### 4.3 必须清理

Web 容器会复用线程。如果请求结束后不清理，下一次使用同一线程的请求可能读到旧值。当前代码提供了 `removeCurrentId()`，但拦截器中没有明显的 `afterCompletion` 清理实现。更稳妥的做法是：

```java
@Override
public void afterCompletion(
        HttpServletRequest request,
        HttpServletResponse response,
        Object handler,
        Exception ex) {
    BaseContext.removeCurrentId();
}
```

在异步线程、线程池和消息消费场景中，ThreadLocal 不会自动跨线程传递，不能把它当作分布式用户上下文。

---

## 5. 日期格式化

日期问题通常分成两种方向：请求参数进入 Java（反序列化/参数绑定），以及 Java 返回客户端（序列化）。

### 5.1 方式一：在参数或字段上声明格式

报表接口接收 URL 查询参数：

```java
@GetMapping("/turnoverStatistics")
public Result<TurnoverReportVO> turnoverStatistics(
        @DateTimeFormat(pattern = "yyyy-MM-dd") LocalDate begin,
        @DateTimeFormat(pattern = "yyyy-MM-dd") LocalDate end) {
    ...
}
```

请求示例：

```text
GET /admin/report/turnoverStatistics?begin=2026-09-01&end=2026-09-07
```

`@DateTimeFormat` 主要用于 Spring MVC 的查询参数、路径参数和表单参数，不是 JSON 全局规则。JSON 字段可以在 DTO 上使用 `@JsonFormat`，例如订单预计送达时间字段的格式覆盖。

### 5.2 方式二：统一配置 Jackson

项目的 [`JacksonObjectMapper.java`](../sky-common/src/main/java/com/sky/json/JacksonObjectMapper.java) 注册了 Java 8 时间类型格式：

```text
yyyy-MM-dd          -> LocalDate
yyyy-MM-dd HH:mm    -> LocalDateTime
HH:mm:ss            -> LocalTime
```

`WebMvcConfiguration.extendMessageConverters` 把这个 ObjectMapper 放到消息转换器首位，因此 Controller 返回 `Result` 时会按这些规则输出 JSON，接收 `@RequestBody` 时也会按这些规则解析。

同时它关闭了未知字段失败：客户端多传一个字段不会直接因为 Jackson 抛错。这个兼容性比较方便，但也意味着严格发现拼写错误的能力变弱。

### 5.3 常见坑

- `LocalDate` 没有时分秒；`LocalDateTime` 才有。
- `LocalTime.MAX` 是当天最后一纳秒，数据库查询使用严格 `< end` 时要理解边界。
- 时区会影响 `Instant`、时间戳和数据库时间；项目 JDBC URL 指定了 `Asia/Shanghai`。
- 同一个系统尽量统一格式，不要让前端一会儿传 `yyyy-MM-dd`、一会儿传带秒的字符串。

---

# 三、Day03 技术总结

## 1. 公共字段自动填充

### 1.1 问题分析

多个表都有相同的审计字段：

```text
create_time   创建时间
update_time   修改时间
create_user   创建人
update_user   修改人
```

如果每个 Service 手动设置，代码重复且容易遗漏。例如员工新增、菜品新增、套餐新增都要写同样四个字段。项目把这项横切逻辑提取为 AOP。

### 1.2 代码实现

#### 第一步：定义操作枚举和注解

[`OperationType.java`](../sky-common/src/main/java/com/sky/enumeration/OperationType.java) 表示 `INSERT` 或 `UPDATE`。自定义注解 [`AutoFill.java`](../sky-server/src/main/java/com/sky/annotation/AutoFill.java)：

```java
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface AutoFill {
    OperationType value();
}
```

- `@Target(METHOD)`：只能标在方法上。
- `@Retention(RUNTIME)`：运行时仍保留，AOP 才能通过反射读取。

#### 第二步：在 Mapper 方法上标注

```java
@Insert("insert into employee (...) values (...)")
@AutoFill(value = OperationType.INSERT)
void insert(Employee employee);

@AutoFill(value = OperationType.UPDATE)
void update(Employee employee);
```

#### 第三步：切面拦截

[`AutoFIllAspect.java`](../sky-server/src/main/java/com/sky/aspect/AutoFIllAspect.java) 的切入点：

```java
@Pointcut("execution(* com.sky.mapper.*.*(..)) && " +
          "@annotation(com.sky.annotation.AutoFill)")
```

意思是：拦截 `com.sky.mapper` 包下、并且带 `@AutoFill` 注解的方法。

前置通知中做四件事：

1. 从方法签名读取注解值，判断 INSERT 还是 UPDATE。
2. 从 `joinPoint.getArgs()` 取得实体参数。
3. 生成 `LocalDateTime.now()`，从 `BaseContext` 取得当前操作人。
4. 通过反射调用 `setCreateTime`、`setUpdateTime` 等 setter。

```text
Service 创建不完整的 Employee
        |
        v
调用带 @AutoFill 的 Mapper.insert
        |
        v
AOP 前置通知补全时间和用户
        |
        v
MyBatis 执行 SQL
```

#### 为什么用反射

切面希望同时处理 `Employee`、`Dish`、`Setmeal` 等不同实体，而不想为每个实体写一套相同代码。反射允许它按方法名动态调用 setter。代价是：方法名拼错、类型不匹配时，错误会推迟到运行时。

#### 需要理解的边界

- 登录接口没有当前用户时，`createUser` 可能为空，数据库字段要允许这种情况或做特殊处理。
- 批量插入是否只处理第一个参数，要看 Mapper 设计；当前切面直接取 `args[0]`。
- 当前 `CategoryMapper.insert` 和 `update` 没有标注 `@AutoFill`，而 `CategoryServiceImpl` 中原本手动设置审计字段的代码也被注释；因此分类新增/修改的创建人、修改人和时间可能不会自动填充。这是阅读教学代码时必须主动发现的真实缺口，不能假设所有 Mapper 都已经接入切面。
- AOP 依赖 Spring 代理；对象必须由容器管理，自己 `new` 的对象不会自动织入。

---

# 四、Day05 技术总结：Redis 与 Spring Cache

> 课程编号中没有单列 Day04，本仓库和你给出的提纲也没有对应的独立技术主题。可以把它理解为前面员工、分类、菜品等基础业务的项目实操与巩固阶段；下面直接进入 Day05 的 Redis。

> 这是后半段开始的重要转折：从“同步调用数据库”进入“内存缓存、序列化、失效和并发一致性”。建议一边读，一边打开 Redis 客户端观察 Key。

## 1. Redis 入门

### 1.1 Redis 简介

Redis 是以内存为主的 Key-Value 数据库。MySQL 的强项是关系、事务和持久化；Redis 的强项是高吞吐、低延迟和丰富的数据结构。

```text
第一次查询：客户端 -> 应用 -> Redis 未命中 -> MySQL -> Redis -> 返回
后续查询：客户端 -> 应用 -> Redis 命中 -> 返回
```

适合缓存的数据通常是“读多写少、允许短时间最终一致”的热点数据，例如用户端按分类查询起售菜品。

### 1.2 Redis 下载与安装

Windows 学习可以使用 WSL、Docker 或兼容 Redis 协议的服务；Linux 常用系统包或 Docker。安装后要确认：

```text
服务地址：127.0.0.1
端口：6379
数据库编号：0
```

本项目开发配置默认读取：

```yaml
sky:
  redis:
    host: ${SKY_REDIS_HOST:127.0.0.1}
    port: ${SKY_REDIS_PORT:6379}
    password: ${SKY_REDIS_PASSWORD:}
    database: ${SKY_REDIS_DATABASE:0}
```

### 1.3 Redis 服务启动与停止

不同安装方式命令不同，原则是先启动服务，再启动 Spring Boot。检查端口和连接比“代码看起来没问题”更重要。Spring Boot 启动时如果 Redis 不可达，相关 Bean 或第一次访问缓存可能报连接异常。

### 1.4 Redis 图形客户端

图形客户端可以直观看到 Key、类型、值和 TTL。CLI 更适合练习命令：

```text
redis-cli
127.0.0.1:6379> PING
PONG
```

## 2. Redis 数据类型

| 类型 | 直观理解 | 外卖项目中的可想象场景 |
| --- | --- | --- |
| String | 一个值 | `SHOP_STATUS=1`、缓存一整个列表序列化值 |
| Hash | 一个 Key 下多组字段 | 用户资料、商品属性 |
| List | 有顺序的列表 | 消息队列、最近浏览 |
| Set | 无重复集合 | 标签、参与活动的用户 |
| Sorted Set | 带分数排序的集合 | 销量排行榜 |

当前项目主要使用 String：店铺状态和菜品列表缓存；Spring Cache 也默认把方法结果存为 Redis 值。

## 3. Redis 常用命令

### 3.1 字符串操作命令

```text
SET name sky
GET name
SETEX token 60 value
DEL name
INCR counter
```

### 3.2 哈希操作命令

```text
HSET user:1 name Alice phone 13800000000
HGET user:1 name
HGETALL user:1
```

### 3.3 列表操作命令

```text
LPUSH queue order-1
RPUSH queue order-2
LRANGE queue 0 -1
LPOP queue
```

### 3.4 集合操作命令

```text
SADD tags spicy sweet
SISMEMBER tags spicy
SMEMBERS tags
```

### 3.5 有序集合操作命令

```text
ZADD sales 10 dish:1
ZADD sales 20 dish:2
ZREVRANGE sales 0 9 WITHSCORES
```

### 3.6 通用命令

```text
EXISTS key
TTL key
TYPE key
KEYS dish_*
SCAN 0 MATCH dish_* COUNT 100
```

`KEYS` 会一次扫描整个数据库，数据量大时可能阻塞 Redis；生产环境优先使用 `SCAN`，并避免使用过于宽泛的模式。

## 4. 在 Java 中操作 Redis

### 4.1 Spring Data Redis

项目通过 `spring-boot-starter-data-redis` 提供 `RedisTemplate`。配置类 [`RedisConfiguration.java`](../sky-server/src/main/java/com/sky/config/RedisConfiguration.java) 设置了连接工厂和 String Key 序列化器。

当前只显式设置 Key 序列化器，Value 仍使用默认 JDK 序列化。因此 Redis 客户端看到的 List/Integer 可能是二进制，不代表数据不存在。生产代码通常会明确配置 JSON 或 String Value 序列化，并统一兼容策略。

### 4.2 环境搭建

启动 Redis 后，启动应用，检查 `application.yml` 是否读取到了正确的 host/port/password。不要把生产密码硬编码在仓库里，使用环境变量或密钥管理服务。

### 4.3 操作常见类型数据

#### 4.3.1 操作字符串类型数据

```java
redisTemplate.opsForValue().set("SHOP_STATUS", status);
Integer status = (Integer) redisTemplate.opsForValue().get("SHOP_STATUS");
```

实际代码：管理端 [`ShopController.java`](../sky-server/src/main/java/com/sky/controller/admin/ShopController.java) 设置店铺状态，用户端 [`ShopController.java`](../sky-server/src/main/java/com/sky/controller/user/ShopController.java) 读取。

#### 4.3.2 操作哈希类型数据

```java
redisTemplate.opsForHash().put("user:1", "name", "Alice");
Object name = redisTemplate.opsForHash().get("user:1", "name");
```

Hash 适合一个对象拆成多个字段，但要提前约定字段名和类型。

#### 4.3.3 操作列表类型数据

```java
redisTemplate.opsForList().rightPush("orders", "order-1");
List values = redisTemplate.opsForList().range("orders", 0, -1);
```

#### 4.3.4 操作集合类型数据

```java
redisTemplate.opsForSet().add("dish:tags", "spicy", "sweet");
Boolean exists = redisTemplate.opsForSet().isMember("dish:tags", "spicy");
```

#### 4.3.5 操作有序集合类型数据

```java
redisTemplate.opsForZSet().add("dish:sales", "dish:1", 100);
Set values = redisTemplate.opsForZSet().reverseRange("dish:sales", 0, 9);
```

#### 4.3.6 通用命令操作

```java
Boolean exists = redisTemplate.hasKey("dish_1");
Long deleted = redisTemplate.delete("dish_1");
Set keys = redisTemplate.keys("dish_*");
```

### 4.4 菜品旁路缓存：项目中的完整案例

用户端 [`DishController.java`](../sky-server/src/main/java/com/sky/controller/user/DishController.java) 使用 Cache-Aside（旁路缓存）：

```java
String key = "dish_" + categoryId;
List<DishVO> list = (List<DishVO>) redisTemplate.opsForValue().get(key);

if (list == null || list.isEmpty()) {
    Dish dish = new Dish();
    dish.setCategoryId(categoryId);
    dish.setStatus(StatusConstant.ENABLE);
    list = dishService.listWithFlavor(dish);
    redisTemplate.opsForValue().set(key, list);
}
return Result.success(list);
```

管理端新增、修改、删除或启停菜品后，会清理 `dish_*` 缓存，见 [`admin/DishController.java`](../sky-server/src/main/java/com/sky/controller/admin/DishController.java)。

读写策略是：

```text
读：Redis 命中 -> 返回
读：Redis 未命中 -> MySQL -> 写 Redis -> 返回
写：MySQL 成功 -> 删除相关 Redis Key
```

当前实现没有 TTL、没有空值缓存、没有击穿保护，并且使用 `keys("dish_*")`。这些都是非常适合继续练习的地方。

### 4.5 Spring Cache 对比

启动类的 `@EnableCaching` 打开缓存代理。用户端套餐查询：

```java
@Cacheable(cacheNames = "setmealCache", key = "#categoryId")
public Result<List<Setmeal>> list(Long categoryId) { ... }
```

管理端套餐变更使用：

```java
@CacheEvict(cacheNames = "setmealCache", allEntries = true)
```

常见注解：

| 注解 | 行为 |
| --- | --- |
| `@Cacheable` | 先查缓存，未命中才执行方法 |
| `@CachePut` | 执行方法，并把返回值写入缓存 |
| `@CacheEvict` | 删除缓存 |
| `allEntries=true` | 清空该 Cache 区域全部条目 |
| `beforeInvocation=true` | 方法执行前删除，而不是成功后删除 |

Spring Cache 依靠 AOP 代理。一个类内部 `this.list(...)` 调用自己的缓存方法，可能绕过代理，导致缓存注解不生效。缓存 Key、序列化和 TTL 仍需理解，注解并不等于自动解决所有缓存问题。

---

# 五、Day06 技术总结：HttpClient、微信小程序和微信登录

## 1. HttpClient

### 1.1 入门案例

`HttpClientUtil` 在 [`sky-common/src/main/java/com/sky/utils/HttpClientUtil.java`](../sky-common/src/main/java/com/sky/utils/HttpClientUtil.java) 封装了 Apache HttpClient。远程调用的基本步骤是：

```text
创建客户端 -> 构造 URL/请求体 -> 设置请求头 -> execute
-> 检查 HTTP 状态 -> 读取响应体 -> 关闭资源
```

#### 1.1.1 GET 方式请求

```java
Map<String, String> params = new HashMap<>();
params.put("address", "北京市朝阳区...");
params.put("output", "json");
String json = HttpClientUtil.doGet(url, params);
```

工具类用 `URIBuilder` 把参数拼到查询字符串，并读取 UTF-8 响应。百度地图和微信登录都用这种方式。

#### 1.1.2 POST 方式请求

表单 POST：

```java
HttpClientUtil.doPost(url, formParams);
```

JSON POST：

```java
HttpClientUtil.doPost4Json(url, jsonParams);
```

工具类设置了连接超时、连接池获取超时和 socket 读取超时，当前值是 5 秒。但当前 `doGet` 没有复用同一套 RequestConfig，且异常路径的资源关闭不够稳健；生产代码应使用 try-with-resources、检查所有非 2xx 状态和第三方业务错误码，并关闭 HttpClient。

## 2. 微信小程序开发

### 2.1 准备工作

#### 2.1.1 注册或登录小程序

登录微信公众平台，获取 AppID 和 AppSecret。AppSecret 只能放在后端，不能写入小程序代码。

#### 2.1.2 完善小程序信息

真实小程序需要配置主体、服务器域名、request 合法域名、socket 合法域名和业务类目。开发工具的本地调试可以临时放宽校验，但上线不能依赖这个开关。

#### 2.1.3 下载开发者工具

导入 `mp-weixin` 后，先检查页面能否编译，再检查接口请求地址。当前 `app.json` 注册了首页、订单、详情、支付、地址、个人中心等页面。

### 2.2 入门案例

#### 2.2.1 小程序目录结构

小程序页面通常由四个文件组成：

```text
index.js     页面逻辑和生命周期
index.wxml   页面结构
index.wxss   页面样式
index.json   页面配置
```

本仓库的页面已经是编译结果。原始 API 模块被打包进 `common/vendor.js`，因此阅读时用 `rg` 搜索 URL 比直接找 `api.js` 更有效。

#### 2.2.2 编写和编译小程序

原始 UniApp 通常由 HBuilderX/Vite/Webpack 编译为微信小程序；微信开发者工具负责预览、调试和上传。编译产物不应手工大规模修改，因为下一次构建会覆盖它。

#### 2.2.3 发布小程序

发布通常包括：开发版上传、体验版测试、提交审核、审核通过后发布。上线前必须检查 HTTPS、域名白名单、隐私授权、支付资质和后端环境变量。

## 3. 微信登录

### 3.1 导入小程序代码

小程序登录通常先调用 `uni.login` 获取临时 `code`，再请求后端 `/user/user/login`。这个 `code` 短时有效且通常只能使用一次，不是用户 Token。

### 3.2 微信登录流程

```text
小程序 uni.login
        |
        | 临时 code
        v
POST /user/user/login
        |
        v
UserServiceImpl.getOpenId
        |
        | appid + appsecret + js_code
        v
微信 jscode2session
        |
        v
openid
        |
        +--> user.openid 已存在：读取本地用户
        +--> 不存在：创建 user 记录
        |
        v
JwtUtil 用本地 user.id 签发项目 JWT
        |
        v
UserLoginVO 返回小程序
```

后端入口 [`UserController.java`](../sky-server/src/main/java/com/sky/controller/user/UserController.java)，业务实现 [`UserServiceImpl.java`](../sky-server/src/main/java/com/sky/service/impl/UserServiceImpl.java)：

```java
String openid = getOpenId(userLoginDTO.getCode());
User user = userMapper.getByOpenId(openid);
if (user == null) {
    user = User.builder()
            .openid(openid)
            .createTime(LocalDateTime.now())
            .build();
    userMapper.insert(user);
}
```

为什么还要创建本地 `user.id`？因为业务表只依赖自己的主键，未来更换登录渠道时，不必让所有订单表依赖微信身份格式。

当前代码对微信返回的错误码解析较少，生产环境还要处理：code 过期、AppID/Secret 错误、网络超时、openid 缺失，以及两个并发首次登录请求导致的重复用户。数据库应给 `openid` 加唯一约束，并对重复插入做幂等处理。

---

# 六、Day07 技术总结：Spring Cache

## 1. Spring Cache 简介

Spring Cache 是一套缓存抽象：业务方法只声明缓存语义，具体缓存实现由 CacheManager 决定。当前项目引入 `spring-boot-starter-cache` 和 Redis Starter，Spring Boot 会自动配置 Redis CacheManager。

```text
Controller 调用缓存方法
      |
      v
Spring AOP CacheInterceptor
      |
      +--> Key 命中：直接返回缓存值
      +--> 未命中：执行真实方法、保存返回值、返回
```

## 2. Spring Cache 常用注解

| 注解 | 适用场景 | 关键属性 |
| --- | --- | --- |
| `@Cacheable` | 查询缓存 | `cacheNames`、`key`、`unless`、`condition` |
| `@CachePut` | 更新后写入缓存 | 方法总会执行，返回值放入缓存 |
| `@CacheEvict` | 删除缓存 | `key`、`allEntries`、`beforeInvocation` |
| `@Caching` | 同时组合多个缓存动作 | 多个 `@Cacheable`/`@CacheEvict` |
| `@CacheConfig` | 类级别默认配置 | 默认 cacheNames/keyGenerator |

## 3. Spring Cache 入门案例

### 3.1 环境准备

启动类有：

```java
@EnableCaching
public class SkyApplication { ... }
```

Redis 地址来自 `application.yml`。没有 Redis 时，第一次调用缓存方法可能连接失败，不能只看编译是否通过。

### 3.2 `@CachePut`

概念示例：

```java
@CachePut(cacheNames = "userCache", key = "#user.id")
public User update(User user) {
    userMapper.update(user);
    return user;
}
```

它和 `@Cacheable` 的根本区别是：`@CachePut` 不跳过方法，数据库更新逻辑一定执行，然后把返回值写入缓存。

### 3.3 `@Cacheable`

项目套餐查询：

```java
@Cacheable(cacheNames = "setmealCache", key = "#categoryId")
public Result<List<Setmeal>> list(Long categoryId) {
    Setmeal query = new Setmeal();
    query.setCategoryId(categoryId);
    query.setStatus(StatusConstant.ENABLE);
    return Result.success(setmealService.list(query));
}
```

第一次请求会查数据库，第二次相同 `categoryId` 直接返回缓存。缓存的是方法返回值，因此要考虑 `Result` 外层是否也要存入缓存，以及序列化后的泛型类型是否稳定。

### 3.4 `@CacheEvict`

套餐新增按分类删除缓存，删除/修改/启停则清空整个套餐缓存区：

```java
@CacheEvict(cacheNames = "setmealCache", key = "#setmealDTO.categoryId")
public Result save(@RequestBody SetmealDTO setmealDTO) { ... }

@CacheEvict(cacheNames = "setmealCache", allEntries = true)
public Result update(@RequestBody SetmealDTO setmealDTO) { ... }
```

失效策略是缓存设计的核心。数据库更新成功但删除缓存失败，可能短时间读到旧值；更新后删除比手工重新组装缓存简单，但要考虑并发读写和多实例一致性。详见 [缓存一致性与失效策略](java-backend-knowledge-ai/05-cache-config/23-缓存一致性与失效策略.md)。

---

# 七、Day08 技术总结：微信支付

> 这一部分涉及证书、签名、加密、异步通知和金额校验，初学时不要只记 API 名称。重点是理解“为什么支付结果不能只相信浏览器页面”。

## 1. 微信支付介绍

典型 JSAPI 支付流程：

```text
用户提交订单
  -> 后端创建本地待支付订单
  -> 后端向微信统一下单，得到 prepay_id
  -> 后端签名小程序调起支付所需参数
  -> 小程序调起微信支付
  -> 微信异步通知商户后端
  -> 后端验签、解密、校验金额并更新订单
```

客户端支付成功回调不能作为唯一依据，因为用户可能关掉页面、断网或伪造客户端请求。

## 2. 微信支付准备工作

### 2.1 数据加密

微信支付 API v3 涉及多种材料：

- 商户私钥：用于商户请求签名。
- 商户证书序列号：标识商户证书。
- API v3 Key：用于解密回调资源。
- 微信支付平台证书：用于验证微信平台签名。

配置绑定类是 [`WeChatProperties.java`](../sky-common/src/main/java/com/sky/properties/WeChatProperties.java)，配置来自 `application.yml` 的 `sky.wechat`。工具类 [`WeChatPayUtil.java`](../sky-common/src/main/java/com/sky/utils/WeChatPayUtil.java) 使用 `WechatPayHttpClientBuilder` 创建客户端，自动处理请求签名和响应验签相关能力。

支付金额在微信接口中以分为单位，代码通常先把元转换为分：

```java
int totalFen = total.multiply(new BigDecimal("100")).intValue();
```

不要用 `double` 直接做金额核心计算，优先使用 `BigDecimal`，并明确舍入规则。

支付签名给小程序的内容大致是：

```text
appid\n
timeStamp\n
nonceStr\n
prepay_id=...\n
```

再使用商户私钥做 `SHA256withRSA` 签名，返回 `timeStamp`、`nonceStr`、`package`、`signType`、`paySign`。

### 2.2 公网域名获取

微信服务器不能访问你电脑的 `localhost`。支付回调必须配置公网可访问的 HTTPS 地址，例如：

```text
https://your-domain.example/notify/paySuccess
```

开发阶段可以使用内网穿透工具，但要注意域名变化、HTTPS 证书、回调路径和安全风险。真实密钥、私钥文件路径和证书绝不应提交到公共仓库；当前开发配置里的历史示例值应立即替换。

## 3. 当前仓库的“模拟支付”现状

这是最容易误解的地方：

- `OrderServiceImpl.payment` 中真正调用 `weChatPayUtil.pay(...)` 的代码被注释了。
- 代码改为 `new JSONObject()`，因此返回的支付 VO 实际上是空数据。
- `OrderController.payment` 调用完 payment 后直接调用 `orderService.paySuccess(...)`，等于服务端主动模拟支付成功。
- 小程序支付页中真实 `wx.requestPayment` 代码也被注释，目前只是弹出“支付成功”提示。

所以本仓库可以学习支付流程的代码结构，但不能据此判断已经完成真实微信支付。

## 4. 异步回调和幂等

回调入口 [`PayNotifyController.java`](../sky-server/src/main/java/com/sky/controller/nofity/PayNotifyController.java)：

```text
POST /notify/paySuccess
  -> 读取原始请求体
  -> AES-GCM 解密 resource
  -> 取得 out_trade_no 和 transaction_id
  -> orderService.paySuccess
  -> 返回 HTTP 200 和 SUCCESS
```

生产处理至少要校验：

1. 微信平台签名和证书，而不是只要能解密就接受。
2. 商户号、订单号、订单金额和币种是否与本地订单一致。
3. 本地订单当前是否已经支付；重复回调不能重复推送或重复记账。
4. 更新应使用条件更新，例如 `where id=? and pay_status=0`，并根据影响行数实现幂等。
5. 成功后快速返回，复杂通知可以异步处理。

当前 `paySuccess` 没有充分检查订单为空、金额匹配和重复回调，属于学习代码需要重点标记的风险。

---

# 八、Day09 技术总结：百度地图开放平台

## 1. 百度地图开放平台简介

订单提交时，项目需要判断用户地址是否在配送范围内。百度地图提供：

- 地理编码：地址文本 -> 经纬度。
- 路线规划：起点和终点 -> 驾车距离。

## 2. 代码开发

### 2.1 `application.yml`

配置：

```yaml
sky:
  shop:
    address: 北京市朝阳区北苑东路11号院
  baidu:
    ak: ${BAIDU_AK:请替换为自己的AK}
```

当前项目使用 `@Value("${sky.shop.address}")` 和 `@Value("${sky.baidu.ak}")` 注入到 `OrderServiceImpl`。更复杂的配置也可以像微信那样使用 `@ConfigurationProperties`。

### 2.2 `OrderServiceImpl`

在 [`OrderServiceImpl.checkOutOfRange`](../sky-server/src/main/java/com/sky/service/impl/OrderServiceImpl.java) 中：

```text
店铺地址 --geocoding/v3--> 店铺经纬度
用户地址 --geocoding/v3--> 用户经纬度
两点坐标 --directionlite/v1/driving--> 驾车距离
距离 > 30000 米 -> 抛出“超出配送范围”
```

在 `submit` 中，地址存在并且购物车非空之前，先调用这个检查。远程响应通过 Fastjson 读取 `status`、`result.location` 和 `routes[0].distance`。

### 2.3 小程序代码补充

小程序只负责收集和提交地址；配送范围的可信判断必须在后端，因为前端代码可以被修改。开发时检查：

- 请求是否发到正确的 `/user/order/submit`。
- 地址字符串是否包含省、市、区和详细地址。
- 后端异常是否被前端展示为可理解提示。

### 2.4 第三方调用的可靠性

当前实现适合演示，但要继续改进：

- 对 HTTP 非 2xx、JSON 缺字段、空 routes 做明确处理。
- 设置超时、限流和有限重试。
- 店铺坐标可以缓存，不必每次订单都地理编码。
- 百度不可用时要明确拒绝下单、使用备用策略还是人工处理。
- AK 只能放服务端，日志不要输出完整请求中的敏感信息。

还要注意：当前地图调用位于 `@Transactional submit` 方法中，网络慢会让数据库事务持有更久。生产系统会评估是否把远程校验放在事务开始前，或者缩小事务边界。

---

# 九、Day10 技术总结：Spring Task 与 WebSocket

## 1. Spring Task

### 1.1 cron 表达式

Spring 的 cron 常用六位：

```text
秒 分 时 日 月 星期
```

示例：

| 表达式 | 含义 |
| --- | --- |
| `0 0 1 * * ?` | 每天凌晨 1 点 |
| `0 * * * * ?` | 每分钟第 0 秒 |
| `0/5 * * * * ?` | 每 5 秒 |
| `1/5 * * * * ?` | 从第 1 秒开始每 5 秒 |

`?` 表示不指定“日”或“星期”中的一个字段。写 cron 时先用自然语言描述，再逐位对应，避免把分钟和小时写反。

### 1.2 入门案例

任务类：

- [`MyTask.java`](../sky-server/src/main/java/com/sky/task/MyTask.java)：每天 1 点打印日志。
- [`OrderTask.java`](../sky-server/src/main/java/com/sky/task/OrderTask.java)：处理超时未支付和配送超时订单。
- [`WebSocketTask.java`](../sky-server/src/main/java/com/sky/task/WebSocketTask.java)：每 5 秒广播测试消息。

订单任务当前为了演示改成每 5 秒：

```java
@Scheduled(cron = "1/5 * * * * ?")
public void processTimeoutOrders() {
    LocalDateTime time = LocalDateTime.now().plusMinutes(-15);
    List<Orders> orders = orderMapper
            .getByStatusAndOrderTime(Orders.PENDING_PAYMENT, time);
    ...
}
```

标准 Spring 还需要 `@EnableScheduling`。当前仓库能找到 `@Scheduled`，但没有明显找到 `@EnableScheduling`，所以启动后要用日志验证任务是否真的执行；必要时在启动类或配置类添加：

```java
@EnableScheduling
```

生产环境必须考虑：

- 多实例时每台机器都会执行，需分布式锁或任务平台。
- 重复执行必须幂等。
- 更新订单要带状态条件，避免覆盖已经被用户或管理员改变的状态。
- 大量订单要分页/分批处理。
- 失败要记录数量、堆栈并告警。

## 2. WebSocket

### 2.1 WebSocket 简介

HTTP 通常是“一问一答”；WebSocket 建立长连接后，服务端可以主动推送：

```text
客户端 --建立连接--> 服务端
客户端 <------------> 服务端（双方都能发送）
```

外卖场景中，支付成功产生新订单、用户催单时，管理端希望立即收到提醒，而不是每几秒轮询一次。

### 2.2 入门案例

配置类 [`WebSocketConfiguration.java`](../sky-server/src/main/java/com/sky/config/WebSocketConfiguration.java) 注册 `ServerEndpointExporter`。服务端 [`WebSocketServer.java`](../sky-server/src/main/java/com/sky/websocket/WebSocketServer.java)：

```java
@Component
@ServerEndpoint("/ws/{sid}")
public class WebSocketServer {
    private static Map<String, Session> sessionMap = new HashMap();

    @OnOpen
    public void onOpen(Session session, @PathParam("sid") String sid) {
        sessionMap.put(sid, session);
    }

    @OnMessage
    public void onMessage(String message, @PathParam("sid") String sid) { ... }

    @OnClose
    public void onClose(@PathParam("sid") String sid) {
        sessionMap.remove(sid);
    }
}
```

生命周期：

```text
连接 -> @OnOpen 保存 Session
消息 -> @OnMessage 接收
断开 -> @OnClose 清理
业务 -> sendToAllClient 主动广播
```

订单服务支付成功时广播：

```json
{"type":1,"orderId":1001,"content":"订单号：..."}
```

催单时 `type=2`。消息最好定义稳定 JSON 协议，前端根据 `type` 和 `orderId` 做动作，不要依赖中文字符串解析。

### 2.3 当前代码与小程序产物的差异

仓库的编译产物里还有一个连接外部 `wss://...` 并使用 STOMP/MQ 的客户端模块，它与本项目 Java 服务端的 `/ws/{sid}` 并不匹配；管理端源码也不在仓库。因此不要假设导入的小程序一定能直接测试本地 WebSocket。

### 2.4 生产注意点

- `HashMap` 不是线程安全的，连接回调和业务线程并发访问时应改用并发集合或加锁。
- 连接身份不能只信任客户端传入的 `sid`，应完成 JWT/权限校验。
- 断线、发送失败和重连要有策略。
- 多实例部署时，单机内存里的 Session 无法广播到其他实例，需要消息代理或 WebSocket 网关。
- `BasicRemote.sendText` 可能阻塞，复杂系统可使用异步发送。

---

# 十、Day11 和 Day12 技术总结：Apache ECharts 与 Apache POI

> 这两天通常最容易觉得“突然变难”：前者把后端数据变成图表，后者把 Java 对象写进 Excel 文件。关键思路都是先搞清楚数据形状，再学习 API。

## 1. Apache ECharts

### 1.1 介绍

ECharts 是前端图表库。后端不负责画图，而是提供适合图表的数据；前端把数据放入 `xAxis`、`series` 等配置。

当前仓库只有 README 对 Vue、ElementUI、ECharts 的技术选型说明，没有管理端前端源码或 ECharts 依赖。因此这一节要结合后端报表 VO 理解数据契约，不能声称仓库已经包含完整图表页面。

### 1.2 后端报表接口

[`ReportController.java`](../sky-server/src/main/java/com/sky/controller/admin/ReportController.java) 提供：

```text
GET /admin/report/turnoverStatistics
GET /admin/report/userStatistics
GET /admin/report/ordersStatistics
GET /admin/report/top10
GET /admin/report/export
```

返回对象：

| 接口 | VO | 图表含义 |
| --- | --- | --- |
| turnover | `TurnoverReportVO` | 每天营业额 |
| user | `UserReportVO` | 截止当天总用户和新增用户 |
| orders | `OrderReportVO` | 总订单、有效订单、完成率 |
| top10 | `SalesTop10ReportVO` | 商品名称和销量 |

当前 VO 中的 `dateList`、`turnoverList` 等字段是逗号分隔字符串。例如：

```text
dateList = "2026-09-01,2026-09-02"
turnoverList = "100.0,230.5"
```

前端图表需要先拆分：

```javascript
const dates = response.data.dateList.split(',')
const turnover = response.data.turnoverList.split(',').map(Number)

option = {
  xAxis: { data: dates },
  yAxis: {},
  series: [{ type: 'line', data: turnover }]
}
```

更适合现代 API 的返回方式通常是 JSON 数组，而不是逗号字符串；这里要先兼容当前接口，再考虑重构契约。

### 1.3 `ReportServiceImpl` 如何统计

在 [`ReportServiceImpl.java`](../sky-server/src/main/java/com/sky/service/impl/ReportServiceImpl.java)：

1. 生成开始日期到结束日期的 `LocalDate` 列表。
2. 每天构造 `LocalDateTime.of(date, LocalTime.MIN/MAX)`。
3. 调 Mapper 聚合查询。
4. 把结果放进列表，再组装 VO。

营业额统计只计算 `Orders.COMPLETED` 完成订单；订单统计用总订单数和完成订单数计算：

```java
double rate = totalOrderCount == 0
        ? 0.0
        : (double) validOrderCount / totalOrderCount;
```

销量 Top10 调用 `OrderMapper.getSalesTop`，SQL 按商品名称分组、数量倒序并限制 10 条。

阅读报表时重点检查：

- `begin` 是否晚于 `end`。当前代码没有显式校验，可能导致日期循环逻辑异常。
- 结束日期是否包含整天。当前使用 `LocalTime.MAX`，但 XML 中的严格比较符仍要一起核对。
- 空结果是否按 0 返回。
- 金额精度、时区、数据库索引和查询范围限制。

## 2. Apache POI

### 2.1 介绍

Apache POI 是 Java 操作 Microsoft Office 文件的库。常用对象：

```text
XSSFWorkbook -> 一个 .xlsx 工作簿
XSSFSheet    -> 一个工作表
XSSFRow      -> 一行
XSSFCell     -> 一个单元格
```

项目依赖在 `sky-server/pom.xml` 的 `poi` 和 `poi-ooxml`。模板位于：

[`sky-server/src/main/resources/template/运营数据报表模板.xlsx`](../sky-server/src/main/resources/template/运营数据报表模板.xlsx)

### 2.2 入门案例

#### 2.2.1 将数据写入 Excel 文件

最小示例：

```java
try (XSSFWorkbook workbook = new XSSFWorkbook()) {
    XSSFSheet sheet = workbook.createSheet("订单");
    XSSFRow header = sheet.createRow(0);
    header.createCell(0).setCellValue("订单号");
    header.createCell(1).setCellValue("金额");

    XSSFRow row = sheet.createRow(1);
    row.createCell(0).setCellValue("NO1001");
    row.createCell(1).setCellValue(38.5);

    try (OutputStream out = Files.newOutputStream(Path.of("orders.xlsx"))) {
        workbook.write(out);
    }
}
```

要点：行号和列号从 0 开始；数字、字符串、日期有不同的 `setCellValue` 重载；大文件要考虑内存和流式写入。

项目实际导出不是从空表创建，而是加载模板：

```java
InputStream in = getClass().getClassLoader()
        .getResourceAsStream("template/运营数据报表模板.xlsx");
XSSFWorkbook excel = new XSSFWorkbook(in);
XSSFSheet sheet = excel.getSheet("sheet1");
sheet.getRow(3).getCell(2).setCellValue(businessData.getTurnover());
```

`ReportServiceImpl.exportBusinessData` 的流程：

```text
最近 30 天（今天往前 30 天到昨天）
  -> WorkspaceService 查询概览和每日数据
  -> 加载 xlsx 模板
  -> 填充营业额、完成率、客单价、新增用户
  -> 填充 30 行每日明细
  -> 写入 HttpServletResponse 输出流
  -> 浏览器下载
```

#### 2.2.2 读取 Excel 文件中的数据

```java
try (InputStream in = Files.newInputStream(Path.of("orders.xlsx"));
     XSSFWorkbook workbook = new XSSFWorkbook(in)) {
    XSSFSheet sheet = workbook.getSheetAt(0);
    for (Row row : sheet) {
        for (Cell cell : row) {
            System.out.println(cell.toString());
        }
    }
}
```

真实导入时要处理空行、不同单元格类型、日期单元格、公式、文件大小限制和恶意文件。不要只用 `cell.toString()` 就认为所有数据类型都可靠。

### 2.3 HTTP 文件下载细节

文件下载接口应设置响应头，例如：

```java
response.setContentType(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
response.setHeader("Content-Disposition",
        "attachment; filename=business-data.xlsx");
```

当前项目的导出方法主要写输出流，没有完整设置下载响应头；异常时只 `printStackTrace`，资源关闭也不如 try-with-resources 稳健。把模板读取、Workbook 和输出流放进 try-with-resources 是很好的练习。

### 2.4 ECharts 与 POI 的共同思路

两者都不是“调用一个神奇方法就结束”：

```text
数据库聚合结果
      |
      +--> 设计 VO/字段含义
      |       |
      |       +--> ECharts：数组 -> xAxis/series
      |       |
      |       +--> POI：行/列 -> 单元格 -> 下载文件
```

先定义数据契约，再写展示代码，问题会比直接堆 API 少很多。

---

# 十一、把完整订单链路串起来

如果你只想精读一个功能，选择用户下单。它把前面几乎所有知识点连接起来。

## 1. 请求进入

```text
POST /user/order/submit
Headers: authentication=<JWT>
Body: OrdersSubmitDTO JSON
```

`WebMvcConfiguration` 注册用户 JWT 拦截器。拦截器解析 Token，把 `userId` 写入 `BaseContext`，然后才放行到 Controller。

## 2. Controller 边界

[`OrderController.submit`](../sky-server/src/main/java/com/sky/controller/user/OrderController.java) 只做三件事：接收 DTO、调用 Service、包装 `Result<OrderSubmitVO>`。它不写 SQL，也不负责决定订单状态。

## 3. Service 事务

[`OrderServiceImpl.submit`](../sky-server/src/main/java/com/sky/service/impl/OrderServiceImpl.java) 标有 `@Transactional`，步骤是：

1. 查询地址，不存在就抛异常。
2. 调百度地图检查配送范围。
3. 从 `BaseContext` 取得用户 ID，查询购物车。
4. 创建 `Orders`，设置订单号、用户、地址、状态和支付状态。
5. 插入订单明细，使用订单主键。
6. 清空购物车。
7. 组装 `OrderSubmitVO` 返回。

任何一步抛出运行时业务异常，事务代理会回滚已经执行的数据库写入。

## 4. Mapper 和数据库

`OrderMapper.xml` 使用 `useGeneratedKeys="true" keyProperty="id"` 把数据库生成的订单 ID 回填到 Entity；`OrderDetailMapper.xml` 使用 `<foreach>` 批量插入多条明细。

## 5. 响应和失败

成功：

```text
Service 返回 VO -> Result.success -> Jackson -> JSON -> 客户端
```

失败分两类：

- Token 失败：拦截器直接返回 HTTP 401。
- 业务失败：事务回滚，`GlobalExceptionHandler` 返回 `code=0` 的 JSON。

## 6. 阅读时主动发现的改进点

- 地址查询应校验地址属于当前用户，不能只凭 ID。
- 订单金额不应完全信任客户端，应按服务端商品价格重新计算。
- 当前毫秒时间生成订单号，高并发下要考虑唯一性。
- 地图远程调用可能拉长事务。
- ThreadLocal 应在请求完成后清理。

这些问题不是为了否定项目，而是从“能跑的教学代码”走向“可靠系统”时必须学会提出的问题。

---

# 十二、学习检查清单与练习

## 1. 每天学习后的自测

- 我能指出这个功能的 URL、Controller、Service、Mapper 和 SQL 吗？
- 我能说清请求参数为什么用 DTO、响应为什么用 VO 吗？
- 我能区分异常发生在拦截器、Controller、Service 还是 Mapper 吗？
- 我能解释数据为什么放 Redis、何时删除、没有 TTL 会怎样吗？
- 我能区分微信临时 `code`、本地 JWT 和支付回调吗？
- 我能说明 WebSocket 和 HTTP 的发起方向差异吗？
- 我能说明 ECharts 需要什么数组，POI 如何定位行和单元格吗？

## 2. 推荐练习顺序

1. 给分类增加一个查询接口，完整走 Controller-Service-Mapper。
2. 给员工登录增加参数校验和统一参数异常处理。
3. 给菜品缓存增加 TTL，并记录命中/未命中日志。
4. 把 `keys("dish_*")` 改为更安全的扫描或精确 Key 清理策略。
5. 给订单金额改为服务端重新计算。
6. 给支付成功更新增加“只允许未支付订单更新”的条件，实现幂等。
7. 为 `OrderTask` 增加状态条件更新和处理数量日志。
8. 为 WebSocket 消息定义统一 DTO，并增加断线清理和并发集合。
9. 把报表 VO 的逗号字符串改为 JSON 数组，配套修改前端图表数据。
10. 为 Excel 导出补齐响应头和 try-with-resources。

## 3. 最终心智模型

> Controller 定义 HTTP 边界，Service 执行业务规则，Mapper 读写数据；DTO、Entity、VO 管理数据在不同边界的形态；配置、拦截器、切面、异常处理、缓存、任务和 WebSocket 为多个业务提供通用能力。

当你能从一个 URL 一直追到 SQL，并能解释结果如何变成 JSON、缓存、消息或 Excel 文件时，就已经掌握了这个项目最重要的后端开发方法。
