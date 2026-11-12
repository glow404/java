# 14 PageHelper 分页

## 1. 分页解决什么问题

数据量大时不能一次把全部记录加载到内存。分页将查询拆成指定页码和页大小，并同时返回总记录数，让前端渲染分页控件。

## 2. 项目中的代码

配置位置：[`PageHelperConfiguration.java`](../../../sky-server/src/main/java/com/sky/config/PageHelperConfiguration.java)

Service 中的典型写法：

```java
PageHelper.startPage(dto.getPage(), dto.getPageSize());
Page<Employee> page = employeeMapper.pageQuery(dto);

return new PageResult(page.getTotal(), page.getResult());
```

员工分页实现位于 [`EmployeeServiceImpl.java`](../../../sky-server/src/main/java/com/sky/service/impl/EmployeeServiceImpl.java)，菜品、套餐和订单也采用类似模式。

## 3. 执行原理

```text
PageHelper.startPage(page, pageSize)
  -> 拦截下一条 Mapper 查询
  -> 改写为分页 SQL
  -> 额外执行 count 查询
  -> 返回 Page<T>
```

`startPage` 通常只影响紧接着的下一条查询，所以不要在它和目标查询之间插入其他 Mapper 查询。

## 4. 分页接口的责任

- Controller 接收页码、页大小和筛选条件。
- Service 开启分页并转换结果。
- Mapper 提供正常的列表 SQL。
- `PageResult` 对外暴露 `total` 和 `records`。

## 5. 学习练习

测试页码为 1、页大小为 10 和页码超出范围时的行为；再观察排序字段是否稳定，否则翻页时可能出现重复或遗漏记录。

