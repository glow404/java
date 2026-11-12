# 17 JWT 身份认证

## 1. JWT 是什么

JWT（JSON Web Token）是一种由服务端签发、客户端携带的令牌。令牌包含 Claims，并使用密钥签名，服务端可以在不保存会话对象的情况下验证请求身份。

## 2. 项目中的实现

工具类：[`JwtUtil.java`](../../../sky-common/src/main/java/com/sky/utils/JwtUtil.java)

```java
public static String createJWT(
        String secretKey,
        long ttlMillis,
        Map<String, Object> claims) {
    return Jwts.builder()
            .setClaims(claims)
            .signWith(SignatureAlgorithm.HS256,
                    secretKey.getBytes(StandardCharsets.UTF_8))
            .setExpiration(new Date(...))
            .compact();
}
```

管理端登录在 `EmployeeController` 中把员工 ID 放入 `EMP_ID`；用户登录在 `UserController` 中把用户 ID 放入 `USER_ID`。

## 3. 完整请求链路

```text
登录成功
  -> 生成 Claims：EMP_ID 或 USER_ID
  -> 使用对应密钥签名
  -> 设置过期时间
  -> 返回 Token
  -> 客户端保存 Token
  -> 后续请求放入 token/authentication 请求头
  -> 拦截器验签、解析 Claims
  -> 将 ID 放入 BaseContext
```

项目为管理端和用户端配置了不同的密钥、过期时间和请求头名称，避免两类 Token 混用。

## 4. JWT 的关键组成

- Header：算法和令牌类型。
- Payload：Claims，例如用户 ID 和过期时间。
- Signature：用密钥对前两部分签名，防止内容被篡改。
- TTL：Token 的有效期。

JWT 是“签发后自包含”的，不代表服务端无法撤销它。要实现主动退出、封禁账号或强制下线，还需要黑名单、版本号或短 Token 加 Refresh Token 等机制。

## 5. 当前实现的学习边界

- 已实现身份认证，但没有细粒度 RBAC 权限控制。
- 登出接口只是返回成功，没有使旧 Token 立即失效。
- HS256 的密钥必须保密，不能放进前端。
- 生产系统应避免记录完整 Token 到日志。

## 6. 学习练习

手动解码一个 JWT，观察其 Header、Payload 和过期时间；再修改 Payload 后重新请求，理解为什么没有正确签名就会校验失败。

