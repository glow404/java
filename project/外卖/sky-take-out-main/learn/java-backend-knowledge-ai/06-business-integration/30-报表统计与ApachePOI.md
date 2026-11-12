# 30 报表统计与 Apache POI

## 1. 统计接口的特点

报表接口通常不是简单查询一条记录，而是按日期范围聚合订单、用户和销量数据，返回图表或卡片所需的专用 VO。

## 2. 项目中的接口

代码位置：[`ReportController.java`](../../../sky-server/src/main/java/com/sky/controller/admin/ReportController.java)

提供：

```text
/admin/report/turnoverStatistics
/admin/report/userStatistics
/admin/report/ordersStatistics
/admin/report/top10
/admin/report/export
```

Controller 接收 `LocalDate begin/end`，调用 `ReportService`，分别返回 `TurnoverReportVO`、`UserReportVO`、`OrderReportVO` 和 `SalesTop10ReportVO`。

## 3. 报表查询链路

```text
日期参数
  -> 转换为开始/结束时间
  -> Mapper 聚合查询 MySQL
  -> Service 组装统计 VO
  -> Result 返回图表数据
```

Excel 导出使用 `resources/template/运营数据报表模板.xlsx` 和 Apache POI，将统计结果写入模板后通过 `HttpServletResponse` 下载。

## 4. 学习重点

- 日期范围通常需要明确是否包含结束日。
- 统计 SQL 要注意空结果、时区、索引和金额精度。
- 报表返回对象应面向图表和页面，而不是直接返回数据库行。
- 文件下载需要设置正确的 Content-Type、文件名和响应流。

## 5. 学习练习

增加“按分类统计销量”的接口，先设计 VO，再写聚合 SQL，最后决定图表需要哪些字段。

