# 08 Jackson 与 JSON 序列化

## 1. 核心概念

HTTP 通常传输 JSON，而 Java 使用对象。两者之间需要转换：

- **序列化（serialization）**：Java 对象 -> JSON，通常用于生成 HTTP 响应。
- **反序列化（deserialization）**：JSON -> Java 对象，通常用于读取 HTTP 请求。

Spring MVC 通过 `HttpMessageConverter` 完成 HTTP 消息和 Java 类型之间的转换。Jackson 是其中负责 JSON 的实现之一。

## 2. Spring MVC 自带的消息转换器

Spring MVC 会维护一个 `HttpMessageConverter<?>` 列表。列表中的每个转换器声明自己能读写哪些 Java 类型以及哪些媒体类型（例如 `application/json`）。常见的默认转换器包括：

| 转换器 | 主要用途 |
| --- | --- |
| `ByteArrayHttpMessageConverter` | `byte[]` |
| `StringHttpMessageConverter` | `String` |
| `ResourceHttpMessageConverter` | 文件、资源 |
| `ResourceRegionHttpMessageConverter` | 资源的范围请求 |
| `AllEncompassingFormHttpMessageConverter` | 表单数据 |
| `MappingJackson2HttpMessageConverter` | JSON 与 Java 对象 |

实际列表会根据 classpath 中的依赖变化。例如，只有存在 Jackson 依赖时，Jackson 转换器才会加入；XML、Gson 等转换器也需要相应依赖。

本项目的 `sky-server` 引入了 `spring-boot-starter-web`，`sky-common` 引入了 `spring-boot-starter-json`，所以 Spring Boot 会自动配置 Jackson 支持和默认转换器。

## 3. 本项目的自定义转换器

配置位于 [`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java) 的 `extendMessageConverters` 方法：

```java
@Override
public void extendMessageConverters(List<HttpMessageConverter<?>> converters) {
    log.info("Registering custom Jackson message converter");

    MappingJackson2HttpMessageConverter converter =
            new MappingJackson2HttpMessageConverter();
    converter.setObjectMapper(new JacksonObjectMapper());
    converters.add(0, converter);
}
```

### 3.1 `extendMessageConverters` 做了什么

Spring MVC 初始化时会把已有的转换器列表传给这个扩展点。`extendMessageConverters` 是在默认转换器基础上增加或调整配置，因此默认转换器仍然保留。

这和 `configureMessageConverters` 不同：后者可能导致默认转换器不再自动加入，通常需要自己提供完整列表。

### 3.2 为什么插入索引 `0`

```java
converters.add(0, converter);
```

这会把自定义转换器放到列表首位。Spring 选择转换器时会按顺序检查 `canRead` 或 `canWrite`，因此对于 JSON 请求和响应，自定义转换器会先于后面的默认 Jackson 转换器尝试。

默认 Jackson 转换器并没有被删除；如果自定义转换器不适用，后面的转换器仍有机会处理。

## 4. `JacksonObjectMapper` 的项目规则

[`JacksonObjectMapper.java`](../../../sky-common/src/main/java/com/sky/json/JacksonObjectMapper.java) 继承 Jackson 的 `ObjectMapper`，是本项目 JSON 规则的入口。

它注册了 Java 8 时间类型的序列化器和反序列化器：

| Java 类型 | JSON 格式 |
| --- | --- |
| `LocalDate` | `yyyy-MM-dd` |
| `LocalDateTime` | `yyyy-MM-dd HH:mm` |
| `LocalTime` | `HH:mm:ss` |

因此，请求读取和响应输出都会使用这些格式（除非字段自身通过注解覆盖规则）。

另外，构造方法中执行：

```java
this.configure(FAIL_ON_UNKNOWN_PROPERTIES, false);
```

这表示 JSON 中出现目标 Java 类没有的字段时，Jackson 会忽略该字段，而不是因为未知字段直接反序列化失败。

文件中的：

```java
this.getDeserializationConfig()
        .withoutFeatures(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);
```

返回了一个新的配置对象但没有保存回来，实际起作用的是前面的 `configure(..., false)`。

## 5. `@RequestBody` 如何关联到 Java 对象

以项目中的地址簿接口为例：

```java
@PostMapping
public Result save(@RequestBody AddressBook addressBook) {
    addressBookService.save(addressBook);
    return Result.success();
}
```

客户端发送：

```http
POST /user/addressBook
Content-Type: application/json
```

```json
{
  "consignee": "张三",
  "phone": "13800000000",
  "provinceCode": "110000"
}
```

`@RequestBody` 本身不解析 JSON，它只是告诉 Spring：这个参数的值应该从 HTTP 请求体读取。大致调用链如下：

```text
HTTP 请求
  -> DispatcherServlet
  -> 找到 Controller 方法
  -> RequestResponseBodyMethodProcessor 发现 @RequestBody
  -> 取得目标类型 AddressBook.class
  -> 按 Content-Type 和目标类型遍历消息转换器
  -> 自定义 MappingJackson2HttpMessageConverter.canRead(...)
  -> JacksonObjectMapper.readValue(..., AddressBook.class)
  -> 创建并填充 AddressBook 对象
  -> 调用 save(addressBook)
```

更具体地说，Spring 会根据方法参数的声明类型和请求的 `Content-Type` 查找第一个可读转换器，逻辑可以抽象为：

```java
Class<?> targetType = AddressBook.class;

for (HttpMessageConverter<?> converter : converters) {
    if (converter.canRead(targetType, MediaType.APPLICATION_JSON)) {
        return converter.read(targetType, request);
    }
}
```

这里的联系来自两部分：

1. `@RequestBody` 让 Spring 从请求体取数据。
2. `AddressBook` 让 Spring 把 JSON 反序列化成 `AddressBook.class`。

Jackson 再根据 JSON 字段名和 Java 属性名进行匹配。`AddressBook` 使用 Lombok 的 `@Data` 和 `@NoArgsConstructor`，会提供 getter、setter 和无参构造方法，便于 Jackson 创建和填充对象。

## 6. 返回值如何变成 JSON

`@RestController` 相当于 `@Controller` 加 `@ResponseBody`。因此 Controller 返回的 Java 对象也会经过消息转换器：

```text
Result / VO Java 对象
  -> RequestResponseBodyMethodProcessor
  -> 按返回类型和 Accept 头选择可写转换器
  -> MappingJackson2HttpMessageConverter
  -> JacksonObjectMapper.writeValue(...)
  -> JSON 响应
```

所以同一个自定义转换器同时参与两个方向：

```text
请求：JSON -> JacksonObjectMapper -> DTO / Entity
响应：DTO / VO / Result -> JacksonObjectMapper -> JSON
```

## 7. 日期格式注解的优先级示例

项目中的订单 DTO 对字段单独指定了格式：

```java
@JsonFormat(
    shape = JsonFormat.Shape.STRING,
    pattern = "yyyy-MM-dd HH:mm:ss")
private LocalDateTime estimatedDeliveryTime;
```

这类字段级注解可以覆盖 `JacksonObjectMapper` 的全局默认格式。因此该字段使用 `yyyy-MM-dd HH:mm:ss`，而没有单独注解的 `LocalDateTime` 通常使用项目全局配置的 `yyyy-MM-dd HH:mm`。

## 8. 总结

```text
@RequestBody
    决定从请求体读取
方法参数类型 AddressBook
    决定目标 Java 类型
HttpMessageConverter
    决定如何在 HTTP 消息和 Java 类型之间转换
MappingJackson2HttpMessageConverter
    负责 JSON 读写
JacksonObjectMapper
    决定日期格式、未知字段等具体 Jackson 规则
```

