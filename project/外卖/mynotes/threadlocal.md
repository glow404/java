# ThreadLocal

## **1. ThreadLocal 是什么？解决什么问题？**

答：它是 JDK 提供的线程隔离工具，全限定名 `java.lang.ThreadLocal`。同一个 ThreadLocal 实例，不同线程调用 `get()/set()` 访问到的是各自线程独立的副本，互不干扰。典型场景就是 Web 请求中传递当前登录用户、数据库连接、事务上下文、日志 traceId。

## **3. 数据到底存在哪里？**

答：存在 `Thread` 对象内部的 `threadLocals` 字段里，类型是 `ThreadLocal.ThreadLocalMap`。ThreadLocal 实例只是作为这个 Map 的 key。

## **4. 为什么 ThreadLocal 实例通常声明为 `static final`？**

答：因为它是 key。声明为静态，保证全 JVM 范围内所有代码拿到的是同一个 key，才能读写到同一份"当前线程的值"。如果每次访问都 `new ThreadLocal<>()`，key 变了，`get()` 永远返回 null。苍穹外卖 `BaseContext` 里 `private static ThreadLocal<Long> threadLocal` 就是这个原因。



```
                BaseContext.tl(index=7)   MDC 的 tl(index=12)   某个 tl(index=20)
exec-1 的 Map   → 1001L                   "trace-abc"           null(无条目)
exec-2 的 Map   → 1002L                   "trace-xyz"           3.14
main   的 Map   → null                    null                  null
```

把 BaseContext.ThreadLocal 想象成同一把公共钥匙，每个线程都有一个自己的储物柜，Thread.threadLocals。



## **5. ThreadLocalMap 的 key 为什么用弱引用？**

答：为了在 ThreadLocal 实例不再被强引用时能被 GC 回收，避免 key 泄漏。但 Entry 中的 value 是**强引用**，key 被回收后变成 `null → value` 的滞留条目，value 仍无法回收。这就是经典的内存泄漏点，也是必须 `remove()` 的核心理由。

## **6. ThreadLocal的`get()`、`set()`、`remove()`、`initialValue()` 各自的职责是什么？`**

答：`set()` 写入当前线程的 Map；`get()` 读取，若当前线程没有该 key 的条目，会调用 `initialValue()`（默认返回 null），可覆写它提供默认值；`remove()` 删除条目。

## **8. 不 remove 会造成什么后果？在 Tomcat 环境下尤其严重的原因是什么？**

答：两个后果。一是**数据串号**：Tomcat 用线程池处理请求，线程复用，上一次请求 set 的用户 ID 若不清理，下一个请求（甚至未登录请求）会读到脏数据，属于越权级别的安全问题。二是**内存泄漏**：如第 5 题所述，滞留的 value 无法回收。规范写法是 `try { ... } finally { remove(); }`。



## **11. 苍穹外卖为什么要封装 BaseContext，而不是各处直接 new ThreadLocal？**

答：统一入口、限定类型、集中清理。如果每个类自己 `new ThreadLocal<>()`，key 不同根本读不到同一个值；而且散落的 ThreadLocal 无法保证都被 remove。封装后"当前用户"这个概念在代码里只有一个出处，便于扩展和排查。

## **12. 拦截器写入、AOP 读取，这个链路能讲一下吗？为什么 AOP 里能拿到值？**

答：`JwtTokenAuthenticationFilter.preHandle` 解析 JWT 取出 `empId`，调用 `BaseContext.setCurrentId()`；因为**同一次请求由同一个线程处理**（Servlet 容器保证），后续进入 Controller、Service、AOP 切面时线程没变，所以 `AutoFillAspect` 里 `getCurrentId()` 能拿到值，用于反射填充 `createUser`/`updateUser`。`afterCompletion` 里再 remove。

如果 Service 里用了 `@Async` 或线程池，这个链路就断了——能主动说出这一点，是加分项。



1. **"如果让你改进 BaseContext，你会怎么做？"** —— 原版只存一个 `Long`，管理端存 empId、用户端存 userId，靠"两端接口分开"保证不冲突。可以改成存一个上下文对象（含 id、type、username），或拆成两个 ThreadLocal 再封装统一工具类。
2. **"MDC 和 ThreadLocal 什么关系？"** —— MDC 底层就是 ThreadLocal，用来放 traceId 等日志上下文，思路完全一致，同样有线程池传递问题（可用 MDC 的 `getCopyOfContextMap` 手动透传）。









