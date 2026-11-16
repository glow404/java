## 二、接入部分（三处，缺一不可）

**1）`sky-server/pom.xml`**

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-redis</artifactId>
</dependency>
```

**2）`application-dev.yml` / `application.yml`**

```yaml
# application-dev.yml
sky:
  redis:
    host: localhost
    port: 6379
    password: 123456
    database: 10

# application.yml
spring:
  profiles:
    active: dev
  redis:
    host: ${sky.redis.host}
    port: ${sky.redis.port}
    password: ${sky.redis.password}
    database: ${sky.redis.database}
```

`database: 10` 是刻意和别的项目隔离——Redis 默认有 16 个库（0~15）

**3）`com/sky/config/RedisConfiguration.java`**

```java
package com.sky.config;

@Configuration
@Slf4j
public class RedisConfiguration {

    @Bean
    public RedisTemplate redisTemplate(RedisConnectionFactory redisConnectionFactory) {
        log.info("开始创建redis模板对象...");
        RedisTemplate redisTemplate = new RedisTemplate();
        redisTemplate.setConnectionFactory(redisConnectionFactory);
        // 设置redis key的序列化器
        redisTemplate.setKeySerializer(new StringRedisSerializer());
        return redisTemplate;
    }
}
```

**这个类是理解全局的第一个关键点。** Spring Boot 本来会自动装配 `RedisTemplate`，但默认 key 序列化器是 `JdkSerializationRedisSerializer`，写进 Redis 的 key 会带一串二进制前缀，`keys dish_*` 匹配不到、肉眼也读不了 。换成 `StringRedisSerializer` 后，Redis 里才是 `dish_13` 这种可读字符串。

注意它**只改了 key 的序列化器，value 仍是 JDK 序列化**。

------

## 三、读路径：`user/DishController.java`

```java
@RestController("userDishController")
@RequestMapping("/user/dish")
@Api(tags = "C端-菜品浏览接口")
public class DishController {

    @Autowired
    private DishService dishService;

    @Autowired
    private RedisTemplate redisTemplate;

    @GetMapping("/list")
    @ApiOperation("根据分类id查询菜品")
    public Result<List<DishVO>> list(Long categoryId) {
        // 构造redis中的key，规则：dish_分类id
        String key = "dish_" + categoryId;

        // 查询redis中是否存在菜品数据
        List<DishVO> list = (List<DishVO>) redisTemplate.opsForValue().get(key);
        if (list != null && list.size() > 0) {
            // 如果存在，直接返回，无须查询数据库
            return Result.success(list);
        }

        Dish dish = new Dish();
        dish.setCategoryId(categoryId);
        dish.setStatus(StatusConstant.ENABLE); // 查询起售中的菜品

        // 如果不存在，查询数据库，将查询到的数据放入redis中
        list = dishService.listWithFlavor(dish);
        redisTemplate.opsForValue().set(key, list);

        return Result.success(list);
    }
}
```

### 这段代码的知识点

**② `opsForValue()` 是五大 Operations 之一。**
`RedisTemplate` 按 Redis 数据类型归类了操作入口：`opsForValue`（string）、`opsForHash`、`opsForList`、`opsForSet`、`opsForZSet`。这里只用到了 string。另外 `set()` 的 value 参数类型是 `Object`，任何对象都会被序列化后存成 Redis 的字符串——所以 `List<DishVO>` 能直接塞进去。

**③ 缓存的是"起售菜品"这个语义，不是全表。**
`dish.setStatus(StatusConstant.ENABLE)` 说明缓存的 value 是"该分类下起售中的菜品列表"。这个语义很重要：它决定了后面哪些写操作必须清缓存——**起售/停售也必须清**，因为停售的菜品还留在旧缓存里。

------

## 四、写路径：`admin/DishController.java` 的缓存清理

需要改造四个方法：**新增菜品、修改菜品、批量删除、起售/停售**。

先写一个私有清理方法以便批量删除缓存：

```java
private void cleanCache(String pattern) {
    Set<String> keys = redisTemplate.keys(pattern);
    redisTemplate.delete(keys);
}
```

新增菜品时精确清理：

```java
@PostMapping
@ApiOperation("新增菜品")
public Result save(@RequestBody DishDTO dishDTO) {
    log.info("新增菜品：{}", dishDTO);
    dishService.saveWithFlavor(dishDTO);
    // 只清理该分类的缓存
    String key = "dish_" + dishDTO.getCategoryId();
    cleanCache(key);
    return Result.success();
}
```

修改、删除、起售停售时批量清理：

```java
// 将所有的菜品缓存数据清理掉，即所有以dish_开头的key
cleanCache("dish_*");
```

### 这里的知识点

**① 为什么是"删缓存"而不是"改缓存"？**
数据库变更后直接删掉缓存，让下次读请求重新从库里加载，比同步更新缓存更简单、更不容易写进脏数据。代价是多一次数据库查询。这是"简单性优先"的典型工程决策。

**② `delete` 不认通配符，必须 `keys` + `delete` 两步。**
`redisTemplate.delete("dish_*")` 是**无效**的——删除时不会解析通配符。所以要先 `keys("dish_*")` 拿到所有匹配 key 的集合，再把整个集合传给 `delete`。









------

### 1. 缓存穿透（Cache Penetration）

- **核心知识点**：查询的数据在**缓存和数据库中都不存在**，导致请求直接绕过缓存，持续打到数据库。
- **项目现状分析**：苍穹外卖中，如果用户端请求一个不存在的分类 ID（如 `categoryId=9999`），Redis 中没有这个 key，查 MySQL 也没有。由于数据库查不到数据，代码就不会执行 `redisTemplate.opsForValue().set(key, list)`，导致这个无效 key 永远不会被缓存。如果有恶意脚本用大量不存在的 ID 发起请求，数据库会瞬间被压垮。
- **生产环境解决方案**：
  1. **缓存空值（空对象）**：当数据库查询结果为空时，也在 Redis 中写入一个空标记（如 `"NULL"` 或 `""`），并设置一个较短的过期时间（如 60 秒）。后续相同请求命中空值直接返回，不再查库。
  2. **布隆过滤器（Bloom Filter）**：在缓存前加一层布隆过滤器，将所有合法的分类 ID 提前加载进去。请求进来先过布隆过滤器，如果判定“不存在”，则直接拦截，连 Redis 都不查。
  3. **接口参数校验与限流**：在网关层或 Controller 层校验参数合法性（如 ID 必须大于 0），并对异常高频 IP 进行限流。

### 2. 缓存击穿（Cache Breakdown）

- **核心知识点**：**单个热点 Key** 在过期的瞬间，大量并发请求同时发现缓存未命中，进而集中访问数据库。
- **项目现状分析**：苍穹外卖的菜品缓存**没有设置 TTL（过期时间）**，属于“永不过期 + 主动删除”策略，因此**客观上规避了缓存击穿问题**。因为只要管理端不主动删除，热点 key 就一直在 Redis 中。但如果未来为了兜底一致性加上了 TTL，这个风险就会暴露。
- **生产环境解决方案**（如果使用了 TTL）：
  1. **互斥锁（分布式锁）**：缓存失效时，只允许一个线程（通过 `SETNX` 抢锁）去查数据库并重建缓存，其他线程等待或重试。保证回源压力被串行化。
  2. **逻辑过期**：不设置物理 TTL，而是在缓存数据中附带一个“逻辑过期时间”。请求读取时若发现逻辑过期，仍返回旧数据，但触发一个异步线程去后台刷新缓存。适合高并发且能容忍短暂旧数据的场景。

### 3. 缓存雪崩（Cache Avalanche）

- **核心知识点**：**大量 Key 在同一时间集中失效**，或者 Redis 服务整体宕机，导致全量请求涌向数据库。
- **项目现状分析**：同缓存击穿，苍穹外卖未设置 TTL，不存在“集中过期”的物理条件。但它的隐患在于**强依赖清理逻辑的正确性**：如果管理端清理缓存的代码出现 Bug（例如 `cleanCache` 没被调用），或者 Redis 实例宕机，由于没有 TTL 兜底，这些脏数据会永久留在内存中，或者在宕机恢复后导致大面积缓存失效。
- **生产环境解决方案**：
  1. **TTL 加随机值**：为每个 Key 设置基础过期时间的同时，加上一个随机偏移量（如 `30分钟 + 0~5分钟随机值`），打散过期时间点。
  2. **高可用架构**：部署 Redis Sentinel（哨兵）或 Redis Cluster（集群），避免单点故障。
  3. **限流与降级**：当数据库压力过大时，触发熔断降级，返回默认数据或提示“稍后重试”，保护核心系统。

### 4. `keys` 命令风险

- **核心知识点**：Redis 是单线程模型，且是同步阻塞执行。
- **项目现状分析**：苍穹外卖在清理缓存时使用了 `redisTemplate.keys("dish_*")`。在教学项目数据量极少时毫无问题；但在生产环境中，如果 Redis 中有几百万个 key，执行 `keys` 会阻塞 Redis 主线程数秒甚至更久，导致“卡死”。
- **生产环境解决方案**：
  1. **使用 `SCAN` 命令**：`SCAN` 是游标迭代器，每次只返回一小部分匹配的 key，分多次执行，不会长时间阻塞 Redis。Spring Data Redis 提供了 `RedisTemplate.execute(RedisCallback)` 配合 `ScanOptions` 来安全遍历。
  2. **优化 Key 设计**：避免使用通配符删除。例如将同一分类的菜品存入 Redis Hash 结构（`key: dish_category:{id}, field: dish_id`），清理时直接 `DEL dish_category:{id}` 即可，O(1) 复杂度。

### 5. Value 序列化

- **核心知识点**：Java 对象存入 Redis 前必须序列化为字节流。
- **项目现状分析**：苍穹外卖的 `RedisConfiguration` 只配置了 Key 的序列化器（`StringRedisSerializer`），Value 默认使用 `JdkSerializationRedisSerializer`。这导致存入 Redis 的 Value 是一串不可读的 Java 字节码，且**强依赖 Java 的 `serialVersionUID`**。如果后续修改了 `DishVO` 的字段，旧缓存反序列化时会直接报错。此外，JDK 序列化体积大，且无法被 Python、Go 等其他语言读取。
- **生产环境解决方案**： 配置 `GenericJackson2JsonRedisSerializer`。它会将对象序列化为 JSON 字符串，不仅肉眼可读、跨语言兼容，而且体积更小。

### 6. 缓存与事务时序（双写一致性）

- **核心知识点**：更新数据库和删除缓存是两个独立操作，无法保证原子性。
- **项目现状分析**：苍穹外卖的代码是：先执行 `dishService.saveWithFlavor()`（内部有 `@Transactional`），成功后再执行 `cleanCache()`。 **极端并发窗口**：如果清理缓存的动作在数据库事务提交**之前**执行（假设代码顺序写反了，或者在事务内），此时另一个读请求进来，发现缓存没了，去查数据库。由于写事务还没提交（MySQL 默认隔离级别是可重复读），读请求查到的是**旧数据**，并将旧数据重新写入缓存。等写事务提交后，缓存里依然是旧数据，导致严重的不一致。
- **生产环境解决方案**：
  1. **Cache Aside 标准模式**：严格保证**先更新数据库，再删除缓存**。且删除缓存的操作应放在事务提交之后（可通过 Spring 的 `TransactionSynchronizationManager.registerSynchronization` 在事务提交后回调）。
  2. **延迟双删**：更新 DB -> 删除缓存 -> 休眠几百毫秒 -> 再次删除缓存。防止并发读请求在第一次删缓存后、DB更新前，把旧值写回缓存。
  3. **Binlog 异步订阅**：使用 Canal 等中间件监听 MySQL Binlog，当数据库发生真实变更时，异步发送 MQ 消息去删除 Redis 缓存。这是解耦业务代码、保证最终一致性的终极方案



