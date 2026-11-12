# 04 Controller、Service、Mapper 分层架构

## 1. 分层架构解决什么问题

后端接口通常需要完成多类工作：接收 HTTP 请求、校验和转换参数、执行业务规则、访问数据库、封装响应。如果把这些代码全部写在一个类或一个方法中，代码会难以理解、测试和修改。

分层架构把职责拆开：

```text
HTTP 请求
    -> Controller：处理 Web 接口和请求参数
    -> Service：编排业务流程和业务规则
    -> Mapper：访问数据库
    -> MySQL
```

每一层只关注自己的职责，并通过方法或接口与相邻层协作。

## 2. 项目中的三层代码

### 2.1 Controller：接口适配层

项目中的 [`EmployeeController.java`](../../../sky-server/src/main/java/com/sky/controller/admin/EmployeeController.java) 使用 `@RestController` 声明为 Web 控制器：

```java
@RestController
@RequestMapping("/admin/employee")
public class EmployeeController {

    @Autowired
    private EmployeeService employeeService;

    @PostMapping("/login")
    public Result<EmployeeLoginVO> login(
            @RequestBody EmployeeLoginDTO employeeLoginDTO) {
        Employee employee = employeeService.login(employeeLoginDTO);
        return Result.success(...);
    }
}
```

Controller 主要负责：

- 声明 URL 和 HTTP 方法；
- 接收请求参数，例如 `@RequestBody`、`@PathVariable`；
- 调用 Service 完成业务；
- 将业务结果封装成 `Result`、VO 等响应对象。

Controller 不应该直接编写 SQL，也不应该把复杂的订单、库存或权限规则全部堆在接口方法中。

### 2.2 Service：业务逻辑层

业务接口位于 [`EmployeeService.java`](../../../sky-server/src/main/java/com/sky/service/EmployeeService.java)，实现位于 [`EmployeeServiceImpl.java`](../../../sky-server/src/main/java/com/sky/service/impl/EmployeeServiceImpl.java)：

```java
public interface EmployeeService {
    Employee login(EmployeeLoginDTO employeeLoginDTO);
    void save(EmployeeDTO employeeDTO);
}
```

```java
@Service
public class EmployeeServiceImpl implements EmployeeService {

    @Autowired
    private EmployeeMapper employeeMapper;

    @Override
    public Employee login(EmployeeLoginDTO employeeLoginDTO) {
        Employee employee = employeeMapper.getByUsername(
                employeeLoginDTO.getUsername());
        // 校验账号、密码和账号状态
        return employee;
    }
}
```

Service 负责：

- 编排一次完整的业务操作；
- 执行校验、状态判断和业务规则；
- 调用一个或多个 Mapper；
- 在需要时开启事务、调用缓存或外部服务。

例如新增员工时，Service 会设置默认状态、默认密码并调用 Mapper 保存，而 Controller 只负责接收 DTO 和返回结果。

### 2.3 Mapper：数据访问层

项目中的 Mapper 接口通常位于 `sky-server/src/main/java/com/sky/mapper`：

```java
public interface EmployeeMapper {
    Employee getByUsername(String username);
    void insert(Employee employee);
}
```

Mapper 负责描述数据库操作，具体 SQL 通常位于 `sky-server/src/main/resources/mapper` 下的 XML 文件中，或写在注解中。MyBatis-Spring 会为 Mapper 接口创建代理对象，Service 注入的是这个代理对象，而不是手动 `new` 出来的实现类。

## 3. 一次请求的完整链路

以员工登录为例：

```text
POST /admin/employee/login
        |
        v
DispatcherServlet
        |
        v
EmployeeController.login()
        |
        |  employeeService.login(employeeLoginDTO)
        v
EmployeeServiceImpl.login()
        |
        |  employeeMapper.getByUsername(username)
        v
MyBatis Mapper 代理
        |
        v
EmployeeMapper.xml -> MySQL
        |
        v
Employee -> EmployeeLoginVO -> Result JSON
```

请求进入 Controller，Controller 把任务交给 Service；Service 处理业务并调用 Mapper；Mapper 访问数据库并返回数据；结果再沿调用链返回，最终由 Spring MVC 转换成 JSON。

## 4. 为什么 Controller 依赖 Service 接口

Controller 应该依赖 [`EmployeeService`](../../../sky-server/src/main/java/com/sky/service/EmployeeService.java)，而不是直接依赖 `EmployeeServiceImpl`：

```java
private EmployeeService employeeService;
```

这样可以让 Controller 依赖稳定的业务契约，具体实现由 Spring 容器装配。这样做能降低耦合，也便于测试时传入 Mock Service。Service 与 Mapper 之间也可以使用同样的思路，让业务层依赖数据访问接口。

## 5. 分层不是绝对隔离

分层的目的不是增加无意义的类，而是让职责和变化边界清晰。简单查询可以由 Controller 调用一个简单的 Service 方法；复杂业务则应该由 Service 统一编排多个数据访问操作，并通过事务保证整体一致性。

通常不建议：

- Controller 直接调用 Mapper，绕过 Service；
- Service 返回只为数据库设计的对象给前端；
- Mapper 中写业务判断、调用外部服务或生成 JWT；
- 为了“看起来分层”而创建没有任何业务价值的空壳方法。

## 6. 学习重点

- Controller 处理 HTTP 适配，Service 处理业务规则，Mapper 处理数据库访问。
- 三层通过依赖注入协作，不由业务代码手动创建依赖对象。
- Controller 通常依赖 Service 接口，Service 通常依赖 Mapper 接口。
- 分层让代码更容易测试、替换和维护，也更容易定位问题属于哪一层。
