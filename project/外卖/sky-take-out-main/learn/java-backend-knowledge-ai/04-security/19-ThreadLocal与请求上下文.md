# 19 ThreadLocal 与请求上下文

## 1. 为什么需要请求上下文

很多业务方法需要当前登录用户 ID。如果从 Controller 一直作为参数传到每一层，方法签名会变得冗长。项目使用 `ThreadLocal` 在当前请求线程中保存这个 ID。

## 2. 项目中的代码

代码位置：[`BaseContext.java`](../../../sky-common/src/main/java/com/sky/context/BaseContext.java)

```java
public class BaseContext {
    public static ThreadLocal<Long> threadLocal = new ThreadLocal<>();

    public static void setCurrentId(Long id) {
        threadLocal.set(id);
    }

    public static Long getCurrentId() {
        return threadLocal.get();
    }

    public static void removeCurrentId() {
        threadLocal.remove();
    }
}
```

JWT 拦截器负责 `setCurrentId`，地址、购物车、订单等 Service 负责 `getCurrentId`。

## 3. ThreadLocal 的隔离方式

```text
请求 A 线程 -> 保存 userId=10 -> 读取 userId=10
请求 B 线程 -> 保存 userId=20 -> 读取 userId=20
```

`ThreadLocal` 的值属于线程，而不是所有线程共享的一个普通变量，因此可以避免不同请求直接读到彼此的用户 ID。

## 4. 必须注意的问题

应用服务器通常使用线程池。请求结束后如果不清理，线程被复用时可能残留旧值。项目提供了 `removeCurrentId()`，但当前拦截器代码没有展示统一的 `afterCompletion` 清理逻辑；这是值得补充的工程实践。

异步线程、线程池任务和 WebSocket 回调不会自动继承当前请求的 ThreadLocal 值，不能假设它们天然拥有登录上下文。

## 5. 学习练习

在拦截器中实现 `afterCompletion` 调用 `removeCurrentId()`，并为并发请求写测试，验证不同用户不会互相读取身份。

