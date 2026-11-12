# 18 Spring MVC 拦截器

## 1. 拦截器的作用

拦截器可以在 Controller 方法执行前后统一处理请求。鉴权、访问日志、请求耗时统计等逻辑都适合放在这里。

## 2. 项目中的注册

代码位置：[`WebMvcConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/WebMvcConfiguration.java)

```java
registry.addInterceptor(jwtTokenAdminInterceptor)
        .addPathPatterns("/admin/**")
        .excludePathPatterns("/admin/employee/login");

registry.addInterceptor(jwtTokenUserInterceptor)
        .addPathPatterns("/user/**")
        .excludePathPatterns("/user/user/login")
        .excludePathPatterns("/user/shop/status");
```

## 3. 管理端拦截器流程

[`JwtTokenAdminInterceptor.java`](../../../sky-server/src/main/java/com/sky/interceptor/JwtTokenAdminInterceptor.java) 的 `preHandle` 会：

```text
判断是否为 Controller 方法
  -> 从 token 请求头取 Token
  -> 使用管理员密钥解析 JWT
  -> 取出 EMP_ID
  -> 写入 BaseContext
  -> 返回 true 放行
```

任何一步失败都会设置 `401` 并返回 `false`，Controller 不再执行。用户端拦截器流程相同，只是使用用户密钥、用户请求头和 `USER_ID`。

## 4. 学习重点

- `addPathPatterns` 定义保护范围。
- `excludePathPatterns` 定义公开接口。
- `preHandle` 返回 `false` 会中止请求。
- 静态资源或非 Controller 处理器通常可以直接放行。
- 鉴权与授权是两个概念：验证“你是谁”不等于验证“你能做什么”。

## 5. 学习练习

新增一个公开的店铺信息接口，明确它应该放在排除列表还是单独使用 `/public/**`；再设计一个只允许特定角色访问的授权判断。

