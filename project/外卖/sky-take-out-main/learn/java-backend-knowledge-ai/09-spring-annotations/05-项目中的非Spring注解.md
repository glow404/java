# 项目中的非 Spring 注解

学习注解时，不能把所有 `@xxx` 都当成 Spring 注解。当前项目同时使用了 Lombok、Jackson、MyBatis、Swagger 和 Java 自带注解。

## 1. Lombok

| 注解 | 作用 |
| --- | --- |
| `@Data` | 生成 getter、setter、`toString`、`equals`、`hashCode` |
| `@Builder` | 生成 Builder 构造方式 |
| `@NoArgsConstructor` | 生成无参构造方法 |
| `@AllArgsConstructor` | 生成全参构造方法 |
| `@Slf4j` | 生成日志字段 `log` |

例如：

```java
@Data
@NoArgsConstructor
public class AddressBook {
    private Long id;
    private String consignee;
}
```

这些注解主要在编译期生成 Java 代码，不负责把对象注册进 Spring 容器。

## 2. Jackson

```java
@JsonFormat(
        shape = JsonFormat.Shape.STRING,
        pattern = "yyyy-MM-dd HH:mm:ss")
private LocalDateTime estimatedDeliveryTime;
```

`@JsonFormat` 规定某个字段的 JSON 格式。它由 Jackson 读取，不是 Spring MVC 的核心注解。

## 3. MyBatis

```java
@Mapper
public interface EmployeeMapper {
}
```

`@Mapper` 由 MyBatis 或 MyBatis-Spring 体系处理，用来声明 Mapper 接口。`@Select`、`@Insert`、`@Update` 等也属于 MyBatis 注解。

PageHelper 的 `PageInterceptor` 同样属于 MyBatis 生态；但本项目通过 Spring 的 `@Configuration` 和 `@Bean` 把它注册进容器。

## 4. OpenAPI / Swagger

```java
@Tag(name = "菜品相关接口")
@Operation(summary = "根据分类 id 查询菜品")
```

这些注解由 springdoc/OpenAPI 读取，用于生成接口文档，不参与 Controller 的核心业务执行。

## 5. Java 原生注解

```java
@Override
public void addInterceptors(InterceptorRegistry registry) {
}
```

`@Override` 是 Java 语言注解，告诉编译器该方法必须重写父类或接口方法。它不是 Spring 注解。

## 6. 自定义注解的元注解

项目中的 `@AutoFill` 是自定义注解，使用 Java 原生元注解声明自己的使用范围和保留时间：

```java
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface AutoFill {
    OperationType value();
}
```

`@Target` 限制注解可以出现的位置，`@Retention` 决定注解保留到哪个阶段。这里必须保留到 `RUNTIME`，因为 `AutoFIllAspect` 会在运行时通过反射读取 `@AutoFill` 的 `value`。

它本身不具备自动执行能力；只有切面用 `@Pointcut` 匹配它，并在 `@Before` 通知中解释它，才会产生自动填充审计字段的业务效果。

## 7. 判断一个注解属于谁

可以从 import 判断来源：

```java
org.springframework.*       -> Spring / Spring Boot
org.springframework.web.*   -> Spring MVC
org.springframework.cache.* -> Spring Cache
lombok.*                    -> Lombok
com.fasterxml.jackson.*     -> Jackson
org.apache.ibatis.*         -> MyBatis
io.swagger.*                -> OpenAPI / Swagger
java.lang.*                 -> Java 标准库
```

同一个项目里，注解经常协同工作，但每个注解仍由自己的框架解释。例如：`@RestController` 负责 Web 组件和响应语义，`@RequestBody` 负责请求体绑定，`@JsonFormat` 负责 Jackson 字段格式，三者并不是同一个框架的功能。
