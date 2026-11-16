axios 是前端发 HTTP 请求的工具；HttpClientUtil 是后端发 HTTP 请求的工具

### 一、HttpClientUtil 涉及的核心类

| 类                      | 作用                         | 常见用法                                               |
| ----------------------- | ---------------------------- | ------------------------------------------------------ |
| `HttpClients`           | 工厂类，用来创建 HTTP 客户端 | `HttpClients.createDefault()`                          |
| `CloseableHttpClient`   | HTTP 客户端，负责执行请求    | `httpClient.execute(httpGet)`                          |
| `HttpGet`               | GET 请求对象                 | `new HttpGet(uri)`                                     |
| `HttpPost`              | POST 请求对象                | `new HttpPost(url)`                                    |
| `CloseableHttpResponse` | HTTP 响应对象                | `httpClient.execute(...)` 返回                         |
| `HttpEntity`            | 请求体/响应体                | `response.getEntity()`                                 |
| `EntityUtils`           | 把响应体转成字符串           | `EntityUtils.toString(entity, "UTF-8")`                |
| `URIBuilder`            | 拼接带参数的 URL             | `builder.addParameter(key, value)`                     |
| `BasicNameValuePair`    | 表示一个表单参数             | `new BasicNameValuePair("k","v")`                      |
| `UrlEncodedFormEntity`  | 表单参数实体                 | `new UrlEncodedFormEntity(list)`                       |
| `StringEntity`          | JSON/文本请求体              | `new StringEntity(json, ContentType.APPLICATION_JSON)` |
| `RequestConfig`         | 超时、连接配置               | `setConnectTimeout`、`setSocketTimeout`                |



### 四、doPostJson：发送 JSON POST 请求

```java
public static String doPostJson(String url, String json) {
    CloseableHttpClient httpClient = HttpClients.createDefault();
    CloseableHttpResponse response = null;
    try {
        HttpPost httpPost = new HttpPost(url);
        httpPost.setHeader("Content-Type", "application/json;charset=UTF-8");

        StringEntity entity = new StringEntity(json, "UTF-8");
        entity.setContentType("application/json");
        httpPost.setEntity(entity);

        response = httpClient.execute(httpPost);

        if (response.getStatusLine().getStatusCode() == 200) {
            return EntityUtils.toString(response.getEntity(), "UTF-8");
        }
    } catch (Exception e) {
        throw new RuntimeException("JSON POST 请求失败", e);
    } finally {
        try {
            if (response != null) response.close();
            httpClient.close();
        } catch (IOException ignored) {}
    }
    return null;
}
```

