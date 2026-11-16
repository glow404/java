苍穹外卖的登录鉴权是一套**"JWT + 拦截器 + ThreadLocal"的无状态方案**，管理端和小程序端各走一条登录链路，但共用同一套工具类和同一套鉴权机制。下面按"设计选型 → 配置 → 工具 → 两条登录链路 → 鉴权拦截 → 前端配合 → 坑与边界"完整走一遍。

------

## 一、先看清涉及的类

| 层次   | 类                          | 职责                                   |
| ------ | --------------------------- | -------------------------------------- |
| 配置   | `JwtProperties`             | 绑定 `sky.jwt.*`，token 名/密钥/有效期 |
| 工具   | `JwtUtil`                   | 生成、解析 JWT                         |
| 常量   | `JwtClaimsConstant`         | claims 里的 key（`empId` / `userId`）  |
| 工具   | `MD5Util`                   | 密码加密比对                           |
| 上下文 | `BaseContext`               | ThreadLocal 保存当前登录人 id          |
| 鉴权   | `JwtTokenAdminInterceptor`  | 校验 `/admin/**` 的 token              |
| 鉴权   | `JwtTokenUserInterceptor`   | 校验 `/user/**` 的 token（部分版本）   |
| 装配   | `WebMvcConfiguration`       | 注册拦截器、配置排除路径               |
| 业务   | `EmployeeServiceImpl.login` | 管理端账号密码登录                     |
| 业务   | `UserServiceImpl.login`     | 小程序 code 换 openid 登录             |

**一句话概括分工**：Service 负责"证明你是谁并签发 token"，拦截器负责"每次请求验明 token 并还原身份"，`BaseContext` 负责"把身份递给后面的业务代码"。

------

## 二、为什么用 JWT，而不用 Session

这是理解全部代码的前提。

|          | Session + Cookie            | JWT                            |
| -------- | --------------------------- | ------------------------------ |
| 状态存哪 | 服务端内存/Redis            | 不存在服务端，全在 token 里    |
| 集群     | 需要 session 共享或粘滞会话 | 天然支持，任意节点都能验签     |
| 小程序   | Cookie 支持差               | header 带 token，无障碍        |
| 主动失效 | 服务端删 session 即可       | 做不到（无状态），需额外黑名单 |
| 篡改风险 | 只有 id，安全               | payload 可被解码，靠签名防篡改 |

苍穹外卖是前后端分离 + 小程序 + 可能多实例部署，Session 方案在这三个场景都很别扭，所以选 JWT。**代价是"无法主动失效"**——这个代价后面会反复出现，是这套方案最大的边界。

------

## 三、配置层：`JwtProperties` + yml

```yaml
sky:
  jwt:
    admin-token-name: token
    admin-secret-key: 苍穹外卖是一个黑马程序员的实战项目
    admin-ttl: 7200000
    user-token-name: authentication
    user-secret-key: 苍穹外卖是一个黑马程序员的实战项目
    user-ttl: 7200000
package com.sky.properties;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "sky.jwt")
@Data
public class JwtProperties {
    private String adminTokenName;
    private String adminSecretKey;
    private Long adminTtl;
    private String userTokenName;
    private String userSecretKey;
    private Long userTtl;
}
```

**三个知识点：**

**① 管理端和用户端用不同的 token 名和密钥。** header 名一个是 `token`、一个是 `authentication`；密钥也分开。效果是**两端 token 互不通用**——拿小程序的 token 去调 `/admin/**` 会验签失败。这是最小成本的"双端隔离"。

**② `@ConfigurationProperties` 而非 `@Value`。** 一组相关配置绑定成一个对象，类型安全、IDE 有提示、便于统一注入。需要配合 `@Component`（或 `@EnableConfigurationProperties`）才会进容器。

**③ 密钥写在配置文件里是教学做法。** 生产环境应放环境变量或配置中心，且**绝不能提交到 Git**。这个密钥一旦泄露，任何人都能伪造合法 token。

------

## 四、工具层

### 1. `JwtUtil`：签发与验签

```java
package com.sky.utils;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.security.Keys;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.Map;

public class JwtUtil {

    public static String createJWT(String secretKey, long expirationTime, Map<String, Object> claims) {
        SignatureAlgorithm signatureAlgorithm = SignatureAlgorithm.HS256;
        SecretKey key = Keys.hmacShaKeyFor(secretKey.getBytes(StandardCharsets.UTF_8));
        return Jwts.builder()
                .setClaims(claims)
                .signWith(signatureAlgorithm, key)
                .setExpiration(new Date(System.currentTimeMillis() + expirationTime))
                .compact();
    }

    public static Claims parseJWT(String secretKey, String token) {
        SecretKey key = Keys.hmacShaKeyFor(secretKey.getBytes(StandardCharsets.UTF_8));
        return Jwts.parserBuilder()
                .setSigningKey(key)
                .build()
                .parseClaimsJws(token)
                .getBody();
    }
}
```

**四个知识点：**

**① JWT 是三段式：`header.payload.signature`，用 `.` 连接。**

- header：声明算法（HS256）和类型（JWT）
- payload：claims，即业务数据（`empId`、`exp` 等）
- signature：用密钥对前两段做 HMAC-SHA256 签名

前两段只是 **Base64URL 编码，不是加密**。任何人拿到 token 都能解码看到 `{"empId":1,"exp":1700000000}`。**所以 payload 里绝对不能放密码、手机号等敏感信息**——这是 JWT 最常被误解的一点。安全性完全靠第三段签名：没有密钥就伪造不出合法签名。

**② `parseJWT` 同时完成"验签"和"校验过期"。** 签名不对、token 被改、已过期，都会抛异常（`SignatureException` / `ExpiredJwtException` / `MalformedJwtException`）。业务代码统一 catch 成"未登录"，这就是拦截器里 `catch (Exception ex)` 的原因。

**③ HS256 是对称签名。** 加签验签用同一个密钥，所以密钥必须保密。如果换成 RS256（非对称），私钥签发、公钥验签，适合多方验证场景，但本项目没必要。

**④ 密钥长度有要求。** `Keys.hmacShaKeyFor` 对 HS256 要求密钥至少 256 bit（32 字节），太短会直接抛 `WeakKeyException`。这也是配置文件里密钥写得比较长的原因。

### 2. `JwtClaimsConstant`

```java
public class JwtClaimsConstant {
    public static final String EMP_ID = "empId";
    public static final String USER_ID = "userId";
}
```

用常量而非字符串字面量，避免签发端和校验端拼写不一致（这类 bug 极难排查）。

### 3. `MD5Util`：密码加密

```java
public class MD5Util {
    public static String getMd5(String str) {
        try {
            MessageDigest md5 = MessageDigest.getInstance("MD5");
            byte[] bytes = md5.digest(str.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : bytes) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException(e);
        }
    }
}
```

**这里必须明确说：MD5 存密码是不安全的，教学项目简化了。** 三个问题：无盐（相同密码得到相同密文，可被彩虹表批量还原）、MD5 速度快（GPU 可暴力穷举）、已被证明可碰撞。**生产环境应换成 BCrypt 或 Argon2**——它们自带随机盐、计算成本可调：

```java
// 改进写法
String hash = BCrypt.hashpw(rawPassword, BCrypt.gensalt(12));
boolean ok = BCrypt.checkpw(rawPassword, hash);
```

------

## 五、上下文层：`BaseContext`（ThreadLocal）

```java
package com.sky.context;

public class BaseContext {
    private static ThreadLocal<Long> threadLocal = new ThreadLocal<>();

    public static void setCurrentId(Long id) { threadLocal.set(id); }
    public static Long getCurrentId() { return threadLocal.get(); }
    public static void removeCurrentId() { threadLocal.remove(); }
}
```

**为什么需要它？** 拦截器解析出 `empId` 后，后面的 Controller、Service、甚至 AOP 切面（自动填充 `update_user`）都要用。如果靠方法参数一路传，会污染所有接口签名。ThreadLocal 提供"请求级全局变量"，同一线程内随处可取。

**这个类是全套代码里风险最高的一处**，原因在下一节讲。

------

## 六、管理端登录全流程

### 1. `EmployeeController.login`

```java
@RestController("adminEmployeeController")
@RequestMapping("/admin/employee")
@Api(tags = "员工相关接口")
public class EmployeeController {

    @Autowired
    private EmployeeService employeeService;

    @PostMapping("/login")
    @ApiOperation("员工登录")
    public Result<Employee> login(@RequestBody EmployeeLoginDTO employeeLoginDTO) {
        Employee employee = employeeService.login(employeeLoginDTO);
        return Result.success(employee);
    }
}
```

### 2. `EmployeeServiceImpl.login`（核心）

```java
@Service
@Slf4j
public class EmployeeServiceImpl implements EmployeeService {

    @Autowired
    private EmployeeMapper employeeMapper;

    @Autowired
    private JwtProperties jwtProperties;

    @Override
    public Employee login(EmployeeLoginDTO employeeLoginDTO) {
        String username = employeeLoginDTO.getUsername();
        String password = employeeLoginDTO.getPassword();

        // 1. 根据用户名查询员工
        Employee employee = employeeMapper.getByUsername(username);
        if (employee == null) {
            throw new LoginFailedException(MessageConstant.USERNAME_PASSWORD_ERROR);
        }

        // 2. 密码比对（MD5）
        String md5 = MD5Util.getMd5(password);
        if (!md5.equals(employee.getPassword())) {
            throw new LoginFailedException(MessageConstant.USERNAME_PASSWORD_ERROR);
        }

        // 3. 校验账号状态
        if (employee.getStatus() == StatusConstant.DISABLE) {
            throw new LoginFailedException(MessageConstant.ACCOUNT_LOCKED);
        }

        // 4. 生成 JWT
        Map<String, Object> claims = new HashMap<>();
        claims.put(JwtClaimsConstant.EMP_ID, employee.getId());
        String token = JwtUtil.createJWT(
                jwtProperties.getAdminSecretKey(),
                jwtProperties.getAdminTtl(),
                claims);

        employee.setToken(token);
        return employee;
    }
}
```

### 知识点（这一段是面试高频）

**① 校验顺序：先查存在 → 再比密码 → 再查状态。** 顺序错了会泄露信息：如果先查状态再比密码，攻击者能通过错误提示区分"用户名不存在"和"密码错误"，从而枚举出有效用户名。**统一返回"用户名或密码错误"是刻意的安全设计**，代码里两处都抛同一个 `USERNAME_PASSWORD_ERROR` 就是这个用意。

**④ token 里只放 `empId`，不放用户名、角色等。** 需要更多信息时应该拿 id 去查库或查缓存。原因见前面：payload 可被解码。

------

## 七、用户端（小程序）登录全流程

### 1. `UserController.login`

```java
@RestController("userUserController")
@RequestMapping("/user/user")
@Api(tags = "C端用户相关接口")
public class UserController {

    @Autowired
    private UserService userService;

    @PostMapping("/login")
    @ApiOperation("用户小程序登录")
    public Result<User> login(@RequestBody UserLoginDTO userLoginDTO) {
        User user = userService.login(userLoginDTO);
        return Result.success(user);
    }
}
```

### 2. `UserServiceImpl.login`

```java
@Service
@Slf4j
public class UserServiceImpl implements UserService {

    @Autowired
    private UserMapper userMapper;

    @Autowired
    private JwtProperties jwtProperties;

    @Autowired
    private WeChatProperties weChatProperties;

    @Override
    public User login(UserLoginDTO userLoginDTO) {
        String code = userLoginDTO.getCode();

        // 1. 用 code 换取 openid
        String openId = WxLoginUtil.getOpenId(
                code,
                weChatProperties.getAppid(),
                weChatProperties.getSecret());

        // 2. 根据 openid 查询用户，不存在则自动注册
        User user = userMapper.getByOpenId(openId);
        if (user == null) {
            user = User.builder()
                    .openId(openId)
                    .createTime(LocalDateTime.now())
                    .build();
            userMapper.insert(user);
        }

        // 3. 生成 JWT
        Map<String, Object> claims = new HashMap<>();
        claims.put(JwtClaimsConstant.USER_ID, user.getId());
        String token = JwtUtil.createJWT(
                jwtProperties.getUserSecretKey(),
                jwtProperties.getUserTtl(),
                claims);

        user.setToken(token);
        return user;
    }
}
```

### 知识点

**① 小程序登录是"code 换 openid"的三步流程，不是账号密码。**

```
小程序 wx.login() 拿到临时 code（有效期 5 分钟、只能用一次）
  → 后端拿 code + appid + secret 调微信接口
     GET https://api.weixin.qq.com/sns/jscode2session
  → 微信返回 openid（用户在本小程序的唯一标识）+ session_key
  → 后端用 openid 建立/查找自己的用户体系，签发自己的 JWT
```

**关键点：`session_key` 绝不能下发给前端**，它用于后续解密手机号等敏感数据。小程序端只拿到后端的 JWT。

**② 首次登录自动注册（upsert 语义）。** 小程序没有"注册"页面，用户第一次进入就静默建号。所以 `getByOpenId` 查不到就 insert。这带来一个约束：**`open_id` 字段必须有唯一索引**，否则并发首登会插入两条。

**③ 版本差异要留意。** 官方教学版为了方便本地演示（没有真实 appid/secret），部分版本把 openid 写死成模拟值：

```java
// 部分教学版本：微信登录未开放，使用模拟数据
String openId = "mock_open_id_" + code;
```

网上流传的仓库里两种写法都有。理解机制时按真实流程理解，跑不通时确认自己看的是哪个版本。

**④ 用户端没有密码。** 用户表没有 password 字段，身份完全由 openid 决定。所以"改密码"这类需求在用户端不存在。

------

## 八、鉴权：两个拦截器 + 注册

### 1. `JwtTokenAdminInterceptor`

```java
package com.sky.interceptor;

import com.sky.context.BaseContext;
import com.sky.properties.JwtProperties;
import com.sky.utils.JwtUtil;
import com.sky.constant.JwtClaimsConstant;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

@Component
@Slf4j
public class JwtTokenAdminInterceptor implements HandlerInterceptor {

    @Autowired
    private JwtProperties jwtProperties;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        // 非 Controller 方法（静态资源等）直接放行
        if (!(handler instanceof HandlerMethod)) {
            return true;
        }

        String token = request.getHeader(jwtProperties.getAdminTokenName());

        try {
            log.info("jwt校验:{}", token);
            Claims claims = JwtUtil.parseJWT(jwtProperties.getAdminSecretKey(), token);
            Long empId = Long.valueOf(claims.get(JwtClaimsConstant.EMP_ID));
            log.info("当前员工id：{}", empId);
            BaseContext.setCurrentId(empId);   // 存入 ThreadLocal
            return true;
        } catch (Exception ex) {
            response.setStatus(401);           // 未登录/已过期
            return false;
        }
    }
}
```

### 2. `JwtTokenUserInterceptor`（部分版本）

```java
@Component
@Slf4j
public class JwtTokenUserInterceptor implements HandlerInterceptor {

    @Autowired
    private JwtProperties jwtProperties;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        if (!(handler instanceof HandlerMethod)) {
            return true;
        }
        String token = request.getHeader(jwtProperties.getUserTokenName());
        try {
            Claims claims = JwtUtil.parseJWT(jwtProperties.getUserSecretKey(), token);
            Long userId = Long.valueOf(claims.get(JwtClaimsConstant.USER_ID));
            BaseContext.setCurrentId(userId);
            return true;
        } catch (Exception ex) {
            response.setStatus(401);
            return false;
        }
    }
}
```

### 3. 在 `WebMvcConfiguration` 中注册

```java
@Autowired
private JwtTokenAdminInterceptor jwtTokenAdminInterceptor;

@Autowired
private JwtTokenUserInterceptor jwtTokenUserInterceptor;

@Override
protected void addInterceptors(InterceptorRegistry registry) {
    log.info("开始注册自定义拦截器...");

    registry.addInterceptor(jwtTokenAdminInterceptor)
            .addPathPatterns("/admin/**")
            .excludePathPatterns("/admin/employee/login");

    registry.addInterceptor(jwtTokenUserInterceptor)
            .addPathPatterns("/user/**")
            .excludePathPatterns("/user/user/login")
            .excludePathPatterns("/user/shop/status");
}
```

### 知识点（这里最容易出错）

**① `handler instanceof HandlerMethod` 判断不能省。** 拦截器按 URL 匹配，命中的可能包含静态资源、错误页等非 Controller 请求，它们的 `handler` 不是 `HandlerMethod`。直接强转会 `ClassCastException`。

**② 排除路径必须和实际 URL 完全一致。** 漏掉 `/admin/employee/login` 会导致"登录接口需要登录"的死锁；漏掉 `/user/shop/status` 会导致小程序首页（未登录状态）拿不到营业状态。Ant 风格匹配：`**` 跨层级、`*` 单层、`?` 单字符。

**③ 返回 `false` 必须配合状态码。** 只 `return false` 不设 401，前端会收到 200 空响应，无法触发跳转登录。设了 401，前端 axios 响应拦截器才能统一处理。

**⑤ 拦截器 vs 过滤器。** 过滤器是 Servlet 规范、在 Spring MVC 之前执行、拿不到 `HandlerMethod`；拦截器能拿到，可以按"是不是 Controller 方法"决策。JWT 校验需要这个信息，所以用拦截器。

**⑥ 两个拦截器都用 `BaseContext`，但语义不同。** 管理端存的是 `empId`，用户端存的是 `userId`，都塞进同一个 ThreadLocal。所以业务代码里 `BaseContext.getCurrentId()` 到底是员工还是用户，取决于当前请求走的是哪条路径——**这是隐式约定，容易埋雷**。更清晰的做法是分开两个 ThreadLocal 或存一个带类型的上下文对象。

**⑦ ThreadLocal 必须清理，否则脏数据 + 内存泄漏。** Tomcat 用线程池复用线程，请求结束后如果不清理，线程归还池子时值还在；下个请求若走了不设置 id 的路径（比如被排除的登录接口），可能读到上一个用户的 id。**规范做法是重写 `afterCompletion`：**

```java
@Override
public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) {
    BaseContext.removeCurrentId();
}
```

官方教学版很多省略了这一步，属于真实隐患。

------

## 九、前端如何配合（管理端 Vue）

### 1. 登录成功后存 token

```js
// views/login/index.vue
async handleLogin() {
  const res = await api.login(this.form)
  localStorage.setItem('token', res.data.token)
  localStorage.setItem('userInfo', JSON.stringify(res.data))
  this.$router.push('/')
}
```

### 2. axios 请求拦截器：自动带 token

```js
// utils/request.js
const service = axios.create({ baseURL: '/api' })

service.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers['token'] = token   // 名字要和 admin-token-name 一致
  return config
})
```

### 3. 响应拦截器：401 跳登录

```js
service.interceptors.response.use(
  res => res.data,
  error => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('token')
      router.push('/login')
    }
    return Promise.reject(error)
  }
)
```

### 4. 路由守卫：未登录不进内页

```js
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  if (to.path === '/login') return next()
  if (!token) return next('/login')
  next()
})
```

### 知识点

**① header 名必须和后端 `admin-token-name` 完全一致**（`token`）。这是前后端最典型的"隐式契约"，改一边忘改另一边就是全站 401。

**② 路由守卫只是体验优化，不是安全边界。** 前端所有校验都能被绕过（直接调接口），**真正的安全只在后端拦截器**。这是"前端鉴权不可信"的经典结论。

**③ `localStorage` 存 token 有 XSS 风险。** 一旦页面有 XSS 漏洞，脚本能读走 token。更安全的是 `httpOnly` Cookie（但小程序不适用）或内存 + 刷新机制。教学项目用 localStorage 是简化。

**④ 小程序端**用 `wx.setStorageSync('token', ...)`，请求封装里统一加 `authentication` header（注意和用户端后端配置对应）。

------

## 十、登出与改密码：JWT 的先天短板

### 登出

```java
@PostMapping("/logout")
@ApiOperation("退出登录")
public Result logout() {
    return Result.success();   // 什么都没做
}
```

**这个接口是"假登出"。** JWT 无状态，服务端没有存储，无法让一个已签发的 token 失效。前端登出只是删掉本地 token；如果 token 被复制走，在过期前依然有效。

**改进方案：**

- **Redis 黑名单**：登出时把 token（或其 jti）写入 Redis，TTL 设为剩余有效期；拦截器额外查一次黑名单。代价是每个请求多一次 Redis 读。
- **短有效期 + 刷新 token**：access token 只给 15 分钟，配合 refresh token 续期，缩小泄露窗口。
- **版本号机制**：用户表加 `token_version`，登出/改密码时自增，签发时写入 claims，校验时比对。

### 改密码

```java
public void updatePassword(EmployeeUpdatePasswordDTO dto) {
    Employee employee = employeeMapper.getById(BaseContext.getCurrentId());
    if (!MD5Util.getMd5(dto.getOldPassword()).equals(employee.getPassword())) {
        throw new PasswordEditFailedException(MessageConstant.OLD_PASSWORD_NOT_MATCH);
    }
    employee.setPassword(MD5Util.getMd5(dto.getNewPassword()));
    employeeMapper.update(employee);
}
```

**注意：改密码后旧 token 仍然有效**（签名没变、没过期）。这也是无状态方案的固有特性，需要靠上面的版本号机制解决。

------

## 十一、串起来的完整时序

```
【管理端登录】
浏览器 POST /admin/employee/login {username, password}
  → 该路径被 excludePathPatterns 排除，拦截器放行
  → EmployeeController.login → EmployeeServiceImpl.login
      getByUsername 查库 → 不存在则抛错
      MD5 比对密码 → 不匹配则抛错（统一提示）
      校验 status 是否禁用
      生成 JWT（claims: empId）
  → 返回 {token, name, ...}
  → 前端存 localStorage，路由跳转

【管理端后续请求】
浏览器 GET /admin/dish/page  header: token=xxx
  → JwtTokenAdminInterceptor.preHandle
      handler 是 HandlerMethod？否 → 放行
      取 header token → parseJWT 验签 + 校验过期
        成功：BaseContext.setCurrentId(empId)，return true
        失败：setStatus(401)，return false（Controller 不执行）
  → DishController.page → Service → Mapper
      （Service/AOP 可通过 BaseContext 拿 empId 做数据隔离、填充 update_user）
  → 返回 JSON
  → afterCompletion：BaseContext.removeCurrentId()

【小程序登录】
小程序 wx.login() → code
  → POST /user/user/login {code}
  → UserServiceImpl.login
      code + appid + secret 调微信 jscode2session → openid
      getByOpenId 查库，不存在则 insert（自动注册）
      生成 JWT（claims: userId，用 user 密钥）
  → 返回 token，小程序 storage 保存

【小程序后续请求】
GET /user/order/list  header: authentication=xxx
  → JwtTokenUserInterceptor 用 user 密钥验签
  → BaseContext.setCurrentId(userId)
  → 订单查询按 userId 过滤（数据隔离）
```

------

## 十二、坑与边界（这部分最能体现理解深度）

| 问题                         | 现状                         | 说明与改进                                               |
| ---------------------------- | ---------------------------- | -------------------------------------------------------- |
| MD5 存密码                   | 无盐、可彩虹表               | 换 BCrypt/Argon2；存量密码需迁移                         |
| token 无法主动失效           | 登出是假登出                 | Redis 黑名单 / 短 TTL + refresh token / token 版本号     |
| 改密码后旧 token 有效        | 未处理                       | 版本号机制，或登出全端                                   |
| ThreadLocal 未清理           | 多数版本无 `afterCompletion` | 线程复用导致脏数据 + 内存泄漏，必须 remove               |
| payload 明文可读             | 只放 empId 是正确做法        | 严禁放密码、手机号；需敏感信息应查库                     |
| 密钥在配置文件               | 有泄露风险                   | 环境变量/配置中心，禁止提交 Git                          |
| 无登录失败限制               | 可暴力破解                   | 失败计数 + 锁定 + 验证码                                 |
| 无授权（角色权限）           | 只有认证                     | 拿到 empId 后所有接口权限相同；应加 RBAC，区分 401/403   |
| 越权风险                     | 依赖 `BaseContext` 正确性    | 订单/购物车查询必须按当前 id 过滤，不能信前端传的 userId |
| 双端 id 共用一个 ThreadLocal | 语义靠路径隐式决定           | 拆成两个 ThreadLocal 或带类型的上下文对象                |
| 拦截器排除路径易漏           | 漏一个就 401 死锁            | 集中维护排除列表，加集成测试覆盖                         |
| openid 唯一索引              | 并发首登可能重复建号         | `open_id` 加唯一索引 + 捕获重复键异常                    |
| session_key 处理             | 不应下发前端                 | 只存服务端（如需解密手机号）                             |
| 传输安全                     | 教学用 http                  | 生产必须 HTTPS，否则 token 明文被抓包                    |
| 401 无刷新机制               | 过期即登出                   | 双 token 静默续期，提升体验                              |
| 前端存储 XSS                 | localStorage                 | 严格 CSP、输入过滤；或 httpOnly Cookie                   |

------

## 十三、和其他配置类的关系

| 类                       | 在登录鉴权中的角色                                           |
| ------------------------ | ------------------------------------------------------------ |
| `WebMvcConfiguration`    | 注册两个 JWT 拦截器、配置排除路径，是鉴权生效的**装配点**    |
| `JwtProperties`          | 提供 token 名/密钥/TTL，被拦截器和 Service 共同依赖          |
| `WebSocketConfiguration` | 与鉴权无关；但 WebSocket 握手目前**没做 token 校验**，是另一处边界 |
| `RedisConfiguration`     | 当前鉴权未用 Redis；若做 token 黑名单/刷新，Redis 就是存储   |

**一个值得注意的缝隙**：`WebSocketServer` 的 `/ws/{sid}` 不走 Spring MVC 拦截器链，所以 WebSocket 连接**没有 JWT 鉴权**，`sid` 可以随便填。生产环境需要在握手阶段校验 token（改用 Spring WebSocket 的 `HandshakeInterceptor`，或 JSR-356 的 `Configurator`）。

















# 鉴权

------

## 一、归属表：谁提供的

| 类/接口/方法                                                 | 归属                                            | 作用一句话                                             |
| ------------------------------------------------------------ | ----------------------------------------------- | ------------------------------------------------------ |
| `HandlerInterceptor`                                         | Spring MVC 自带                                 | 拦截器顶层接口，定义三个拦截时机                       |
| `preHandle` / `postHandle` / `afterCompletion`               | Spring MVC 自带                                 | 三个回调方法                                           |
| `HttpServletRequest` / `HttpServletResponse`                 | Servlet API（Tomcat 提供实现）                  | 拿 header、设状态码                                    |
| `HandlerMethod`                                              | Spring 核心（`org.springframework.web.method`） | 封装"哪个类的哪个方法"，用来判断是不是 Controller 方法 |
| `InterceptorRegistry`                                        | Spring MVC 自带                                 | 拦截器注册表，`addInterceptor` 的入口                  |
| `InterceptorRegistration`                                    | Spring MVC 自带                                 | 注册结果对象，链式配置路径与顺序                       |
| `WebMvcConfigurationSupport`                                 | Spring MVC 自带                                 | MVC 装配根基类，提供 `addInterceptors` 覆写点          |
| `DispatcherServlet` / `HandlerExecutionChain`                | Spring MVC 自带                                 | 真正在请求链路里调用拦截器的人                         |
| `Claims` / `Jwts` / `Keys` / `SignatureAlgorithm`            | **第三方 jjwt 库**，不是 Spring                 | 解析/生成 JWT                                          |
| `JwtTokenAdminInterceptor` / `JwtTokenUserInterceptor`       | 项目自己写                                      | 具体的校验逻辑                                         |
| `JwtProperties` / `JwtUtil` / `JwtClaimsConstant` / `BaseContext` | 项目自己写                                      | 配置、工具、常量、上下文                               |
| `WebMvcConfiguration`                                        | 项目自己写                                      | 注册拦截器的装配类                                     |

**关键认知：框架只提供"拦截的时机和骨架"，"拦下来干什么"完全由你自己写。** `HandlerInterceptor` 里没有任何 JWT 逻辑，JWT 逻辑是项目代码；反过来，项目代码也造不出"请求进来自动调用"这个能力，靠的是 `DispatcherServlet`。

------

## 二、框架自带的部分逐个拆

### 1. `HandlerInterceptor`：三个方法就是三个时机

```java
public interface HandlerInterceptor {
    boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception;
    void postHandle(HttpServletRequest request, HttpServletResponse response, Object handler, ModelAndView modelAndView) throws Exception;
    void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) throws Exception;
}
```

**含义与作用：**

| 方法              | 触发时机                                   | 典型用途                             | 返回值意义                                                 |
| ----------------- | ------------------------------------------ | ------------------------------------ | ---------------------------------------------------------- |
| `preHandle`       | Controller 方法**执行前**                  | 鉴权、参数预处理、限流               | `true` 继续往下走；`false` **立即中断**，Controller 不执行 |
| `postHandle`      | Controller 执行后、**视图渲染前**          | 修改 ModelAndView、埋点              | 无返回                                                     |
| `afterCompletion` | 整个请求**结束后**（无论成功/异常/被中断） | 清理 ThreadLocal、记录耗时、释放资源 | 无返回                                                     |

**三个必须记住的细节：**

- **Spring 5 起这三个方法都是 `default` 方法**，所以项目里只覆写 `preHandle` 就能编译通过，不用被迫实现空方法。
- **`preHandle` 返回 `false` 时，`postHandle` 不会执行**，但**已经返回过 `true` 的那些拦截器的 `afterCompletion` 仍会执行**——这是框架保证的"谁开的口子谁负责关"，也正是清理 ThreadLocal 该放在 `afterCompletion` 而非 `postHandle` 的原因。
- **拦截器实例是单例、被所有线程共享**（Spring 默认 singleton）。所以**绝对不能用实例字段存请求级数据**（比如把 `empId` 存成成员变量），必须用 ThreadLocal 或方法参数传递。

**参数含义：**

| 参数           | 类型归属    | 作用                                                         |
| -------------- | ----------- | ------------------------------------------------------------ |
| `request`      | Servlet API | 取 header、参数、IP 等。项目用它 `getHeader("token")`        |
| `response`     | Servlet API | 写响应。项目用它 `setStatus(401)`                            |
| `handler`      | Spring MVC  | **被拦截的目标**。可能是 Controller 方法，也可能是静态资源处理器 |
| `modelAndView` | Spring MVC  | 视图与模型，前后端分离项目基本用不到                         |
| `ex`           | JDK         | 请求处理过程中抛出的异常，正常结束为 `null`                  |

### 2. `handler` 参数与 `HandlerMethod`：为什么必须做 `instanceof` 判断

`handler` 声明成 `Object`，是因为它有多种实现：

| 实际类型                        | 什么时候出现                               | 是不是业务接口 |
| ------------------------------- | ------------------------------------------ | -------------- |
| `HandlerMethod`                 | 正常映射到 `@RequestMapping` 方法          | **是**         |
| `ResourceHttpRequestHandler`    | 访问 `/doc.html`、`/webjars/**` 等静态资源 | 否             |
| `ParameterizableViewController` | 纯视图跳转                                 | 否             |
| `ServletHandler`                | 落到默认 Servlet 的请求                    | 否             |

`HandlerMethod` 内部包装了 `Handler`（Bean 名）、`Method`（反射方法对象）、`beanType`（类），常用方法：

```java
handlerMethod.getBeanType();        // 拿到 Controller 的 Class
handlerMethod.getMethod();          // 拿到 Method，可读自定义注解
handlerMethod.getMethodAnnotation(MyAuth.class);
```

所以项目里这行判断：

```java
if (!(handler instanceof HandlerMethod)) {
    return true;   // 不是 Controller 方法，直接放行
}
```

**它解决两个问题：** 一是避免对静态资源做无意义的 token 校验（否则 `/doc.html`、图片都 401）；二是避免后面把 `Object` 强转成 `HandlerMethod` 时抛 `ClassCastException`。**这行不是可选的防御代码，是必需的。**

### 3. `InterceptorRegistry` 与 `InterceptorRegistration`：注册 API

```java
@Override
protected void addInterceptors(InterceptorRegistry registry) {
    registry.addInterceptor(jwtTokenAdminInterceptor)
            .addPathPatterns("/admin/**")
            .excludePathPatterns("/admin/employee/login")
            .order(1);
}
```

| 方法                                 | 归属                      | 含义                                                         |
| ------------------------------------ | ------------------------- | ------------------------------------------------------------ |
| `addInterceptor(HandlerInterceptor)` | `InterceptorRegistry`     | 把一个拦截器加入注册表，**返回 `InterceptorRegistration`** 以支持链式调用 |
| `addPathPatterns(String...)`         | `InterceptorRegistration` | **包含**路径，Ant 风格匹配                                   |
| `excludePathPatterns(String...)`     | `InterceptorRegistration` | **排除**路径，优先级高于包含                                 |
| `order(int)`                         | `InterceptorRegistration` | 多个拦截器的执行顺序，值小者先 `preHandle`、后 `afterCompletion` |

**Ant 风格匹配规则：** `?` 匹配单个字符，`*` 匹配单层路径内任意字符，`**` 匹配任意层级。所以 `/admin/**` 能匹配 `/admin/dish/page`，但 `/admin/*` 匹配不到。

**`excludePathPatterns` 是"救命配置"：** 登录接口本身没有 token，如果被拦就形成"要登录才能登录"的死锁。同理 `/user/shop/status`（小程序未登录也要看营业状态）、`/doc.html`、`/webjars/**` 都要排除。

### 4. `WebMvcConfigurationSupport`：注册入口从哪来

```java
@Configuration
public class WebMvcConfiguration extends WebMvcConfigurationSupport {
    @Override
    protected void addInterceptors(InterceptorRegistry registry) { ... }
}
```

- `WebMvcConfigurationSupport` 是 Spring MVC 配置体系的**根基类**，`@EnableWebMvc` 导入的就是它。它把 `HandlerMapping`、`HandlerAdapter`、消息转换器等都定义成可覆写的 Bean/方法。
- 内部流程大致是：`mvcHandlerMappingInterceptors()` 创建 `InterceptorRegistry` → 调用你覆写的 `addInterceptors(registry)` → 把 `registry.getInterceptors()` 交给 `RequestMappingHandlerMapping`。
- **代价：** 继承它会让 Spring Boot 的 `WebMvcAutoConfiguration`（标了 `@ConditionalOnMissingBean(WebMvcConfigurationSupport.class)`）整体失效，静态资源映射等默认行为需要手动补。
- **更稳妥的替代：** 实现 `WebMvcConfigurer` 接口，只"贡献"配置不"接管"整个 MVC：

```java
@Configuration
public class WebMvcConfiguration implements WebMvcConfigurer {
    @Override
    public void addInterceptors(InterceptorRegistry registry) { ... }
}
```

### 5. `DispatcherServlet` 与 `HandlerExecutionChain`：谁真正调用拦截器

框架侧的调用链（简化）：

```java
// DispatcherServlet.doDispatch 内部逻辑
mappedHandler = getHandler(processedRequest);   // 得到 HandlerExecutionChain
if (mappedHandler == null) { noHandlerFound(...); return; }

if (!mappedHandler.applyPreHandle(processedRequest, response)) {
    return;                                     // 某个 preHandle 返回 false，直接结束
}

ha.handle(processedRequest, response, mappedHandler.getHandler());  // 调 Controller

mappedHandler.applyPostHandle(...);
// finally 块中
mappedHandler.triggerAfterCompletion(...);
```

- `getHandler()` 返回的 `HandlerExecutionChain` 里装着 **handler + 拦截器数组**（按 order 排好序）。
- `applyPreHandle` 按顺序逐个调用；**一旦某个返回 false，就停止后续拦截器的 preHandle，并对已执行成功的拦截器触发 `afterCompletion`，然后直接 return**。
- 这就是"返回 false 能中断请求"的底层依据——不是拦截器自己有能力中断，而是 `DispatcherServlet` 尊重它的返回值。

------

## 三、第三方库 jjwt 的类（不是框架自带）

```java
Claims claims = JwtUtil.parseJWT(jwtProperties.getAdminSecretKey(), token);
Long empId = Long.valueOf(claims.get(JwtClaimsConstant.EMP_ID));
```

| 类                         | 归属       | 作用                                                 |
| -------------------------- | ---------- | ---------------------------------------------------- |
| `Claims`                   | jjwt       | JWT payload 的 Map 视图，`get("empId")` 取自定义声明 |
| `Jwts`                     | jjwt       | 工厂类，`builder()` 生成、`parserBuilder()` 解析     |
| `Keys` / `SecretKey`       | jjwt + JDK | 把字符串密钥转成 HMAC 密钥对象                       |
| `SignatureAlgorithm.HS256` | jjwt       | 对称签名算法枚举                                     |

**注意版本差异：** jjwt 0.9.x 用 `Jwts.parser().setSigningKey(key).parseClaimsJws(token)`；0.11+ 改成 `Jwts.parserBuilder().setSigningKey(key).build()`。苍穹外卖不同版本仓库两种写法都有，编译报错时先确认依赖版本。

`parseJWT` 一次完成**验签 + 过期校验**：签名错误、被篡改、已过期分别抛 `SignatureException`、`MalformedJwtException`、`ExpiredJwtException`。项目统一 `catch (Exception)` 成"未登录"，就是因为这些都是"token 不可信"的不同表现。

------

## 四、项目自己写的类

| 类                         | 作用                        | 关键点                                             |
| -------------------------- | --------------------------- | -------------------------------------------------- |
| `JwtTokenAdminInterceptor` | 校验 `/admin/**` 的 token   | 用 `adminTokenName` + `adminSecretKey`             |
| `JwtTokenUserInterceptor`  | 校验 `/user/**` 的 token    | 用 `userTokenName` + `userSecretKey`               |
| `JwtProperties`            | 绑定 `sky.jwt.*`            | `@ConfigurationProperties`，两端密钥/header 名分开 |
| `JwtUtil`                  | 签发与解析 JWT              | 静态工具类，无状态                                 |
| `JwtClaimsConstant`        | claims 的 key 常量          | 避免签发端与校验端拼写不一致                       |
| `BaseContext`              | ThreadLocal 存当前登录人 id | 拦截器写入，Service/AOP 读取                       |
| `WebMvcConfiguration`      | 注册拦截器                  | 覆写 `addInterceptors`                             |

**为什么必须 `@Component`：** 拦截器要能被 `@Autowired` 注入到 `WebMvcConfiguration`，同时它内部的 `JwtProperties` 也要被注入。没有 `@Component`，`new JwtTokenAdminInterceptor()` 出来的对象不归 Spring 管，字段全是 null。

------

## 五、注册期：启动时发生了什么

```
应用启动
  → WebMvcConfigurationSupport.mvcHandlerMappingInterceptors()
      创建 InterceptorRegistry
      调用 WebMvcConfiguration.addInterceptors(registry)   ← 你覆写的方法
          registry.addInterceptor(jwtTokenAdminInterceptor)
                  .addPathPatterns("/admin/**")
                  .excludePathPatterns("/admin/employee/login")
          registry.addInterceptor(jwtTokenUserInterceptor)
                  .addPathPatterns("/user/**")
                  .excludePathPatterns("/user/user/login")
      返回 registry
  → requestMappingHandlerMapping 持有这些拦截器（按 order 排序）
  → 启动完成，拦截器进入"待命"状态
```

**要点：注册只发生一次，在启动阶段。** 运行期不再解析配置；每次请求只是从 `RequestMappingHandlerMapping` 里查出"这个 URL 该走哪些拦截器"。

------

## 六、运行期：一次请求的逐行拆解

请求：`GET /admin/dish/page?page=1&size=10`，header 带 `token: xxx`

```
① DispatcherServlet 收到请求
② getHandler() 匹配到 DishController.page → 包装成 HandlerMethod
③ 构造 HandlerExecutionChain（handler + 命中的拦截器列表）
   路径 /admin/dish/page 匹配 /admin/**，且不在排除列表 → JwtTokenAdminInterceptor 入链
④ applyPreHandle → 进入 JwtTokenAdminInterceptor.preHandle
```

方法体逐行含义：

```java
public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {

    // 【1】非 Controller 请求（静态资源等）直接放行
    //      依赖框架的 HandlerMethod 类型；不做这步会 ClassCastException 或误拦静态资源
    if (!(handler instanceof HandlerMethod)) {
        return true;
    }

    // 【2】从请求头取 token
    //      header 名来自 JwtProperties（管理端是 "token"），属于前后端隐式契约
    String token = request.getHeader(jwtProperties.getAdminTokenName());

    try {
        // 【3】验签 + 校验过期，返回 payload 的 Map 视图（Claims）
        //      密钥必须与签发时一致，否则 SignatureException
        Claims claims = JwtUtil.parseJWT(jwtProperties.getAdminSecretKey(), token);

        // 【4】取出业务身份。claims 里只有 empId，值可能是 Integer/Long，统一转 Long
        Long empId = Long.valueOf(claims.get(JwtClaimsConstant.EMP_ID));

        // 【5】写入 ThreadLocal，供后续 Controller/Service/AOP 读取
        //      这是"身份传递"的唯一通道，避免把 empId 一路当参数传
        BaseContext.setCurrentId(empId);

        // 【6】放行
        return true;
    } catch (Exception ex) {
        // 【7】token 缺失/被篡改/过期 → 设 401 并中断
        //      必须显式设状态码，否则前端收到 200 空响应，无法触发跳转登录
        response.setStatus(401);
        return false;
    }
}
⑤ 返回 true → 继续执行 DishController.page → Service → Mapper
   （Service 里可用 BaseContext.getCurrentId() 做数据隔离、填充 update_user）
⑥ 返回结果 → 消息转换器序列化 JSON
⑦ postHandle（项目未覆写）
⑧ afterCompletion（项目多数版本未覆写 ← 这是隐患，见第八节）
```

失败分支：

```
④' token 无效 → setStatus(401) → return false
⑤' applyPreHandle 收到 false → 不再调用后续拦截器、不调用 Controller
⑥' 已返回 true 的拦截器触发 afterCompletion → 响应直接返回 401（body 为空）
```

------

## 七、为什么要写两个拦截器，能不能合并

**写两个的直接原因：两端配置不同。**

|            | 管理端                  | 用户端                                  |
| ---------- | ----------------------- | --------------------------------------- |
| header 名  | `token`                 | `authentication`                        |
| 密钥       | `adminSecretKey`        | `userSecretKey`                         |
| claims key | `empId`                 | `userId`                                |
| 拦截路径   | `/admin/**`             | `/user/**`                              |
| 排除路径   | `/admin/employee/login` | `/user/user/login`、`/user/shop/status` |

密钥分开带来一个免费的安全收益：**两端 token 互不通用**，拿小程序 token 调 `/admin/**` 会验签失败。

**但两个类 90% 代码重复，可以合并成一个。** 利用框架给的 `HandlerMethod` 判断归属：

```java
@Component
@Slf4j
public class JwtTokenInterceptor implements HandlerInterceptor {

    @Autowired
    private JwtProperties jwtProperties;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws Exception {
        if (!(handler instanceof HandlerMethod)) {
            return true;
        }

        // 用 Controller 所在包判断是管理端还是用户端
        String pkg = ((HandlerMethod) handler).getBeanType().getPackageName();
        boolean isAdmin = pkg.startsWith("com.sky.controller.admin");

        String tokenName = isAdmin ? jwtProperties.getAdminTokenName() : jwtProperties.getUserTokenName();
        String secretKey = isAdmin ? jwtProperties.getAdminSecretKey() : jwtProperties.getUserSecretKey();
        String claimKey  = isAdmin ? JwtClaimsConstant.EMP_ID : JwtClaimsConstant.USER_ID;

        String token = request.getHeader(tokenName);
        try {
            Claims claims = JwtUtil.parseJWT(secretKey, token);
            Long id = Long.valueOf(claims.get(claimKey));
            BaseContext.setCurrentId(id);
            return true;
        } catch (Exception ex) {
            response.setStatus(401);
            return false;
        }
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) {
        BaseContext.removeCurrentId();   // 关键：清理 ThreadLocal
    }
}
```

注册也简化成一条：

```java
@Override
protected void addInterceptors(InterceptorRegistry registry) {
    registry.addInterceptor(jwtTokenInterceptor)
            .addPathPatterns("/admin/**", "/user/**")
            .excludePathPatterns("/admin/employee/login", "/user/user/login", "/user/shop/status");
}
```

**更优雅的做法是自定义注解**：定义 `@RequireAuth(role = "admin")`，在 `preHandle` 里用 `handlerMethod.getMethodAnnotation(RequireAuth.class)` 判断，这样"哪些接口要鉴权、要什么角色"写在接口上，而不是维护一长串路径。

------

## 八、坑与改进（这部分是重点）

### 1. ThreadLocal 没清理 —— 最严重的隐患

Tomcat 用线程池复用线程。`preHandle` 里 `setCurrentId` 后，如果不在 `afterCompletion` 里 `removeCurrentId()`：

- 线程归还池子时值还在，下个请求若走了**被排除的路径**（如登录接口，`preHandle` 直接 return 或没入链），`BaseContext.getCurrentId()` 可能读到**上一个用户的 id** → 越权/数据串号；
- `ThreadLocal` 的 entry 不删，`ThreadLocalMap` 里的 value 一直被强引用，存在内存泄漏风险。

**修复就是覆写 `afterCompletion`**（见上面合并版代码）。官方教学版很多省略了这一步，属于真实缺陷。

### 2. 401 响应体是空的

`response.setStatus(401)` 只设状态码，body 为空。前端 axios 能靠状态码跳转，但拿不到统一错误结构。改进：

```java
catch (Exception ex) {
    response.setStatus(401);
    response.setContentType("application/json;charset=UTF-8");
    response.getWriter().write(JSON.toJSONString(Result.error("未登录或登录已过期")));
    return false;
}
```

注意别用 `response.sendError(401)`，它会走容器的错误页流程，可能返回 HTML。

### 3. 跨域预检 OPTIONS 被拦

前端跨域时浏览器先发 `OPTIONS` 请求，不带 token。若被拦截器 401，实际请求根本发不出。解决：

```java
if ("OPTIONS".equalsIgnoreCase(request.getMethod())) {
    return true;
}
```

或配置 CORS 时把预检交给框架处理。

### 4. 静态资源与文档路径要排除

继承了 `WebMvcConfigurationSupport` 后手动映射了 `/doc.html`、`/webjars/**`，这些路径如果落在拦截范围内会 401，导致文档打不开。务必加入 `excludePathPatterns`。

### 5. `excludePathPatterns` 必须和真实 URL 完全一致

漏写 `/admin/employee/login` → 永远登不进去；漏写 `/user/shop/status` → 小程序首页拿不到营业状态。这类 bug 报错信息只有 401，很难一眼定位，建议加集成测试覆盖。

### 6. 拦截器不能持有请求级状态

singleton 实例被多线程共享，**成员变量存 `empId`、`userId` 是严重错误**。所有请求级数据走 `ThreadLocal` 或方法参数。

### 7. 拦截器 ≠ 过滤器

|                          | Filter                         | HandlerInterceptor                      |
| ------------------------ | ------------------------------ | --------------------------------------- |
| 规范                     | Servlet 规范                   | Spring MVC                              |
| 执行位置                 | Spring MVC 之前/之后           | `DispatcherServlet` 内、Controller 前后 |
| 能否拿到 `HandlerMethod` | 不能                           | 能                                      |
| 适合做什么               | 编码、跨域、日志、大 body 处理 | 鉴权、权限、按接口决策                  |

JWT 校验需要"是不是 Controller 方法"这个信息，所以用拦截器。

### 8. 拦截器覆盖不到 WebSocket

`/ws/{sid}` 不走 Spring MVC 的 `HandlerMapping`，所以两个 JWT 拦截器**都不拦它**，`sid` 可以随便填。生产环境需在握手阶段校验 token（Spring WebSocket 的 `HandshakeInterceptor`，或 JSR-356 的 `Configurator`）。

### 9. 只有认证、没有授权

拦截器只回答"你是谁"，不回答"你能不能做这件事"。拿到 `empId` 后所有接口权限相同，越权风险靠业务代码自觉（比如订单查询必须按 `BaseContext.getCurrentId()` 过滤，绝不能信前端传的 userId）。加 RBAC 时应区分 **401（未认证）** 与 **403（无权限）**。

### 10. 多拦截器时用 `order` 显式排序

默认按注册顺序执行。如果以后加了"日志拦截器""限流拦截器"，用 `.order(1)` / `.order(2)` 显式声明，避免依赖注册顺序这种隐式约定。

------

## 九、一句话总结

**框架给的是"骨架"：** `HandlerInterceptor` 定义三个时机，`InterceptorRegistry` 提供注册 API，`HandlerMethod` 告诉你拦到了什么，`DispatcherServlet` 按返回值决定要不要继续。

**项目写的是"血肉"：** 取 header、验签、取 `empId`、塞 ThreadLocal、失败设 401。

而这套实现真正的风险不在"写没写出来"，而在三个被省略的细节——**`afterCompletion` 没清 ThreadLocal、401 没有响应体、WebSocket 不在拦截范围内**。能指出这三点，才算真正读懂了这段代码。

需要的话，我可以把改进版完整写出来（合并拦截器 + `afterCompletion` 清理 + 统一 401 响应体 + OPTIONS 放行 + 自定义 `@RequireAuth` 注解方案），你直接对照替换现有代码。