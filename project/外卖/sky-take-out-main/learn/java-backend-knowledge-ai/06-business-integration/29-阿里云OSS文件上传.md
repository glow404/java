# 29 阿里云 OSS 文件上传

## 1. 为什么使用对象存储

图片等大文件不适合直接存进业务数据库。对象存储负责保存文件，数据库或业务对象只保存文件访问地址和元数据。

## 2. 项目中的上传接口

代码位置：[`CommonController.java`](../../../sky-server/src/main/java/com/sky/controller/admin/CommonController.java)

```java
@PostMapping("/upload")
public Result<String> upload(MultipartFile file) {
    String extension = ...;
    String objectName = UUID.randomUUID() + extension;
    String filePath = aliOssUtil.upload(
            file.getBytes(), objectName);
    return Result.success(filePath);
}
```

`AliOssUtil` 位于 `sky-common/utils`，OSS 客户端由 `OssConfiguration` 根据 `AliOssProperties` 创建。

## 3. 上传链路

```text
multipart/form-data 请求
  -> MultipartFile
  -> 校验文件并生成唯一对象名
  -> 上传 OSS
  -> 返回 URL
  -> 业务表保存 URL
```

## 4. 安全与可靠性

- 必须限制文件大小、扩展名和真实 MIME 类型。
- 文件名不能直接使用用户原始文件名作为对象 Key。
- 不能信任前端传来的扩展名。
- 应限制上传权限，必要时使用临时签名 URL。
- 上传成功但数据库保存失败时，需要清理孤儿文件或设计补偿任务。
- Access Key 和 Secret 只能在服务端配置。

## 5. 学习练习

增加文件大小和类型校验，并为上传失败、空文件和非法扩展名分别编写测试。

