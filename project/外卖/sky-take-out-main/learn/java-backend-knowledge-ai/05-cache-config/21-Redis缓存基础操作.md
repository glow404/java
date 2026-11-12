# 21 Redis 基础操作

## 1. Redis 解决什么问题

Redis 是内存中的 Key-Value 数据库，适合保存访问频繁、变化相对较少的数据。把热点数据放到 Redis，可以减少数据库查询和响应时间。

## 2. 项目中的配置

代码位置：[`RedisConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/RedisConfiguration.java)

```java
@Bean
public RedisTemplate redisTemplate(
        RedisConnectionFactory connectionFactory) {
    RedisTemplate redisTemplate = new RedisTemplate();
    redisTemplate.setConnectionFactory(connectionFactory);
    redisTemplate.setKeySerializer(new StringRedisSerializer());
    return redisTemplate;
}
```

Redis 地址、端口和密码来自 `application.yml`。

## 3. 菜品缓存案例

代码位置：[`controller/user/DishController.java`](../../../sky-server/src/main/java/com/sky/controller/user/DishController.java)

```java
String key = "dish_" + categoryId;
List<DishVO> list = (List<DishVO>) redisTemplate
        .opsForValue().get(key);

if (list == null || list.isEmpty()) {
    list = dishService.listWithFlavor(dish);
    redisTemplate.opsForValue().set(key, list);
}
```

这是一种 Cache-Aside（旁路缓存）模式：先查缓存，未命中时查数据库，再写入缓存。

## 4. 学习重点

- Key 的命名要有规律，便于清理和排查。
- Redis 的数据序列化方式会影响兼容性和可读性。
- 缓存不是永久数据，必须考虑过期、删除和数据库更新后的失效。
- 高并发下还要考虑缓存击穿、穿透和雪崩。

## 5. 学习练习

给菜品缓存增加 TTL，分别观察命中、过期和重新加载的日志；再设计一个不存在菜品的缓存策略，理解缓存穿透。

