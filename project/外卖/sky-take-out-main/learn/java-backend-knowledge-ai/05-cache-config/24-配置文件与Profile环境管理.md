# 24 Profile 与配置属性绑定

## 1. 为什么要外置配置

数据库地址、密码、JWT 密钥和第三方证书路径会随环境变化，不应写死在 Java 代码中。Spring Boot 使用 YAML/Properties 文件和 Profile 管理不同环境的配置。

## 2. 项目中的配置文件

- [`application.yml`](../../../sky-server/src/main/resources/application.yml)：通用配置和配置引用。
- [`application-dev.yml`](../../../sky-server/src/main/resources/application-dev.yml)：开发环境具体值。

`application.yml` 中设置：

```yaml
spring:
  profiles:
    active: dev
```

因此启动时会加载 `application-dev.yml`。

## 3. 配置绑定

公共模块中有：

```java
@ConfigurationProperties(prefix = "sky.jwt")
public class JwtProperties { ... }
```

类似地，OSS 和微信配置也绑定到 Java 属性类。业务代码通过注入 `JwtProperties`、`WeChatProperties` 使用配置，而不是直接到处读取字符串。

## 4. 配置优先级和环境变量

开发配置中数据库用户名和密码使用：

```yaml
username: ${SKY_DB_USERNAME}
password: ${SKY_DB_PASSWORD}
```

这说明敏感配置可以从环境变量注入。生产环境还应使用专门的密钥管理服务，并区分开发、测试、生产 Profile。

## 5. 学习重点

- 配置键名称必须与 `@ConfigurationProperties` 前缀和字段匹配。
- Profile 文件会覆盖通用文件中的同名配置。
- 不要把真实密钥、私钥和支付证书提交到 Git。
- 配置外置并不等于配置安全，日志和错误信息也可能泄露配置内容。

## 6. 学习练习

新增一个 `sky.shop` 配置属性并绑定到 Java 类，再分别用默认值、开发配置和环境变量覆盖它，观察最终生效值。

