# 31 WebSocket 实时通信

## 1. WebSocket 解决什么问题

普通 HTTP 通常是客户端发起请求、服务端返回响应。订单来单提醒需要服务端主动通知管理端，因此项目引入 WebSocket 长连接。

## 2. 项目中的代码

服务端位置：[`WebSocketServer.java`](../../../sky-server/src/main/java/com/sky/websocket/WebSocketServer.java)

```java
@Component
@ServerEndpoint("/ws/{sid}")
public class WebSocketServer {
    private static Map<String, Session> sessionMap = new HashMap();

    @OnOpen
    public void onOpen(Session session,
                       @PathParam("sid") String sid) { ... }

    @OnClose
    public void onClose(@PathParam("sid") String sid) { ... }

    public void sendToAllClient(String message) { ... }
}
```

`WebSocketConfiguration` 注册 `ServerEndpointExporter`，让 `@ServerEndpoint` 可以被 Spring Boot 应用发现。

## 3. 连接生命周期

```text
客户端连接 /ws/{sid}
  -> @OnOpen 保存 Session
  -> 客户端发送消息时触发 @OnMessage
  -> 业务代码调用 sendToAllClient 主动推送
  -> 客户端断开时触发 @OnClose 并移除 Session
```

订单 Service 在来单或催单场景中调用 WebSocket 服务，把消息推送给管理端。

## 4. HTTP 与 WebSocket 的区别

| 特征 | HTTP | WebSocket |
| --- | --- | --- |
| 连接 | 请求完成后通常结束 | 建立后保持长连接 |
| 发起方向 | 通常客户端发起 | 双方都可发送 |
| 适合场景 | 查询、提交表单 | 来单提醒、聊天、实时状态 |

## 5. 工程注意点

- `HashMap` 不是线程安全的，生产环境应使用并发集合。
- Session 断开、发送失败和网络重连需要处理。
- 多实例部署时，单机内存中的连接表无法跨实例广播，需要消息代理或 WebSocket 网关。
- 推送消息应定义稳定的 JSON 协议，而不是依赖字符串内容。
- 连接身份应经过认证，不能只信任客户端传入的 `sid`。

## 6. 学习练习

定义 `{type, orderId, data}` 消息格式，增加只向指定员工或管理端角色推送的能力，并测试客户端断线重连。

