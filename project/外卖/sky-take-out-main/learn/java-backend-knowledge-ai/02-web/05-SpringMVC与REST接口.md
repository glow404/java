# 05 Spring MVC 与 REST 接口

## 1. 核心概念

Spring MVC 将 HTTP 请求映射到 Java 方法，并负责参数转换、调用控制器和把返回对象序列化为 JSON。REST 风格通常使用资源路径加 HTTP 方法表达操作。

## 2. 项目中的代码

员工接口位于 [`EmployeeController.java`](../../../sky-server/src/main/java/com/sky/controller/admin/EmployeeController.java)：

```java
@RestController
@RequestMapping("/admin/employee")
public class EmployeeController {

    @PostMapping("/login")
    public Result<EmployeeLoginVO> login(
            @RequestBody EmployeeLoginDTO dto) { ... }

    @GetMapping("/page")
    public Result<PageResult> page(EmployeePageQueryDTO dto) { ... }

    @PutMapping
    public Result update(@RequestBody EmployeeDTO dto) { ... }
}
```

## 3. 请求方式与请求路径如何决定接口

### 3.1 URL 与 HTTP 请求方式是两个概念

严格来说，URL 指的是请求地址，例如：

```text
/admin/employee/login
```

HTTP 请求方式是请求的另一项信息，例如 `GET`、`POST`、`PUT`、`DELETE`。Spring MVC 匹配 Controller 方法时，通常会把下面两项结合起来判断：

```text
HTTP 请求方式 + 最终请求路径
```

因此，可以把接口匹配条件理解为：

```text
最终请求路径 = 类级别路径 + 方法级别路径
接口匹配条件 = HTTP 请求方式 + 最终请求路径 + 可选条件
```

例如项目中的：

```java
@RequestMapping("/admin/employee")
public class EmployeeController {

    @PostMapping("/login")
    public Result login(...) {
        // POST /admin/employee/login
    }

    @PostMapping
    public Result save(...) {
        // POST /admin/employee
    }

    @PutMapping
    public Result update(...) {
        // PUT /admin/employee
    }
}
```

类上的 `@RequestMapping("/admin/employee")` 是公共路径前缀，方法上的路径会与它拼接：

```text
类级别路径：/admin/employee
方法级别路径：/login
最终路径：/admin/employee/login
```

### 3.2 相同路径可以使用不同请求方式

下面两个接口的请求路径相同，但请求方式不同，因此可以同时存在：

```text
POST /admin/employee
PUT  /admin/employee
```

在这个项目中，它们表达不同的业务操作：

- `POST /admin/employee`：新增员工；
- `PUT /admin/employee`：修改员工。

这也是 REST 风格的常见设计。请求路径描述操作的资源，请求方式描述对资源执行的动作：

| 请求方式 | 常见语义 | 项目中的典型用途 |
| --- | --- | --- |
| `GET` | 查询资源 | 分页查询、根据 ID 查询 |
| `POST` | 新增资源或提交操作 | 新增员工、登录、下单 |
| `PUT` | 修改或整体更新资源 | 修改员工、修改菜品 |
| `DELETE` | 删除资源 | 删除分类、删除地址等 |

### 3.3 什么情况下会发生映射冲突

如果两个方法的请求方式和最终路径完全相同，Spring 无法判断应该调用哪一个，通常会在启动时报告映射冲突：

```java
@GetMapping("/employee")
public Result methodA() { }

@GetMapping("/employee")
public Result methodB() { }
```

它们都是：

```text
GET /employee
```

这种写法不能仅依靠方法名区分，因为方法名不是 HTTP 路由的一部分。需要修改路径、请求方式，或增加额外匹配条件。

### 3.4 还可以使用其他条件区分接口

Spring MVC 还支持通过查询参数、请求头和请求内容类型等条件进一步区分映射：

```java
@GetMapping(value = "/employee", params = "type=all")
public Result all() { }

@GetMapping(value = "/employee", params = "type=enabled")
public Result enabled() { }
```

此时两个请求的路径部分都是 `/employee`，但查询参数不同：

```text
GET /employee?type=all
GET /employee?type=enabled
```

Spring 会根据 `params` 条件选择不同的方法。实际开发中，也可以使用 `headers`、`consumes`、`produces` 等属性表达请求头或请求内容类型要求。

## 4. 请求如何被绑定

```text
POST /admin/employee/login
Content-Type: application/json
        │
        ▼
@RequestBody EmployeeLoginDTO
        │
        ▼
Controller 方法执行
        │
        ▼
Result<EmployeeLoginVO>
        │
        ▼
Jackson 序列化为 JSON
```

常见参数来源：

| 注解 | 来源 | 项目示例 |
| --- | --- | --- |
| `@RequestBody` | JSON 请求体 | 登录、下单、修改 |
| `@PathVariable` | `/resource/{id}` | 订单详情、菜品详情 |
| `@RequestParam` | 查询参数或表单参数 | 批量删除的 `ids` |
| 无注解对象参数 | Query String | 分页查询参数 |

## 5. 学习重点

- URL 前缀用来区分管理端和用户端：`/admin/**`、`/user/**`。
- `@PostMapping`、`@PutMapping` 等既是路由声明，也表达操作语义。
- 请求路径相同并不一定冲突，只要 HTTP 请求方式不同，或者存在其他明确的匹配条件。
- 类级别 `@RequestMapping` 和方法级别 `@GetMapping`、`@PostMapping` 等会拼接成最终请求路径。
- 相同请求方式和相同路径的映射不能重复，否则 Spring 无法确定调用哪个方法。
- Controller 返回 Java 对象，通常不手写 JSON 字符串。
- 接口边界应明确请求格式、响应格式和错误行为。

## 6. 学习练习

用 Postman 或 Swagger 调用一个分页接口，观察 Query 参数如何绑定到 DTO，再观察返回的 `Result` JSON 结构。
