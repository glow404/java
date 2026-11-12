# 22 Spring Cache 声明式缓存

## 1. 核心概念

Spring Cache 用注解把缓存行为声明在方法上。业务代码只描述“这个方法的结果可以缓存”或“执行后清除缓存”，框架负责读写缓存。

## 2. 项目中的代码

启动类开启缓存：

```java
@EnableCaching
public class SkyApplication { ... }
```

用户端套餐查询使用：

```java
@Cacheable(cacheNames = "setmealCache", key = "#categoryId")
public Result<List<Setmeal>> list(Long categoryId) {
    ...
}
```

管理端新增、修改、删除或启停套餐时使用 `@CacheEvict` 清理缓存。相关文件是两个 `SetmealController`。

## 3. 注解语义

| 注解 | 含义 |
| --- | --- |
| `@Cacheable` | 先按 Key 查缓存，未命中才执行方法 |
| `@CacheEvict` | 执行方法后删除指定缓存 |
| `allEntries=true` | 清空某个 Cache 区域的全部条目 |
| `key="#categoryId"` | 用方法参数生成缓存 Key |

## 4. 与手动 Redis 的比较

菜品使用 `RedisTemplate` 手动控制，套餐使用 Spring Cache 注解。前者更灵活，后者代码更简洁，但缓存 Key、序列化和失效时机需要理解框架默认行为。

## 5. 学习重点

缓存注解依赖 Spring AOP 代理。一个类内部直接调用自己的缓存方法，可能绕过代理，导致注解不生效；缓存方法也应避免把不稳定的对象或敏感数据直接放入缓存。

