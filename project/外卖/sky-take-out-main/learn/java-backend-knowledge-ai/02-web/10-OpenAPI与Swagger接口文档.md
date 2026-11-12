# 10 OpenAPI 与 Swagger 接口文档

## 1. 作用

接口文档让前后端知道：接口路径是什么、使用什么 HTTP 方法、参数格式是什么、返回什么数据。OpenAPI 可以根据 Controller 和注解自动生成文档。

## 2. 项目中的代码

配置位置：[`OpenApiConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/OpenApiConfiguration.java)

```java
@Bean
public GroupedOpenApi adminOpenApi() {
    return GroupedOpenApi.builder()
            .group("admin")
            .pathsToMatch("/admin/**")
            .build();
}
```

Controller 中使用：

```java
@Tag(name = "员工相关接口")
@Operation(summary = "新增员工")
```

项目按 `/admin/**` 和 `/user/**` 分成两个文档组，便于分别查看管理端和用户端 API。

## 3. 学习重点

- 文档注解是接口契约的一部分，不只是装饰。
- 接口变更时，参数 DTO、返回 VO 和文档应同步。
- 自动文档只能描述代码已经表达的内容，业务规则和状态限制仍需要补充说明。

