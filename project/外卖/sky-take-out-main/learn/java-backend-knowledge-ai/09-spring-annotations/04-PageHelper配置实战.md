# PageHelper 配置实战

## 1. 完整配置代码

项目中的 [`PageHelperConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/PageHelperConfiguration.java) 如下：

```java
@Configuration
public class PageHelperConfiguration {

    @Bean
    public Interceptor pageInterceptor() {
        PageInterceptor interceptor = new PageInterceptor();
        Properties properties = new Properties();
        properties.setProperty("helperDialect", "mysql");
        properties.setProperty("reasonable", "true");
        interceptor.setProperties(properties);
        return interceptor;
    }
}
```

这里重点不是注解本身做了分页，而是注解把 PageHelper 的拦截器接入 Spring 和 MyBatis。

## 2. `@Configuration` 的作用

启动时，Spring 扫描到 `PageHelperConfiguration`，知道它是一个配置类，并处理其中的 `@Bean` 方法。

```text
@Configuration
  -> Spring 读取配置类
  -> 查找其中的 @Bean 方法
```

如果没有 `@Configuration`（或其他被 Spring 扫描的配置入口），这个类可能不会被 Spring 发现，分页拦截器也就不会被注册。

## 3. `@Bean` 的作用

`@Bean` 方法返回的 `PageInterceptor` 会被注册为 Spring Bean：

```java
@Bean
public Interceptor pageInterceptor() {
    PageInterceptor interceptor = new PageInterceptor();
    // 设置方言和分页行为
    return interceptor;
}
```

返回类型写成 MyBatis 的 `Interceptor` 接口，实际返回实现类 `PageInterceptor`。Spring 容器管理的是这个实例，而不是每次业务查询时临时创建一个新的拦截器。

## 4. 配置属性如何影响插件

```java
properties.setProperty("helperDialect", "mysql");
properties.setProperty("reasonable", "true");
interceptor.setProperties(properties);
```

- `helperDialect = mysql`：告诉 PageHelper 生成适合 MySQL 的分页 SQL。
- `reasonable = true`：对不合理的页码进行合理化处理，例如页码小于 1 时按第一页处理。

这些属性由 PageHelper 读取，不是 Spring 注解参数。

## 5. Bean 如何被 MyBatis 使用

项目引入 MyBatis Spring Boot Starter 后，框架会收集容器中的 MyBatis `Interceptor` Bean，并把它们装配到 MyBatis 执行链中。

业务 Service 中这样使用：

```java
public PageResult pageQuery(CategoryPageQueryDTO query) {
    PageHelper.startPage(query.getPage(), query.getPageSize());
    Page<Category> page = categoryMapper.pageQuery(query);
    return new PageResult(page.getTotal(), page.getResult());
}
```

执行顺序：

```text
Controller 接收 page / pageSize
  -> Service 调用 PageHelper.startPage(...)
  -> Mapper 执行紧随其后的 SELECT
  -> PageInterceptor 拦截 SQL
  -> 生成分页查询和总数查询
  -> 返回 Page<T>
  -> Service 封装为 PageResult
```

因此，`@Configuration` 和 `@Bean` 负责“把插件装进框架”，`PageHelper.startPage` 负责“告诉插件本次查询的分页参数”。

## 6. 与依赖注入的关系

这个配置类本身没有被 Controller 注入。它的作用是启动阶段注册基础设施：

```text
PageHelperConfiguration
  -> @Bean PageInterceptor
  -> Spring 容器
  -> MyBatis-Spring 集成
  -> Mapper 查询自动分页
```

这正是配置类的典型特点：它不承载“查询某个分类”的业务，而是提供业务代码依赖的通用能力。

