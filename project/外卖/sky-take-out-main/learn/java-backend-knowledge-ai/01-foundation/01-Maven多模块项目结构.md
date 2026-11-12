# 01 Maven 多模块项目

## 1. 这个知识点解决什么问题

Maven 负责项目构建、依赖下载、编译、测试和打包。多模块结构把一个较大的工程拆成多个可以独立编译、但又能互相依赖的模块。

这样做的价值是：公共代码可以复用，数据模型可以集中管理，最终运行模块与基础模块的职责更清晰。

## 2. 项目中的代码

根目录 [`pom.xml`](../../../pom.xml) 的关键配置是：

```xml
<packaging>pom</packaging>

<modules>
    <module>sky-common</module>
    <module>sky-pojo</module>
    <module>sky-server</module>
</modules>
```

`sky-server/pom.xml` 显式依赖：

```xml
<dependency>
    <groupId>com.sky</groupId>
    <artifactId>sky-common</artifactId>
</dependency>
<dependency>
    <groupId>com.sky</groupId>
    <artifactId>sky-pojo</artifactId>
</dependency>
```

依赖关系是：

```text
sky-server -> sky-common
sky-server -> sky-pojo
```

根 POM 还通过 `properties` 和 `dependencyManagement` 统一管理 Spring Boot、MyBatis、PageHelper、POI 等依赖版本。

## 3. 构建时发生什么

从根目录执行 Maven 命令时，Maven 会先解析父工程，再按照模块依赖顺序构建：

```text
读取根 pom.xml
  -> 构建 sky-common
  -> 构建 sky-pojo
  -> 构建 sky-server
  -> 打包 Spring Boot 应用
```

`sky-common` 和 `sky-pojo` 生成的类库会成为 `sky-server` 的编译依赖。只有 `sky-server` 配置了 `spring-boot-maven-plugin`，所以它是主要的可运行模块。

## 4. 关键概念

- `parent`：继承公共配置和版本。
- `modules`：告诉父工程要聚合哪些子模块。
- `dependencies`：当前模块实际需要编译或运行的依赖。
- `dependencyManagement`：统一声明版本，不代表子模块自动拥有该依赖。
- `packaging=pom`：当前项目用于组织模块，而不是生成 Jar。

## 5. 学习练习

尝试回答：如果 `sky-server` 不依赖 `sky-pojo`，Controller 中的 `EmployeeDTO` 会发生什么？如果把一个依赖放进父工程的 `dependencyManagement`，子模块是否可以直接使用它？

