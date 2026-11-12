# 13 MyBatis 动态 SQL

## 1. 为什么需要动态 SQL

分页搜索通常允许用户只填写部分条件。动态 SQL 可以根据参数是否为空，决定是否拼接对应的 `WHERE`、`SET` 或批量条件。

## 2. 项目中的代码

[`EmployeeMapper.xml`](../../../sky-server/src/main/resources/mapper/EmployeeMapper.xml) 中的分页查询使用：

```xml
SELECT * FROM employee
<where>
    <if test="name != null and name != ''">
        AND name like concat('%', #{name}, '%')
    </if>
</where>
ORDER BY create_time DESC
```

更新语句使用 `<set>`：

```xml
<set>
    <if test="name != null">name = #{name},</if>
    <if test="status != null">status = #{status},</if>
</set>
```

`<where>` 会帮助处理开头多余的 `AND`，`<set>` 会帮助处理结尾逗号。

## 3. 常见标签

| 标签 | 用途 |
| --- | --- |
| `<if>` | 条件成立时拼接 SQL |
| `<where>` | 自动处理 WHERE 和开头连接符 |
| `<set>` | 动态更新字段并处理逗号 |
| `<foreach>` | 批量 ID、批量插入、`IN` 查询 |
| `<choose>` | 类似 Java 的 if/else if/else |

## 4. 学习重点

- `test` 使用的是 OGNL 表达式，不是 SQL。
- 动态 SQL 仍然应该使用 `#{}` 绑定参数。
- 条件为空时要确认 SQL 语义是否安全，尤其是批量更新和删除。
- 阅读 XML 时同时想象参数对象的字段值，才能理解最终生成的 SQL。

## 5. 学习练习

为订单查询增加“订单号、状态、开始时间、结束时间”四个可选条件，并分别测试全部为空、只填一个和全部填写的 SQL 结果。

