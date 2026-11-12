# 12 MyBatis Mapper

## 1. 核心概念

MyBatis 是一个 SQL 映射框架：开发者编写 SQL，MyBatis 负责把方法参数传入 SQL，并把查询结果映射成 Java 对象。

## 2. Mapper 接口

代码位置：[`EmployeeMapper.java`](../../../sky-server/src/main/java/com/sky/mapper/EmployeeMapper.java)

```java
@Mapper
public interface EmployeeMapper {
    @Select("select * from employee where username = #{username}")
    Employee getByUsername(String username);

    Page<Employee> pageQuery(EmployeePageQueryDTO dto);
}
```

`@Mapper` 让 MyBatis-Spring 识别接口，并为它创建代理对象。业务层调用的是接口方法，实际执行的是 MyBatis 生成的代理逻辑。

## 3. 注解 SQL 和 XML SQL

项目同时使用两种方式：

- 简单的单表查询、插入和更新使用 `@Select`、`@Insert`、`@Update`。
- 分页、动态条件、批量删除和跨表查询放在 `resources/mapper/*.xml`。

例如 [`EmployeeMapper.xml`](../../../sky-server/src/main/resources/mapper/EmployeeMapper.xml) 的 namespace 必须与接口全限定名一致：

```xml
<mapper namespace="com.sky.mapper.EmployeeMapper">
    <select id="pageQuery" resultType="com.sky.entity.Employee">
        ...
    </select>
</mapper>
```

`id="pageQuery"` 对应接口中的 `pageQuery` 方法。

## 4. 字段映射

配置中的：

```yaml
map-underscore-to-camel-case: true
```

使数据库字段 `create_time` 可以自动映射到 Java 属性 `createTime`。这减少了重复的 `resultMap` 配置，但复杂查询仍可能需要显式映射。

## 5. 学习重点

- `#{}` 是参数绑定，不应把用户输入直接拼接进 SQL。
- Mapper 只负责数据访问，不应放订单状态等业务规则。
- 方法名、XML 的 `namespace` 和 `id` 必须一致。
- SQL 错误通常要同时检查 Java 接口、XML 路径和数据库字段名。

## 6. 学习练习

新增一个分类查询 Mapper，分别用注解 SQL 和 XML SQL 实现，比较两种方式在条件变多后哪种更容易维护。

