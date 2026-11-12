# Web 请求与异常注解

## 1. Controller 与 URL 映射

### 1.1 `@RestController`

`@RestController` 等价于：

```java
@Controller
@ResponseBody
```

它表示这是一个 Controller，并且方法返回值默认直接写入 HTTP 响应体，通常由 Jackson 转成 JSON。

```java
@RestController
@RequestMapping("/user/addressBook")
public class AddressBookController {
}
```

### 1.2 `@RequestMapping` 与快捷映射注解

`@RequestMapping` 可以标记类或方法，声明 URL 路径和 HTTP 方法：

```java
@RequestMapping("/admin/dish")
public class DishController {

    @GetMapping("/page")
    public Result<PageResult> page(DishPageQueryDTO query) {
        return Result.success(dishService.pageQuery(query));
    }

    @PostMapping
    public Result save(@RequestBody DishDTO dishDTO) {
        dishService.saveWithFlavor(dishDTO);
        return Result.success();
    }
}
```

快捷注解及其对应关系：

```text
@GetMapping    = @RequestMapping(method = GET)
@PostMapping   = @RequestMapping(method = POST)
@PutMapping    = @RequestMapping(method = PUT)
@DeleteMapping = @RequestMapping(method = DELETE)
```

类级别的路径会与方法级别的路径拼接，例如上面的完整地址是 `/admin/dish/page`。

## 2. 请求参数绑定

### 2.1 `@RequestBody`

`@RequestBody` 表示从 HTTP 请求体读取数据。它本身不负责解析 JSON，而是让 Spring MVC 使用消息转换器将请求体转换为声明的 Java 类型：

```java
@PostMapping
public Result save(@RequestBody AddressBook addressBook) {
    addressBookService.save(addressBook);
    return Result.success();
}
```

当请求头为 `Content-Type: application/json` 时，Spring 会选择 `MappingJackson2HttpMessageConverter`，再使用项目自定义的 `JacksonObjectMapper` 创建 `AddressBook`。

```text
JSON 请求体
  -> HttpMessageConverter
  -> JacksonObjectMapper.readValue(..., AddressBook.class)
  -> addressBook 参数
```

### 2.2 `@RequestParam`

`@RequestParam` 绑定查询参数或表单参数：

```java
@DeleteMapping
public Result delete(@RequestParam List<Long> ids) {
    dishService.deleteBatch(ids);
    return Result.success();
}
```

请求示例：

```text
DELETE /admin/dish?ids=1&ids=2&ids=3
```

如果参数名和 Java 参数名一致，通常可以省略注解中的名称；需要明确映射时可以写 `@RequestParam("ids")`。

### 2.3 `@PathVariable`

`@PathVariable` 绑定 URL 路径变量：

```java
@PostMapping("/status/{status}")
public Result startOrStop(@PathVariable Integer status, Long id) {
    dishService.startOrStop(status, id);
    return Result.success();
}
```

请求中的 `/status/1` 会把 `1` 绑定到 `status`。

### 2.4 不加注解的简单参数

对于简单类型参数，Spring MVC 通常可以从查询参数绑定：

```java
@GetMapping("/list")
public Result<List<DishVO>> list(Long categoryId) {
    // categoryId 来自 ?categoryId=1
}
```

为了让接口契约更清楚、避免参数名编译信息缺失，正式代码中可以显式写成 `@RequestParam Long categoryId`。

## 3. `@ResponseBody` 与返回值

`@ResponseBody` 表示方法返回值写入 HTTP 响应体，而不是交给视图解析器渲染页面。

```java
@Controller
public class HealthController {
    @ResponseBody
    @GetMapping("/health")
    public Result<String> health() {
        return Result.success("ok");
    }
}
```

`@RestController` 已经包含了类级别的 `@ResponseBody`，因此项目中的 Controller 不需要在每个方法上重复写它。

## 4. 全局异常处理

### 4.1 `@RestControllerAdvice`

`@RestControllerAdvice` 将一个类注册为全局 Controller 异常处理器，并让处理方法的返回值直接作为响应体。

项目中的 [`GlobalExceptionHandler.java`](../../../sky-server/src/main/java/com/sky/handler/GlobalExceptionHandler.java)：

```java
@RestControllerAdvice
public class GlobalExceptionHandler {
}
```

它会参与所有 Controller 请求的异常处理，不需要业务代码手动调用。

### 4.2 `@ExceptionHandler`

`@ExceptionHandler` 声明某个方法负责处理指定异常。异常类型可以通过方法参数推断，也可以显式写在注解中：

```java
@ExceptionHandler
public Result exceptionHandler(BaseException ex) {
    return Result.error(ex.getMessage());
}

@ExceptionHandler(SQLIntegrityConstraintViolationException.class)
public Result duplicateHandler(SQLIntegrityConstraintViolationException ex) {
    return Result.error("数据已存在");
}
```

当前项目的业务异常继承自 `BaseException`。例如 Service 中抛出：

```java
throw new AccountNotFoundException(MessageConstant.ACCOUNT_NOT_FOUND);
```

请求链路为：

```text
Service 抛出 BaseException
  -> Spring MVC 查找匹配的 @ExceptionHandler
  -> GlobalExceptionHandler 返回 Result.error(...)
  -> Jackson 将 Result 写成 JSON
```

当前处理器没有覆盖所有普通 `RuntimeException` 或 `IllegalArgumentException`，所以这类异常可能交给 Spring Boot 默认错误处理机制。

